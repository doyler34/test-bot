import os
import re
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from panel import attacks, auth
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB, now
from panel.servers import ATTACK_LOGINS, ServerManager
from panel.web import create_app

SSH_LINES = """\
Oct 16 22:01:01 ssrver sshd[1201]: Failed password for root from 77.239.124.35 port 51122 ssh2
Oct 16 22:01:03 ssrver sshd[1202]: Failed password for invalid user admin from 77.239.124.35 port 51130 ssh2
Oct 16 22:01:04 ssrver sshd[1203]: Invalid user oracle from 45.9.20.1 port 40000
2026-10-16T22:01:05.123456+00:00 ssrver sshd[1204]: pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 tty=ssh ruser= rhost=103.1.2.3  user=ubuntu
Oct 16 22:01:06 ssrver sshd[1205]: Accepted publickey for gaz from 81.2.3.4 port 5000 ssh2
Oct 16 22:01:07 ssrver CRON[99]: pam_unix(cron:session): session opened for user root
"""


class ParseTests(unittest.TestCase):
    def test_ssh_failures_and_nothing_else(self):
        got = [attacks.ssh_attempt(line, time.time()) for line in SSH_LINES.splitlines()]
        found = [(a["ip"], a["user"]) for a in got if a]
        self.assertEqual(found, [("77.239.124.35", "root"), ("77.239.124.35", "admin"), ("45.9.20.1", "oracle"),
                                 ("103.1.2.3", "ubuntu")])
        self.assertTrue(all(a["source"] == "SSH" for a in got if a))

    def test_amp_failures(self):
        line = "[22:05:01] [Core:Auth Warning] : Failed login attempt for user 'admin' from 185.220.101.4"
        self.assertEqual({k: v for k, v in attacks.amp_attempt(line, 1.0).items() if k != "at"},
                         {"ip": "185.220.101.4", "source": "AMP", "user": "admin"})
        self.assertIsNone(attacks.amp_attempt("[22:05:02] [Core:Auth] : User admin logged in from 81.2.3.4", 1.0))
        self.assertIsNone(attacks.amp_attempt("[22:05:03] [Core] : Instance Classic01 failed to start", 1.0))

    def test_reads_each_line_once_and_follows_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder, "auth.log")
            log.write_text(SSH_LINES)
            reader = attacks.Reader(auth_logs=(str(log),), amp_logs=str(Path(folder, "none", "*.log")))
            self.assertEqual(len(reader.read()), 4)
            self.assertEqual(reader.read(), [])
            with log.open("a") as fh:
                fh.write("Oct 16 22:09:00 ssrver sshd[2]: Failed password for root from 9.9.9.9 port 1 ssh2\n")
            self.assertEqual([a["ip"] for a in reader.read()], ["9.9.9.9"])
            log.rename(Path(folder, "auth.log.1"))
            log.write_text("Oct 16 22:10:00 ssrver sshd[3]: Invalid user x from 8.8.4.4 port 2\n")
            self.assertEqual([a["ip"] for a in reader.read()], ["8.8.4.4"])

    def test_open_ports(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "net").mkdir()
            header = "  sl  local_address rem_address   st\n"
            Path(root, "net/tcp").write_text(header + "   0: 00000000:0016 00000000:0000 0A\n"
                                                      "   1: 0100007F:1F90 00000000:0000 0A\n"
                                                      "   2: 00000000:1F90 00000000:0000 0A\n"
                                                      "   3: 00000000:01BB 0A0B0C0D:D431 01\n")
            Path(root, "net/tcp6").write_text(header + "   0: 00000000000000000000000000000000:0050 00000000000000000000000000000000:0000 0A\n")
            self.assertEqual(attacks.open_ports(root), [22, 80, 8080])   # 127.0.0.1 and connected ones left out


class FakeAlerts:
    def __init__(self):
        self.sent = []

    def send(self, title, description="", colour=0, fields=()):
        self.sent.append((title, description))

    async def close(self):
        pass


class FakeReader:
    def __init__(self, batches):
        self.batches, self.unreadable = list(batches), set()

    def read(self):
        return self.batches.pop(0) if self.batches else []


class WatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_brute_force_is_reported_once(self):
        db, alerts = PanelDB(":memory:"), FakeAlerts()
        manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "One")]), db, alerts=alerts)
        t = now()
        burst = [{"at": t - i, "ip": "77.239.124.35", "source": "SSH", "user": u} for i, u in
                 enumerate(["root", "admin", "ubuntu"] * 4)]
        few = [{"at": t, "ip": "1.2.3.4", "source": "AMP", "user": "admin"}] * 3
        reader, reported = FakeReader([burst + few, burst]), {}
        await manager.check_attacks(reader, reported)
        await manager.check_attacks(reader, reported)
        self.assertEqual(len(alerts.sent), 1)
        title, text = alerts.sent[0]
        self.assertEqual(title, "Login attack on SSH")
        self.assertIn("12 failed logins from 77.239.124.35", text)
        self.assertIn("root", text)


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_failures_count_and_the_page_shows_them(self):
        db = PanelDB(":memory:")
        db.add_user("boss", auth.hash_password("boss-password"), "owner")
        db.add_user("adm", auth.hash_password("adm-password"), "admin")
        db.add_attempts([{"at": now() - 60, "ip": "185.220.101.4", "source": "AMP", "user": "admin"}] * 5)
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[ServerConfig("one", "One")])
        client = TestClient(TestServer(create_app(config, db, ServerManager(config, db), start_manager=False)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        await client.post("/login", data={"username": "boss", "password": "wrong-guess"})
        self.assertIn(("Panel", 1), [(r["source"], r["n"]) for r in db.attack_sources(0)])
        await client.post("/login", data={"username": "boss", "password": "boss-password"})
        page = await (await client.get("/attacks")).text()
        self.assertIn('href="/ip-lookup?ip=185.220.101.4"', page)
        self.assertIn("<b>5</b> failed", page)
        self.assertIn("Panel failures today", page)
        self.assertIn("Ports open to the internet", page)
        self.assertIn('href="/attacks"', await (await client.get("/data")).text())
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        await client.post("/logout", data={"csrf": token})
        await client.post("/login", data={"username": "adm", "password": "adm-password"})
        self.assertEqual((await client.get("/attacks", allow_redirects=False)).status, 403)

    async def test_admin_ips_are_never_flagged(self):
        db = PanelDB(":memory:")
        db.add_user("boss", auth.hash_password("boss-password"), "owner")
        db.add_attempts([{"at": now() - 60, "ip": "86.40.12.9", "source": "AMP", "user": "gaz"}] * 12
                        + [{"at": now() - 60, "ip": "185.220.101.4", "source": "SSH", "user": "root"}] * 3)
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[ServerConfig("one", "One")])
        client = TestClient(TestServer(create_app(config, db, ServerManager(config, db), start_manager=False)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        await client.post("/login", data={"username": "boss", "password": "boss-password"})
        token = re.search(r'name="csrf" value="([^"]+)"', await (await client.get("/attacks")).text()).group(1)
        await client.post("/attacks/trust", data={"csrf": token, "ip": "86.40.12.0/24 ", "name": "Gaz home"})
        self.assertEqual([r["ip"] for r in db.attackers(0)], ["185.220.101.4"])    # earlier failures cleared
        db.add_attempts([{"at": now(), "ip": "86.40.12.77", "source": "SSH", "user": "gaz"}])
        self.assertEqual([r["ip"] for r in db.attackers(0)], ["185.220.101.4"])
        page = await (await client.get("/attacks")).text()
        self.assertIn("86.40.12.0/24", page)
        self.assertIn("Gaz home", page)
        await client.post("/attacks/trust", data={"csrf": token, "ip": "not an ip"})
        self.assertEqual(len(db.trusted_ips()), 1)
        await client.post("/attacks/untrust", data={"csrf": token, "ip": "86.40.12.0/24"})
        self.assertEqual(db.trusted_ips(), [])
        db.add_attempts([{"at": now(), "ip": "86.40.12.77", "source": "SSH", "user": "gaz"}])
        self.assertIn("86.40.12.77", [r["ip"] for r in db.attackers(0)])
