import tempfile
import unittest
from pathlib import Path
from playtime_tracker import Tracker

UID = "11111111-2222-3333-4444-555555555555"


def join(t, name="Tester", connection="0x00000000"):
    return f"{t}.000 NETWORK : ### Updating player: PlayerId=1, Name={name}, rplIdentity={connection}, IdentityId={UID}\n"


def heartbeat(t, count=1):
    return f"{t}.000 DEFAULT : FPS: 60.0, Mem: 1024 kB, Player: {count}, AI: 16\n"


def leave(t):
    return f"{t}.000 RPL : ServerImpl event: disconnected (identity=0x00000000, slots=0/2)\n"


class PlaytimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "logs_2026-09-07_13-00-00" / "console.log"
        self.path.parent.mkdir()
        self.path.touch()
        self.database = self.root / "time.sqlite3"
        self.tracker = Tracker(self.root, self.database)

    def tearDown(self):
        self.tracker.close()
        self.temp.cleanup()

    def append(self, text, path=None):
        with (path or self.path).open("a", encoding="utf-8") as f:
            f.write(text)
        self.tracker.tick()

    def seconds(self):
        return self.tracker.db.execute("SELECT COALESCE(SUM(seconds),0) FROM totals").fetchone()[0]

    def test_reconnect_and_rename(self):
        self.append(join("13:00:00") + heartbeat("13:00:30") + leave("13:01:00")
                    + join("13:02:00", "New name") + heartbeat("13:02:30"))
        self.assertEqual(self.seconds(), 90)
        self.assertEqual(self.tracker.db.execute("SELECT name FROM totals").fetchone()[0], "New name")

    def test_restart_and_repeated_scans_do_not_duplicate(self):
        self.append(join("13:00:00") + heartbeat("13:00:30"))
        self.tracker.close()
        self.tracker = Tracker(self.root, self.database)
        self.tracker.tick()
        self.append(heartbeat("13:01:00"))
        self.tracker.tick()
        self.assertEqual(self.seconds(), 60)

    def test_duplicate_mapping(self):
        self.append(join("13:00:00") + join("13:00:10") + heartbeat("13:00:30"))
        self.assertEqual(self.seconds(), 30)

    def test_midnight(self):
        self.append(join("23:59:30") + heartbeat("00:00:00") + leave("00:00:30"))
        self.assertEqual(self.seconds(), 60)

    def test_gap_drops_uncertain_connection(self):
        self.append(join("13:00:00") + heartbeat("13:00:30") + heartbeat("14:00:00")
                    + heartbeat("14:00:30"))
        self.assertEqual(self.seconds(), 30)

    def test_zero_count_without_leave(self):
        self.append(join("13:00:00") + heartbeat("13:00:30") + heartbeat("13:01:00", 0)
                    + heartbeat("13:01:30", 0))
        self.assertEqual(self.seconds(), 30)

    def test_rotation_no_carry_and_import_while_offline(self):
        self.append(join("13:00:00") + heartbeat("13:00:30"))
        other = self.root / "logs_2026-09-07_14-00-00" / "console.log"
        other.parent.mkdir()
        self.append(heartbeat("14:00:00", 0) + join("14:01:00") + heartbeat("14:01:30"), other)
        self.assertEqual(self.seconds(), 60)
        self.tracker.tick()
        self.assertEqual(self.seconds(), 60)

    def test_partial_line_and_truncation(self):
        self.append(join("13:00:00") + heartbeat("13:00:30").rstrip("\n"))
        self.assertEqual(self.seconds(), 0)
        self.append("\n")
        self.assertEqual(self.seconds(), 30)
        self.path.write_text(join("13:00:00"))
        self.tracker.tick()
        self.assertEqual(self.seconds(), 30)

    def test_active_identities_reflect_current_connections(self):
        self.append(join("13:00:00") + heartbeat("13:00:30"))
        self.assertEqual(self.tracker.active_identities, {UID})
        self.append(leave("13:01:00") + heartbeat("13:01:30", 0))
        self.assertEqual(self.tracker.active_identities, set())

    def test_one_tracker_failure_does_not_affect_others(self):
        # Two independent trackers share one combined database. The one whose
        # log directory is absent must not stop the other from recording time.
        healthy_root = self.root / "ok"
        log = healthy_root / "logs_2026-09-07_13-00-00" / "console.log"
        log.parent.mkdir(parents=True)
        log.write_text(join("13:00:00") + heartbeat("13:01:00") + leave("13:02:00"))
        healthy = Tracker(healthy_root, self.database, "ok")
        broken = Tracker(self.root / "does-not-exist", self.database, "broken")
        try:
            broken.tick()  # no logs present; must return cleanly, not raise
            self.assertTrue(broken.initialized)
            healthy.tick()
            recorded = healthy.db.execute(
                "SELECT SUM(seconds) FROM totals WHERE server='ok'").fetchone()[0]
            self.assertEqual(recorded, 120)
            self.assertIsNone(
                healthy.db.execute("SELECT SUM(seconds) FROM totals WHERE server='broken'").fetchone()[0])
        finally:
            healthy.close()
            broken.close()

    def test_transaction_rollback_retries(self):
        self.append(join("13:00:00"))
        original = self.tracker.consume
        def fail(state, line):
            original(state, line)
            raise RuntimeError("simulated crash before cursor commit")
        self.tracker.consume = fail
        with self.assertRaises(RuntimeError):
            self.append(heartbeat("13:00:30"))
        self.assertEqual(self.seconds(), 0)
        self.tracker.consume = original
        self.tracker.tick()
        self.assertEqual(self.seconds(), 30)


if __name__ == "__main__":
    unittest.main()
