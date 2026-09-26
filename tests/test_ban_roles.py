import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

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
        self.dms = []
        self.closed = False

    async def send(self, embed=None):
        if self.closed:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Cannot send messages to this user")
        self.dms.append(embed)

    async def add_roles(self, role, reason=None):
        self.roles.append(role)

    async def remove_roles(self, role, reason=None):
        self.roles.remove(role)


class Guild:
    """Like a bot without the members intent: nothing cached, role.members
    stays empty, and every member has to be fetched."""
    id = 1
    name = "OYB"

    def __init__(self, members):
        self.members = {m.id: m for m in members}
        self.roles = []
        self.fetched = 0
        self.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True))

    def get_member(self, member_id):
        return None

    async def fetch_member(self, member_id):
        self.fetched += 1
        if member_id not in self.members:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")
        return self.members[member_id]

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


class BanSetup(unittest.IsolatedAsyncioTestCase):
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
        self.bot = bot
        self.env = {"PANEL_DB": self.path, "BAN_DM_STATE": str(Path(self.tmp.name, "ban_dms.json")),
                    "BAN_APPEAL": "Appeal in #ban-appeals"}
        with patch.dict(os.environ, self.env):
            self.roles = BanRoles(bot)

    def names(self, member):
        return [r.name for r in member.roles]


class BanRoleTests(BanSetup):
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

    async def test_not_in_the_server(self):
        self.guild.members.pop(10)
        self.panel.add_ban(HAVOC, "Havoc", "a", "boss")
        await self.roles.tick()
        self.assertEqual(self.guild.roles, [])

    async def test_does_not_ask_discord_every_minute(self):
        self.panel.add_ban(HAVOC, "Havoc", "a", "boss")
        await self.roles.tick()
        fetched = self.guild.fetched
        await self.roles.tick()
        await self.roles.tick()
        self.assertEqual(self.guild.fetched, fetched)

    async def test_removed_after_a_restart(self):
        ban = self.panel.add_ban(HAVOC, "Havoc", "a", "boss", expires_at=int(time.time()) + 86400)
        await self.roles.tick()
        self.roles.applied.clear()
        self.panel.remove_ban(ban, "boss")
        await self.roles.tick()
        self.assertEqual(self.names(self.havoc), [])

    async def test_role_is_made_once(self):
        self.panel.add_ban(HAVOC, "Havoc", "a", "boss")
        self.panel.add_ban(ROOK, "Rook", "b", "boss")
        await self.roles.tick()
        await self.roles.tick()
        self.assertEqual([r.name for r in self.guild.roles], [PREFIX + "Permanent"])


class BanDmTests(BanSetup):
    def fields(self, embed):
        return {f.name: f.value for f in embed.fields}

    async def test_told_once_why_and_for_how_long(self):
        expires = int(time.time()) + 604800
        self.panel.add_ban(HAVOC, "Havoc", "Teamkilling at main", "boss", expires_at=expires)
        await self.roles.tick()
        await self.roles.tick()
        self.assertEqual(len(self.havoc.dms), 1)
        embed = self.havoc.dms[0]
        self.assertEqual(embed.title, "You've been banned from OYB")
        self.assertIn("for **7 days**", embed.description)
        self.assertEqual(self.fields(embed), {"Reason": "Teamkilling at main", "Ends": f"<t:{expires}:F> (<t:{expires}:R>)",
                                              "Appeal": "Appeal in #ban-appeals"})
        self.assertEqual(self.rook.dms, [])
        with patch.dict(os.environ, self.env):
            again = BanRoles(self.bot)
        await again.tick()
        self.assertEqual(len(self.havoc.dms), 1)

    async def test_old_bans_are_not_messaged(self):
        ban = self.panel.add_ban(HAVOC, "Havoc", "old", "boss")
        self.panel.write("UPDATE bans SET created_at = ? WHERE id = ?", int(time.time()) - 7200, ban)
        await self.roles.tick()
        self.assertEqual(self.havoc.dms, [])

    async def test_one_dm_for_several_accounts_without_naming_the_other(self):
        self.bot.account_links = Links({HAVOC: 10, ROOK: 10})
        self.panel.add_ban(HAVOC, "Havoc", "Cheating", "boss", expires_at=int(time.time()) + 86400)
        self.panel.add_ban(ROOK, "Rook", "Cheating (same IP as Havoc)", "boss")
        await self.roles.tick()
        self.assertEqual(len(self.havoc.dms), 1)
        self.assertIn("permanently", self.havoc.dms[0].description)
        self.assertEqual(self.fields(self.havoc.dms[0])["Reason"], "Cheating")
        self.assertEqual(self.fields(self.havoc.dms[0])["Ends"], "Never")

    async def test_closed_dms_are_not_retried(self):
        self.havoc.closed = True
        self.panel.add_ban(HAVOC, "Havoc", "a", "boss")
        await self.roles.tick()
        self.havoc.closed = False
        await self.roles.tick()
        self.assertEqual(self.havoc.dms, [])

    async def test_dms_without_manage_roles(self):
        self.guild.me.guild_permissions.manage_roles = False
        self.panel.add_ban(HAVOC, "Havoc", "a", "boss")
        await self.roles.tick()
        self.assertEqual(len(self.havoc.dms), 1)


if __name__ == "__main__":
    unittest.main()
