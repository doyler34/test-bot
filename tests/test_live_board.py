"""The boards the live channel keeps: one message per server, A first, edited in place."""
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
from bot.discord.live_board import IDLE, LIVE, MARKER, LiveBoard, board_embeds, idle_embed, slot_server

OTHER = '99999999-9999-9999-9999-999999999999'
START = datetime(2026, 9, 14, 20, 0)


def rows(count):
    return [(f'Player {i}', count - i, i) for i in range(count)]


class RenderTests(unittest.TestCase):
    def test_a_running_match_is_marked_live(self):
        embed, = board_embeds('OYB Classic', rows(3), START)
        self.assertIn('LIVE', embed.title)
        self.assertIn('OYB Classic', embed.title)
        self.assertIn('updates every 30s', embed.footer.text)
        self.assertRegex(embed.description, r'Player 0\s+3\s+0')

    def test_the_board_carries_the_matchs_own_number(self):
        live, = board_embeds('OYB Classic', rows(3), START, tag='#0007B')
        self.assertIn('#0007B', live.title)
        self.assertIn('LIVE', live.title)
        self.assertIn('OYB Classic', live.title)

    def test_a_server_between_matches_says_so_and_names_its_letter(self):
        waiting = idle_embed('server-2', 'OYB Classic')
        self.assertIn('B — OYB Classic', waiting.title)
        self.assertNotIn('LIVE', waiting.title)
        self.assertIn('No game running', waiting.description)
        self.assertNotIn('Last game', waiting.description)
        after = idle_embed('server-2', 'OYB Classic', '#0007B • 12 players • 1h 35m')
        self.assertIn('Last game: #0007B • 12 players • 1h 35m', after.description)

    def test_a_running_board_is_green_and_a_waiting_one_is_grey(self):
        running, = board_embeds('OYB Classic', rows(3), START)
        waiting = idle_embed('server-1', 'OYB Classic')
        self.assertEqual(running.colour.value, LIVE)
        self.assertEqual(waiting.colour.value, IDLE)
        self.assertNotEqual(LIVE, IDLE)

    def test_a_full_server_still_fits_one_message(self):
        # Everyone who played is listed, and Discord allows 6000 characters
        # across one message's embeds. A full 128-slot server must clear it.
        embeds = board_embeds('OYB Classic', rows(128), START)
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
                           id=1234, edit=AsyncMock(), delete=AsyncMock())


