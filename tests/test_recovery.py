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

    async def test_online_when_log_is_written_without_a_matching_heartbeat(self):
        # Some builds format the FPS line differently; a fresh log still means up.
        self.write("12:00:00.000 SCRIPT : a line the parser ignores\n", age=0)
        monitor = self.monitor()
        await monitor._tick()
        self.assertTrue(monitor.online)

    async def test_offline_when_log_stops_being_written(self):
        self.write(heartbeat("12:00:00"), age=10000)
        monitor = self.monitor()
        await monitor._tick()
        self.assertFalse(monitor.online)

    async def test_match_recovers_on_fresh_log_without_a_heartbeat(self):
        # A build with an unrecognized heartbeat line: an open match on a log
        # that is still being written should still be picked up as live.
        self.write(game("10:00:00"), age=0)
        monitor = self.monitor()
        await monitor._tick()
        monitor.on_session_start.assert_awaited_once()
        self.assertTrue(monitor.online)

    async def test_heartbeatless_open_match_resumes_on_a_quiet_log(self):
        # No heartbeat anywhere + an open match on a quiet log: resume it live.
        # A quiet log on this build just means an idle match, so we keep it live
        # across restarts and let POSTGAME end it.
        self.write(game("10:00:00"), age=10000)
        monitor = self.monitor()
        await monitor._tick()
        monitor.on_session_start.assert_awaited_once()

    async def test_heartbeat_build_open_match_on_stale_log_waits_then_recovers(self):
        # This build DOES write heartbeats, so a stale log means the server is
        # down: don't resume until it writes again.
        self.write(game("10:00:00") + heartbeat("10:00:05"), age=10000)
        monitor = self.monitor()
        await monitor._tick()
        monitor.on_session_start.assert_not_awaited()
        os.utime(self.path, None)  # server writes again
        await monitor._tick()
        monitor.on_session_start.assert_awaited_once()

    async def test_heartbeatless_live_match_survives_a_stale_log(self):
        # A live match on a heartbeat-less build must not be ended just because
        # the log went quiet (empty server).
        monitor = self.monitor()
        monitor._live = True
        monitor._seen_heartbeat = False
        monitor._log_mtime = time.time() - 10000
        monitor._last_heartbeat = time.monotonic() - 10000
        await monitor._check_staleness()
        monitor.on_session_end.assert_not_awaited()

    async def test_heartbeat_build_still_ends_on_a_stale_log(self):
        # Regression guard: a build that writes heartbeats still ends the match
        # when both the log and the heartbeat go stale.
        monitor = self.monitor()
        monitor._live = True
        monitor._seen_heartbeat = True
        monitor._log_mtime = time.time() - 10000
        monitor._last_heartbeat = time.monotonic() - 10000
        await monitor._check_staleness()
        monitor.on_session_end.assert_awaited_once()

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
