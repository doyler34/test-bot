"""Talks to AMP (CubeCoders) to read and change each server's in-game admin list.

The list is AMP's "Admins" setting (game.admins in serverconfig.json). AMP writes
serverconfig.json itself when the server starts, so changing the file directly
would be undone; the change has to go through AMP and needs a restart to apply.
"""
import json
import re

import aiohttp

MAX_ADMINS = 20
TIMEOUT = aiohttp.ClientTimeout(total=15)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "OYB-Control"}


class AmpError(Exception):
    pass


def parse_ids(value) -> list[str]:
    """AMP keeps the list as text: "id","id" (the format its own settings page asks for)."""
    if isinstance(value, list):
        items = value
    else:
        text = str(value or "").strip()
        quoted = re.findall(r'"([^"]*)"', text)
        items = quoted if quoted else re.split(r"[\s,]+", text)
    seen = []
    for item in items:
        item = str(item).strip().lower()
        if item and item not in seen:
            seen.append(item)
    return seen


def format_ids(ids) -> str:
    return ",".join(f'"{i}"' for i in ids)


def instance_name(log_dir: str) -> str:
    """AMP keeps each server under /home/amp/.ampdata/instances/<name>/..., logs included."""
    parts = [p for p in str(log_dir).split("/") if p]
    if "instances" in parts and parts.index("instances") + 1 < len(parts):
        return parts[parts.index("instances") + 1]
    return ""


