import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from panel import netcheck


def nic(root, name, driver, state, speed, stats, carrier_changes=2):
    here = Path(root, "class/net", name)
    (here / "statistics").mkdir(parents=True)
    (here / "device").mkdir()
    drivers = Path(root, "drivers", driver)
    drivers.mkdir(parents=True, exist_ok=True)
    os.symlink(drivers, here / "device" / "driver")
    for key, value in (("operstate", state), ("speed", speed), ("duplex", "full"), ("mtu", 1500),
                       ("carrier", 1 if state == "up" else 0), ("carrier_changes", carrier_changes)):
        (here / key).write_text(f"{value}\n")
    for key in netcheck.STAT_KEYS:
        (here / "statistics" / key).write_text(f"{stats.get(key, 0)}\n")


def proc(root, softnet_dropped, udp_errors, sock_drops, irq):
    root = Path(root)
    (root / "net").mkdir(parents=True, exist_ok=True)
    (root / "net/route").write_text("Iface\tDestination\tGateway\nenp4s0\t00000000\t0100A8C0\n")
    (root / "net/softnet_stat").write_text(f"00001000 {softnet_dropped:08x} 00000000\n00001000 00000000 00000000\n")
    (root / "net/snmp").write_text("Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors\n"
                                   f"Udp: 100 0 {udp_errors} 100 {udp_errors} 0\n"
                                   "Tcp: OutSegs RetransSegs\nTcp: 1000 1\n")
    (root / "stat").write_text("cpu  1 1 1 1\ncpu0 100 0 0 900 0 0 0 0\ncpu1 100 0 0 900 0 0 0 0\n")
    (root / "interrupts").write_text("           CPU0       CPU1\n"
                                     f" 40:   {irq}   0   PCI-MSI  enp4s0-TxRx-0\n 41:   5   0   PCI-MSI  enp4s0-TxRx-1\n")
    for key, value in (("net/core/rmem_max", 212992), ("net/core/rmem_default", 212992), ("net/core/wmem_max", 212992),
                       ("net/core/netdev_max_backlog", 1000), ("net/netfilter/nf_conntrack_count", 240000),
                       ("net/netfilter/nf_conntrack_max", 262144)):
        Path(root, "sys", key).parent.mkdir(parents=True, exist_ok=True)
        Path(root, "sys", key).write_text(f"{value}\n")
    game = root / "4242"
    (game / "fd").mkdir(parents=True, exist_ok=True)
    (game / "net").mkdir(exist_ok=True)
    if not (game / "fd/7").is_symlink():
        os.symlink("socket:[555]", game / "fd/7")
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops\n"
    (game / "net/udp").write_text(header + f"  1: 00000000:07D1 00000000:0000 07 00000000:00000400 00:00000000 00000000  1000  0 555 2 0 {sock_drops}\n")
    (game / "net/udp6").write_text(header)


