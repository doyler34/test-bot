import re
import unittest
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

from panel import auth
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB, now
from panel.servers import ServerManager
from panel.web import AMP, create_app

PERSON = "0a1b2c3d-0000-4000-8000-000000000001"


class ControlPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = PanelDB(":memory:")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        self.db.add_user("mod", auth.hash_password("mod-password"), "mod")
        config = PanelConfig(database=":memory:", cookie_secure=False,
                             servers=[ServerConfig("one", "Everon Tactical", log_dir="/home/amp/.ampdata/instances/A3x01/logs"),
                                      ServerConfig("two", "Conflict Legacy")])
        self.manager = ServerManager(config, self.db)
        one = self.manager.states["one"]
        one.online, one.online_since = True, now() - 7200
        one.players = [{"name": "Gaz", "identity": PERSON}]
        one.joined = {PERSON: now() - 5400}
        one.query = {"ok": True, "ms": 31.6, "map": "Everon", "players": 1, "slots": 128}
        one.kill_spots.extend([
            {"at": now() - 60, "kind": "teamkill", "killer_name": "Gaz", "victim_name": "Dave", "damage": "KINETIC",
             "distance": 41.2, "killer_at": (4000.0, 5000.0), "victim_at": (4030.0, 5020.0), "by_ai": False},
            {"at": now() - 3000, "kind": "kill", "killer_name": "Old", "victim_name": "News", "damage": "KINETIC",
             "distance": 10, "killer_at": (1, 1), "victim_at": (2, 2), "by_ai": False}])
        for i in range(10):
            self.db.add_server_sample("one", {"players": 1, "proc_cpu": 20 + i, "rss": 3 * 1024 ** 3}, at=now() - 600 + i * 15)
        self.app = create_app(config, self.db, self.manager, start_manager=False)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def login(self, who):
        await self.client.post("/login", data={"username": who, "password": f"{who}-password"})

    async def test_home_has_a_card_per_server_and_each_opens_its_page(self):
        await self.login("boss")
        home = await (await self.client.get("/")).text()
        for words in ("Everon Tactical", "Conflict Legacy", "1 / 128", "32 ms", "No admin on", 'href="/server/one"',
                      'href="/server/two"'):
            self.assertIn(words, home)
        self.assertNotIn("/power/stop", home)      # no AMP, no buttons
        page = await (await self.client.get("/server/one")).text()
        for words in ("Server information", "2h 0m", "Player list", "Gaz", "1h 30m", "CPU usage", "Memory usage",
                      "Network", "Kill map", "Live feed", "aria-label=\"All servers\"", "Memory", "bar cpu"):
            self.assertIn(words, page)
        two = await (await self.client.get("/server/two")).text()
        self.assertIn("The server is offline.", two)
        self.assertEqual((await self.client.get("/control", allow_redirects=False)).headers["Location"], "/")

    async def test_needs_attention(self):
        self.manager.states["one"].players = [{"name": f"P{i}", "identity": ""} for i in range(12)]
        self.db.add_feed("one", "sus", "5 players were blown up in the same second")
        await self.login("boss")
        home = await (await self.client.get("/")).text()
        self.assertIn("Needs attention", home)
        self.assertIn("No admin on Everon Tactical · 12 players", home)
        self.assertIn("1 sus flag in the last hour. Latest: 5 players were blown up", home)

    async def test_kills_from_the_last_half_hour(self):
        await self.login("mod")
        kills = (await (await self.client.get("/server/one/kills.json")).json())["kills"]
        self.assertEqual(len(kills), 1)
        self.assertEqual(kills[0]["killer"], "Gaz")
        self.assertTrue(kills[0]["teamkill"])
        self.assertEqual(kills[0]["to"], [4030.0, 5020.0])

    async def test_power_goes_through_amp_and_needs_permission(self):
        amp = mock.Mock()
        amp.instances = mock.AsyncMock(return_value={"A3x01": {"InstanceID": "x", "InstanceName": "A3x01"}})
        amp.power = mock.AsyncMock()
        self.app[AMP] = amp
        await self.login("boss")
        page = await (await self.client.get("/")).text()
        self.assertIn('action="/server/one/power/stop"', page)
        self.assertIn('action="/server/one/power/update"', page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        reply = await self.client.post("/server/one/power/restart", data={"csrf": token}, allow_redirects=False)
        self.assertEqual(reply.headers["Location"], "/")
        amp.power.assert_awaited_once_with({"InstanceID": "x", "InstanceName": "A3x01"}, "restart")
        self.assertEqual(self.db.all("SELECT action FROM audit WHERE action LIKE '%AMP%'")[0]["action"], "restart through AMP")
        await self.client.post("/logout", data={"csrf": token})
        await self.login("mod")
        page = await (await self.client.get("/")).text()
        self.assertNotIn('action="/server/one/power/stop"', page)
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        reply = await self.client.post("/server/one/power/stop", data={"csrf": token}, allow_redirects=False)
        self.assertEqual(reply.status, 403)
        self.assertEqual(amp.power.await_count, 1)


class PictureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        from panel.web import PICTURES
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = PanelDB(":memory:")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        self.db.add_user("mod", auth.hash_password("mod-password"), "mod")
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[ServerConfig("one", "Everon Tactical")])
        app = create_app(config, self.db, ServerManager(config, self.db), start_manager=False)
        app[PICTURES] = self.folder = Path(self.tmp.name) / "server-pictures"
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def login(self, who):
        await self.client.post("/login", data={"username": who, "password": f"{who}-password"})
        page = await (await self.client.get("/")).text()
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    async def upload(self, token, target, data, name="pic.png"):
        import aiohttp
        form = aiohttp.FormData()
        form.add_field("picture", data, filename=name, content_type="application/octet-stream")
        return await self.client.post(f"/settings/pictures/{target}/picture/upload?csrf={token}", data=form,
                                      allow_redirects=False)

    async def test_senior_admins_set_a_picture_that_shows_on_the_card(self):
        token = await self.login("boss")
        self.assertIn("No picture", await (await self.client.get("/settings/pictures")).text())
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 100
        self.assertEqual((await self.upload(token, "one", png)).headers["Location"], "/settings/pictures")
        self.assertTrue((self.folder / "one.png").is_file())
        home = await (await self.client.get("/")).text()
        address = re.search(r'src="(/server-picture/one\.png\?v=\d+)"', home).group(1)
        self.assertEqual(await (await self.client.get(address)).read(), png)
        jpg = b"\xff\xd8\xff\xe0" + b"\0" * 50
        await self.upload(token, "one", jpg, "new.jpg")
        self.assertFalse((self.folder / "one.png").exists())
        self.assertTrue((self.folder / "one.jpg").is_file())
        await self.upload(token, "one", b"<script>alert(1)</script>", "evil.png")
        self.assertTrue((self.folder / "one.jpg").is_file())
        self.assertEqual([p.name for p in self.folder.iterdir()], ["one.jpg"])
        await self.upload(token, "default", png)
        self.assertTrue((self.folder / "default.png").is_file())
        await self.client.post("/settings/pictures/one/remove", data={"csrf": token})
        self.assertFalse((self.folder / "one.jpg").exists())
        self.assertIn('src="/server-picture/default.png', await (await self.client.get("/")).text())
        self.assertEqual((await self.upload(token, "nope", png)).status, 404)
        self.assertEqual((await self.client.get("/server-picture/../panel.sqlite3")).status, 404)

    async def test_others_cant(self):
        token = await self.login("mod")
        self.assertEqual((await self.client.get("/settings/pictures")).status, 403)
        reply = await self.upload(token, "one", b"\x89PNG\r\n\x1a\n")
        self.assertEqual(reply.status, 403)
        self.assertFalse(self.folder.exists())
