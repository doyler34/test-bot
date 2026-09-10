import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
import discord
from category_timer import CategoryTimers, category_name, matches_category
from notification_store import NotificationStore


class CategoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.tmp.name) / "state.sqlite3")
        self.server = SimpleNamespace(id="server-1", name="Server 1", enabled=True)
        self.bot = SimpleNamespace(store=self.store, config=SimpleNamespace(servers=[self.server]),
                                   match_times={"server-1": (1000, 100)})
        self.manager = CategoryTimers(self.bot)
        self.category = Mock(spec=discord.CategoryChannel)
        self.category.id = 10
        self.category.name = "SERVER ONE · WAITING"
        self.category.edit = AsyncMock(return_value=self.category)
        self.guild = SimpleNamespace(get_channel=Mock(return_value=self.category),
                                     create_category=AsyncMock(return_value=self.category))

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_create_reuse_and_five_minute_budget_survives_restart(self):
        await self.manager.prepare(self.guild, self.server)
        with patch("category_timer.time.time", return_value=1000), patch("category_timer.time.monotonic", return_value=1600):
            await self.manager.tick()
        self.category.edit.assert_awaited_once_with(name="🟢 SERVER ONE · ~25 MIN", reason="OYB approximate match time")
        restarted = CategoryTimers(self.bot)
        await restarted.prepare(self.guild, self.server)
        self.guild.create_category.assert_awaited_once()
        with patch("category_timer.time.time", return_value=1299):
            await restarted.tick()
        self.assertEqual(self.category.edit.await_count, 1)
        with patch("category_timer.time.time", return_value=1300), patch("category_timer.time.monotonic", return_value=1900):
            await restarted.tick()
        self.assertEqual(self.category.edit.await_count, 2)
        self.assertIn("~30 MIN", self.category.edit.await_args.kwargs["name"])

    async def test_end_and_disabled_labels(self):
        self.assertEqual(category_name(self.server, None, 0), "SERVER ONE · WAITING")
        self.server.enabled = False
        self.assertEqual(category_name(self.server, (0, 0), 999), "SERVER ONE · COMING SOON")

    async def test_long_timers_format_and_remain_discoverable_after_reinstall(self):
        for minutes, expected in [(59, "59 MIN"), (60, "1h 00m"), (351, "5h 51m"), (1440, "24h 00m")]:
            self.category.name = category_name(self.server, (0, 0), minutes * 60)
            self.assertEqual(self.category.name, f"🟢 SERVER ONE · ~{expected}")
            self.assertTrue(matches_category(self.category, self.server))
        self.category.name = "🟢 SERVER ONE · ~351 MIN"
        self.assertTrue(matches_category(self.category, self.server))

    async def test_reinstall_reuses_existing_voice_category_over_saved_duplicate(self):
        older = Mock(spec=discord.CategoryChannel)
        older.id, older.name, older.voice_channels = 5, "🟢 SERVER ONE · ~351 MIN", [object()]
        self.category.voice_channels = []
        self.guild.categories = [self.category, older]
        with self.store.db:
            self.store.db.execute("INSERT INTO category_timers VALUES ('server-1',10,0)")
        selected = await self.manager.prepare(self.guild, self.server)
        self.assertEqual(selected.id, 5)
        self.guild.create_category.assert_not_awaited()

    async def test_reinstall_without_saved_state_reuses_empty_category(self):
        self.category.voice_channels = []
        self.guild.categories = [self.category]
        await self.manager.prepare(self.guild, self.server)
        self.guild.create_category.assert_not_awaited()

    async def test_rate_limit_error_does_not_retry_immediately(self):
        await self.manager.prepare(self.guild, self.server)
        self.category.edit.side_effect = discord.HTTPException(SimpleNamespace(status=429, reason="Rate limited"), "retry")
        with patch("category_timer.time.time", return_value=1000), patch("category_timer.time.monotonic", return_value=1600):
            await self.manager.tick()
            await self.manager.tick()
        self.assertEqual(self.category.edit.await_count, 1)