class OwnershipTests(unittest.TestCase):
    def test_only_our_own_board_says_which_server_it_is(self):
        mine = message_with('🔴 LIVE — OYB Classic', f'{MARKER}:server-1\n2 players')
        self.assertEqual(slot_server(mine, 7), 'server-1')
        # Somebody else's message, a board from before servers had one each, and
        # an unrelated embed of ours must all be left alone.
        self.assertIsNone(slot_server(mine, 8))
        self.assertIsNone(slot_server(message_with('🔴 LIVE — OYB Classic', MARKER + '\n2 players'), 7))
        self.assertIsNone(slot_server(message_with('🔴 LIVE — OYB Classic', 'other'), 7))


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
        self.posted = []

        async def send(**kwargs):
            message = message_with(kwargs['embed'].title, kwargs['embed'].footer.text)
            self.posted.append((kwargs['embed'], message))
            return message
        self.channel.send = AsyncMock(side_effect=send)
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

    def live(self, server='s', name='OYB Classic'):
        self.board.setup([(server, name)])
        self.board.start(server, name, START)

    async def test_every_server_gets_its_board_up_front_and_a_goes_first(self):
        with self.enable():
            self.board.setup([('server-2', 'OYB Arland'), ('server-1', 'OYB Classic'), ('server-3', 'OYB Plus')])
            await self.board.tick()
        self.assertEqual([embed.title for embed, _ in self.posted],
                         ['⚪ A — OYB Classic', '⚪ B — OYB Arland', '⚪ C — OYB Plus'])

    async def test_nothing_goes_live_until_a_linked_player_takes_the_field(self):
        with self.enable():
            self.live()
            await self.board.tick()
        self.assertEqual(self.channel.send.await_count, 1)             # the waiting board
        self.posted[0][1].edit.assert_not_awaited()

    async def test_the_board_goes_live_then_edits_the_same_message(self):
        with self.enable():
            self.live()
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
            await self.board.tick()
            message = self.posted[0][1]
            self.assertEqual(self.channel.send.await_count, 1)
            self.assertRegex(message.edit.await_args.kwargs['embeds'][0].description, r'Test Player\s+1\s+0')
            # A second kill edits, never posts again.
            self.kill('two', START + timedelta(minutes=6), OTHER, self.identity)
            await self.board.tick()
        self.assertEqual(self.channel.send.await_count, 1)
        self.assertEqual(message.edit.await_count, 2)
        self.assertRegex(message.edit.await_args.kwargs['embeds'][0].description, r'Test Player\s+2\s+0')

    async def test_an_unchanged_board_is_not_edited(self):
        with self.enable():
            self.live()
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
            await self.board.tick()
            await self.board.tick()
        self.assertEqual(self.posted[0][1].edit.await_count, 1)

    async def test_a_quiet_player_who_only_took_the_field_is_listed(self):
        with self.links.db:
            record_presence(self.links.db, 's', stamp(START + timedelta(minutes=2)), self.identity)
        with self.enable():
            self.live()
            await self.board.tick()
        self.assertRegex(self.posted[0][1].edit.await_args.kwargs['embeds'][0].description,
                         r'Test Player\s+0\s+0')

    async def test_another_servers_kills_stay_off_this_board(self):
        with self.enable():
            self.live()
            self.kill('mine', START + timedelta(minutes=5), OTHER, self.identity)
            self.kill('elsewhere', START + timedelta(minutes=6), OTHER, self.identity, server='arland')
            await self.board.tick()
        self.assertRegex(self.posted[0][1].edit.await_args.kwargs['embeds'][0].description,
                         r'Test Player\s+1\s+0')

    async def test_when_the_match_ends_the_board_goes_back_to_waiting_and_names_the_game(self):
        with self.enable(), patch('bot.discord.live_board.SETTLE', 0):
            self.live()
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
            await self.board.tick()
            self.board.finish('s')
            await _drain(self.board)
        self.assertEqual(self.channel.send.await_count, 1)              # still one message
        embed = self.posted[0][1].edit.await_args.kwargs['embeds'][0]
        self.assertTrue(embed.title.startswith('⚪'))
        self.assertIn('Last game:', embed.description)
        self.assertIn('1 players', embed.description)
        self.assertNotIn('s', self.board.matches)
        # The next tick leaves it waiting, and a new match takes the same message over.
        edits = self.posted[0][1].edit.await_count
        with self.enable():
            await self.board.tick()
        self.assertEqual(self.posted[0][1].edit.await_count, edits)

    async def test_a_restart_takes_the_boards_back_over_when_they_are_all_there_in_order(self):
        old_a = message_with('⚪ A — OYB Classic', f'{MARKER}:server-1\nold')
        old_b = message_with('🔴 LIVE — OYB Arland', f'{MARKER}:server-2\nold')
        self.history = [old_b, old_a]                                 # newest first, as Discord gives them
        with self.enable():
            self.board.setup([('server-1', 'OYB Classic'), ('server-2', 'OYB Arland')])
            await self.board.tick()
        self.channel.send.assert_not_awaited()
        old_a.delete.assert_not_awaited()
        # A board left saying LIVE after the match ended is put back to waiting.
        old_b.edit.assert_awaited()
        self.assertTrue(old_b.edit.await_args.kwargs['embeds'][0].title.startswith('⚪'))

    async def test_boards_that_are_missing_or_out_of_order_are_replaced_in_order(self):
        only_b = message_with('⚪ B — OYB Arland', f'{MARKER}:server-2\nold')
        self.history = [only_b]
        with self.enable():
            self.board.setup([('server-1', 'OYB Classic'), ('server-2', 'OYB Arland')])
            await self.board.tick()
        only_b.delete.assert_awaited()
        self.assertEqual([embed.title for embed, _ in self.posted], ['⚪ A — OYB Classic', '⚪ B — OYB Arland'])

    async def test_boards_from_before_servers_had_one_each_are_left_where_they_are(self):
        old = message_with('🏁 Match results — OYB Classic', MARKER + '\n12 players')
        self.history = [old]
        with self.enable():
            self.board.setup([('server-1', 'OYB Classic')])
            await self.board.tick()
        old.delete.assert_not_awaited()
        old.edit.assert_not_awaited()
        self.assertEqual(self.channel.send.await_count, 1)

    async def test_a_board_someone_deleted_is_put_back(self):
        with self.enable():
            self.board.setup([('server-1', 'OYB Classic')])
            await self.board.tick()
            gone = self.posted[0][1]
            gone.edit.side_effect = discord.NotFound(MagicMock(status=404), 'gone')
            self.board.start('server-1', 'OYB Classic', START)
            self.kill('one', START + timedelta(minutes=5), OTHER, self.identity, server='server-1')
            await self.board.tick()                                   # the edit finds it gone
            await self.board.tick()                                   # so the boards are made again
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_the_board_stays_off_until_a_channel_is_set(self):
        self.board.setup([('s', 'OYB Classic')])
        self.board.start('s', 'OYB Classic', START)
        self.kill('one', START + timedelta(minutes=5), OTHER, self.identity)
        await self.board.tick()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.board.matches, {})


class NumberTests(unittest.TestCase):
    """Each match is named #0001 upwards on its own server, with the server's
    letter on the end, so two boards side by side can be told apart."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = AccountLinks(Path(self.tmp.name) / 'links.db')
        self.addCleanup(self.links.close)
        migrate(self.links.db)
        self.board = LiveBoard(SimpleNamespace(account_links=self.links))

    def tag(self, server_id, started):
        with patch.dict(os.environ, {'LIVE_BOARD_CHANNEL_ID': '555'}):
            self.board.start(server_id, 'OYB Classic', started)
        return self.board.matches[server_id]['tag']

    def test_each_match_on_a_server_takes_the_next_number(self):
        self.assertEqual(self.tag('server-1', START), '#0001A')
        self.assertEqual(self.tag('server-1', START + timedelta(hours=3)), '#0002A')
        self.assertEqual(self.tag('server-1', START + timedelta(hours=6)), '#0003A')

    def test_every_server_counts_on_its_own_and_keeps_its_letter(self):
        self.assertEqual(self.tag('server-1', START), '#0001A')
        self.assertEqual(self.tag('server-2', START), '#0001B')
        self.assertEqual(self.tag('server-3', START + timedelta(minutes=20)), '#0001C')
        self.assertEqual(self.tag('server-2', START + timedelta(hours=3)), '#0002B')

    def test_a_restart_carries_on_with_the_same_number(self):
        # Coming back up mid-match, the start is recovered from the log a few
        # seconds off; that is the same game, not the next one.
        self.assertEqual(self.tag('server-1', START), '#0001A')
        self.assertEqual(self.tag('server-1', START + timedelta(seconds=4)), '#0001A')

    def test_a_match_that_cannot_be_numbered_still_gets_a_board(self):
        self.links.db.execute('DROP TABLE combat_match_numbers')
        self.assertIsNone(self.tag('server-1', START))


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
