import asyncio
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from dev.fake_rcon import SAMPLE_PLAYERS, serve
import sqlite3

from aiohttp import web as aioweb

from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate as migrate_combat, record as record_kill
from bot.tracking.combat_parser import KillEvent
from panel import auth, memory
from panel.alerts import Alerts
from panel.connections import LogReader
from panel.oyb_stats import OybStats
from panel.web import memory_chart
from deploy.setup_config import render_unit
from panel.config import PanelConfig, PanelConfigError, ServerConfig, load_config
from panel.db import PanelDB, now
from panel.rcon import RconClient, RconError, packet, parse
from panel.servers import ServerManager, ServerState, parse_players
from panel.suspicion import Detector
from panel.web import STATS as STATS_KEY, create_app

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

    def test_log_dir_falls_back_to_the_bots(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = Path(tmp, "servers.json")
            bot.write_text(json.dumps([{"id": "s1", "enabled": True, "log_dir": "${TEST_LOGS}/one"}]))
            with unittest.mock.patch.dict(os.environ, {"SERVERS_CONFIG": str(bot), "TEST_LOGS": "/srv/logs"}):
                config = self.load({"servers": [{"id": "s1"}, {"id": "s2", "log_dir": "/own"}]})
        self.assertEqual([s.log_dir for s in config.servers], ["/srv/logs/one", "/own"])

    def test_webhook_must_be_discord(self):
        with self.assertRaises(PanelConfigError):
            self.load({"discord_webhook": "https://example.com/hook"})
        url = "https://discord.com/api/webhooks/1/abc"
        self.assertEqual(self.load({"discord_webhook": url}).discord_webhook, url)

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
        self.assertEqual(memory.verdict(int(1.2 * GB), int(1.5 * GB), 0)[0], "unknown")
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

    async def test_fresh_reading_survives_a_panel_restart(self):
        await self.manager.refresh_memory(self.state)
        self.proc.sample.update(rss=int(3.5 * GB))
        again = ServerManager(panel_config(1, service="reforger-test"), self.db, sampler=self.proc)
        state = again.state("server-1")
        await again.refresh_memory(state)
        self.assertEqual(state.fresh, 3 * GB)
        self.proc.sample.update(pid=101, age=4000)
        await again.refresh_memory(state)
        self.assertEqual(state.fresh, 0)

    async def test_no_fresh_reading_is_not_a_leak(self):
        self.proc.sample.update(rss=int(1.2 * GB), age=5000)
        await self.manager.start_mission_check("server-1", "gaz")
        self.proc.sample["rss"] = int(1.5 * GB)
        self.state.check["started"] -= 1
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.check["status"], "unknown")

    async def test_a_server_restart_clears_the_warning(self):
        await self.manager.refresh_memory(self.state)
        self.proc.sample["rss"] = 6 * GB
        await self.manager.start_mission_check("server-1", "gaz")
        self.state.check["started"] -= 1
        await self.manager.refresh_memory(self.state)
        self.assertEqual(self.state.check["status"], "leak")
        self.proc.sample.update(pid=101, rss=3 * GB, age=10)
        await self.manager.refresh_memory(self.state)
        self.assertIsNone(self.state.check)

    async def test_no_service_no_check(self):
        await self.manager.start_mission_check("server-2", "gaz")
        self.assertIsNone(self.manager.state("server-2").check)


ENEMY = "11111111-2222-4333-8444-555555555555"


def bot_data(root):
    """Databases shaped by the bot's own code, holding one linked player with some kills."""
    with sqlite3.connect(Path(root, "playtime.sqlite3")) as db:
        db.execute("CREATE TABLE totals (server TEXT, identity TEXT, name TEXT, seconds REAL NOT NULL, PRIMARY KEY(server, identity))")
        db.execute("CREATE TABLE global_time(identity TEXT PRIMARY KEY, milliseconds INTEGER NOT NULL)")
        db.executemany("INSERT INTO totals VALUES (?, ?, ?, ?)",
                       [("server-1", HAVOC, "Sgt Havoc", 7200), ("server-2", HAVOC, "Sgt Havoc", 3600)])
        db.execute("INSERT INTO global_time VALUES (?, ?)", (HAVOC, 10_000_000))
    links = AccountLinks(str(Path(root, "account_links.sqlite3")))
    migrate_combat(links.db)
    with links.db:
        for n in range(3):
            record_kill(links.db, "server-1", f"2026-09-20T10:00:0{n}+00:00", KillEvent("10:00:00", "ENEMY", ENEMY, HAVOC))
        record_kill(links.db, "server-1", "2026-09-20T11:00:00+00:00", KillEvent("11:00:00", "ENEMY", HAVOC, ENEMY))
        links.db.execute("INSERT INTO account_links VALUES (1, 42, ?, 0, 'test')", (HAVOC,))
    links.db.close()


class OybStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bot_data(self.tmp.name)
        self.stats = OybStats(self.tmp.name)

    def test_matches_the_bot(self):
        stats = self.stats.player(HAVOC)
        self.assertEqual((stats["kills"], stats["deaths"], stats["teamkills"]), (3, 1, 0))
        self.assertEqual(stats["playtime"], 10_000)
        self.assertEqual([r["server"] for r in stats["servers"]], ["server-1", "server-2"])
        self.assertEqual((stats["discord"], stats["name"]), (42, "Sgt Havoc"))
        self.assertNotIn("xp", stats)

    def test_unknown_and_missing(self):
        self.assertIsNone(self.stats.player("00000000-0000-4000-8000-000000000000"))
        self.assertIsNone(OybStats("/nonexistent").player(HAVOC))
        self.assertFalse(OybStats("/nonexistent").available)

    def test_read_only(self):
        before = Path(self.tmp.name, "account_links.sqlite3").read_bytes()
        self.stats.player(HAVOC)
        self.assertEqual(Path(self.tmp.name, "account_links.sqlite3").read_bytes(), before)

    def test_search(self):
        self.assertEqual(self.stats.search("havoc"), [{"identity": HAVOC, "name": "Sgt Havoc"}])


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)

    def test_uptime(self):
        t = now()
        self.assertIsNone(self.db.uptime("s", t - 100))
        self.db.add_event("s", "online", at=t - 1000)
        self.db.add_event("s", "offline", at=t - 60)
        self.db.add_event("s", "online", at=t - 30)
        self.assertAlmostEqual(self.db.uptime("s", t - 100), 0.7, delta=0.03)

    def test_memory_chart(self):
        rows = [{"at": 0, "rss": 1 * GB}, {"at": 50, "rss": 2 * GB}, {"at": 100, "rss": 1 * GB}]
        chart = memory_chart(rows, 0, 100, fresh=1 * GB)
        self.assertEqual(chart["peak"], 2 * GB)
        self.assertTrue(chart["line"].startswith("0.0,"))
        self.assertIsNone(memory_chart(rows[:1], 0, 100))


class FakeAlerts(Alerts):
    def __init__(self):
        super().__init__("")
        self.sent = []

    def send(self, title, description="", colour=0, fields=()):
        self.sent.append((title, description, dict(fields)))


class AlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_an_embed(self):
        received = []

        async def hook(request):
            received.append(await request.json())
            return aioweb.Response(status=204)

        app = aioweb.Application()
        app.router.add_post("/api/webhooks/1/x", hook)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        alerts = Alerts(str(server.make_url("/api/webhooks/1/x")))
        alerts.send("Banned Havoc", "teamkilling", fields=[("By", "gaz")])
        await alerts.close()
        embed = received[0]["embeds"][0]
        self.assertEqual((embed["title"], embed["fields"][0]["value"]), ("Banned Havoc", "gaz"))
        self.assertEqual(received[0]["allowed_mentions"], {"parse": []})

    async def test_off_without_a_webhook(self):
        alerts = Alerts("")
        alerts.send("nothing")
        self.assertFalse(alerts._tasks)


class CrashTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.proc = FakeProcess()
        self.alerts = FakeAlerts()
        self.manager = ServerManager(panel_config(1, service="reforger-test"), self.db,
                                     sampler=self.proc, alerts=self.alerts)
        self.state = self.manager.state("server-1")

    async def test_unexpected_restart_is_a_crash(self):
        await self.manager.refresh_memory(self.state)
        self.proc.sample.update(pid=200, age=5)
        await self.manager.refresh_memory(self.state)
        kinds = [e["kind"] for e in self.db.events("server-1")]
        self.assertEqual(kinds[:2], ["started", "crashed"])
        self.assertIn("crashed", self.alerts.sent[0][0])

    async def test_admin_restart_is_not_a_crash(self):
        await self.manager.refresh_memory(self.state)
        self.manager.expect_restart("server-1")
        self.proc.sample.update(pid=200, age=5)
        await self.manager.refresh_memory(self.state)
        self.assertEqual([e["kind"] for e in self.db.events("server-1")][:2], ["started", "stopped"])
        self.assertEqual(self.alerts.sent, [])

    async def test_memory_is_sampled_each_minute(self):
        await self.manager.refresh_memory(self.state)
        await self.manager.refresh_memory(self.state)
        self.assertEqual(len(self.db.memory("server-1", 0)), 1)

    async def test_panel_restart_adds_no_duplicate_online(self):
        self.manager._changed(self.state, True)
        other = ServerManager(panel_config(1, service="reforger-test"), self.db, sampler=self.proc, alerts=self.alerts)
        other._changed(other.state("server-1"), True)
        self.assertEqual([e["kind"] for e in self.db.events("server-1")], ["online"])

    async def test_offline_alert(self):
        self.manager._offline(self.state, RconError("gone"))
        self.assertEqual(self.alerts.sent, [])
        self.manager._changed(self.state, True)
        self.manager._offline(self.state, RconError("gone"))
        self.assertEqual(self.alerts.sent[-1][0], "Server 1 went offline")
        self.assertEqual([e["kind"] for e in self.db.events("server-1")], ["offline", "online", "offline"])


GAZ = "d0d8bcaf-e30b-49e2-b32a-9618871f3b89"
BURD = "d35f38a2-aff1-4155-870c-29407fdf49ea"
REAL_LOG = """10:16:40.000  ENGINE       : Arma Reforger server
10:17:12.153  DEFAULT      : BattlEye Server: Adding player identity=0x00000000, name='GazLagom'
10:17:12.153  DEFAULT      : BattlEye Server: 'Player #0 GazLagom (203.0.113.7:57449) connected'
10:17:12.153  DEFAULT      : BattlEye Server: 'Player #0 GazLagom - BE GUID: 2ec9f958b59989197d397674c956d316'
10:17:20.000   NETWORK      : ### Updating player: PlayerId=1, Name=GazLagom, rplIdentity=0x00000000, IdentityId=D0D8BCAF-E30B-49E2-B32A-9618871F3B89
10:18:29.653  DEFAULT      : BattlEye Server: 'Player #1 Sgt_Burd (198.51.100.4:58289) connected'
10:18:29.653  DEFAULT      : BattlEye Server: 'Player #1 Sgt_Burd - BE GUID: 1cbf92c878ccc9d6441e2b52aa7e9654'
10:18:42.155   NETWORK      : ### Updating player: PlayerId=2, Name=Sgt_Burd, rplIdentity=0x00000001, IdentityId=d35f38a2-aff1-4155-870c-29407fdf49ea
10:46:51.302   NETWORK      : ### Updating player: PlayerId=1, Name=GazLagom, rplIdentity=0x00000000, IdentityId=d0d8bcaf-e30b-49e2-b32a-9618871f3b89
"""


