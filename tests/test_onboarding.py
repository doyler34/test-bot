"""The Start here panel: verifying, picking a side, and reporting progress."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot.storage.account_links import AccountLinks
from bot.discord.onboarding import MARKER, by_name, grantable, panel_embed, progress, verify

IDENTITY = '11111111-2222-3333-4444-555555555555'


def role(rid, name, managed=False, above=False):
    r = Mock(spec=discord.Role)
    r.id, r.name, r.managed = rid, name, managed
    r.__lt__ = Mock(return_value=not above)
    return r


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = AccountLinks(Path(self.tmp.name) / 'links.db')
        self.addCleanup(self.links.close)
        self.member_role = role(10, 'OYB Member')
        self.unverified = role(11, 'Unverified')
        self.us = role(12, 'US')
        self.member = SimpleNamespace(id=5, roles=[self.unverified],
                                      add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.guild = SimpleNamespace(
            id=1, roles=[self.member_role, self.unverified, self.us],
            me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True),
                               top_role=role(99, 'Bot', above=True)),
            get_role=lambda rid: next((r for r in [self.member_role, self.unverified, self.us]
                                       if r.id == rid), None))
        self.bot = SimpleNamespace(account_links=self.links,
                                   config=SimpleNamespace(guild_id=1),
                                   rank_sync=SimpleNamespace(status=lambda m: 'Rank: **OYB Recruit**'))

    async def test_accepting_the_rules_swaps_the_roles(self):
        self.assertEqual(await verify(self.bot, self.guild, self.member),
                         "You're in. Step 2 — pick your faction.")
        self.member.add_roles.assert_awaited_once()
        self.assertIs(self.member.add_roles.await_args.args[0], self.member_role)
        self.member.remove_roles.assert_awaited_once()
        self.assertIs(self.member.remove_roles.await_args.args[0], self.unverified)

    async def test_verifying_twice_changes_nothing(self):
        self.member.roles = [self.member_role]
        self.assertIn('already verified', await verify(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_a_missing_member_role_is_said_out_loud(self):
        self.guild.roles = [self.unverified]
        self.assertIn("isn't set up yet", await verify(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_a_role_above_the_bot_is_refused_rather_than_attempted(self):
        # Discord would reject the call anyway; saying so beats a stack trace.
        self.member_role.__lt__ = Mock(return_value=False)  # not below the bot's role
        self.assertIn('needs to sit above it', await verify(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_duplicate_role_names_are_never_guessed_between(self):
        self.guild.roles = [role(10, 'OYB Member'), role(20, 'OYB Member')]
        self.assertIsNone(by_name(self.guild, 'OYB Member'))
        self.assertIn("isn't set up yet", await verify(self.bot, self.guild, self.member))

    async def test_no_unverified_role_configured_is_fine(self):
        self.member.roles = []
        with patch.dict(os.environ, {'UNVERIFIED_ROLE_NAME': ''}):
            self.assertIn("You're in", await verify(self.bot, self.guild, self.member))
        self.member.add_roles.assert_awaited_once()
        self.member.remove_roles.assert_not_awaited()

    async def test_a_failed_unverified_removal_still_verifies(self):
        self.member.remove_roles.side_effect = discord.HTTPException(Mock(status=403), 'nope')
        self.assertIn("You're in", await verify(self.bot, self.guild, self.member))
        self.member.add_roles.assert_awaited_once()

    def test_progress_walks_the_three_steps(self):
        text = progress(self.bot, self.guild, self.member)
        self.assertIn('⬜ **1.**', text)
        self.assertIn('⬜ **2.**', text)
        self.assertIn('⬜ **3.**', text)
        self.assertIn('stay **OYB Renegade**', text)

    def test_progress_ticks_what_is_done(self):
        self.member.roles = [self.member_role, self.us]
        self.links.save_faction_role(1, 'US', self.us.id)
        self.links.verified_link(1, 5, IDENTITY, 'admin:9')
        text = progress(self.bot, self.guild, self.member)
        self.assertIn('✅ **1.**', text)
        self.assertIn('✅ **2.**', text)
        self.assertIn('**US**', text)
        self.assertIn('✅ **3.**', text)
        self.assertIn('All done', text)
        self.assertIn('OYB Recruit', text)
        self.assertNotIn('Renegade', text)

    def test_the_panel_carries_its_marker_and_the_three_steps(self):
        embed = panel_embed()
        self.assertEqual(embed.footer.text, MARKER)
        for step in ('**1. Accept the rules.**', '**2. Pick your faction**',
                     '**3. Link your Reforger account.**'):
            self.assertIn(step, embed.description)
        self.assertLessEqual(len(embed.description), 4096)

    def test_grantable_refuses_managed_and_missing_roles(self):
        self.assertTrue(grantable(self.guild, self.member_role))
        self.assertFalse(grantable(self.guild, None))
        self.assertFalse(grantable(self.guild, role(30, 'Booster', managed=True)))
        self.guild.me.guild_permissions.manage_roles = False
        self.assertFalse(grantable(self.guild, self.member_role))
