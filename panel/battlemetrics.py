"""Keeps the panel's bans and a BattleMetrics ban list the same, both ways.

Bans are matched on the player's Reforger ID (BattleMetrics calls it
reforgerUUID). A BattleMetrics ban with no Reforger ID on it can't be tied to
a player here, so it is only counted, never guessed at.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import aiohttp

from .db import now
from .servers import clean, valid_identity

log = logging.getLogger("panel.battlemetrics")

API = "https://api.battlemetrics.com"
EVERY = 60
PUSH_PER_ROUND = 20
LOOKUPS_PER_ROUND = 30
RECHECK = 86400


class BattleMetricsError(Exception):
    pass


def epoch(text):
    if not text:
        return None
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if seconds else None


def plain_reason(text):
    """BattleMetrics reasons can carry its own placeholders, like {{timeLeft}}."""
    text = re.sub(r"\{\{[^}]*\}\}", "", text or "")
    return clean(re.sub(r"\s*[|·:-]+\s*$", "", " ".join(text.split()))) or "Banned on BattleMetrics"


def reforger_ids(ban, included):
    """The Reforger IDs on a ban, whether typed in by hand or linked to a known identifier."""
    found = []
    for item in ban["attributes"].get("identifiers") or []:
        if isinstance(item, dict):
            kind, value = item.get("type"), item.get("identifier")
        else:
            known = included.get(("identifier", str(item))) or {}
            kind, value = known.get("type"), known.get("identifier")
        value = str(value or "").lower()
        if kind == "reforgerUUID" and valid_identity(value) and value not in found:
            found.append(value)
    return found


def related(item, name):
    return str((((item.get("relationships") or {}).get(name) or {}).get("data") or {}).get("id") or "")


def read_bans(pages):
    """Active bans from the listing pages. A Reforger ID BattleMetrics keeps private
    comes without its value, so the ban's player is kept to look it up by."""
    included = {}
    for page in pages:
        for item in page.get("included", []):
            included[(item.get("type"), str(item.get("id")))] = item.get("attributes", {})
    bans = []
    for page in pages:
        for ban in page.get("data", []):
            user = (ban.get("relationships", {}).get("user") or {}).get("data") or {}
            by = (included.get(("user", str(user.get("id")))) or {}).get("nickname") or "BattleMetrics"
            bans.append({"bm_id": str(ban["id"]), "identities": reforger_ids(ban, included),
                         "player": related(ban, "player"),
                         "name": clean(str((ban.get("meta") or {}).get("player") or ""), 64),
                         "reason": plain_reason(ban["attributes"].get("reason")),
                         "expires": epoch(ban["attributes"].get("expires")), "by": clean(by, 40)})
    return bans


class Client:
    def __init__(self, token, session=None, base=API):
        self.token, self.base = token, base
        self.session = session

    async def call(self, method, path, body=None):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        url = path if path.startswith("http") else self.base + path
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        async with self.session.request(method, url, json=body, headers=headers) as response:
            if response.status == 204:
                return None
            data = await response.json(content_type=None)
            if response.status >= 400:
                detail = "; ".join(e.get("detail") or e.get("title") or "" for e in (data or {}).get("errors", []))
                raise BattleMetricsError(f"{response.status} {detail or response.reason}")
            return data

    async def ban_lists(self):
        data = await self.call("GET", "/ban-lists?page[size]=100")
        return [{"id": b["id"], "name": b["attributes"].get("name", ""),
                 "org": ((b.get("relationships", {}).get("owner") or {}).get("data") or {}).get("id")}
                for b in data.get("data", [])]

    async def ban_home(self):
        """The ban list and organization of the newest ban, for a token that can't list ban lists."""
        data = await self.call("GET", "/bans?page[size]=1")
        if not data.get("data"):
            return None
        ban = data["data"][0]
        return {"id": related(ban, "banList"), "name": "", "org": related(ban, "organization")}

    async def player_info(self, player):
        """A BattleMetrics player's Reforger IDs and name."""
        data = await self.call("GET", f"/players/{player}?include=identifier")
        name = clean(str(((data.get("data") or {}).get("attributes") or {}).get("name") or ""), 64)
        found = []
        for item in data.get("included", []):
            attrs = item.get("attributes", {})
            value = str(attrs.get("identifier") or "").lower()
            if item.get("type") == "identifier" and attrs.get("type") == "reforgerUUID" and valid_identity(value):
                found.append(value)
        return found, name

    async def active_bans(self, ban_list):
        pages, url = [], (f"/bans?filter[banList]={ban_list}&filter[expired]=false"
                          "&include=user&page[size]=100")
        while url:
            page = await self.call("GET", url)
            pages.append(page)
            url = (page.get("links") or {}).get("next")
        return read_bans(pages)

    async def create_ban(self, ban, ban_list, org):
        body = {"data": {"type": "ban", "attributes": {
            "reason": ban["reason"][:255], "note": f"From OYB Control, banned by {ban['created_by']}",
            "expires": iso(ban["expires_at"]), "orgWide": True, "autoAddEnabled": True, "nativeEnabled": None,
            "identifiers": [{"type": "reforgerUUID", "identifier": ban["identity"], "manual": True}]},
            "relationships": {"organization": {"data": {"type": "organization", "id": str(org)}},
                              "banList": {"data": {"type": "banList", "id": ban_list}}}}}
        return str((await self.call("POST", "/bans", body))["data"]["id"])

    async def update_reason(self, bm_id, reason):
        await self.call("PATCH", f"/bans/{bm_id}",
                        {"data": {"type": "ban", "id": bm_id, "attributes": {"reason": reason[:255]}}})

    async def delete_ban(self, bm_id):
        await self.call("DELETE", f"/bans/{bm_id}")

    async def close(self):
        if self.session is not None:
            await self.session.close()