def write_log(root, folder, text):
    path = Path(root, folder)
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "console.log", "a") as fh:
        fh.write(text)


class ConnectionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reader = LogReader(self.tmp.name)
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)

    def scan(self):
        events, moved = self.reader.scan(self.db.log_positions("server-1"))
        self.db.add_connections("server-1", events, moved)
        return [e for e in events if e["kind"] == "identity"]

    def test_feed_from_the_log(self):
        kill = ("10:30:00.000   SCRIPT       : INFO: KILL ENEMY: Sgt_Burd (playerID = 2 | UUID = " + BURD + ") "
                "from US faction at <1, 2, 3> was killed by GazLagom (playerID = 1 | UUID = " + GAZ + ") "
                "from USSR faction [120.4m away from the corpse] With last inflicted damage type KINETIC\n")
        side = ("10:20:00.000   SCRIPT       : INFO: Faction: player GazLagom (playerID = 1 | UUID = " + GAZ + ") "
                "has joined faction #AR-Faction_USSR (USSR)\n")
        leave = "10:49:23.433  DEFAULT      : BattlEye Server: 'Player #1 Sgt_Burd disconnected'\n"
        write_log(self.tmp.name, "logs_2026-09-25_10-16-32", REAL_LOG + side + kill + leave)
        self.scan()
        feed = [(r["kind"], r["text"]) for r in self.db.feed("server-1")]
        self.assertEqual(feed[:3], [("leave", "Sgt_Burd disconnected"), ("kill", "GazLagom killed Sgt_Burd (120 m)"),
                                    ("side", "GazLagom joined USSR")])
        self.assertIn(("join", "GazLagom connected"), feed)
        self.db.log("boss", "kick", "server-1", "Sgt_Burd")
        self.assertEqual(self.db.feed("server-1")[0]["text"], "boss: kick Sgt_Burd")

    def test_real_reforger_lines(self):
        write_log(self.tmp.name, "logs_2026-09-25_10-16-32", REAL_LOG)
        events = self.scan()
        self.assertEqual([(e["name"], e["ip"], e["guid"][:6]) for e in events],
                         [("GazLagom", "203.0.113.7", "2ec9f9"), ("Sgt_Burd", "198.51.100.4", "1cbf92"), ("GazLagom", "", "")])
        self.assertEqual(events[0]["identity"], GAZ)
        start = time.mktime((2026, 9, 25, 10, 17, 20, 0, 0, -1))
        self.assertEqual(events[0]["at"], int(start))
        self.assertEqual(self.db.ips(GAZ)[0]["ip"], "203.0.113.7")
        self.assertEqual(self.db.player(BURD)["name"], "Sgt_Burd")

    def test_reads_only_new_lines_and_whole_lines(self):
        write_log(self.tmp.name, "logs_2026-09-25_10-16-32", REAL_LOG[:REAL_LOG.index("10:18:29")])
        self.assertEqual(len(self.scan()), 1)
        self.assertEqual(self.scan(), [])
        write_log(self.tmp.name, "logs_2026-09-25_10-16-32", REAL_LOG[REAL_LOG.index("10:18:29"):] + "10:50:00.000  half a li")
        self.assertEqual([e["name"] for e in self.scan()], ["Sgt_Burd", "GazLagom"])

    def test_same_ip_finds_alts_and_banned_ones(self):
        log = REAL_LOG.replace("198.51.100.4", "203.0.113.7")
        write_log(self.tmp.name, "logs_2026-09-25_10-16-32", log)
        self.scan()
        self.assertEqual([a["name"] for a in self.db.alts(GAZ)], ["Sgt_Burd"])
        self.assertEqual(self.db.alt_summary([GAZ]), {GAZ: {"count": 1, "banned": []}})
        self.db.add_ban(BURD, "Sgt_Burd", "cheating", "boss")
        self.assertEqual(self.db.alt_summary([GAZ])[GAZ]["banned"], ["Sgt_Burd"])
        self.assertEqual([r["identity"] for r in self.db.search_ip("203.0.113")], [GAZ, BURD])

    def test_after_midnight(self):
        write_log(self.tmp.name, "logs_2026-09-25_23-59-00", "23:59:30.000 x\n" + REAL_LOG.replace("10:1", "00:1"))
        events = self.scan()
        self.assertEqual(events[0]["at"], int(time.mktime((2026, 9, 26, 0, 17, 20, 0, 0, -1))))

    def test_server_fps(self):
        write_log(self.tmp.name, "logs_2026-09-25_10-16-32",
                  "10:17:00.000  DEFAULT      : FPS: 58.3, frame time (avg: 17.2 ms), Mem: 1234 kB, Player: 1, AI: 20\n")
        events, _ = self.reader.scan({})
        self.assertEqual([e["fps"] for e in events if e["kind"] == "fps"], [58.3])
        self.db.add_connections("server-1", events, {})
        self.assertEqual(self.db.feed("server-1"), [])

    def test_missing_folder(self):
        self.assertEqual(LogReader("/nonexistent").scan({}), ([], {}))


BUFORD = "4bd39e3d-a6e3-4090-a338-00e1dc08ca69"


