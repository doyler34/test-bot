import asyncio
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from panel import auth, monitor
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB, now
from panel.servers import ServerManager, UNREACHABLE_AFTER
from panel.web import create_app


def fake_proc(root, cpu, rx, udp_err, rcvbuf, rx_drop=0):
    root = Path(root)
    (root / "net").mkdir(parents=True, exist_ok=True)
    (root / "stat").write_text(f"cpu  {cpu[0]} 0 {cpu[1]} {cpu[2]} 0 0 0 0 0 0\ncpu0 1 1 1 1\n")
    (root / "meminfo").write_text("MemTotal:  8000000 kB\nMemFree: 1000000 kB\nMemAvailable: 2000000 kB\n"
                                  "SwapTotal: 0 kB\nSwapFree: 0 kB\n")
    (root / "loadavg").write_text("1.50 1.20 1.00 2/300 1234\n")
    (root / "net" / "dev").write_text(
        "Inter-|   Receive |  Transmit\n face |bytes packets errs drop fifo frame compressed multicast|bytes packets\n"
        f"    lo: 999999 9999 0 0 0 0 0 0 999999 9999 0 0 0 0 0 0\n"
        f"  eth0: {rx} {rx // 100} 0 {rx_drop} 0 0 0 0 {rx * 2} {rx // 50} 0 0 0 0 0 0\n")
    (root / "net" / "snmp").write_text(
        "Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors\n"
        f"Udp: 1000 0 {udp_err} 1000 {rcvbuf} 0\n")


class ReadingTests(unittest.TestCase):
    def test_box_rates_from_two_readings(self):
        with tempfile.TemporaryDirectory() as root:
            fake_proc(root, (100, 100, 800), 1_000_000, 0, 0)
            before = monitor.read_box(root)
            fake_proc(root, (200, 200, 1400), 3_500_000, 6, 3, rx_drop=2)
            after = monitor.read_box(root)
        after["at"] = before["at"] + 10
        row = monitor.box_rates(before, after)
        self.assertEqual(row["cpu"], 25.0)                  # 200 busy of 800 ticks
        self.assertEqual(row["rx_mbps"], 2.0)               # 2.5 MB in 10 s
        self.assertEqual(row["rx_pps"], 2500)
        self.assertEqual((row["udp_errors"], row["udp_rcvbuf"], row["rx_drop"]), (36, 18, 12))   # per minute
        self.assertEqual(row["mem_used"], 6_000_000 * 1024)

    def test_reads_the_server_browser_reply(self):
        reply = (b"\xff\xff\xff\xffI\x11OYB Classic\x00Everon\x00reforger\x00Arma Reforger\x00\x00\x00\x2a\x80\x00")
        self.assertEqual(monitor.parse_info(reply), {"name": "OYB Classic", "map": "Everon", "players": 42, "slots": 128})

    def test_squeezing_keeps_spikes(self):
        rows = [{"at": i, "cpu": 10.0, "udp_errors": 50.0 if i == 7 else 0.0} for i in range(100)]
        out = monitor.downsample(rows, 0, 100, ("cpu", "udp_errors"), buckets=10, peak=("udp_errors",))
        self.assertEqual(len(out), 10)
        self.assertEqual(max(r["udp_errors"] for r in out), 50.0)
        self.assertEqual(out[0]["cpu"], 10.0)
        drawn = monitor.chart(out + [{"at": 101, "cpu": None, "udp_errors": None}], 0, 101, [("cpu", "CPU", "a")])
        self.assertEqual(drawn["lines"][0]["now"], 10.0)


class FakeBrowserServer(asyncio.DatagramProtocol):
    """Answers the A2S query the way a newer server does: challenge first."""
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data == monitor.A2S_INFO:
            self.transport.sendto(b"\xff\xff\xff\xffA\x01\x02\x03\x04", addr)
        elif data == monitor.A2S_INFO + b"\x01\x02\x03\x04":
            self.transport.sendto(b"\xff\xff\xff\xffI\x11OYB\x00Everon\x00r\x00g\x00\x00\x00\x05\x40\x00", addr)


class QueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_asks_like_the_server_browser(self):
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(FakeBrowserServer, local_addr=("127.0.0.1", 0))
        self.addCleanup(transport.close)
        port = transport.get_extra_info("sockname")[1]
        answer = await monitor.query("127.0.0.1", port)
        self.assertTrue(answer["ok"])
        self.assertEqual((answer["players"], answer["slots"]), (5, 64))
        silent, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
        self.addCleanup(silent.close)
        nothing = await monitor.query("127.0.0.1", silent.get_extra_info("sockname")[1], timeout=0.3)
        self.assertFalse(nothing["ok"])


class FakeAlerts:
    def __init__(self):
        self.sent = []

    def send(self, title, description="", colour=0, fields=()):
        self.sent.append(title)

    async def close(self):
        pass


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = PanelDB(":memory:")
        self.alerts = FakeAlerts()
        config = PanelConfig(database=":memory:", cookie_secure=False,
                             servers=[ServerConfig("one", "Server 1", query_host="127.0.0.1", query_port=9)])
        self.manager = ServerManager(config, self.db, alerts=self.alerts)
        self.state = self.manager.state("one")

    async def test_a_reading_and_the_unreachable_alert(self):
        self.state.online, self.state.players = True, [{"identity": "a"}, {"identity": "b"}]
        self.state.disconnects.update({9: 3, 4: 2})
        replies = iter([{"ok": True, "ms": 12.0}] + [{"ok": False, "error": "no answer"}] * UNREACHABLE_AFTER
                       + [{"ok": True, "ms": 15.0}])

        async def fake_query(host, port, timeout=3.0):
            return next(replies)
        original, monitor.query = monitor.query, fake_query
        try:
            for i in range(UNREACHABLE_AFTER + 2):
                await self.manager.monitor_server(self.state)
                self.db.db.execute("UPDATE server_samples SET at = at - ? WHERE at >= ?", (100 - i, now() - 5))
        finally:
            monitor.query = original
        rows = self.db.server_samples("one", 0)
        first = min(rows, key=lambda r: r["at"])
        self.assertEqual((first["players"], first["disconnects"], json.loads(first["reasons"])), (2, 5, {"9": 3, "4": 2}))
        self.assertEqual(self.alerts.sent, ["Server 1 can't be reached", "Server 1 is reachable again"])
        self.assertFalse(self.state.disconnects)

    async def test_the_page(self):
        t = now()
        for i in range(20):
            self.db.add_box_sample({"cpu": 95.0 if i == 10 else 30.0, "mem_used": 4 * 1024 ** 3,
                                    "mem_total": 8 * 1024 ** 3, "rx_mbps": 3.0, "tx_mbps": 9.0, "rx_pps": 900,
                                    "tx_pps": 1200, "rx_drop": 0, "udp_errors": 12.0 if i == 12 else 0,
                                    "udp_rcvbuf": 0, "udp_sndbuf": 0}, at=t - 300 + i * 15)
            self.db.add_server_sample("one", {"players": 40, "proc_cpu": 80.0, "rss": 3 * 1024 ** 3, "fps": 25.0 if i == 5 else 60.0,
                                              "query_ok": 0 if i == 15 else 1, "query_ms": 20.0, "disconnects": 6 if i == 3 else 0,
                                              "errors": 0, "reasons": json.dumps({"6": 6}) if i == 3 else None}, at=t - 300 + i * 15)
        self.db.add_user("mod", auth.hash_password("mod-password"), "moderator")
        client = TestClient(TestServer(create_app(self.manager.config, self.db, self.manager, start_manager=False)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        await client.post("/login", data={"username": "mod", "password": "mod-password"})
        page = await (await client.get("/server/one/monitor?range=1h")).text()
        for words in ("The server browser couldn&#39;t reach it", "Server FPS under 30", "The whole box was nearly out of CPU",
                      "Game packets were thrown away on arrival", "Several players dropped at once", "Reason 6: <b>6</b>",
                      "Lost packets per minute", "<polyline"):
            self.assertIn(words, page)
        self.assertIn("Open the Monitor", await (await client.get("/server/one")).text())
