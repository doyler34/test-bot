"""The Start here panel: verifying, picking a side, and reporting progress."""
import os
import sqlite3
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot.storage.account_links import AccountLinks
from bot.discord.onboarding import (MARKER, NO_FACTION, by_name, ensure_member_role,
                                    grantable, panel_embed, progress, verify)

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

    def test_progress_starts_with_both_steps_open(self):
        text = progress(self.bot, self.guild, self.member)
        self.assertIn('⬜ **1.** Reforger account', text)
        self.assertIn('⬜ **2.** Side', text)
        self.assertIn('stay **OYB Renegade**', text)

    def test_progress_ticks_what_is_done(self):
        self.member.roles = [self.member_role, self.us]
        self.links.save_faction_role(1, 'US', self.us.id)
        self.links.verified_link(1, 5, IDENTITY, 'auto:tracker')
        text = progress(self.bot, self.guild, self.member)
        self.assertIn('✅ **1.** Reforger account linked (1)', text)
        self.assertIn('✅ **2.** Side — **US**', text)
        self.assertIn('You are in', text)
        self.assertIn('OYB Recruit', text)
        self.assertNotIn('Renegade', text)

    def test_choosing_no_side_counts_as_done(self):
        # Staying Renegade on purpose is a decision, not an unfinished step.
        self.links.verified_link(1, 5, IDENTITY, 'auto:tracker')
        self.links.set_faction(1, 5, NO_FACTION)
        text = progress(self.bot, self.guild, self.member)
        self.assertIn('✅ **2.** Side — none, by choice', text)
        self.assertIn('stay **OYB Renegade**', text)

    def test_a_link_waiting_on_an_admin_says_so(self):
        self.links.verified_link(1, 5, IDENTITY, 'admin:9')
        self.member.roles = []          # no member role yet
        text = progress(self.bot, self.guild, self.member)
        self.assertIn('waiting on an admin', text)

    def test_the_panel_leads_with_the_link_because_that_is_the_gate(self):
        embed = panel_embed()
        self.assertEqual(embed.footer.text, MARKER)
        for step in ('**1 — Link your Reforger account**', '**2 — Pick your side (optional)**',
                     '**No faction**'):
            self.assertIn(step, embed.description)
        self.assertIn('opens up the rest of the server', embed.description)
        # Picking a side here has never changed which faction you play in game,
        # and people kept reading it as if it did.
        self.assertIn('DOES NOT LOCK YOUR FACTION IN GAME', embed.description)
        self.assertLessEqual(len(embed.description), 4096)

    async def test_the_member_role_is_created_when_it_is_missing(self):
        # A new role lands below the bot's own, so it is grantable straight away.
        made = role(40, 'OYB Member')
        self.guild.roles = [self.unverified]
        self.guild.create_role = AsyncMock(return_value=made)
        self.assertIs(await ensure_member_role(self.guild), made)
        self.assertEqual(self.guild.create_role.await_args.kwargs['name'], 'OYB Member')
        self.assertEqual(self.guild.create_role.await_args.kwargs['permissions'],
                         discord.Permissions.none())

    async def test_an_existing_member_role_is_adopted_not_duplicated(self):
        self.guild.create_role = AsyncMock()
        self.assertIs(await ensure_member_role(self.guild), self.member_role)
        self.guild.create_role.assert_not_awaited()

    async def test_duplicates_are_left_alone_rather_than_made_worse(self):
        self.guild.roles = [role(10, 'OYB Member'), role(20, 'OYB Member')]
        self.guild.create_role = AsyncMock()
        self.assertIsNone(await ensure_member_role(self.guild))
        self.guild.create_role.assert_not_awaited()

    async def test_no_manage_roles_means_no_role_and_no_crash(self):
        self.guild.roles = []
        self.guild.me.guild_permissions.manage_roles = False
        self.guild.create_role = AsyncMock()
        self.assertIsNone(await ensure_member_role(self.guild))
        self.guild.create_role.assert_not_awaited()

    def test_the_wording_follows_the_panel_when_there_is_one(self):
        from bot.config import linking_channel
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ONBOARDING_CHANNEL_ID', None)
            self.assertEqual(linking_channel(), '**#join-oyb**')
        with patch.dict(os.environ, {'ONBOARDING_CHANNEL_ID': '777'}):
            # A mention, so it stays a working link wherever it is quoted.
            self.assertEqual(linking_channel(), '<#777>')

    def test_an_auto_link_still_shows_a_name_on_the_boards(self):
        # Boards read a player's name from their approved request, so linking
        # without writing one would print "Member 5" on every leaderboard.
        from bot.storage.combat_store import migrate, NAME
        migrate(self.links.db)
        self.links.auto_link(1, 5, IDENTITY, 'Test Player', 'TestDiscord')
        self.assertEqual(self.links.identities(1, 5), [IDENTITY])
        row = self.links.db.execute(
            f'SELECT {NAME} FROM account_links a WHERE a.guild=1').fetchone()
        self.assertEqual(row[0], 'Test Player')

    def test_an_auto_link_refuses_an_account_someone_else_holds(self):
        from bot.storage.account_links import LinkConflict
        self.links.auto_link(1, 5, IDENTITY, 'Test Player')
        with self.assertRaises(LinkConflict):
            self.links.auto_link(1, 6, IDENTITY, 'Impostor')
        self.assertEqual(self.links.owner(1, IDENTITY), 5)

    async def test_an_arrival_is_labelled_unverified(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = False
        self.member.roles = []
        self.assertTrue(await mark_unverified(self.bot, self.guild, self.member))
        self.assertIs(self.member.add_roles.await_args.args[0], self.unverified)

    async def test_somebody_already_linked_is_not_labelled(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = False
        self.member.roles = [self.member_role]
        self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_bots_and_repeat_labels_are_left_alone(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = True
        self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.bot = False
        self.member.roles = [self.unverified]        # already carries it
        self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_no_label_role_configured_is_not_an_error(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = False
        self.member.roles = []
        with patch.dict(os.environ, {'UNVERIFIED_ROLE_NAME': ''}):
            self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_an_arrival_is_labelled_unverified(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = False
        self.member.roles = []
        self.assertTrue(await mark_unverified(self.bot, self.guild, self.member))
        self.assertIs(self.member.add_roles.await_args.args[0], self.unverified)

    async def test_somebody_already_linked_is_not_labelled(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = False
        self.member.roles = [self.member_role]
        self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_bots_and_repeat_labels_are_left_alone(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = True
        self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.bot = False
        self.member.roles = [self.unverified]        # already carries it
        self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    async def test_no_label_role_configured_is_not_an_error(self):
        from bot.discord.onboarding import mark_unverified
        self.member.bot = False
        self.member.roles = []
        with patch.dict(os.environ, {'UNVERIFIED_ROLE_NAME': ''}):
            self.assertFalse(await mark_unverified(self.bot, self.guild, self.member))
        self.member.add_roles.assert_not_awaited()

    def test_grantable_refuses_managed_and_missing_roles(self):
        self.assertTrue(grantable(self.guild, self.member_role))
        self.assertFalse(grantable(self.guild, None))
        self.assertFalse(grantable(self.guild, role(30, 'Booster', managed=True)))
        self.guild.me.guild_permissions.manage_roles = False
        self.assertFalse(grantable(self.guild, self.member_role))


class ReviewerFallbackTests(unittest.TestCase):
    """A name the tracker cannot place goes to a reviewer, not a dead end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'links.db'
        self.links = AccountLinks(self.path)
        self.addCleanup(self.links.close)

    def test_a_request_can_wait_without_a_matched_account(self):
        token = self.links.submit_unresolved(1, 5, 'NeverPlayed', 'SomeNick')
        discord_id, identity, name, status, discord_name = self.links.request(1, token)
        self.assertEqual((discord_id, identity, name, status), (5, None, 'NeverPlayed', 'pending'))
        self.assertEqual(discord_name, 'SomeNick')
        self.assertEqual([r[0] for r in self.links.pending(1)], [token])

    def test_approving_one_with_nothing_matched_says_to_force_link(self):
        from bot.storage.account_links import LinkConflict
        token = self.links.submit_unresolved(1, 5, 'NeverPlayed')
        with self.assertRaises(LinkConflict) as caught:
            self.links.review(1, token, 99, True)
        self.assertIn('Force-link', str(caught.exception))
        # Still pending, so the reviewer has not lost it.
        self.assertEqual(self.links.request(1, token)[3], 'pending')

    def test_rejecting_one_closes_it(self):
        token = self.links.submit_unresolved(1, 5, 'NeverPlayed')
        self.links.review(1, token, 99, False)
        self.assertEqual(self.links.request(1, token)[3], 'rejected')

    def test_force_linking_closes_whatever_they_asked_for(self):
        token = self.links.submit_unresolved(1, 5, 'NeverPlayed')
        self.links.verified_link(1, 5, IDENTITY, 'admin:99')
        self.links.close_requests(1, 5)
        self.assertEqual(self.links.request(1, token)[3], 'approved')
        self.assertEqual(self.links.pending(1), [])

    def test_an_old_database_is_rebuilt_to_allow_it(self):
        # The live database predates this and declared identity NOT NULL, so
        # the migration has to run against that exact shape.
        self.links.close()
        old = Path(self.tmp.name) / 'old.db'
        db = sqlite3.connect(old)
        db.executescript('''CREATE TABLE link_requests (
            token TEXT PRIMARY KEY, guild INTEGER NOT NULL, discord_id INTEGER NOT NULL,
            identity TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, reviewer INTEGER);''')
        db.execute("INSERT INTO link_requests VALUES ('t',1,5,?,'Old','approved',0,9)", (IDENTITY,))
        db.commit(); db.close()
        links = AccountLinks(old)
        self.addCleanup(links.close)
        # The existing row survived...
        self.assertEqual(links.request(1, 't')[1], IDENTITY)
        # ...and an unmatched one can now be stored.
        token = links.submit_unresolved(1, 6, 'NeverPlayed')
        self.assertIsNone(links.request(1, token)[1])