class NetCheckTests(unittest.TestCase):
    def test_the_noc_change_situation(self):
        with tempfile.TemporaryDirectory() as sys_root, tempfile.TemporaryDirectory() as proc_root:
            nic(sys_root, "enp4s0", "igb", "up", 1000, {"rx_bytes": 0, "tx_bytes": 0, "rx_missed_errors": 10})
            nic(sys_root, "eno1np0", "bnxt_en", "down", -1, {})
            nic(sys_root, "eno2np1", "bnxt_en", "down", -1, {})
            proc(proc_root, 0, 0, 3, 1000)
            before = netcheck.snapshot({"one": 4242}, proc_root, sys_root)
            for path, value in (("class/net/enp4s0/statistics/rx_bytes", 500_000_000),
                                ("class/net/enp4s0/statistics/tx_bytes", 300_000_000),
                                ("class/net/enp4s0/statistics/rx_missed_errors", 250)):
                Path(sys_root, path).write_text(f"{value}\n")
            proc(proc_root, 40, 120, 90, 90000)
            after = netcheck.snapshot({"one": 4242}, proc_root, sys_root)
            after["at"] = before["at"] + 10
            extras = {"routes": netcheck.default_routes(proc_root), "sysctl": netcheck.sysctls(proc_root),
                      "queries": {"one": {"tries": 10, "failed": 2, "avg": 40.0, "worst": 300.0, "best": 20.0, "jitter": 35.0}},
                      "internet": {"Cloudflare (1.1.1.1)": {"tries": 10, "failed": 0, "avg": 3.0, "worst": 4.0, "best": 2.0, "jitter": 0.5}},
                      "ethtool": {"enp4s0": {"rx_max": 4096, "rx_now": 256, "counters": {"rx_missed_errors": 250, "tx_timeout_count": 0,
                                                                                         "rx_queue_0_csum_err": 80, "rx_queue_1_csum_err": 72}}}}
        rows = {(r["section"], r["label"]): r for r in netcheck.judge(before, after, [("one", "Classic", 4242, ("x", 1))], extras)}
        v = lambda section, label: rows[(section, label)]["verdict"]
        self.assertEqual(rows[("Network ports", "Default route")]["value"], "enp4s0 via 192.168.0.1")
        self.assertEqual(v("Network ports", "enp4s0: motherboard Intel 1 Gb port"), "ok")
        self.assertEqual(rows[("Network ports", "eno1np0: Broadcom 10/25 Gb port")]["value"], "not connected")
        port = rows[("Network ports", "Running on the motherboard port")]
        self.assertEqual(port["verdict"], "warn")                                         # 64% of 1 Gb in use
        self.assertEqual(port["value"], "enp4s0 (motherboard Intel 1 Gb port); the Broadcom 10/25 Gb ports aren't connected")
        self.assertIn("You're using 64% of it", port["why"])
        self.assertEqual(rows[("Network ports", "enp4s0 packets arriving damaged")]["value"],
                         "152 since boot (rx_queue_0_csum_err 80, rx_queue_1_csum_err 72)")
        self.assertEqual(v("Network ports", "enp4s0 traffic"), "warn")                 # 640 Mb/s of 1000
        self.assertEqual(v("Network ports", "enp4s0 errors right now"), "bad")          # rx_missed grew
        self.assertEqual(rows[("Network ports", "enp4s0 receive buffer")]["fix"], "ethtool -G enp4s0 rx 4096")
        self.assertEqual(v("Kernel", "Packets dropped by the kernel (backlog full)"), "bad")
        self.assertEqual(v("Kernel", "Incoming UDP packets thrown away"), "bad")
        self.assertEqual(v("Kernel", "Largest incoming UDP buffer allowed"), "warn")
        self.assertEqual(v("Kernel", "Connection tracking table"), "bad")
        self.assertEqual(v("Kernel", "enp4s0 interrupts"), "warn")                       # all on CPU0
        self.assertEqual(rows[("Server: Classic", "UDP port 2001")]["value"],
                         "87 dropped in 10 s (90 since it opened), 1024 bytes waiting")
        self.assertEqual(v("Server: Classic", "UDP port 2001"), "bad")
        self.assertEqual(v("Server: Classic", "Server browser check"), "bad")            # 2 of 10 unanswered
        text = netcheck.report_text({"at": 0, "lines": list(rows.values()), "host": "box", "kernel": "6.1"})
        self.assertIn("[BAD ] UDP port 2001", text)
        self.assertIn("== Network ports ==", text)