def person(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def blast(clock, victim, x, y, zone="Head", damage="FRAGMENTATION"):
    return (f"{clock}   SCRIPT       : INFO: KILL KILLED_BY_NEUTRAL_OR_FACTIONLESS: Victim{victim} "
            f"(playerID = {victim + 10} | UUID = {person(victim)}) from US faction at <{x}, 30, {y}> "
            f"was killed by AI [0m away from the corpse]. With last inflicted damage type {damage} "
            f"to the '{zone}' hit zone\n")


def shot(clock, killer, killer_id, victim, x, y, zone="Head", metres=40):
    return (f"{clock}   SCRIPT       : INFO: KILL ENEMY: Victim{victim} (playerID = {victim + 10} | UUID = {person(victim)}) "
            f"from US faction at <{x}, 30, {y}> was killed by {killer} (playerID = 3 | UUID = {killer_id}) "
            f"from FIA faction who was at that time at <{x + 60}, 30, {y + 20}> [{metres}m away from the corpse]. "
            f"With last inflicted damage type KINETIC to the '{zone}' hit zone\n")


def arrive(clock, name, identity, ip):
    return (f"{clock}  DEFAULT      : BattlEye Server: 'Player #3 {name} ({ip}:2304) connected'\n"
            f"{clock}   NETWORK      : ### Updating player: PlayerId=3, Name={name}, rplIdentity=0x3, IdentityId={identity}\n")


class SuspicionTests(unittest.TestCase):
    """Replays the pattern from the Buford investigation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def flags(self, text, **settings):
        write_log(self.tmp.name, "logs_2026-09-26_04-14-00", text)
        events, _ = LogReader(self.tmp.name).scan({})
        detector = Detector(settings)
        return [f for e in events for f in detector.feed(e)] + detector.flush(10 ** 10)

    def test_explosions_credited_to_ai_name_the_new_arrival(self):
        log = (arrive("06:10:00.000", "Regular", person(90), "10.0.0.9")
               + "06:16:09.000  DEFAULT      : BattlEye Server: 'Player #3 Regular disconnected'\n"
               + arrive("06:16:46.000", "Regular", person(90), "10.0.0.9")
               + arrive("06:16:52.000", "Buford", BUFORD, "146.70.168.126")
               + arrive("06:17:02.000", "pook4lyfe", person(1), "10.0.0.1")
               + arrive("06:17:30.000", "Gone", person(91), "10.0.0.2")
               + "06:19:00.000  DEFAULT      : BattlEye Server: 'Player #4 Gone disconnected'\n"
               + blast("06:19:09.000", 1, 3810, 5448) + blast("06:19:09.000", 2, 3812, 5450, zone="Chest")
               + blast("06:19:09.000", 3, 3815, 5446)
               + shot("06:19:40.000", "Buford", BUFORD, 4, 3700, 5400))
        flags = self.flags(log)
        self.assertEqual(len(flags), 1)
        text = flags[0]["text"]
        self.assertTrue(flags[0]["incident"])
        self.assertIn("3 players killed by explosions credited to AI in the same second", text)
        self.assertIn("2 of 3 to the head", text)
        self.assertIn("joined just before: Buford (146.70.168.126)", text)
        self.assertIn("killing nearby: Buford", text)
        for innocent in ("pook4lyfe", "Gone", "Regular"):
            self.assertNotIn(innocent, text)

    def test_a_steady_trickle_of_ai_explosions_adds_up(self):
        log = "".join(blast(f"06:2{i}:00.000", i, 1000 * i, 500, zone="Chest") for i in range(1, 6))
        flags = self.flags(log)
        self.assertEqual(len(flags), 1)
        self.assertIn("5 explosive deaths credited to AI in 5 min", flags[0]["text"])

    def test_gunships_and_attribution_quirks_are_left_alone(self):
        log = "".join(shot(f"06:0{i}:00.000", "SEPHRAP", person(80), i, 100 * i, 100, zone="Chest", metres=700)
                      .replace("KINETIC", "EXPLOSIVE") for i in range(1, 9))
        log += blast("06:30:00.000", 1, 0, 0, damage="COLLISION") * 3
        log += shot("06:31:00.000", "DauntlessNZr", person(81), 5, 0, 0, metres=2700) * 6
        log += (shot("06:40:00.000", "SOF Reaper1254", person(82), 6, 0, 0).replace(person(82), "")
                .replace("KINETIC", "EXPLOSIVE") * 5)
        self.assertEqual(self.flags(log), [])

    def test_player_patterns(self):
        rapid = "".join(shot(f"07:00:{i * 4:02d}.000", "Spray", person(70), i, 0, 0, zone="Chest") for i in range(6))
        heads = "".join(shot(f"08:{i:02d}:00.000", "Aimer", person(71), i, 0, 0) for i in range(10))
        tks = "".join(shot(f"09:0{i}:00.000", "Rogue", person(72), i, 0, 0).replace("KILL ENEMY", "KILL TK")
                      for i in range(3))
        texts = [f["text"] for f in self.flags(rapid + heads + tks)]
        self.assertEqual(texts, ["Spray: 6 kills in 30 s", "Aimer: 10 of 10 kills were headshots in 15 min",
                                 "Rogue: 3 teamkills in 10 min"])

    def test_script_error_spike(self):
        def error(clock, where):
            return (f"{clock} SCRIPT    (E): Virtual Machine Exception\n\nReason: NULL pointer to instance\n\n"
                    f"Class:      '{where}'\nFunction: 'Something'\n")
        ai_loop = error("06:10:00.100", "SCR_AIChangeCompartment") * 70
        one_frame = error("06:12:00.100", "SCR_MapMarkerSquadLeader") * 40
        spawns = "".join(error(f"06:18:{i:02d}.000", "SCR_PlayerControllerGroupComponent") for i in range(15, 40))
        flags = self.flags(ai_loop + one_frame + arrive("06:16:52.000", "Buford", BUFORD, "146.70.168.126") + spawns)
        self.assertEqual(len(flags), 1)
        self.assertIn("script errors in 20 different seconds within 5 min, mostly SCR_PlayerControllerGroupComponent",
                      flags[0]["text"])
        self.assertIn("joined just before: Buford", flags[0]["text"])

    def test_a_burst_waits_for_the_killers_around_it(self):
        write_log(self.tmp.name, "logs_2026-09-26_04-14-00", blast("06:19:09.000", 1, 0, 0) * 3)
        events, _ = LogReader(self.tmp.name).scan({})
        detector = Detector()
        self.assertEqual([f for e in events for f in detector.feed(e)], [])
        at = events[-1]["at"]
        self.assertEqual(detector.flush(at + 60), [])
        self.assertEqual(len(detector.flush(at + 120)), 1)

    def test_thresholds_come_from_the_config(self):
        log = blast("06:19:09.000", 1, 0, 0) + blast("06:19:09.000", 2, 0, 0)
        self.assertEqual(self.flags(log), [])
        self.assertEqual(len(self.flags("", same_second=2)), 1)


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "One"), ServerConfig("two", "Two")]),
                                     self.db, alerts=FakeAlerts())

    def incident(self, server, *players):
        state = self.manager.states[server]
        state.recent = {i: (n, now()) for n, i in players}
        self.manager.suspicious(state, {"at": now(), "text": "3 players killed", "identity": "", "incident": True})

    def test_the_same_face_at_incidents_on_both_servers(self):
        self.incident("one", ("Buford", BUFORD), ("M1SF1T", person(5)))
        self.assertNotIn("earlier incidents", self.db.feed("one")[0]["text"])
        self.incident("two", ("Buford", BUFORD), ("Someone", person(6)))
        self.assertEqual(self.db.feed("two")[0]["kind"], "sus")
        self.assertIn("At earlier incidents too: Buford (2x)", self.db.feed("two")[0]["text"])
        title, _, fields = self.manager.alerts.sent[-1]
        self.assertEqual(title, "Suspicious on Two")
        self.assertIn(BUFORD, fields["Buford"])
        self.assertEqual(len(self.db.incidents_for(BUFORD)), 2)
        self.assertEqual(len(self.db.incidents_for(person(5))), 1)

    def test_old_flags_from_a_backlog_only_go_in_the_feed(self):
        state = self.manager.states["one"]
        state.recent = {BUFORD: ("Buford", now())}
        self.manager.suspicious(state, {"at": now() - 86400, "text": "old", "identity": "", "incident": True})
        self.assertEqual(self.manager.alerts.sent, [])
        self.assertEqual(self.db.incidents_for(BUFORD), [])
        self.assertEqual(self.db.feed("one")[0]["text"], "old")


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = Path(self.tmp.name, "logs")
        server = ServerConfig(id="server-1", name="Server 1", log_dir=str(self.logs))
        self.config = PanelConfig(database=str(Path(self.tmp.name, "data", "panel.sqlite3")), cookie_secure=False,
                                  servers=[server])
        self.db = PanelDB(self.config.database)
        self.addCleanup(self.db.close)
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        self.db.add_user("mod", auth.hash_password("mod-password"), "moderator")
        self.manager = ServerManager(self.config, self.db, alerts=FakeAlerts())
        self.client = TestClient(TestServer(create_app(self.config, self.db, self.manager, start_manager=False)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        kill = shot("10:30:00.000", "GazLagom", GAZ, 1, 0, 0)
        write_log(self.logs, "logs_2026-09-25_10-16-32", REAL_LOG + kill + blast("10:40:00.000", 2, 0, 0) * 3)
        Path(self.logs, "logs_2026-09-25_10-16-32", "script.log").write_text("SCRIPT (E): Virtual Machine Exception\n")
        write_log(self.logs, "logs_2026-09-25_11-00-00", arrive("11:05:00.000", "Buford", BUFORD, "146.70.168.126"))

    async def archive(self):
        state = self.manager.states["server-1"]
        await self.manager.archive_logs(state, LogReader(str(self.logs)))

    async def test_finished_games_are_kept(self):
        await self.archive()
        game = self.db.archived_game("server-1", "logs_2026-09-25_10-16-32")
        self.assertEqual((game["players"], game["kills"], game["flags"]), (2, 4, 1))
        self.assertTrue(Path(game["path"]).is_file())
        self.assertEqual(Path(game["path"]).parent, Path(self.tmp.name, "data", "log-archive", "server-1"))
        self.assertIsNone(self.db.archived_game("server-1", "logs_2026-09-25_11-00-00"))
        # The game's own folder can go; the archive stays.
        shutil.rmtree(Path(self.logs, "logs_2026-09-25_10-16-32"))
        await self.archive()
        self.assertEqual(len(self.db.archived("server-1")), 1)

    async def test_history_and_a_past_game(self):
        await self.archive()
        await self.client.post("/login", data={"username": "boss", "password": "boss-password"})
        shutil.rmtree(Path(self.logs, "logs_2026-09-25_10-16-32"))
        html = await (await self.client.get("/server/server-1?day=2026-09-25")).text()
        self.assertIn("Games on 2026-09-25", html)
        self.assertIn("/server/server-1/game/logs_2026-09-25_10-16-32", html)
        self.assertIn("In progress", html)
        self.assertIn("1 sus", html)
        html = await (await self.client.get("/server/server-1/game/logs_2026-09-25_10-16-32")).text()
        self.assertIn("GazLagom", html)
        self.assertIn("203.0.113.7", html)
        self.assertIn("3 players killed by explosions credited to AI in the same second", html)
        live = await (await self.client.get("/server/server-1/game/logs_2026-09-25_11-00-00")).text()
        self.assertIn("Buford", live)
        response = await self.client.get("/server/server-1/game/logs_2026-09-25_10-16-32/log")
        self.assertEqual(response.headers["Content-Type"], "application/gzip")
        with tarfile.open(fileobj=io.BytesIO(await response.read())) as tar:
            self.assertIn(b"GazLagom", tar.extractfile("logs_2026-09-25_10-16-32/console.log").read())
            self.assertIn("logs_2026-09-25_10-16-32/script.log", tar.getnames())
        live = await self.client.get("/server/server-1/game/logs_2026-09-25_11-00-00/log")
        with tarfile.open(fileobj=io.BytesIO(await live.read())) as tar:
            self.assertIn(b"Buford", tar.extractfile("logs_2026-09-25_11-00-00/console.log").read())
        self.assertEqual(self.db.audit()[0]["action"], "download log")

    async def test_moderators_see_games_but_not_ips_or_logs(self):
        await self.archive()
        await self.client.post("/login", data={"username": "mod", "password": "mod-password"})
        html = await (await self.client.get("/server/server-1/game/logs_2026-09-25_10-16-32")).text()
        self.assertIn("GazLagom", html)
        self.assertNotIn("203.0.113.7", html)
        self.assertEqual((await self.client.get("/server/server-1/game/logs_2026-09-25_10-16-32/log")).status, 403)

    async def test_upload_an_old_game(self):
        await self.client.post("/login", data={"username": "boss", "password": "boss-password"})
        html = await (await self.client.get("/server/server-1#history")).text()
        action = re.search(r'action="(/server/server-1/history/upload\?csrf=[^"]+)"', html)[1]
        text = "Log /x/logs/logs_2026-09-20_08-00-00/console.log started at 2026-09-20 08:00:00\n" + \
            arrive("08:05:00.000", "Buford", BUFORD, "146.70.168.126") + blast("08:10:00.000", 2, 0, 0) * 3
        form = aiohttp.FormData()
        form.add_field("log", text.encode(), filename="logs1-926.txt", content_type="text/plain")
        response = await self.client.post(action, data=form)
        self.assertEqual(response.url.path, "/server/server-1/game/logs_2026-09-20_08-00-00")
        page = await response.text()
        self.assertIn("Buford", page)
        self.assertIn("same second", page)
        self.assertEqual(self.db.archived_game("server-1", "logs_2026-09-20_08-00-00")["players"], 1)
        self.assertEqual(self.db.audit()[0]["action"], "upload log")
        self.assertEqual(list(Path(self.manager.archive_root, "server-1", ".upload").iterdir()), [])
        again = aiohttp.FormData()
        again.add_field("log", text.encode(), filename="x.txt")
        self.assertIn("already in History", await (await self.client.post(action, data=again)).text())
        stale = aiohttp.FormData()
        stale.add_field("log", text.encode(), filename="x.txt")
        self.assertEqual((await self.client.post("/server/server-1/history/upload?csrf=nope", data=stale)).status, 403)

    async def test_moderators_cannot_upload(self):
        await self.client.post("/login", data={"username": "mod", "password": "mod-password"})
        html = await (await self.client.get("/server/server-1")).text()
        self.assertNotIn("history/upload", html)

    async def test_only_real_game_folders(self):
        await self.client.post("/login", data={"username": "boss", "password": "boss-password"})
        for folder in ("..", "logs_2026-09-25_10-16-3", "nope"):
            self.assertEqual((await self.client.get(f"/server/server-1/game/{folder}")).status, 404)
        self.assertEqual((await self.client.get("/server/server-1/game/logs_2020-01-01_00-00-00")).status, 404)


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

    async def test_command_replies_are_not_server_messages(self):
        self.fake.direct = False
        client = RconClient("127.0.0.1", self.port, "secret", timeout=2)
        seen = []
        client.on_message = seen.append
        await client.connect()
        self.addCleanup(client.close)
        await client.command("#players")
        self.fake.say("Admin message from the game")
        await asyncio.sleep(0.1)
        self.assertEqual(seen, ["Logged In! Client ID: #1", "Admin message from the game"])

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

    async def test_stop_survives_a_swallowed_cancel(self):
        async def stubborn():
            while not self.manager._stopping:
                try:
                    await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    pass
        self.manager._tasks.append(asyncio.create_task(stubborn()))
        await asyncio.wait_for(self.manager.stop(), 5)
        self.assertEqual(self.manager._tasks, [])

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
        self.assertIn("Banned 1 account: Sgt Havoc (server-1: done", html)
        self.assertEqual(self.fake.bans[HAVOC], "team killing")
        self.assertEqual(sum(c.startswith("#ban create") for c in self.fake.commands), 1)
        ban = self.db.active_ban(HAVOC)
        await self.client.post(f"/bans/{ban['id']}/remove", data={"csrf": token})
        self.assertNotIn(HAVOC, self.fake.bans)
        self.assertEqual([r["action"] for r in self.db.audit(limit=2)], ["unban", "ban"])

    async def test_welcome_and_guide(self):
        await self.login("mod", "mod-password")
        html = await (await self.client.get("/")).text()
        self.assertIn("Welcome, mod", html)
        self.assertIn('href="/guide"', html)
        guide = await (await self.client.get("/guide")).text()
        self.assertIn("Spotting cheaters", guide)
        self.assertIn("Bans need an admin", guide)
        self.assertNotIn("id=\"console\"", guide)

    async def test_ban_needs_a_known_player(self):
        await self.login("boss", "boss-password")
        token = await self.csrf("/bans")
        html = await (await self.client.post("/bans", data={
            "csrf": token, "player": "Nobody", "reason": "x", "duration": "0"})).text()
        self.assertIn("Pick the player from the list", html)
        self.assertEqual(self.db.bans(), [])

    async def test_ban_by_typed_name_or_pasted_id(self):
        await self.login("boss", "boss-password")
        self.db.saw_player(GAZ, "GazLagom", "server-1")
        self.db.saw_player(BURD, "Sgt_Burd", "server-1")
        token = await self.csrf("/bans")
        await self.client.post("/bans", data={"csrf": token, "player": "gazlagom", "reason": "x", "duration": "0"})
        self.assertEqual([(b["identity"], b["name"]) for b in self.db.bans()], [(GAZ, "GazLagom")])
        await self.client.post("/bans", data={"csrf": token, "player": BURD.upper(), "reason": "x", "duration": "0"})
        self.assertEqual(self.db.active_ban(BURD)["name"], "Sgt_Burd")

    async def test_two_players_with_one_name_must_be_picked(self):
        await self.login("boss", "boss-password")
        self.db.saw_player(GAZ, "Twin", "server-1")
        self.db.saw_player(BURD, "Twin", "server-1")
        token = await self.csrf("/bans")
        html = await (await self.client.post("/bans", data={
            "csrf": token, "player": "Twin", "reason": "x", "duration": "0"})).text()
        self.assertIn("2 players have gone by Twin", html)
        await self.client.post("/bans", data={"csrf": token, "player": "Twin", "identity": BURD,
                                              "reason": "x", "duration": "0"})
        self.assertEqual([b["identity"] for b in self.db.bans()], [BURD])

    async def test_player_suggestions(self):
        await self.login("mod", "mod-password")
        self.db.saw_player(GAZ, "GazLagom", "server-1")
        self.db.saw_player(GAZ, "Gaz", "server-1")
        self.db.saw_player(BURD, "Sgt_Burd", "server-1")
        matches = await (await self.client.get("/players/search.json?q=gaz")).json()
        self.assertEqual([(m["identity"], m["name"]) for m in matches], [(GAZ, "Gaz")])
        self.assertEqual(matches[0]["aka"], ["GazLagom"])
        self.assertEqual(await (await self.client.get("/players/search.json?q=g")).json(), [])
        await self.client.post("/logout", data={"csrf": await self.csrf("/")})
        self.assertEqual((await self.client.get("/players/search.json?q=gaz")).status, 401)

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

    async def test_brand_assets_need_no_login(self):
        self.assertEqual((await self.client.get("/brand/fonts/Display.ttf")).status, 200)
        self.assertEqual((await self.client.get("/static/oyb-mark.svg")).status, 200)

    async def test_player_page_shows_oyb_record(self):
        with tempfile.TemporaryDirectory() as root:
            bot_data(root)
            self.client.app[STATS_KEY].data = Path(root)
            await self.login("boss", "boss-password")
            html = await (await self.client.get(f"/player/{HAVOC}")).text()
            self.assertIn("In-game record", html)
            self.assertIn("https://discord.com/users/42", html)
            self.assertNotIn("XP", html)
            html = await (await self.client.get("/server/server-1")).text()
            self.assertIn("2h 46m", html)
            html = await (await self.client.get("/players?q=havoc")).text()
            self.assertIn("Sgt Havoc", html)

    async def test_other_account_shows_its_discord_link(self):
        with tempfile.TemporaryDirectory() as root:
            bot_data(root)
            with sqlite3.connect(Path(root, "playtime.sqlite3")) as db:
                db.execute("INSERT INTO totals VALUES ('server-1', ?, 'Sgt Havoc', 60)", (ENEMY,))
            self.client.app[STATS_KEY].data = Path(root)
            self.connect(HAVOC, "Sgt Havoc", "203.0.113.7")
            self.connect(ENEMY, "Sgt Havoc", "203.0.113.7")
            await self.login("boss", "boss-password")
            html = await (await self.client.get(f"/player/{ENEMY}")).text()
            self.assertIn("their other account", html)
            self.assertIn("https://discord.com/users/42", html)

    async def test_server_page_has_health(self):
        await self.login("boss", "boss-password")
        html = await (await self.client.get("/server/server-1")).text()
        self.assertIn("Up, last 24h", html)
        self.assertIn("online", html)

    async def test_actions_raise_alerts(self):
        self.manager.alerts = FakeAlerts()
        await self.login("boss", "boss-password")
        token = await self.csrf("/bans")
        await self.client.post("/bans", data={"csrf": token, "identity": HAVOC, "name": "Sgt Havoc",
                                              "reason": "team killing", "duration": "3600"})
        title, _, fields = self.manager.alerts.sent[0]
        self.assertEqual((title, fields["By"], fields["Reason"]), ("Banned Sgt Havoc", "boss", "team killing"))

    async def test_connections_are_for_admins(self):
        with tempfile.TemporaryDirectory() as root:
            write_log(root, "logs_2026-09-25_10-16-32", REAL_LOG.replace("198.51.100.4", "203.0.113.7"))
            events, moved = LogReader(root).scan({})
            self.db.add_connections("server-1", events, moved)
        await self.login("boss", "boss-password")
        html = await (await self.client.get(f"/player/{GAZ}")).text()
        self.assertIn("203.0.113.7", html)
        self.assertIn("Sgt_Burd", html)
        html = await (await self.client.get("/players?q=203.0.113")).text()
        self.assertIn("GazLagom", html)
        await self.client.post("/logout", data={"csrf": await self.csrf()})
        await self.login("mod", "mod-password")
        html = await (await self.client.get(f"/player/{GAZ}")).text()
        self.assertNotIn("203.0.113.7", html)
        self.assertNotIn("Connections", html)

    async def test_live_list_flags_alts(self):
        self.fake.players.append(("5", "Sgt_Burd", BURD))
        self.fake.players.append(("6", "GazLagom", GAZ))
        with tempfile.TemporaryDirectory() as root:
            write_log(root, "logs_2026-09-25_10-16-32", REAL_LOG.replace("198.51.100.4", "203.0.113.7"))
            events, moved = LogReader(root).scan({})
            self.db.add_connections("server-1", events, moved)
        self.db.add_ban(BURD, "Sgt_Burd", "cheating", "boss")
        await self.manager.refresh_players(self.manager.state("server-1"))
        await self.login("boss", "boss-password")
        html = await (await self.client.get("/server/server-1/players.part")).text()
        self.assertIn("Banned alt", html)
        self.assertIn("1 alt", html)

    def connect(self, identity, name, ip, at=None):
        self.db.add_connections("server-1", [{"identity": identity, "name": name, "ip": ip, "guid": "",
                                              "at": at or now()}], {})

    async def test_ip_ban_kicks_alts(self):
        self.connect(HAVOC, "Sgt Havoc", "203.0.113.7")
        await self.login("boss", "boss-password")
        token = await self.csrf("/bans")
        html = await (await self.client.post("/bans", data={"csrf": token, "identity": HAVOC, "name": "Sgt Havoc",
                                                             "reason": "cheating", "duration": "0", "ip": "1"})).text()
        self.assertIn("IP banned 1 address", html)
        self.assertIn("Banned 1 account", html)
        rook = SAMPLE_PLAYERS[1]
        self.connect(rook[2], rook[1], "203.0.113.7")
        state = self.manager.state("server-1")
        await self.manager.refresh_players(state)
        await self.manager.enforce_ip_bans(state)
        for _ in range(100):
            if f"#kick {rook[0]}" in self.fake.commands and self.db.audit()[0]["action"] == "IP ban":
                break
            await asyncio.sleep(0.02)
        self.assertIn(f"#kick {rook[0]}", self.fake.commands)
        self.assertIn(rook[2], self.fake.bans)
        self.assertIn("same IP as Sgt Havoc", self.db.active_ban(rook[2])["reason"])
        entry = self.db.audit()[0]
        self.assertEqual((entry["username"], entry["action"], entry["target"]), ("panel", "IP ban", "Pte Rook"))
        kicks = self.fake.commands.count(f"#kick {rook[0]}")
        await self.manager.enforce_ip_bans(state)
        self.assertEqual(self.fake.commands.count(f"#kick {rook[0]}"), kicks)

    async def test_no_ip_kick_after_unban_or_for_old_addresses(self):
        rook = SAMPLE_PLAYERS[1]
        ban = self.db.add_ban(HAVOC, "Sgt Havoc", "cheating", "boss")
        self.db.add_ip_bans(ban, ["203.0.113.7"])
        self.connect(rook[2], rook[1], "203.0.113.7", at=now() - 2 * 86400)
        state = self.manager.state("server-1")
        await self.manager.refresh_players(state)
        await self.manager.enforce_ip_bans(state)
        self.assertNotIn(f"#kick {rook[0]}", self.fake.commands)
        self.connect(rook[2], rook[1], "203.0.113.7")
        self.db.remove_ban(ban, "boss")
        await self.manager.enforce_ip_bans(state)
        self.assertNotIn(f"#kick {rook[0]}", self.fake.commands)

    async def test_ip_ban_takes_every_account_on_the_ip(self):
        rook, fennel = SAMPLE_PLAYERS[1], SAMPLE_PLAYERS[2]
        self.connect(HAVOC, "Sgt Havoc", "203.0.113.7")
        self.connect(rook[2], rook[1], "203.0.113.7")
        self.connect(fennel[2], fennel[1], "198.51.100.9")
        await self.login("boss", "boss-password")
        token = await self.csrf("/bans")
        html = await (await self.client.post("/bans", data={"csrf": token, "identity": HAVOC, "name": "Sgt Havoc",
                                                             "reason": "cheating", "duration": "86400", "ip": "1"})).text()
        self.assertIn("Banned 2 accounts", html)
        self.assertTrue(self.db.active_ban(rook[2]))
        self.assertIsNone(self.db.active_ban(fennel[2]))
        self.assertEqual(self.db.active_ban(rook[2])["expires_at"], self.db.active_ban(HAVOC)["expires_at"])
        self.assertIn(f"#kick {rook[0]}", self.fake.commands)

    async def test_ip_ban_box_is_off_by_default(self):
        self.connect(HAVOC, "Sgt Havoc", "203.0.113.7")
        await self.login("boss", "boss-password")
        token = await self.csrf("/bans")
        await self.client.post("/bans", data={"csrf": token, "identity": HAVOC, "reason": "x", "duration": "0"})
        self.assertEqual(self.db.banned_ips(), set())

    async def test_summary_strip(self):
        state = self.manager.state("server-1")
        state.fps, state.fps_at = 57.6, now()
        await self.login("boss", "boss-password")
        html = await (await self.client.get("/server/server-1/summary.part")).text()
        self.assertIn("Ping (RCON)", html)
        self.assertIn(">58<", html)
        self.assertIn(" ms<", html)
        page = await (await self.client.get("/server/server-1")).text()
        self.assertIn('data-tab-panel="health"', page)

    async def test_live_feed(self):
        self.db.add_feed("server-1", "kill", "GazLagom killed Sgt_Burd (120 m)")
        self.fake.say("Something from the server")
        await asyncio.sleep(0.1)
        await self.login("boss", "boss-password")
        html = await (await self.client.get("/server/server-1")).text()
        self.assertIn("Live feed", html)
        self.assertIn("GazLagom killed Sgt_Burd (120 m)", html)
        part = await (await self.client.get("/server/server-1/feed.part")).text()
        self.assertIn("Something from the server", part)
        self.assertNotIn("Logged In!", part)

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
