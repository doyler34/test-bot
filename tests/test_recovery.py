"""Recover match age from actual log files across monitor restarts."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bot.tracking.reforger_monitor import ReforgerMonitor


def game(stamp):
    return f"{stamp}.000 SCRIPT : SCR_BaseGameMode::OnGameStateChanged = GAME\n"


def end(stamp):
    return f"{stamp}.000 SCRIPT : SCR_BaseGameMode::OnGameStateChanged = POSTGAME\n"


def heartbeat(stamp):
    return f"{stamp}.000 DEFAULT : FPS: 60.0, Mem: 200 kB, Player: 12,\n"


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "console.log"

    def write(self, text, age=0):
        self.path.write_text(text, encoding="utf-8")
        modified = time.time() - age
        os.utime(self.path, (modified, modified))

    def monitor(self):
        return ReforgerMonitor(self.tmp.name, AsyncMock(), AsyncMock())

    def assert_age(self, monitor, seconds):
        monitor.on_session_start.assert_awaited_once()
        elapsed = monitor.on_session_start.await_args.args[0]
        self.assertAlmostEqual(elapsed, seconds, delta=3)

    async def test_fresh_bot_recovers_same_match_age_twice(self):
        self.write(game("10:00:00") + heartbeat("12:14:00"))
        first = self.monitor()
        await first._tick()
        self.assert_age(first, 8040)
        second = self.monitor()
        await second._tick()
        self.assert_age(second, 8040)

    async def test_only_latest_match_is_published(self):
        self.write(game("08:00:00") + end("09:00:00") +
                   game("10:00:00") + heartbeat("10:42:00"))
        monitor = self.monitor()
        await monitor._tick()
        self.assert_age(monitor, 2520)
        monitor.on_session_end.assert_not_awaited()

    async def test_midnight_rollover(self):
        self.write(game("23:50:00") + heartbeat("23:59:00") + heartbeat("00:10:00"))
        monitor = self.monitor()
        await monitor._tick()
        self.assert_age(monitor, 1200)

    async def test_multiple_days(self):
        self.write(game("23:50:00") + heartbeat("00:10:00") +
                   heartbeat("12:00:00") + heartbeat("23:59:00") +
                   heartbeat("00:10:00"))
        monitor = self.monitor()
        await monitor._tick()
        self.assert_age(monitor, 87600)

    async def test_time_since_last_log_write_is_included(self):
        self.write(game("10:00:00") + heartbeat("10:30:00"), age=30)
        monitor = self.monitor()
        await monitor._tick()
        self.assert_age(monitor, 1830)

    async def test_finished_or_stale_match_is_not_replayed(self):
        for text, age in [(game("10:00:00") + end("11:00:00"), 0),
                          (game("10:00:00") + heartbeat("11:00:00"), 300)]:
            with self.subTest(age=age):
                self.write(text, age)
                monitor = self.monitor()
                await monitor._tick()
                monitor.on_session_start.assert_not_awaited()

    async def test_live_a2s_fallback_preserves_recovered_age(self):
        self.write(game("10:00:00") + heartbeat("11:00:00"), age=300)
        monitor = self.monitor()
        monitor._server_alive_via_a2s = AsyncMock(return_value=True)
        await monitor._tick()
        self.assert_age(monitor, 3900)

    async def test_new_match_resets_after_end(self):
        self.write(game("10:00:00") + heartbeat("11:00:00"))
        monitor = self.monitor()
        await monitor._tick()
        monitor.on_session_start.reset_mock()
        with self.path.open("a") as fh:
            fh.write(end("11:01:00") + game("11:02:00") + heartbeat("11:02:05"))
        await monitor._tick()
        monitor.on_session_end.assert_awaited_once()
        self.assert_age(monitor, 5)

    async def test_new_game_server_session_resets(self):
        folder = Path(self.tmp.name) / "logs_2026-09-07_10-00-00"
        folder.mkdir()
        self.path = folder / "console.log"
        self.write(game("10:00:00") + heartbeat("11:00:00"))
        monitor = self.monitor()
        await monitor._tick()
        monitor.on_session_start.reset_mock()
        folder = Path(self.tmp.name) / "logs_2026-09-07_12-00-00"
        folder.mkdir()
        self.path = folder / "console.log"
        self.write(game("12:00:00") + heartbeat("12:00:10"))
        await monitor._tick()
        monitor.on_session_end.assert_awaited_once()
        self.assert_age(monitor, 10)


if __name__ == "__main__":
    unittest.main()
