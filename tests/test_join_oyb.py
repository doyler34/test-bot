import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import sqlite3
import discord

from account_links import AccountLinks
from join_oyb import JoinView, ReviewDecision, find_identity, prepare_join_channel


class JoinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.links = AccountLinks(self.root / "links.sqlite3")
        self.bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1),
                                   user=SimpleNamespace(id=99))

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    async def test_only_admin_can_approve(self):
        identity = "11111111-2222-3333-4444-555555555555"
        token = self.links.submit(1, 10, identity, "Player")
        interaction = SimpleNamespace(guild_id=1, user=SimpleNamespace(id=22),
            permissions=SimpleNamespace(manage_guild=False, administrator=False),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()))
        view = ReviewDecision(self.bot, token, 22)
        await view.decide(interaction, True)
        self.assertIsNone(self.links.lookup(1, 10))
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        interaction.permissions.manage_guild = True
        await view.decide(interaction, True)
        self.assertEqual(self.links.lookup(1, 10), identity)

    async def test_create_reuse_card_and_persistent_buttons(self):
        channel = Mock(spec=discord.TextChannel)
        channel.id = 55
        from join_oyb import MARKER
        channel.topic = MARKER
        info = SimpleNamespace(id=66, author=self.bot.user, embeds=[], edit=AsyncMock())
        channel.send = AsyncMock(return_value=info)
        channel.fetch_message = AsyncMock(return_value=info)
        channel.edit = AsyncMock()
        async def empty(**kwargs):
            if False:
                yield
        channel.history = empty
        guild = SimpleNamespace(id=1, text_channels=[], get_channel=Mock(return_value=channel),
                                create_text_channel=AsyncMock(return_value=channel))
        await prepare_join_channel(self.bot, guild, {})
        info.embeds = [channel.send.await_args.kwargs["embed"]]
        view = channel.send.await_args.kwargs["view"]
        self.assertTrue(view.is_persistent())
        self.assertEqual(len(view.children), 3)
        await prepare_join_channel(self.bot, guild, {})
        guild.create_text_channel.assert_awaited_once()
        channel.send.assert_awaited_once()
        info.edit.assert_awaited_once()
        self.assertTrue(channel.send.await_args.kwargs["silent"])

    async def test_name_lookup_is_unique_and_does_not_change_totals(self):
        path = self.root / "playtime.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.execute("CREATE TABLE totals (identity TEXT, name TEXT, seconds REAL)")
            db.execute("INSERT INTO totals VALUES ('id-one','Player',123)")
            db.commit()
            self.assertEqual(find_identity(path, "player"), "id-one")
            db.execute("INSERT INTO totals VALUES ('id-two','Player',456)")
            db.commit()
            with self.assertRaises(ValueError):
                find_identity(path, "Player")
            self.assertEqual(db.execute("SELECT SUM(seconds) FROM totals").fetchone()[0], 579)
        finally:
            db.close()
