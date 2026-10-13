import json
import re
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from panel import auth
from panel.amp import Amp, AmpError, format_ids, instance_name, parse_ids
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB
from panel.servers import ServerManager
from panel.web import create_app

ALICE = "0f6e1a3c-1111-4c2a-9a55-5b6f0e2a0001"
BOB = "0f6e1a3c-2222-4c2a-9a55-5b6f0e2a0002"
NODE = "GenericModule.App.admins"


class FakeAmp:
    """Just enough of AMP's API: an ADS that lists instances and forwards to them."""

    def __init__(self):
        self.admins = {"inst-everon": f'"{ALICE}"', "inst-modded": ""}
        self.restarts = []
        self.power = []
        self.proxy = True
        self.app = web.Application()
        self.app.router.add_post("/API/{endpoint:.+}", self.handle)

    async def handle(self, request):
        body = json.loads(await request.text())
        endpoint = request.match_info["endpoint"]
        match = re.fullmatch(r"ADSModule/Servers/([\w-]+)/API/(.+)", endpoint)
        if match and not self.proxy:
            raise web.HTTPNotFound()
        if (match.group(2) if match else endpoint) == "Core/Login":
            ok = body["username"] == "panel" and body["password"] == "pw"
            return web.json_response({"success": ok, "sessionID": "s1" if ok else "", "resultReason": "" if ok else "Bad login"})
        if body.get("SESSIONID") != "s1":
            return web.json_response({"Title": "Unauthorized", "Message": "No session", "StackTrace": ""})
        if endpoint == "ADSModule/GetInstances":
            return web.json_response({"result": [{"AvailableInstances": [
                {"InstanceID": "ads", "InstanceName": "ADS01", "Module": "ADS"},
                {"InstanceID": "inst-everon", "InstanceName": "OGEveron01", "FriendlyName": "OG Everon", "Module": "GenericModule"},
                {"InstanceID": "inst-modded", "InstanceName": "Modded01", "FriendlyName": "Modded", "Module": "GenericModule"},
            ]}]})
        if not match:
            raise web.HTTPNotFound()
        inst, method = match.groups()
        if method == "Core/GetSettingsSpec":
            return web.json_response({"result": {"Arma Reforger:stadia_controller": [
                {"Node": "GenericModule.App.adminPassword", "Name": "Admin Password", "CurrentValue": "x"},
                {"Node": NODE, "Name": "Admins", "CurrentValue": self.admins[inst]},
            ]}})
        if method == "Core/GetConfig":
            return web.json_response({"Node": body["node"], "CurrentValue": self.admins[inst]})
        if method == "Core/SetConfig":
            self.admins[inst] = body["value"]
            return web.json_response({"Status": True, "Reason": ""})
        if method == "Core/Restart":
            self.restarts.append(inst)
            return web.json_response({"Status": True})
        if method in ("Core/Start", "Core/Stop"):
            self.power.append((inst, method))
            return web.json_response({"Status": True})
        raise web.HTTPNotFound()


class ParseTests(unittest.TestCase):
    def test_amps_text_format(self):
        self.assertEqual(parse_ids(f'"{ALICE}","76561198000000000"'), [ALICE, "76561198000000000"])
        self.assertEqual(parse_ids(f"{ALICE}, {BOB}\n{ALICE}"), [ALICE, BOB])
        self.assertEqual(parse_ids(""), [])
        self.assertEqual(format_ids([ALICE, BOB]), f'"{ALICE}","{BOB}"')
        self.assertEqual(instance_name("/home/amp/.ampdata/instances/OGEveron01/arma-reforger/1874900/AReforgerMaster/logs"),
                         "OGEveron01")
        self.assertEqual(instance_name("/srv/logs"), "")


class AmpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeAmp()
        self.server = TestServer(self.fake.app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.amp = Amp(str(self.server.make_url("")), "panel", "pw")

    async def test_reads_and_changes_the_list_through_ads(self):
        found = await self.amp.instances()
        self.assertEqual(set(found), {"OGEveron01", "Modded01"})
        everon = found["OGEveron01"]
        self.assertEqual(await self.amp.admins(everon), [ALICE])
        self.assertEqual(await self.amp.change_admins(everon, add=[BOB]), [ALICE, BOB])
        self.assertEqual(self.fake.admins["inst-everon"], f'"{ALICE}","{BOB}"')
        self.assertEqual(await self.amp.change_admins(everon, remove=[ALICE]), [BOB])

    async def test_a_wrong_password_says_so(self):
        with self.assertRaisesRegex(AmpError, "refused the login"):
            await Amp(str(self.server.make_url("")), "panel", "nope").instances()

    async def test_the_twenty_id_limit(self):
        everon = (await self.amp.instances())["OGEveron01"]
        many = [f"0f6e1a3c-{n:04d}-4c2a-9a55-5b6f0e2a0000" for n in range(20)]
        with self.assertRaisesRegex(AmpError, "up to 20"):
            await self.amp.change_admins(everon, add=many)
        self.assertEqual(self.fake.admins["inst-everon"], f'"{ALICE}"')


class GameAdminPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeAmp()
        self.amp_server = TestServer(self.fake.app)
        await self.amp_server.start_server()
        self.addAsyncCleanup(self.amp_server.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "instances"
        logs = {}
        for name in ("OGEveron01", "Modded01"):
            game = root / name / "arma-reforger" / "1874900"
            (game / "AReforgerMaster" / "logs").mkdir(parents=True)
            (game / "Configs").mkdir()
            logs[name] = str(game / "AReforgerMaster" / "logs")
        self.everon_config = Path(logs["OGEveron01"]).parent.parent / "Configs" / "serverconfig.json"
        self.everon_config.write_text(json.dumps({"game": {"admins": [ALICE]}}))
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[
            ServerConfig(id="everon", name="OG Everon", log_dir=logs["OGEveron01"]),
            ServerConfig(id="modded", name="Modded", log_dir=logs["Modded01"]),
        ], amp={"url": str(self.amp_server.make_url("")), "username": "panel", "password": "pw"})
        self.db = PanelDB(":memory:")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        self.db.add_user("mod", auth.hash_password("mod-password"), "moderator")
        self.db.saw_player(BOB, "Bob", "everon")
        self.client = TestClient(TestServer(create_app(config, self.db, ServerManager(config, self.db), start_manager=False)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def login(self, username, password):
        await self.client.post("/login", data={"username": username, "password": password})
        html = await (await self.client.get("/game-admins")).text()
        found = re.search(r'name="csrf" value="([^"]+)"', html)
        return html, found.group(1) if found else ""

    async def test_owner_adds_on_two_servers_then_restarts_one(self):
        page, token = await self.login("boss", "boss-password")
        self.assertIn("OG Everon", page)
        self.assertIn(ALICE, page)
        self.assertIn("Live", page)
        await self.client.post("/game-admins", data={"csrf": token, "player": "Bob", "identity": BOB,
                                                     "server": ["everon", "modded"]})
        self.assertEqual(self.fake.admins, {"inst-everon": f'"{ALICE}","{BOB}"', "inst-modded": f'"{BOB}"'})
        page = await (await self.client.get("/game-admins")).text()
        self.assertIn("Restart to apply", page)
        self.assertIn('href="/player/' + BOB + '">Bob</a>', page)
        await self.client.post("/game-admins/everon/restart", data={"csrf": token})
        self.assertEqual(self.fake.restarts, ["inst-everon"])
        await self.client.post("/game-admins/modded/remove", data={"csrf": token, "identity": BOB})
        self.assertEqual(self.fake.admins["inst-modded"], "")
        actions = [row["action"] for row in self.db.audit()]
        self.assertEqual(actions[:4], ["removed game admin", "restart through AMP", "made game admin", "made game admin"])

    async def test_server_page_starts_and_stops_through_amp(self):
        await self.client.post("/login", data={"username": "boss", "password": "boss-password"})
        page = await (await self.client.get("/server/everon")).text()
        self.assertIn("Stop server", page)
        self.assertNotIn("done outside the panel", page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        await self.client.post("/server/everon/power", data={"csrf": token, "action": "stop"})
        await self.client.post("/server/everon/power", data={"csrf": token, "action": "start"})
        await self.client.post("/server/everon/power", data={"csrf": token, "action": "restart"})
        self.assertEqual(self.fake.power, [("inst-everon", "Core/Stop"), ("inst-everon", "Core/Start")])
        self.assertEqual(self.fake.restarts, ["inst-everon"])
        self.assertEqual([row["action"] for row in self.db.audit()][:3], ["restart server", "start server", "stop server"])

    async def test_moderators_cant_see_it(self):
        await self.client.post("/login", data={"username": "mod", "password": "mod-password"})
        self.assertEqual((await self.client.get("/game-admins")).status, 403)
        self.assertNotIn('href="/game-admins"', await (await self.client.get("/")).text())

    async def test_amp_down_is_explained(self):
        self.fake.proxy = False
        page, _ = await self.login("boss", "boss-password")
        self.assertIn("AMP answered 404", page)
        self.assertNotIn("Restart now", page)


if __name__ == "__main__":
    unittest.main()
