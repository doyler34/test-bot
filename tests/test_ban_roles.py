import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot.discord.ban_roles import PREFIX, BanRoles, active_bans, length_label
from panel.db import PanelDB

HAVOC = "5f1c2a90-8b1e-4a57-9a3e-2d4b6c8e0f11"
ROOK = "a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d"


class Role:
    def __init__(self, name):
        self.name = name
        self.members = []


class Member:
    def __init__(self, member_id):
        self.id = member_id
        self.roles = []

    async def add_roles(self, role, reason=None):
        self.roles.append(role)
        role.members.append(self)

    async def remove_roles(self, role, reason=None):
        self.roles.remove(role)
        role.members.remove(self)


class Guild:
    id = 1

    def __init__(self, members):
        self.members = {m.id: m for m in members}
        self.roles = []
        self.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True))

    def get_member(self, member_id):
        return self.members.get(member_id)

    async def create_role(self, name, **kwargs):
        role = Role(name)
        self.roles.append(role)
        return role


class Links:
    def __init__(self, links):
        self.links = links

    def owner(self, guild, identity):
        return self.links.get(identity)


class LabelTests(unittest.TestCase):
    def test_panel_lengths(self):
        self.assertEqual(length_label(100, 100 + 3600), "1 hour")
        self.assertEqual(length_label(100, 100 + 604800), "7 days")
        self.assertEqual(length_label(100, None), "Permanent")
        self.assertEqual(length_label(0, 3 * 86400), "3 days")


class BanRoleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name, "panel.sqlite3"))
        self.panel = PanelDB(self.path)
        self.addCleanup(self.panel.close)
        self.havoc, self.rook = Member(10), Member(20)
        self.guild = Guild([self.havoc, self.rook])
        bot = SimpleNamespace(config=SimpleNamespace(guild_id=1), get_guild=lambda _: self.guild,
                              account_links=Links({HAVOC: 10, ROOK: 20}), is_closed=lambda: False)
        with patch.dict(os.environ, {"PANEL_DB": self.path}):
            self.roles = BanRoles(bot)

    def names(self, member):
        return [r.name for r in member.roles]

    async def test_role_follows_the_ban(self):
        ban = self.panel.add_ban(HAVOC, "Havoc", "teamkilling", "boss", expires_at=int(time.time()) + 86400)
        await self.roles.tick()
        self.assertEqual(self.names(self.havoc), [PREFIX + "1 day"])
        self.assertEqual(self.names(self.rook), [])
        self.panel.remove_ban(ban, "boss")
        self.panel.add_ban(HAVOC, "Havoc", "again", "boss")
        await self.roles.tick()
        self.assertEqual(self.names(self.havoc), [PREFIX + "Permanent"])
        self.panel.write("UPDATE bans SET removed_at = 1")
        await self.roles.tick()
        self.assertEqual(self.names(self.havoc), [])

    async def test_expired_ban_loses_the_role(self):
        ban = self.panel.add_ban(ROOK, "Rook", "spam", "boss", expires_at=int(time.time()) + 3600)
        await self.roles.tick()
        self.assertEqual(self.names(self.rook), [PREFIX + "1 hour"])
        self.panel.write("UPDATE bans SET expires_at = ? WHERE id = ?", int(time.time()) - 1, ban)
        await self.roles.tick()
        self.assertEqual(self.names(self.rook), [])

    async def test_unreadable_panel_changes_nothing(self):
        self.panel.add_ban(HAVOC, "Havoc", "teamkilling", "boss")
        await self.roles.tick()
        self.roles.path = str(Path(self.tmp.name, "missing.sqlite3"))
        await self.roles.tick()
        self.assertEqual(self.names(self.havoc), [PREFIX + "Permanent"])
        self.assertIsNone(active_bans(self.roles.path))

    async def test_role_is_made_once(self):
        self.panel.add_ban(HAVOC, "Havoc", "a", "boss")
        self.panel.add_ban(ROOK, "Rook", "b", "boss")
        await self.roles.tick()
        await self.roles.tick()
        self.assertEqual([r.name for r in self.guild.roles], [PREFIX + "Permanent"])


if __name__ == "__main__":
    unittest.main()
