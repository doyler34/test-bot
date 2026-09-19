"""The self-updating board a running match posts: rendering, edits and handover."""
from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate, record_presence, stamp
from bot.discord.live_board import MARKER, LiveBoard, board_embeds, ours

OTHER = '99999999-9999-9999-9999-999999999999'
START = datetime(2026, 9, 14, 20, 0)
END = START + timedelta(minutes=95)


def rows(count):
    return [(f'Player {i}', count - i, i) for i in range(count)]


class RenderTests(unittest.TestCase):
    def test_a_running_match_is_marked_live(self):
        embed, = board_embeds('OYB Classic', rows(3), START)
        self.assertIn('LIVE', embed.title)
        self.assertIn('OYB Classic', embed.title)
        self.assertIn('updates every 30s', embed.footer.text)
        self.assertRegex(embed.description, r'Player 0\s+3\s+0')

    def test_a_finished_match_reads_as_the_result(self):
        embed, = board_embeds('OYB Classic', rows(3), START, END)
        self.assertIn('Match results', embed.title)
        self.assertNotIn('LIVE', embed.title)
        self.assertIn('1h 35m', embed.footer.text)
        self.assertIn('20:00', embed.footer.text)
        self.assertIn('21:35', embed.footer.text)

    def test_a_full_server_still_fits_one_message(self):
        # Everyone who played is listed, and Discord allows 6000 characters
        # across one message's embeds. A full 128-slot server must clear it.
        embeds = board_embeds('OYB Classic', rows(128), START, END)
        listed = sum(len(e.description.splitlines()) - 3 for e in embeds)
        self.assertEqual(listed, 128)
        self.assertLessEqual(sum(len(e.description) for e in embeds), 6000)
        self.assertIn('128 players', embeds[-1].footer.text)

    def test_an_empty_board_still_renders(self):
        embed, = board_embeds('OYB Classic', [], START)
        self.assertIn('No linked players yet', embed.description)
        self.assertIn('0 players', embed.footer.text)

    def test_names_cannot_escape_the_table(self):
        embed, = board_embeds('OYB', [('```\n@everyone\r\n' + 'X' * 90, 1, 0)], START)
        self.assertEqual(embed.description.count('```'), 2)


def message_with(title, footer, author_id=7):
    embed = discord.Embed(title=title, description='x')
    embed.set_footer(text=footer)
    return SimpleNamespace(author=SimpleNamespace(id=author_id), embeds=[embed],
                           id=1234, edit=AsyncMock())


class OwnershipTests(unittest.TestCase):
    def test_only_our_own_board_for_that_server_is_adopted(self):
        mine = message_with('🔴 LIVE — OYB Classic', MARKER + '\n2 players')
        self.assertTrue(ours(mine, 7, 'OYB Classic'))
        # Another server's board, somebody else's message, and an unrelated
        # embed of ours must all be left alone.
        self.assertFalse(ours(mine, 7, 'OYB Arland'))
        self.assertFalse(ours(mine, 8, 'OYB Classic'))
        self.assertFalse(ours(message_with('🔴 LIVE — OYB Classic', 'other'), 7, 'OYB Classic'))


class TickTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = AccountLinks(Path(self.tmp.name) / 'links.db')
        self.addCleanup(self.links.close)
        migrate(self.links.db)
        self.identity = '11111111-2222-3333-4444-555555555555'
        token = self.links.submit(1, 10, self.identity, 'Test Player')
        self.links.review(1, token, 99, True)
        self.channel = MagicMock(spec=discord.TextChannel)
        self.sent = message_with('🔴 LIVE — OYB Classic', MARKER)
        self.channel.send = AsyncMock(return_value=self.sent)
        self.history = []
        self.channel.history = lambda limit=None: _aiter(self.history)
        self.guild = SimpleNamespace(id=1, get_channel={555: self.channel}.get,
                                     get_member=lambda _id: None)
        self.bot = SimpleNamespace(config=SimpleNamespace(guild_id=1), account_links=self.links,
                                   get_guild=lambda _id: self.guild, user=SimpleNamespace(id=7))
        self.board = LiveBoard(self.bot)

    def kill(self, tag, when, victim, killer, server='s'):
        with self.links.db:
            self.links.db.execute(
                "INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation)"
                " VALUES (?,?,?,?,?,'ENEMY')", (server, tag, stamp(when), victim, killer))

    def enable(self, channel_id='555'):
        return patch.dict(os.environ, {'LIVE_BOARD_CHANNEL_ID': channel_id})

    async def test_nothing_is_posted_until_a_linked_player_takes_the_field(self):
        with self.enable():
            self.board.start('s', 'OYB Classic', START)
            await self.board.tick()
        self.channel.send.assert_not_awaited()

    async def test_the_board_opens_then_edits_the_same_message(self):
        with self.enable():
            self.board.start('s', 'OYB Classic', START)
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
            await self.board.tick()
            self.assertEqual(self.channel.send.await_count, 1)
            embed = self.channel.send.await_args.kwargs['embeds'][0]
            self.assertRegex(embed.description, r'Test Player\s+1\s+0')
            # A second kill edits, never posts again.
            self.kill('two', START + timedelta(minutes=6), OTHER, self.identity)
            await self.board.tick()
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertEqual(self.sent.edit.await_count, 1)
        self.assertRegex(self.sent.edit.await_args.kwargs['embeds'][0].description,
                         r'Test Player\s+2\s+0')

    async def test_an_unchanged_board_is_not_edited(self):
        with self.enable():
            self.board.start('s', 'OYB Classic', START)
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
            await self.board.tick()
            await self.board.tick()
        self.sent.edit.assert_not_awaited()

    async def test_a_quiet_player_who_only_took_the_field_is_listed(self):
        with self.links.db:
            record_presence(self.links.db, 's', stamp(START + timedelta(minutes=2)), self.identity)
        with self.enable():
            self.board.start('s', 'OYB Classic', START)
            await self.board.tick()
        self.assertRegex(self.channel.send.await_args.kwargs['embeds'][0].description,
                         r'Test Player\s+0\s+0')

    async def test_another_servers_kills_stay_off_this_board(self):
        with self.enable():
            self.board.start('s', 'OYB Classic', START)
            self.kill('mine', START + timedelta(minutes=5), OTHER, self.identity)
            self.kill('elsewhere', START + timedelta(minutes=6), OTHER, self.identity, server='arland')
            await self.board.tick()
        self.assertRegex(self.channel.send.await_args.kwargs['embeds'][0].description,
                         r'Test Player\s+1\s+0')

    async def test_the_live_message_becomes_the_final_board(self):
        with self.enable(), patch('bot.discord.live_board.SETTLE', 0):
            self.board.start('s', 'OYB Classic', START)
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
            await self.board.tick()
            self.board.finish('s')
            await _drain(self.board)
        self.assertEqual(self.channel.send.await_count, 1)  # still one message
        embed = self.sent.edit.await_args.kwargs['embeds'][0]
        self.assertIn('Match results', embed.title)
        self.assertNotIn('s', self.board.matches)

    async def test_a_restart_takes_the_old_board_back_over(self):
        self.history = [message_with('🔴 LIVE — OYB Classic', MARKER + '\nold')]
        with self.enable():
            self.board.start('s', 'OYB Classic', START)
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
            await self.board.tick()
        # The board left behind is edited, not abandoned with a second posted.
        self.channel.send.assert_not_awaited()
        self.history[0].edit.assert_awaited()

    async def test_the_board_stays_off_until_a_channel_is_set(self):
        self.board.start('s', 'OYB Classic', START)
        self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
        await self.board.tick()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.board.matches, {})


async def _drain(board):
    import asyncio
    await asyncio.gather(*list(board._tasks), return_exceptions=True)


class _aiter:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)
