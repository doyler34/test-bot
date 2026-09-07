import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import discord
from notification_roles import NotificationView, prepare_role
from notification_store import NotificationStore


class RoleTests(unittest.IsolatedAsyncioTestCase):
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
