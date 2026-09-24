import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import discord
from bot.discord.notification_roles import (PANEL_MARKER, NotificationView,
                                            prepare_notifications, prepare_role)
from bot.storage.notification_store import NotificationStore


class Fixture:
    """Shared guild/store doubles for the role and panel tests."""
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NotificationStore(Path(self.tmp.name) / "state.sqlite3")
        self.role = Mock(spec=discord.Role)
        self.role.id = 77
        self.role.name = "Server One"
        self.role.permissions = discord.Permissions.none()
        self.role.managed = False
        self.role.mentionable = True
        self.role.is_default.return_value = False
        top = Mock(spec=discord.Role)
        top.__gt__ = Mock(return_value=True)
        self.guild = SimpleNamespace(roles=[], me=SimpleNamespace(top_role=top),
            get_role=Mock(return_value=self.role), create_role=AsyncMock(return_value=self.role),
            fetch_member=AsyncMock())
        self.bot = SimpleNamespace(store=self.store, config=SimpleNamespace(guild_id=1),
            roles_by_server={}, notification_role_lock=asyncio.Lock())
        self.server = SimpleNamespace(id="server-1", name="Server 1")

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()


class RoleTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def test_create_and_reuse_after_restart(self):
        await prepare_role(self.bot, self.guild, self.server)
        self.bot.roles_by_server.clear()
        await prepare_role(self.bot, self.guild, self.server)
        self.guild.create_role.assert_awaited_once()
        self.assertEqual(self.bot.roles_by_server["server-1"].id, 77)

    async def test_reject_permission_bearing_role(self):
        self.guild.roles = [self.role]
        self.role.permissions = discord.Permissions(administrator=True)
        with self.assertRaises(RuntimeError):
            await prepare_role(self.bot, self.guild, self.server)

    async def test_persistent_button_and_toggle_both_ways(self):
        self.bot.roles_by_server["server-1"] = self.role
        view = NotificationView(self.bot, "server-1")
        self.assertTrue(view.is_persistent())
        self.assertEqual(view.children[0].custom_id,
                         NotificationView(self.bot, "server-1").children[0].custom_id)
        member = SimpleNamespace(roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.guild.fetch_member.return_value = member
        interaction = SimpleNamespace(guild_id=1, guild=self.guild, user=SimpleNamespace(id=10),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        await view.toggle(interaction)
        member.add_roles.assert_awaited_once_with(self.role, reason="Member opted in to match alerts")
        member.roles = [self.role]
        await view.toggle(interaction)
        member.remove_roles.assert_awaited_once_with(self.role, reason="Member opted out of match alerts")
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    async def test_role_error_has_private_feedback(self):
        self.bot.roles_by_server["server-1"] = self.role
        member = SimpleNamespace(roles=[], add_roles=AsyncMock(side_effect=discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")))
        self.guild.fetch_member.return_value = member
        interaction = SimpleNamespace(guild_id=1, guild=self.guild, user=SimpleNamespace(id=10),
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        await NotificationView(self.bot, "server-1").toggle(interaction)
        self.assertIn("Could not update", interaction.followup.send.await_args.args[0])

    async def test_only_notification_button_remains(self):
        view = NotificationView(self.bot, "server-1")
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].label, "Toggle match notifications")
        self.assertTrue(view.is_persistent())


class PanelTests(Fixture, unittest.IsolatedAsyncioTestCase):
    """The opt-in panel lives in the faction-roles channel, one message forever."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bot.user = SimpleNamespace(id=5)
        self.bot.config = SimpleNamespace(guild_id=1, servers=(
            SimpleNamespace(id="server-1", name="Server 1", enabled=True),
            SimpleNamespace(id="server-3", name="Server 3", enabled=False)))
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.send = AsyncMock()
        self.history = []
        self.channel.history = Mock(side_effect=lambda limit: self.replay())

    def replay(self):
        async def cursor():
            for message in self.history:
                yield message
        return cursor()

    async def test_posts_once_then_edits_on_restart(self):
        await prepare_notifications(self.bot, self.guild, self.channel)
        self.channel.send.assert_awaited_once()
        embed = self.channel.send.await_args.kwargs["embed"]
        self.assertEqual(embed.footer.text, PANEL_MARKER)
        view = self.channel.send.await_args.kwargs["view"]
        self.assertEqual(len(view.children), 1)
        self.assertFalse(self.channel.send.await_args.kwargs["allowed_mentions"].everyone)
        posted = SimpleNamespace(author=SimpleNamespace(id=5), embeds=[embed], edit=AsyncMock())
        self.history = [posted]
        await prepare_notifications(self.bot, self.guild, self.channel)
        self.channel.send.assert_awaited_once()
        posted.edit.assert_awaited_once()

    async def test_only_enabled_servers_get_a_role(self):
        await prepare_notifications(self.bot, self.guild, self.channel)
        self.assertEqual(list(self.bot.roles_by_server), ["server-1"])
