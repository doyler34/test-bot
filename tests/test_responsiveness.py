"""The bot has to answer a click in three seconds and must not burn the loop.

These pin the three things that made the admin panel time out: a query with no
usable index, recomputing all-time combat XP on every rank tick, and buttons
that did their work before acknowledging the click.
"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import uuid

from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate, record, record_match, stamp
from bot.storage.rank_persistence import XPStore
from bot.tracking.combat_parser import KillEvent
from bot.discord.link_review import AdminPanelView


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = AccountLinks(Path(self.tmp.name) / 'a.db')
        self.addCleanup(self.links.close)
        migrate(self.links.db)

    def test_a_match_span_is_found_by_index_not_by_scanning(self):
        # Without (server, occurred) the planner narrows on the primary key and
        # then reads every kill on that server, once per match, per member.
        plan = ' '.join(str(row) for row in self.links.db.execute(
            'EXPLAIN QUERY PLAN SELECT COUNT(*) FROM combat_matches m WHERE EXISTS ('
            ' SELECT 1 FROM combat_events e WHERE e.server=m.server AND e.occurred>=m.started'
            ' AND e.occurred<=m.ended AND (e.killer=? OR e.victim=?)'
            ' AND killer IS NOT NULL AND killer<>victim)', ('a', 'a')))
        # Which index it picks depends on the data; that it seeks at all does not.
        self.assertNotIn('SCAN e', plan, plan)
        self.assertIn('SEARCH e USING INDEX', plan, plan)
        self.assertNotIn('sqlite_autoindex_combat_events', plan, plan)

    def test_the_span_index_exists(self):
        names = {r[0] for r in self.links.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn('combat_events_span', names)


class CombatCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = AccountLinks(Path(self.tmp.name) / 'a.db')
        self.addCleanup(self.links.close)
        migrate(self.links.db)
        self.wallet = XPStore(self.links.db)
        self.me = str(uuid.uuid4())
        self.foe = str(uuid.uuid4())
        self.base = datetime(2026, 9, 21, 20, 0)

    def kill(self, minute):
        with self.links.db:
            record(self.links.db, 's', stamp(self.base + timedelta(minutes=minute)),
                   KillEvent('20:00:00.000', 'ENEMY', self.foe, self.me))

    def test_the_answer_is_reused_until_the_history_moves(self):
        record_match(self.links.db, 's', 'OYB', self.base, self.base + timedelta(hours=1))
        self.kill(1)
        first = self.wallet.combat_xp(self.me)
        self.assertGreater(first, 0)
        # Same generation: served from the cache, not recomputed.
        self.assertEqual(self.wallet.combat_xp(self.me), first)
        self.assertIn(self.me, self.wallet._combat)
        # A new kill moves the generation and the cache is dropped.
        self.kill(2)
        self.assertGreater(self.wallet.combat_xp(self.me), first)

    def test_a_finished_match_invalidates_even_with_no_new_kill(self):
        self.kill(1)
        record_match(self.links.db, 's', 'OYB', self.base, self.base + timedelta(hours=1))
        first = self.wallet.combat_xp(self.me)
        record_match(self.links.db, 's2', 'Arland', self.base, self.base + timedelta(hours=1))
        self.assertNotEqual(self.wallet.combat_generation(), self.wallet._combat_at)

    def test_caching_never_changes_the_number(self):
        record_match(self.links.db, 's', 'OYB', self.base, self.base + timedelta(hours=1))
        for minute in range(1, 6):
            self.kill(minute)
            from bot.storage.combat_store import combat_xp as live
            self.assertEqual(self.wallet.combat_xp(self.me), live(self.links.db, self.me))


def clicked():
    """A component interaction that behaves like Discord's: once deferred, the
    reply has to go through followup."""
    state = {'done': False}

    async def defer(**_):
        state['done'] = True

    return SimpleNamespace(
        guild_id=1, user=SimpleNamespace(id=9),
        permissions=__import__('discord').Permissions(manage_guild=True),
        followup=SimpleNamespace(send=AsyncMock()),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(side_effect=defer),
                                 is_done=lambda: state['done']))


class AcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = AccountLinks(Path(self.tmp.name) / 'a.db')
        self.addCleanup(self.links.close)
        self.bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1))
        self.view = AdminPanelView(self.bot)

    async def test_the_queue_button_acknowledges_before_it_reads(self):
        i = clicked()
        await self.view.pending.callback(i)
        i.response.defer.assert_awaited_once()
        i.followup.send.assert_awaited_once()
        i.response.send_message.assert_not_awaited()

    async def test_the_unlink_button_acknowledges_before_it_reads(self):
        i = clicked()
        await self.view.unlink.callback(i)
        i.response.defer.assert_awaited_once()
        i.followup.send.assert_awaited_once()

    async def test_a_refusal_also_arrives_through_the_followup(self):
        import discord
        i = clicked()
        i.permissions = discord.Permissions.none()
        await self.view.pending.callback(i)
        i.response.defer.assert_awaited_once()
        self.assertIn('Only staff', i.followup.send.await_args.args[0])

    async def test_force_link_never_defers_because_a_modal_cannot_follow_one(self):
        i = clicked()
        i.response.send_modal = AsyncMock()
        await self.view.force.callback(i)
        i.response.defer.assert_not_awaited()
        i.response.send_modal.assert_awaited_once()