if __name__ == "__main__":
    unittest.main()


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def test_owners_run_it_and_others_cant(self):
        from aiohttp.test_utils import TestClient, TestServer
        from panel import auth
        from panel.config import PanelConfig, ServerConfig
        from panel.db import PanelDB
        from panel.servers import ServerManager
        from panel.web import create_app
        import re
        db = PanelDB(":memory:")
        db.add_user("boss", auth.hash_password("boss-password"), "owner")
        db.add_user("adm", auth.hash_password("adm-password"), "admin")
        config = PanelConfig(database=":memory:", cookie_secure=False, servers=[ServerConfig("one", "Classic")])
        client = TestClient(TestServer(create_app(config, db, ServerManager(config, db), start_manager=False)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        await client.post("/login", data={"username": "boss", "password": "boss-password"})
        page = await (await client.get("/network")).text()
        token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        result = {"at": 0, "host": "box", "kernel": "6.1", "ethtool": False, "lines": [
            netcheck.line("Network ports", "enp4s0 errors right now", "rx_missed_errors +240", netcheck.BAD, "Lost on the card."),
            netcheck.line("Kernel", "UDP packets thrown away", "0 in 10 s", netcheck.OK)]}
        with mock.patch("panel.netcheck.run", new=mock.AsyncMock(return_value=result)) as run:
            page = await (await client.post("/network", data={"csrf": token})).text()
        self.assertEqual(run.await_args.args[0], [("one", "Classic", 0, None)])
        self.assertIn("1 problem", page)
        self.assertIn("rx_missed_errors +240", page)
        self.assertIn("[BAD ] enp4s0 errors right now", page)
        self.assertIn("apt install ethtool", page)
        for words in ("Box dashboard", "What stood out", "Busiest programs right now", "Game packets lost (per minute)",
                      "Disk used (%)", "Temperature (°C)", "Login attacks today", "Port drops / min"):
            self.assertIn(words, page)
        self.assertIn("rx_missed_errors +240", await (await client.get("/network")).text())   # the last result stays
        await client.post("/logout", data={"csrf": token})
        await client.post("/login", data={"username": "adm", "password": "adm-password"})
        self.assertEqual((await client.get("/network", allow_redirects=False)).status, 403)


class HistoryTests(unittest.TestCase):
    def test_old_send_buffer_drops_still_warn(self):
        before = {"at": 0, "nics": {}, "softnet": [], "irq": {}, "cores": {}, "sockets": {},
                  "snmp": {"Udp": {"SndbufErrors": 93530224}, "Tcp": {"OutSegs": 1000, "RetransSegs": 0}}}
        after = {**before, "at": 10, "snmp": {"Udp": {"SndbufErrors": 93530224}, "Tcp": {"OutSegs": 1237, "RetransSegs": 14}}}
        extras = {"routes": [], "queries": {}, "internet": {}, "ethtool": {},
                  "sysctl": {"rmem_max": 212992, "wmem_max": 212992, "backlog": 1000, "conntrack": None, "conntrack_max": None}}
        rows = {r["label"]: r for r in netcheck.judge(before, after, [], extras)}
        send = rows["Outgoing UDP packets thrown away"]
        self.assertEqual((send["verdict"], send["value"]), ("warn", "0 in 10 s, 93,530,224 since boot"))
        self.assertIn("wmem_max", send["fix"])
        self.assertEqual(rows["TCP resends"]["verdict"], "warn")         # 5.9% but only 237 sent
        self.assertIn("small sample", rows["TCP resends"]["why"])


class FindGameTests(unittest.TestCase):
    def test_matches_programs_to_servers_by_amp_folder(self):
        with tempfile.TemporaryDirectory() as root:
            for pid, cmd in ((101, "/home/amp/.ampdata/instances/Classic01/arma-reforger/ArmaReforgerServer\0-profile\0./profile"),
                             (102, "/home/amp/.ampdata/instances/Plus01/arma-reforger/ArmaReforgerServer\0-config\0x"),
                             (103, "python3\0-m\0panel")):
                Path(root, str(pid)).mkdir()
                Path(root, str(pid), "cmdline").write_bytes(cmd.replace("\\0", "\0").encode())
            games = netcheck.find_games(root)
            self.assertEqual(sorted(g["pid"] for g in games), [101, 102])
            servers = [("s1", "Classic", 0, None), ("s4", "Plus01", 0, None), ("s2", "Server 2", 0, None)]
            dirs = {"s1": "/home/amp/.ampdata/instances/Classic01/arma-reforger/profile/logs",
                    "s4": "/home/amp/.ampdata/instances/Plus01/arma-reforger/profile/logs",
                    "s2": "/home/amp/.ampdata/instances/Other/arma-reforger/profile/logs"}
            self.assertEqual(netcheck.match_games(servers, dirs, games), {"s1": 101, "s4": 102, "s2": 0})
