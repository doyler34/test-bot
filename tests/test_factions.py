import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.storage.account_links import AccountLinks
from bot.discord.factions import (FACTIONS, FactionView, apply_faction,
                                   ensure_faction_roles)


class Role:
    def __init__(self, id, name):
        self.id, self.name = id, name


class StorageTests(unittest.TestCase):
    def test_faction_choice_and_role_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            links = AccountLinks(Path(tmp) / "l.db")
            try:
                self.assertIsNone(links.faction(1, 10))
                links.set_faction(1, 10, "US")
                self.assertEqual(links.faction(1, 10), "US")
                links.set_faction(1, 10, "USSR")  # switching overwrites
                self.assertEqual(links.faction(1, 10), "USSR")
                links.save_faction_role(1, "US", 555)
                self.assertEqual(links.faction_role(1, "US"), 555)
            finally:
                links.close()


class ApplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / "l.db")
        self.roles = {}
        for rid, (name, _, _) in zip(range(100, 103), FACTIONS):
            self.links.save_faction_role(1, name, rid)
            self.roles[rid] = Role(rid, name)
        self.bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1))
        self.guild = SimpleNamespace(id=1, get_role=lambda i: self.roles.get(i))

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    async def test_pick_assigns_role_and_saves_choice(self):
        member = SimpleNamespace(id=10, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        await apply_faction(self.bot, self.guild, member, "US")
        member.add_roles.assert_awaited_once()
        member.remove_roles.assert_not_awaited()
        self.assertEqual(self.links.faction(1, 10), "US")

    async def test_switch_removes_the_old_faction_role(self):
        us = self.roles[100]
        member = SimpleNamespace(id=10, roles=[us], add_roles=AsyncMock(), remove_roles=AsyncMock())
        await apply_faction(self.bot, self.guild, member, "USSR")
        member.remove_roles.assert_awaited_once()
        member.add_roles.assert_awaited_once()
        self.assertEqual(self.links.faction(1, 10), "USSR")

    async def test_ensure_creates_the_three_roles_when_missing(self):
        created = []
        async def create_role(**kwargs):
            role = Role(200 + len(created), kwargs["name"])
            created.append(role)
            return role
        with tempfile.TemporaryDirectory() as tmp:
            links = AccountLinks(Path(tmp) / "fresh.db")
            bot = SimpleNamespace(account_links=links, config=SimpleNamespace(guild_id=1))
            guild = SimpleNamespace(id=1, roles=[], me=SimpleNamespace(top_role=None),
                                    get_role=lambda i: None, create_role=create_role)
            try:
                await ensure_faction_roles(bot, guild)
                self.assertEqual([r.name for r in created], ["US", "USSR", "FIA"])
                self.assertEqual(links.faction_role(1, "US"), created[0].id)
            finally:
                links.close()

    async def test_view_button_applies_faction(self):
        view = FactionView(self.bot)
        self.assertEqual(len(view.children), 3)
        interaction = SimpleNamespace(guild_id=1, guild=self.guild, user=SimpleNamespace(id=10),
                                      response=SimpleNamespace(send_message=AsyncMock()))
        with patch("bot.discord.factions.apply_faction", new=AsyncMock()) as applied:
            await view.children[0].callback(interaction)
        applied.assert_awaited_once()
        self.assertEqual(applied.await_args.args[3], "US")  # first button = US
        interaction.response.send_message.assert_awaited()


if __name__ == "__main__":
    unittest.main()
