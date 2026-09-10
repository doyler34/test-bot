import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import discord
from notification_store import NotificationStore
from server_layout import cleanup_legacy_layout, remove_timer_categories


async def stream(items):
    for item in items:
        yield item


class LayoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.tmp.name) / "state.db")
        self.server = SimpleNamespace(id="server-1", name="Server 1")
        self.old = Mock(spec=discord.TextChannel)
        self.old.id, self.old.category_id = 20, 30
        self.old.topic = "OYB • server-1 • Settings, rules and match notifications"
        self.old.threads = []
        self.old.archived_threads = Mock(side_effect=lambda **kw: stream([]))
        embed = discord.Embed().set_footer(text="OYB • Server 1 • In-game rules")
        self.info = SimpleNamespace(author=SimpleNamespace(id=99), embeds=[embed], attachments=[])
        self.old.history = Mock(side_effect=lambda **kw: stream([self.info]))
        self.old.delete = AsyncMock()
        self.duplicate = Mock(spec=discord.CategoryChannel)
        self.duplicate.id, self.duplicate.name = 30, "SERVER ONE · WAITING"
        self.duplicate.category_id = None
        self.duplicate.delete = AsyncMock()
        self.guild = SimpleNamespace(fetch_channels=AsyncMock(side_effect=[[self.old, self.duplicate], [self.duplicate]]))
        self.bot = SimpleNamespace(store=self.store, user=SimpleNamespace(id=99),
            config=SimpleNamespace(servers=[self.server]), channels_by_server={"server-1": SimpleNamespace(id=50)},
            category_timers=SimpleNamespace(channels={"server-1": SimpleNamespace(id=10)}))

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_removes_old_bot_cards_and_empty_duplicate_only(self):
        await cleanup_legacy_layout(self.bot, self.guild)
        self.old.delete.assert_awaited_once()
        self.duplicate.delete.assert_awaited_once()

    async def test_preserves_user_messages_and_occupied_categories(self):
        self.info.author.id = 123
        self.guild.fetch_channels.side_effect = [[self.old, self.duplicate]] * 2
        await cleanup_legacy_layout(self.bot, self.guild)
        self.old.delete.assert_not_awaited()
        self.duplicate.delete.assert_not_awaited()

    async def test_pending_alert_keeps_legacy_channel_until_expiry(self):
        self.store.enqueue("server-1", "match", 20, "Server 1", 100, 100)
        self.guild.fetch_channels.side_effect = [[self.old, self.duplicate]] * 2
        await cleanup_legacy_layout(self.bot, self.guild)
        self.old.delete.assert_not_awaited()
        self.duplicate.delete.assert_not_awaited()

    async def test_remove_timer_categories_ungroups_channels_then_deletes(self):
        voice = Mock(spec=discord.VoiceChannel)
        voice.id, voice.category_id, voice.edit = 40, 30, AsyncMock()
        self.guild.fetch_channels = AsyncMock(return_value=[voice, self.duplicate])
        await remove_timer_categories(self.bot, self.guild)
        # The contained channel is ungrouped (category=None), never deleted.
        voice.edit.assert_awaited_once()
        self.assertIsNone(voice.edit.await_args.kwargs["category"])
        # The retired category itself is removed.
        self.duplicate.delete.assert_awaited_once()
