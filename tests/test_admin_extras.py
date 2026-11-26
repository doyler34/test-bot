"""Vote kicks in the live feed, players per side, crash reports to the staff channel, and automatic bans to check."""
import tempfile
import unittest
from pathlib import Path

from panel.alerts import Alerts
from panel.config import PanelConfig, ServerConfig
from panel.connections import read_lines
from panel.db import PanelDB, now
from panel.servers import CRASH_LINES, ServerManager, last_log_lines

GAZ = "aaaaaaaa-0000-4000-8000-000000000001"
BURD = "bbbbbbbb-0000-4000-8000-000000000002"
VOTE_START = ("10:31:00.000   SCRIPT       : VOTING SYSTEM - Player 'GazLagom' (with player id = 1) started a vote to "
              "kick player 'Sgt_Burd' (with player id = 2)")
VOTE_PASSED = "10:31:40.000   SCRIPT       : VOTING SYSTEM - Vote to kick player 'Sgt_Burd' (with player id = 2) succeeded"
VOTE_FAILED = "10:40:00.000   SCRIPT       : VOTING SYSTEM - Vote to kick player 'Rook' (with player id = 3) failed"
SIDE = ("10:20:00.000   SCRIPT       : INFO: Faction: player GazLagom (playerID = 1 | UUID = " + GAZ + ") "
        "has joined faction #AR-Faction_USSR (USSR)")


class LogLines(unittest.TestCase):
    def test_vote_kicks_and_their_outcome(self):
        events = read_lines([VOTE_START, VOTE_PASSED, VOTE_FAILED], 0)
        self.assertEqual([(e["kind"], e["text"]) for e in events], [
            ("vote", "GazLagom started a vote to kick Sgt_Burd"),
            ("vote", "Vote to kick Sgt_Burd passed: they were kicked"),
            ("vote", "Vote to kick Rook failed")])

    def test_a_side_pick_says_who_and_which_side(self):
        event = read_lines([SIDE], 0)[0]
        self.assertEqual((event["kind"], event["identity"], event["faction"]), ("side", GAZ, "USSR"))

    def test_the_votes_filter(self):
        db = PanelDB(":memory:")
        self.addCleanup(db.close)
        db.add_connections("one", read_lines([SIDE, VOTE_START], 0), {})
        self.assertEqual([r["kind"] for r in db.feed("one", show="votes")], ["vote"])


class Server(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        config = ServerConfig("one", "Server 1", log_dir=str(self.tmp))
        self.manager = ServerManager(PanelConfig(servers=[config]), self.db, alerts=Alerts(""))
        self.state = self.manager.states["one"]

    def write_log(self, lines):
        folder = self.tmp / "logs_2026-09-25_10-16-32"
        folder.mkdir(exist_ok=True)
        (folder / "console.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def test_players_per_side_from_the_side_they_joined(self):
        self.manager.check(self.state, read_lines([SIDE], 0))
        self.state.online = True
        self.state.players = [{"identity": GAZ, "name": "GazLagom", "id": "1"},
                              {"identity": BURD, "name": "Sgt_Burd", "id": "2"}]
        await self.manager.monitor_server(self.state, proc_root=str(self.tmp / "no-proc"))
        row = self.db.server_samples("one", 0)[-1]
        self.assertEqual((row["players"], row["side_us"], row["side_ussr"], row["side_fia"]), (2, 0, 1, 0))

    def test_a_crash_sends_the_end_of_the_log_to_staff(self):
        self.write_log([f"line {n}" for n in range(100)] + ["ENGINE (F): Application crashed"])
        self.manager._process_gone(self.state)
        alert = self.db.staff_alerts()[0]
        self.assertEqual(alert["title"], "Server crash on Server 1")
        self.assertIn("ENGINE (F): Application crashed", alert["text"])
        self.assertNotIn("line 10\n", alert["text"])

    def test_an_admin_stop_is_not_a_crash(self):
        self.state.expected_until = now() + 60
        self.manager._process_gone(self.state)
        self.assertEqual(self.db.staff_alerts(), [])

    def test_the_log_end_fits_a_staff_alert(self):
        self.write_log(["x" * 500] * 200)
        tail = last_log_lines(str(self.tmp))
        self.assertLessEqual(len(tail), 3200)
        self.assertLessEqual(len(tail.splitlines()), CRASH_LINES)
        self.assertEqual(last_log_lines(str(self.tmp / "missing")), "")


class AutoBans(unittest.TestCase):
    def test_automatic_bans_wait_to_be_checked(self):
        db = PanelDB(":memory:")
        self.addCleanup(db.close)
        auto = db.add_ban(GAZ, "GazLagom", "Teamkilling (automatic ban)", "Automatic")
        matched = db.add_ban(BURD, "Sgt_Burd", "Same IP", "panel")
        db.add_ban("cccccccc-0000-4000-8000-000000000003", "Rook", "Cheating", "boss")
        self.assertEqual([b["id"] for b in db.auto_bans_to_review()], [matched, auto])
        db.review_ban(auto, "boss")
        self.assertEqual([b["id"] for b in db.auto_bans_to_review()], [matched])
        db.remove_ban(matched, "boss")
        self.assertEqual(db.auto_bans_to_review(), [])


if __name__ == "__main__":
    unittest.main()


class FeedSides(unittest.TestCase):
    def test_a_line_shows_the_side_the_player_was_on_then(self):
        db = PanelDB(":memory:")
        self.addCleanup(db.close)
        for at, text, kind in ((100, "GazLagom joined USSR", "side"), (200, "GazLagom killed Rook (40 m)", "kill"),
                               (300, "GazLagom joined US", "side"), (400, "GazLagom killed Brix (50 m)", "kill")):
            db.db.execute("INSERT INTO feed (server, at, kind, text, ip) VALUES ('one', ?, ?, ?, '')", (at, kind, text))
        db.db.commit()
        chips = {row["text"]: row["parts"][0][0] for row in db.feed("one") if row["kind"] == "kill"}
        self.assertEqual(chips, {"GazLagom killed Rook (40 m)": "ussr", "GazLagom killed Brix (50 m)": "us"})