class BanSync:
    def __init__(self, db, token, ban_list="", client=None):
        self.db = db
        self.client = client or Client(token)
        self.ban_list, self.org = ban_list, None
        self.status = {"ok": None, "at": 0, "error": "", "unmatched": 0, "imported": 0, "sent": 0}
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self.run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.client.close()

    async def run(self):
        while True:
            try:
                await self.round()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("BattleMetrics sync failed: %s", exc)
                self.status.update(ok=False, at=now(), error=str(exc)[:200])
            await asyncio.sleep(EVERY)

    async def pick_list(self):
        lists = await self.client.ban_lists()
        if not lists:
            home = await self.client.ban_home()
            lists = [home] if home and home["id"] and home["org"] else []
        if self.ban_list:
            chosen = next((b for b in lists if b["id"] == self.ban_list), None)
            if chosen is None:
                raise BattleMetricsError("the ban list in panel.local.json isn't one this token can see")
        elif len(lists) == 1:
            chosen = lists[0]
        else:
            names = ", ".join(f"{b['name']} ({b['id']})" for b in lists) or "none"
            raise BattleMetricsError(f"pick a ban list in panel.local.json; this token sees: {names}")
        self.ban_list, self.org = chosen["id"], chosen["org"]

    async def round(self):
        if not self.org:
            await self.pick_list()
        remote = await self.client.active_bans(self.ban_list)
        waiting = await self.find_players(remote)
        imported = self.bring_in(remote)
        sent = await self.send_out()
        self.status.update(ok=True, at=now(), error="", imported=imported, sent=sent, waiting=waiting,
                           unmatched=sum(1 for b in remote if not b["identities"] and not b.get("pending")))

    async def find_players(self, remote):
        """Fill in what BattleMetrics only gives on the player (a private Reforger ID, or the
        name on older bans), a few lookups a round, remembering each player so it is asked once."""
        budget, waiting = LOOKUPS_PER_ROUND, 0
        for ban in remote:
            if not ban.get("player") or (ban["identities"] and ban.get("name")):
                continue
            known = self.db.bm_player(ban["player"])
            if known and known["named"] and (known["identities"] or now() - known["at"] < RECHECK):
                ban["identities"] = ban["identities"] or known["identities"]
                ban["name"] = ban.get("name") or known["name"]
                continue
            if budget <= 0:
                ban["pending"] = not ban["identities"]
                waiting += 1
                continue
            budget -= 1
            identities, name = await self.client.player_info(ban["player"])
            self.db.save_bm_player(ban["player"], identities, name)
            ban["identities"] = ban["identities"] or identities
            ban["name"] = ban.get("name") or name
        return waiting

    def bring_in(self, remote):
        """New BattleMetrics bans become panel bans; ones gone from BattleMetrics are lifted here."""
        added = 0
        live = {b["bm_id"] for b in remote}
        for ban in remote:
            if self.db.bm_link(ban["bm_id"]):
                if ban.get("name"):
                    self.db.name_bm_bans(ban["bm_id"], ban["name"])
                continue
            for identity in ban["identities"]:
                current = self.db.active_ban(identity)
                if current:
                    if not self.db.bm_ban(current["id"]):
                        self.db.link_bm(current["id"], ban["bm_id"], "panel")
                    continue
                name = (self.db.player(identity) or {"name": ""})["name"] or ban.get("name", "")
                ban_id = self.db.add_ban(identity, name, ban["reason"], f"{ban['by']} (BattleMetrics)", ban["expires"])
                self.db.link_bm(ban_id, ban["bm_id"], "battlemetrics")
                added += 1
        for link in self.db.bm_links_active():
            if link["bm_id"] not in live and (link["expires_at"] is None or link["expires_at"] > now() + 60):
                self.db.remove_ban(link["ban_id"], "BattleMetrics")
                self.db.mark_bm_removed(link["ban_id"])
        return added

    async def send_out(self):
        sent = 0
        for ban in self.db.bans_not_on_bm()[:PUSH_PER_ROUND]:
            bm_id = await self.client.create_ban(ban, self.ban_list, self.org)
            self.db.link_bm(ban["id"], bm_id, "panel")
            sent += 1
        for link in self.db.bm_reasons_changed():
            await self.client.update_reason(link["bm_id"], link["reason"])
            self.db.mark_bm_reason_sent(link["ban_id"])
        for link in self.db.bm_unbans_to_send():
            try:
                await self.client.delete_ban(link["bm_id"])
            except BattleMetricsError as exc:
                if not str(exc).startswith("404"):
                    raise
            self.db.mark_bm_removed(link["ban_id"])
        return sent
