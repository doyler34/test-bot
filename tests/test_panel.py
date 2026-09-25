import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from dev.fake_rcon import SAMPLE_PLAYERS, serve
from panel import auth, memory
from deploy.setup_config import render_unit
from panel.config import PanelConfig, PanelConfigError, ServerConfig, load_config
from panel.db import PanelDB, now
from panel.rcon import RconClient, RconError, packet, parse
from panel.servers import ServerManager, parse_players
from panel.web import create_app

HAVOC = SAMPLE_PLAYERS[0][2]


class PacketTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(parse(packet(1, b"\x05#players")), (1, b"\x05#players"))

    def test_rejects_bad_checksum(self):
        data = bytearray(packet(1, b"\x00hello"))
        data[-1] ^= 1
        self.assertIsNone(parse(bytes(data)))

    def test_rejects_other_traffic(self):
        self.assertIsNone(parse(b"GET / HTTP/1.1"))


class ConfigTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "panel.json"
            path.write_text(json.dumps(data))
            return load_config(path)

    def test_example_loads(self):
        config = load_config(Path(__file__).parents[1] / "panel.example.json")
        self.assertEqual([s.id for s in config.servers], ["server-1", "server-2", "server-3"])
        self.assertFalse(any(s.configured for s in config.servers))
        self.assertEqual(config.listen, "127.0.0.1")

    def test_command_overrides_keep_the_rest(self):
        config = self.load({"servers": [{"id": "s1", "commands": {"players": "players"}}]})
        self.assertEqual(config.servers[0].commands["players"], "players")
        self.assertEqual(config.servers[0].commands["kick"], "#kick {player}")

    def test_bad_values_are_refused(self):
        for server in ({"id": "bad id"}, {"id": "s1", "service": "x; rm -rf /"}):
            with self.assertRaises(PanelConfigError):
                self.load({"servers": [server]})
        with self.assertRaises(PanelConfigError):
            self.load({"servers": [{"id": "s1"}, {"id": "s1"}]})
        with self.assertRaises(PanelConfigError):
            load_config("/nonexistent/panel.json")

    def test_service_unit(self):
        unit = render_unit("/home/gaz/Arma-bot", "gaz", "oyb-panel.service.tpl")
        self.assertIn('ExecStart="/home/gaz/Arma-bot/.venv/bin/python" -m panel serve', unit)
        self.assertIn("User=gaz", unit)


class ParsePlayersTests(unittest.TestCase):
    def test_semicolon_list(self):
        players = parse_players("Players on server:\n0 ; Sgt Havoc ; 5F1C2A90-8B1E-4A57-9A3E-2D4B6C8E0F11\n")
        self.assertEqual(players, [{"id": "0", "name": "Sgt Havoc", "identity": HAVOC}])

    def test_real_reforger_reply(self):
        output = ("Players on server: [Player#] ; [Player UID] ; [Player Name]\n"
                  "1 ; d0d8bcaf-e30b-49e2-b32a-9618871f3b89 ; GazLagom")
        self.assertEqual(parse_players(output),
                         [{"id": "1", "name": "GazLagom", "identity": "d0d8bcaf-e30b-49e2-b32a-9618871f3b89"}])

    def test_without_identity(self):
        self.assertEqual(parse_players("3  Rook"), [{"id": "3", "name": "Rook", "identity": ""}])

    def test_headers_and_blank_lines_are_skipped(self):
        self.assertEqual(parse_players("Players:\n\n[#] [Name]\n(2 players in total)"), [])


class AuthTests(unittest.TestCase):
    def test_password_hash(self):
        stored = auth.hash_password("correct horse")
        self.assertTrue(auth.check_password("correct horse", stored))
        self.assertFalse(auth.check_password("wrong horse", stored))
        self.assertFalse(auth.check_password("x", "garbage"))

    def test_roles(self):
        self.assertTrue(auth.can("moderator", "kick"))
        self.assertFalse(auth.can("moderator", "ban"))
        self.assertTrue(auth.can("admin", "ban"))
        self.assertFalse(auth.can("admin", "console"))
        self.assertTrue(auth.can("owner", "users"))
        self.assertFalse(auth.can("nobody", "view"))

    def test_throttle(self):
        throttle = auth.LoginThrottle(limit=3)
        for _ in range(3):
            self.assertFalse(throttle.blocked("Bob"))
            throttle.failed("bob")
        self.assertTrue(throttle.blocked("BOB"))
        throttle.cleared("bob")
        self.assertFalse(throttle.blocked("bob"))


