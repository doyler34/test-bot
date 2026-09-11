"""Exercise real log recovery through category names, across restarts/new games."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.discord.category_timer import CategoryTimers
from bot.storage.notification_store import NotificationStore
from bot.tracking.reforger_monitor import ReforgerMonitor


def log(start, tail):
    return (f"{start}.000 SCRIPT : SCR_BaseGameMode::OnGameStateChanged = GAME\n"
            f"{tail}.000 DEFAULT : FPS: 60.0, Mem: 200 kB, Player: 1,\n")


class CategoryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_restart_then_server_restart_then_new_game(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            session = root / "logs_2026-09-07_10-00-00"
            session.mkdir()
            path = session / "console.log"
            original = log("10:00:00", "10:25:00")
            path.write_text(original)
            store = NotificationStore(root / "state.sqlite3")
            try:
                server = SimpleNamespace(id="server-1", name="Server 1", enabled=True)
                bot = SimpleNamespace(store=store, config=SimpleNamespace(servers=[server]), match_times={})
                categories = CategoryTimers(bot)
                category = SimpleNamespace(name="🟢 SERVER ONE · ~24 MIN")
                category.edit = AsyncMock(return_value=category)
                categories.channels[server.id] = category
                with store.db:
                    store.db.execute("INSERT INTO category_timers VALUES ('server-1',10,0)")

                def new_monitor():
                    async def started(age):
                        bot.match_times[server.id] = (time.time()-age, time.monotonic()-age)
                    async def ended():
                        bot.match_times.pop(server.id, None)
                    m = ReforgerMonitor(str(root), started, ended)
                    bot.monitors = [(server.id, m)]
                    return m

                async def scan_and_show(m, wall):
                    await m._tick()
                    m.initialized = True
                    with patch("bot.discord.category_timer.time.time", return_value=wall):
                        await categories.tick()
                    return category.edit.await_args.kwargs["name"]

                m = new_monitor()
                await categories.tick()  # Recovery not complete; preserve old heading.
                category.edit.assert_not_awaited()
                self.assertIn("~25 MIN", await scan_and_show(m, 1000))
                key = m.session_key
                bot.match_times.clear()  # Simulate fresh bot memory.
                m = new_monitor()
                self.assertIn("~25 MIN", await scan_and_show(m, 1300))
                self.assertEqual(key, m.session_key)
                self.assertEqual(path.read_text(), original)  # Never write game logs.

                session2 = root / "logs_2026-09-07_11-00-00"
                session2.mkdir()
                path2 = session2 / "console.log"
                path2.write_text(log("11:00:00", "11:00:10"))
                self.assertIn("~0 MIN", await scan_and_show(m, 1600))
                self.assertNotEqual(key, m.session_key)
                key = m.session_key
                with path2.open("a") as f:
                    f.write("11:01:00.000 SCRIPT : SCR_BaseGameMode::OnGameStateChanged = POSTGAME\n")
                    f.write(log("11:02:00", "11:02:05"))
                self.assertIn("~0 MIN", await scan_and_show(m, 1900))
                self.assertNotEqual(key, m.session_key)
                # An empty replacement log during a wipe must not resurrect time.
                path2.write_text("")
                self.assertIn("WAITING", await scan_and_show(m, 2200))
                path2.write_text(log("11:03:00", "11:03:10"))
                self.assertIn("~0 MIN", await scan_and_show(m, 2500))
            finally:
                store.close()
