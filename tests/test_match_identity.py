"""Real monitor callbacks enqueue recoverable announcements, including on restart."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from notification_store import NotificationStore
from reforger_monitor import ReforgerMonitor


class MatchIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_and_recovered_match_share_key_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "console.log"
            path.write_text("10:00:00.000 DEFAULT : FPS: 60.0, Mem: 200 kB, Player: 1,\n")
            store = NotificationStore(Path(directory) / "notifications.sqlite3")
            self.addCleanup(store.close)
            keys = []

            def make_monitor():
                async def started(elapsed):
                    self.assertTrue(monitor.session_key)
                    keys.append(monitor.session_key)
                    store.enqueue("server-1", monitor.session_key, 123, "Test", 1000, 1000)
                monitor = ReforgerMonitor(directory, started, AsyncMock())
                return monitor

            live = make_monitor()
            await live._tick()
            with path.open("a") as f:
                f.write("10:00:30.000 SCRIPT : SCR_BaseGameMode::OnGameStateChanged = GAME\n")
            await live._tick()
            recovered = make_monitor()
            await recovered._tick()
            self.assertEqual(keys[0], keys[1])
            self.assertEqual(len(store.pending()), 1)
            with path.open("a") as f:
                f.write("10:01:00.000 SCRIPT : SCR_BaseGameMode::OnGameStateChanged = POSTGAME\n")
                f.write("10:01:30.000 SCRIPT : SCR_BaseGameMode::OnGameStateChanged = GAME\n")
            await recovered._tick()
            self.assertNotEqual(keys[1], keys[2])
            self.assertEqual(len(store.pending()), 2)
            store.close()