class BanQueueTests(unittest.TestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)

    def test_pending_until_applied(self):
        ban = self.db.add_ban(HAVOC, "Havoc", "teamkilling", "owner")
        self.assertEqual([b["id"] for b in self.db.pending_bans("server-1")], [ban])
        self.db.mark_synced(ban, "server-1", "ban")
        self.assertEqual(self.db.pending_bans("server-1"), [])
        self.assertEqual(len(self.db.pending_bans("server-2")), 1)

    def test_unban_only_where_it_was_applied(self):
        ban = self.db.add_ban(HAVOC, "Havoc", "teamkilling", "owner")
        self.db.mark_synced(ban, "server-1", "ban")
        self.db.remove_ban(ban, "owner")
        self.assertEqual([b["id"] for b in self.db.pending_unbans("server-1")], [ban])
        self.assertEqual(self.db.pending_unbans("server-2"), [])
        self.assertEqual(self.db.pending_bans("server-2"), [])

    def test_expired_bans_are_not_pushed(self):
        self.db.add_ban(HAVOC, "Havoc", "spam", "owner", expires_at=now() - 5)
        self.assertEqual(self.db.pending_bans("server-1"), [])
        self.assertIsNone(self.db.active_ban(HAVOC))


GB = 1024 ** 3


class MemoryTests(unittest.TestCase):
    def test_verdict_against_fresh_start(self):
        self.assertEqual(memory.verdict(6 * GB, int(3.2 * GB), 3 * GB)[0], "ok")
        status, message = memory.verdict(6 * GB, int(5.5 * GB), 3 * GB)
        self.assertEqual(status, "leak")
        self.assertIn("Still holding 2.5 GB more than a fresh start", message)

    def test_verdict_without_fresh_start(self):
        self.assertEqual(memory.verdict(6 * GB, int(5.8 * GB), 0)[0], "leak")
        self.assertEqual(memory.verdict(6 * GB, 3 * GB, 0)[0], "unknown")

    def test_reads_proc(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "42").mkdir()
            Path(root, "42", "status").write_text("Name:\tArmaReforgerSer\nVmRSS:\t 2097152 kB\n")
            ticks = os.sysconf("SC_CLK_TCK")
            fields = ["S"] + ["0"] * 18 + [str(100 * ticks)]
            Path(root, "42", "stat").write_text("42 (Arma Reforger) " + " ".join(fields) + " 0 0")
            Path(root, "uptime").write_text("400.5 1000.0\n")
            Path(root, "meminfo").write_text("MemTotal:       8000000 kB\n")
            self.assertEqual(memory.process_memory(42, root), {"pid": 42, "rss": 2 * GB, "age": 300})
            self.assertIsNone(memory.process_memory(43, root))
            self.assertEqual(memory.box_total(root), 8000000 * 1024)


class FakeProcess:
    def __init__(self):
        self.sample = {"pid": 100, "rss": 3 * GB, "age": 200}

    async def __call__(self, unit):
        return dict(self.sample) if self.sample else None


class MissionCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        config = panel_config(1, service="reforger-test")
        self.proc = FakeProcess()
        self.manager = ServerManager(config, self.db, sampler=self.proc)
        self.manager.settle = 0
        self.state = self.manager.state("server-1")

    async def test_fresh_start_reading(self):
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.fresh, 3 * GB)
        self.proc.sample.update(rss=5 * GB, age=5000)
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.fresh, 3 * GB)
        self.proc.sample.update(pid=101, rss=6 * GB, age=5000)
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.fresh, 0)

    async def test_memory_left_behind_is_flagged(self):
        await self.manager.refresh_memory(self.state)
        self.proc.sample["rss"] = 6 * GB
        await self.manager.start_mission_check("server-1", "gaz")
        self.assertEqual(self.state.check["before"], 6 * GB)
        self.proc.sample["rss"] = int(5.5 * GB)
        self.state.check["started"] -= 1
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.check["status"], "leak")
        entry = self.db.audit()[0]
        self.assertEqual((entry["username"], entry["action"], entry["ok"]), ("gaz", "mission restart check", 0))

    async def test_memory_cleared(self):
        await self.manager.refresh_memory(self.state)
        await self.manager.start_mission_check("server-1", "gaz")
        self.proc.sample["rss"] = int(3.1 * GB)
        self.state.check["started"] -= 1
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.check["status"], "ok")

    async def test_waits_for_the_mission_to_load(self):
        self.manager.settle = 180
        await self.manager.start_mission_check("server-1", "gaz")
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.check["status"], "waiting")

    async def test_process_restart_counts_as_cleared(self):
        await self.manager.start_mission_check("server-1", "gaz")
        self.proc.sample.update(pid=555)
        self.state.check["started"] -= 1
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.check["status"], "ok")
        self.assertIn("restarted", self.state.check["message"])

    async def test_no_service_no_check(self):
        await self.manager.start_mission_check("server-2", "gaz")
        self.assertIsNone(self.manager.state("server-2").check)


class RconClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport, self.fake = await serve(0, "secret", chunk=20, direct=True)
        self.port = self.transport.get_extra_info("sockname")[1]
        self.addCleanup(self.transport.close)

    async def test_login_and_multipart_reply(self):
        client = RconClient("127.0.0.1", self.port, "secret", timeout=2)
        await client.connect()
        self.addCleanup(client.close)
        self.assertEqual(await client.command("#players"), self.fake.players_text())

    async def test_reforger_sends_output_as_messages(self):
        self.fake.direct = False
        client = RconClient("127.0.0.1", self.port, "secret", timeout=2)
        await client.connect()
        self.addCleanup(client.close)
        self.assertEqual(await client.command("#players"), self.fake.players_text())
        self.assertEqual(await client.command("#kick 1"), "Player 1 kicked")

    async def test_unknown_command_is_an_error(self):
        self.fake.direct = False
        client = RconClient("127.0.0.1", self.port, "secret", timeout=2)
        await client.connect()
        self.addCleanup(client.close)
        with self.assertRaisesRegex(RconError, "unknown command 'unknown'"):
            await client.command("#unknown")

    async def test_no_output_does_not_hang(self):
        self.fake.direct = False
        self.fake.reply = lambda text: ""
        client = RconClient("127.0.0.1", self.port, "secret", timeout=2, output_wait=0.2)
        await client.connect()
        self.addCleanup(client.close)
        self.assertEqual(await client.command("#restart"), "")

    async def test_wrong_password(self):
        client = RconClient("127.0.0.1", self.port, "nope", timeout=2)
        with self.assertRaisesRegex(RconError, "wrong rcon password"):
            await client.connect()

    async def test_server_messages_are_acknowledged(self):
        client = RconClient("127.0.0.1", self.port, "secret", timeout=2)
        seen = []
        client.on_message = seen.append
        await client.connect()
        self.addCleanup(client.close)
        self.fake.say("hello", seq=7)
        await client.command("#players")
        self.assertEqual(seen, ["hello"])

    async def test_nobody_listening(self):
        client = RconClient("127.0.0.1", 1, "secret", timeout=0.3)
        with self.assertRaises((RconError, OSError)):
            await client.connect()