class Amp:
    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.nodes = {}

    async def _post(self, http, base, endpoint, args):
        try:
            async with http.post(f"{base}/API/{endpoint}", data=json.dumps(args), headers=HEADERS) as response:
                if response.status in (401, 403):
                    raise AmpError("AMP refused the login. Check the AMP username and password the panel uses.")
                if response.status >= 400:
                    raise AmpError(f"AMP answered {response.status} to {endpoint}.")
                text = await response.text()
        except aiohttp.ClientError as exc:
            raise AmpError(f"Couldn't reach AMP at {self.url}: {exc}") from None
        except TimeoutError:
            raise AmpError(f"AMP at {self.url} didn't answer in time.") from None
        try:
            data = json.loads(text) if text.strip() else None
        except ValueError:
            raise AmpError(f"AMP sent something that isn't JSON for {endpoint}.") from None
        if isinstance(data, dict) and "StackTrace" in data:
            raise AmpError(f"AMP error on {endpoint}: {data.get('Message') or data.get('Title') or 'unknown'}")
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        return data

    async def _login(self, http, base):
        result = await self._post(http, base, "Core/Login", {"username": self.username, "password": self.password,
                                                              "token": "", "rememberMe": False})
        if not isinstance(result, dict) or not result.get("success") or not result.get("sessionID"):
            reason = result.get("resultReason") if isinstance(result, dict) else ""
            raise AmpError(f"AMP refused the login{': ' + str(reason) if reason else ''}. "
                           "Check the AMP username and password the panel uses.")
        return result["sessionID"]

    async def _call(self, http, base, session, endpoint, args=None):
        return await self._post(http, base, endpoint, {**(args or {}), "SESSIONID": session})

    async def instances(self) -> dict:
        """Every instance AMP manages, by its instance name (the folder name under instances/)."""
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            session = await self._login(http, self.url)
            targets = await self._call(http, self.url, session, "ADSModule/GetInstances", {"ForceIncludeSelf": False})
        found = {}
        for target in targets if isinstance(targets, list) else []:
            for inst in target.get("AvailableInstances") or []:
                if inst.get("InstanceName") and inst.get("Module") != "ADS":
                    found[inst["InstanceName"]] = inst
        return found

    async def instance_power(self, name, action):
        """Start or stop a whole AMP instance (its manager, not just the game) through ADS."""
        endpoint = {"start": "ADSModule/StartInstance", "stop": "ADSModule/StopInstance"}[action]
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            session = await self._login(http, self.url)
            result = await self._call(http, self.url, session, endpoint, {"InstanceName": name})
        if isinstance(result, dict) and result.get("Status") is False:
            raise AmpError(f"AMP didn't {action} {name}: {result.get('Reason') or 'no reason given'}")

    async def _instance(self, http, inst):
        """A logged-in way to reach one instance: through ADS first, straight to its own port if that fails."""
        routes = [f"{self.url}/API/ADSModule/Servers/{inst['InstanceID']}"]
        if inst.get("Port"):
            scheme = "https" if inst.get("IsHTTPS") else "http"
            routes.append(f"{scheme}://{inst.get('IP') or '127.0.0.1'}:{inst['Port']}")
        problem = None
        for base in routes:
            try:
                return base, await self._login(http, base)
            except AmpError as exc:
                problem = exc
        raise problem

    async def _admins_node(self, http, base, session, inst):
        node = self.nodes.get(inst["InstanceID"])
        if node:
            return node
        spec = await self._call(http, base, session, "Core/GetSettingsSpec")
        settings = [s for group in (spec.values() if isinstance(spec, dict) else []) for s in group or []
                    if isinstance(s, dict) and s.get("Node")]
        matches = [s["Node"] for s in settings if s["Node"].lower().split(".")[-1] == "admins"] or \
                  [s["Node"] for s in settings if str(s.get("Name", "")).strip().lower() == "admins"]
        if len(matches) != 1:
            raise AmpError(f"Couldn't find the Admins setting in AMP for {inst.get('FriendlyName') or inst['InstanceName']}.")
        self.nodes[inst["InstanceID"]] = matches[0]
        return matches[0]

    async def _setting_node(self, http, base, session, inst, key):
        cached = self.nodes.get((inst["InstanceID"], key))
        if cached:
            return cached
        spec = await self._call(http, base, session, "Core/GetSettingsSpec")
        settings = [s for group in (spec.values() if isinstance(spec, dict) else []) for s in group or []
                    if isinstance(s, dict) and s.get("Node")]
        matches = [s["Node"] for s in settings if s["Node"].lower().split(".")[-1] == key.lower()]
        if len(matches) != 1:
            raise AmpError(f"Couldn't find the {key} setting in AMP for {inst.get('FriendlyName') or inst['InstanceName']}.")
        self.nodes[(inst["InstanceID"], key)] = matches[0]
        return matches[0]

    async def setting(self, inst, key) -> str:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            base, session = await self._instance(http, inst)
            node = await self._setting_node(http, base, session, inst, key)
            found = await self._call(http, base, session, "Core/GetConfig", {"node": node})
        return str(found.get("CurrentValue") or "") if isinstance(found, dict) else ""

    async def change_setting(self, inst, key, change):
        """Reads the value fresh, runs change() on it and writes the result back,
        so an edit made in AMP in the meantime isn't lost."""
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            base, session = await self._instance(http, inst)
            node = await self._setting_node(http, base, session, inst, key)
            found = await self._call(http, base, session, "Core/GetConfig", {"node": node})
            value = change(str(found.get("CurrentValue") or "") if isinstance(found, dict) else "")
            result = await self._call(http, base, session, "Core/SetConfig", {"node": node, "value": value})
            if isinstance(result, dict) and result.get("Status") is False:
                raise AmpError(f"AMP didn't save it: {result.get('Reason') or 'no reason given'}")
        return value

    async def admins(self, inst) -> list[str]:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            base, session = await self._instance(http, inst)
            node = await self._admins_node(http, base, session, inst)
            setting = await self._call(http, base, session, "Core/GetConfig", {"node": node})
        return parse_ids(setting.get("CurrentValue") if isinstance(setting, dict) else "")

    async def change_admins(self, inst, add=(), remove=()) -> list[str]:
        """Reads the list fresh, applies the change and writes it back, so edits made in AMP aren't lost."""
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            base, session = await self._instance(http, inst)
            node = await self._admins_node(http, base, session, inst)
            setting = await self._call(http, base, session, "Core/GetConfig", {"node": node})
            ids = parse_ids(setting.get("CurrentValue") if isinstance(setting, dict) else "")
            ids = [i for i in ids if i not in remove] + [i for i in add if i not in ids]
            if len(ids) > MAX_ADMINS:
                raise AmpError(f"Reforger allows up to {MAX_ADMINS} admins per server.")
            result = await self._call(http, base, session, "Core/SetConfig", {"node": node, "value": format_ids(ids)})
            if isinstance(result, dict) and result.get("Status") is False:
                raise AmpError(f"AMP didn't save it: {result.get('Reason') or 'no reason given'}")
            check = await self._call(http, base, session, "Core/GetConfig", {"node": node})
            saved = parse_ids(check.get("CurrentValue") if isinstance(check, dict) else "")
            if saved != ids:
                raise AmpError("AMP took the change but reads back something different. Check the Admins setting in AMP.")
            return saved

    async def restart(self, inst):
        await self.power(inst, "restart")

    async def power(self, inst, action):
        """Start, stop, restart or update the game server in one instance."""
        endpoint = {"start": "Core/Start", "stop": "Core/Stop", "restart": "Core/Restart",
                    "update": "Core/UpdateApplication"}[action]
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            base, session = await self._instance(http, inst)
            result = await self._call(http, base, session, endpoint)
        if isinstance(result, dict) and result.get("Status") is False:
            raise AmpError(f"AMP didn't {action} it: {result.get('Reason') or 'no reason given'}")
