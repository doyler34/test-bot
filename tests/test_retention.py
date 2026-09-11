import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate as migrate_combat, record, totals as combat_totals
from bot.config import configure_connection
from bot.storage.notification_store import NotificationStore
from bot.storage.rank_persistence import XPStore, migrate_time, record_interval
import bot.storage.retention as retention

VICTIM = "11111111-2222-3333-4444-555555555555"
KILLER = "99999999-2222-3333-4444-555555555555"


def kill(relation="ENEMY"):
    return SimpleNamespace(victim=VICTIM, killer=KILLER, relation=relation)


class CombatRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / "links.db")
        migrate_combat(self.links.db)
        self.db = self.links.db

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def test_old_events_pruned_but_totals_and_recent_dedup_kept(self):
        with self.db:
            for day in range(10):
                record(self.db, "server-1", f"2026-01-{day:02d}T10:00:00.000", kill())
            for day in range(3):
                record(self.db, "server-1", f"2026-09-{day:02d}T10:00:00.000", kill())
        kills = combat_totals(self.db, KILLER)["player_kills"]
        deaths = combat_totals(self.db, VICTIM)["deaths"]
        self.assertEqual((kills, deaths), (13, 13))
        removed = retention.prune_combat_events(self.db, "2026-06-01T00:00:00.000")
        self.assertEqual(removed, 10)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM combat_events").fetchone()[0], 3)
        # Totals are the permanent aggregate; pruning raw dedup rows must not move them.
        self.assertEqual(combat_totals(self.db, KILLER)["player_kills"], kills)
        self.assertEqual(combat_totals(self.db, VICTIM)["deaths"], deaths)

    def test_duplicate_event_after_restart_counts_once(self):
        with self.db:
            self.assertTrue(record(self.db, "server-1", "2026-09-01T10:00:00.000", kill()))
        self.links.close()
        self.links = AccountLinks(Path(self.tmp.name) / "links.db")  # simulate restart
        self.db = self.links.db
        with self.db:
            self.assertFalse(record(self.db, "server-1", "2026-09-01T10:00:00.000", kill()))
        self.assertEqual(combat_totals(self.db, KILLER)["player_kills"], 1)


class PostRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / "links.db")
        self.wallet = XPStore(self.links.db)
        self.db = self.links.db
        self.links.verified_link(1, 10, VICTIM, "admin:1")

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def test_award_is_idempotent_across_restart(self):
        now = time.time()
        self.assertEqual(self.wallet.award_post(1, 10, 555, now + 10), 1)
        self.assertEqual(self.wallet.award_post(1, 10, 555, now + 10), 0)  # same id, no double count
        self.links.close()
        self.links = AccountLinks(Path(self.tmp.name) / "links.db")
        self.wallet = XPStore(self.links.db)
        self.db = self.links.db
        self.assertEqual(self.wallet.award_post(1, 10, 555, now + 10), 0)  # dedup row survived restart
        self.assertEqual(self.wallet.post_xp(1, 10), 1)

    def test_old_post_events_pruned_total_preserved_and_growth_bounded(self):
        now = time.time()
        for message in range(50):
            self.wallet.award_post(1, 10, 1000 + message, now + 10)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM discord_post_events").fetchone()[0], 50)
        total = self.wallet.post_xp(1, 10)
        self.assertEqual(total, 50)
        # Age every dedup row well past the retention window, then sweep.
        with self.db:
            self.db.execute("UPDATE discord_post_events SET created=?", (now - 90 * 86400,))
        removed = retention.prune_post_events(self.db, now - 30 * 86400)
        self.assertEqual(removed, 50)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM discord_post_events").fetchone()[0], 0)
        self.assertEqual(self.wallet.post_xp(1, 10), total)  # XP total is preserved


class AnnouncementRetentionTests(unittest.TestCase):
    def test_only_finished_and_old_announcements_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = NotificationStore(Path(tmp) / "notifications.db")
            try:
                now = time.time()
                store.enqueue("server-1", "old", 1, "Server", now - 100 * 86400, now - 100 * 86400)
                store.enqueue("server-1", "recent", 1, "Server", now, now)
                store.enqueue("server-2", "live", 1, "Server", now - 100 * 86400, now - 100 * 86400)
                for row in store.pending():
                    if row["session"] in ("old", "recent"):
                        store.finish(row)  # done=1
                removed = retention.prune_finished_announcements(store.db, now - 7 * 86400)
                self.assertEqual(removed, 1)  # only the old, finished one
                sessions = {r[0] for r in store.db.execute("SELECT session FROM announcements").fetchall()}
                self.assertEqual(sessions, {"recent", "live"})
            finally:
                store.close()


class IntervalRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = sqlite3.connect(Path(self.tmp.name) / "playtime.db")
        self.db.execute("CREATE TABLE totals(server TEXT, identity TEXT, seconds REAL)")
        migrate_time(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_old_intervals_compacted_combined_time_preserved(self):
        with self.db:
            record_interval(self.db, "p", 0, 600)              # old session
            record_interval(self.db, "p", 5000, 5600)          # old session
            record_interval(self.db, "p", 1_000_000, 1_000_600)  # recent session
        total = self.db.execute("SELECT milliseconds FROM global_time").fetchone()[0]
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM global_intervals").fetchone()[0], 3)
        removed = retention.compact_intervals(self.db, 600_000_000)  # cutoff in ms
        self.assertEqual(removed, 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM global_intervals").fetchone()[0], 1)
        # Permanent combined-time total is unaffected by interval compaction.
        self.assertEqual(self.db.execute("SELECT milliseconds FROM global_time").fetchone()[0], total)


class SqliteConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "links.db"
        self.links = AccountLinks(self.path)

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def test_wal_and_busy_timeout_configured(self):
        self.assertEqual(self.links.db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertEqual(self.links.db.execute("PRAGMA busy_timeout").fetchone()[0], 10000)

    def test_reader_not_blocked_by_open_writer_under_wal(self):
        with closing(sqlite3.connect(self.path, timeout=1)) as reader:
            configure_connection(reader)  # configure before the writer holds a lock
            self.links.db.execute("BEGIN IMMEDIATE")
            self.links.db.execute("INSERT INTO account_links VALUES (1,10,?,?, 'admin')",
                                  (VICTIM, time.time()))
            try:
                # Under WAL the reader sees the last committed snapshot rather than
                # blocking/timing out on the open writer (rollback journal would).
                rows = reader.execute("SELECT COUNT(*) FROM account_links").fetchone()[0]
            finally:
                self.links.db.rollback()
        self.assertEqual(rows, 0)


if __name__ == "__main__":
    unittest.main()