def panel_config(port, **extra):
    server = ServerConfig(id="server-1", name="Server 1", rcon_port=port, rcon_password="secret", **extra)
    spare = ServerConfig(id="server-2", name="Server 2")
    return PanelConfig(database=":memory:", cookie_secure=False, poll_seconds=0.05, servers=[server, spare])


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport, self.fake = await serve(0, "secret")
        self.addCleanup(self.transport.close)
        port = self.transport.get_extra_info("sockname")[1]
        self.db = PanelDB(":memory:")
        self.manager = ServerManager(panel_config(port), self.db)

    async def asyncTearDown(self):
        await self.manager.stop()
        self.db.close()

    async def wait_for(self, check):
        for _ in range(100):
            if check():
                return
            await asyncio.sleep(0.02)
        self.fail("timed out")

    async def test_players_are_polled_and_remembered(self):
        self.manager.start()
        state = self.manager.state("server-1")
        await self.wait_for(lambda: len(state.players) == 3)
        self.assertTrue(state.online)
        self.assertEqual(self.db.player(HAVOC)["name"], "Sgt Havoc")
        self.assertIn("not set up", self.manager.state("server-2").error)

    async def test_ban_made_while_offline_is_applied_on_connect(self):
        ban = self.db.add_ban(HAVOC, "Havoc", "teamkilling", "owner")
        self.assertEqual(await self.manager.push_ban(self.db.ban(ban)), {"server-1": "queued until the server is back"})
        self.manager.start()
        await self.wait_for(lambda: HAVOC in self.fake.bans)
        self.assertEqual(self.fake.bans[HAVOC], "teamkilling")
        self.assertIn(f"#ban create {HAVOC} 0 teamkilling", self.fake.commands)

    async def test_kick_rejects_non_numeric_ids(self):
        self.manager.start()
        await self.wait_for(lambda: self.manager.state("server-1").online)
        with self.assertRaises(RconError):
            await self.manager.kick("server-1", "0; #shutdown")
        await self.manager.kick("server-1", "1")
        self.assertIn("#kick 1", self.fake.commands)

    async def test_service_actions_need_a_service(self):
        with self.assertRaisesRegex(RconError, "no systemd service"):
            await self.manager.power("server-1", "stop")


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport, self.fake = await serve(0, "secret")
        self.addCleanup(self.transport.close)
        port = self.transport.get_extra_info("sockname")[1]
        self.config = panel_config(port)
        self.db = PanelDB(":memory:")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        self.db.add_user("mod", auth.hash_password("mod-password"), "moderator")
        self.manager = ServerManager(self.config, self.db)
        self.manager.start()
        app = create_app(self.config, self.db, self.manager, start_manager=False)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        for _ in range(100):
            if self.manager.state("server-1").players:
                break
            await asyncio.sleep(0.02)

    async def asyncTearDown(self):
        await self.client.close()
        await self.manager.stop()
        self.db.close()

    async def login(self, username, password):
        response = await self.client.post("/login", data={"username": username, "password": password})
        return response

    async def csrf(self, path="/"):
        html = await (await self.client.get(path)).text()
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    async def test_pages_need_a_login(self):
        response = await self.client.get("/bans", allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual((await self.client.get("/cards.part")).status, 401)

    async def test_wrong_password_is_logged(self):
        response = await self.login("boss", "nope")
        self.assertIn("Wrong username or password", await response.text())
        self.assertEqual(self.db.audit()[0]["action"], "login failed")

    async def test_dashboard_shows_live_server(self):
        await self.login("boss", "boss-password")
        html = await (await self.client.get("/")).text()
        self.assertIn("Online", html)
        self.assertIn("Not set up", html)
        html = await (await self.client.get("/server/server-1")).text()
        self.assertIn("Sgt Havoc", html)
        self.assertEqual((await self.client.get("/")).headers["X-Frame-Options"], "DENY")

    async def test_post_without_csrf_is_refused(self):
        await self.login("boss", "boss-password")
        response = await self.client.post("/server/server-1/kick", data={"player": "0"})
        self.assertEqual(response.status, 403)
        self.assertNotIn("#kick 0", self.fake.commands)

    async def test_kick_is_audited(self):
        await self.login("mod", "mod-password")
        token = await self.csrf()
        await self.client.post("/server/server-1/kick", data={"csrf": token, "player": "0", "name": "Sgt Havoc"})
        self.assertIn("#kick 0", self.fake.commands)
        entry = self.db.audit()[0]
        self.assertEqual((entry["username"], entry["action"], entry["target"]), ("mod", "kick", "Sgt Havoc"))

    async def test_moderator_cannot_ban_or_use_console(self):
        await self.login("mod", "mod-password")
        token = await self.csrf()
        response = await self.client.post("/bans", data={"csrf": token, "identity": HAVOC, "reason": "x", "duration": "0"})
        self.assertEqual(response.status, 403)
        self.assertEqual((await self.client.get("/console")).status, 403)
        self.assertNotIn("Console", await (await self.client.get("/")).text())

    async def test_ban_and_unban_hit_the_server(self):
        await self.login("boss", "boss-password")
        token = await self.csrf("/bans")
        html = await (await self.client.post("/bans", data={
            "csrf": token, "identity": HAVOC.upper(), "name": "Sgt Havoc", "reason": "team killing", "duration": "86400"})).text()
        self.assertIn("Banned Sgt Havoc. server-1: done", html)
        self.assertEqual(self.fake.bans[HAVOC], "team killing")
        ban = self.db.active_ban(HAVOC)
        await self.client.post(f"/bans/{ban['id']}/remove", data={"csrf": token})
        self.assertNotIn(HAVOC, self.fake.bans)
        self.assertEqual([r["action"] for r in self.db.audit(limit=2)], ["unban", "ban"])

    async def test_ban_needs_a_real_identity(self):
        await self.login("boss", "boss-password")
        token = await self.csrf("/bans")
        html = await (await self.client.post("/bans", data={
            "csrf": token, "identity": "Havoc", "reason": "x", "duration": "0"})).text()
        self.assertIn("not a Reforger identity ID", html)
        self.assertEqual(self.db.bans(), [])

    async def test_new_admin_must_change_password(self):
        await self.login("boss", "boss-password")
        token = await self.csrf("/users")
        html = await (await self.client.post("/users", data={"csrf": token, "username": "newbie", "role": "admin"})).text()
        password = re.search(r'Temporary password: <b class="mono">([^<]+)</b>', html).group(1)
        await self.client.post("/logout", data={"csrf": token})
        await self.login("newbie", password)
        response = await self.client.get("/bans", allow_redirects=False)
        self.assertEqual(response.headers["Location"], "/account")
        token = await self.csrf("/account")
        await self.client.post("/account", data={"csrf": token, "current": password, "new": "a-new-password", "confirm": "a-new-password"})
        self.assertEqual((await self.client.get("/bans", allow_redirects=False)).status, 200)

    async def test_last_owner_cannot_be_demoted(self):
        await self.login("boss", "boss-password")
        token = await self.csrf("/users")
        boss = self.db.user_by_name("boss")
        html = await (await self.client.post(f"/users/{boss['id']}", data={"csrf": token, "action": "role", "role": "admin"})).text()
        self.assertIn("at least one owner", html)
        self.assertEqual(self.db.user_by_name("boss")["role"], "owner")

    async def test_disabled_admin_is_logged_out(self):
        await self.login("mod", "mod-password")
        mod_cookies = self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))
        self.client.session.cookie_jar.clear()
        await self.login("boss", "boss-password")
        token = await self.csrf("/users")
        await self.client.post(f"/users/{self.db.user_by_name('mod')['id']}", data={"csrf": token, "action": "disable"})
        self.client.session.cookie_jar.clear()
        self.client.session.cookie_jar.update_cookies(mod_cookies)
        response = await self.client.get("/", allow_redirects=False)
        self.assertEqual(response.headers["Location"], "/login")

    async def test_restart_mission_starts_a_memory_check(self):
        state = self.manager.state("server-1")
        state.config.service = "reforger-test"
        self.manager.sampler = FakeProcess()
        await self.login("boss", "boss-password")
        token = await self.csrf("/server/server-1")
        html = await (await self.client.post("/server/server-1/power", data={"csrf": token, "action": "restart_mission"})).text()
        self.assertIn("Memory check in 3 minutes", html)
        self.assertIn("Mission restart check", html)
        self.assertIn("#restart", self.fake.commands)
        self.assertEqual(state.check["by"], "boss")

    async def test_console_is_audited(self):
        await self.login("boss", "boss-password")
        token = await self.csrf("/console")
        html = await (await self.client.post("/console", data={"csrf": token, "server": "server-1", "command": "#status"})).text()
        self.assertIn("ok: #status", html)
        self.assertEqual(self.db.audit()[0]["target"], "#status")

    async def test_names_are_escaped(self):
        self.fake.players.append(("9", "<script>alert(1)</script>", "11111111-2222-4333-8444-555555555555"))
        await self.manager.refresh_players(self.manager.state("server-1"))
        await self.login("boss", "boss-password")
        html = await (await self.client.get("/server/server-1")).text()
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
