"""Per-match results board: window, rendering and posting rules."""
from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate, stamp
from bot.discord.match_results import LIMIT, MatchResults, duration, match_embed

OTHER = '99999999-9999-9999-9999-999999999999'
START = datetime(2026, 9, 14, 20, 0)
END = START + timedelta(minutes=95)


def rows(count):
    return [(f'Player {i}', count - i, i) for i in range(count)]


class RenderTests(unittest.TestCase):
    def test_duration_reads_in_hours_and_minutes(self):
        for minutes, text in [(0, '0m'), (7, '7m'), (60, '1h 0m'), (95, '1h 35m')]:
            self.assertEqual(duration(START, START + timedelta(minutes=minutes)), text)
        # A clock that goes backwards must not print a negative match length.
        self.assertEqual(duration(END, START), '0m')

    def test_embed_lists_players_and_the_match_window(self):
        embed = match_embed('OYB Classic', rows(3), START, END)
        self.assertIn('OYB Classic', embed.title)
        self.assertRegex(embed.description, r'Player 0\s+3\s+0')
        self.assertIn('3 players', embed.footer.text)
        self.assertIn('1h 35m', embed.footer.text)
        self.assertIn('20:00', embed.footer.text)

    def test_big_match_is_capped_and_says_so(self):
        embed = match_embed('OYB Classic', rows(LIMIT + 6), START, END)
        self.assertEqual(embed.description.count('\n'), LIMIT + 2)  # header + fence
        self.assertIn(f'top {LIMIT} of {LIMIT + 6}', embed.footer.text)

    def test_names_cannot_escape_the_table(self):
        embed = match_embed('OYB', [('```\n@everyone\r\n' + 'X' * 90, 1, 0)], START, END)
        self.assertEqual(embed.description.count('```'), 2)


class PublishTests(unittest.IsolatedAsyncioTestCase):
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
        self.channel.send = AsyncMock()
        self.channels = {555: self.channel}
        self.guild = SimpleNamespace(id=1, get_channel=self.channels.get,
                                     get_member=lambda _id: None)
        self.bot = SimpleNamespace(config=SimpleNamespace(guild_id=1), account_links=self.links,
                                   get_guild=lambda _id: self.guild)
        self.results = MatchResults(self.bot)

    def kill(self, tag, when, victim, killer):
        with self.links.db:
            self.links.db.execute(
                "INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation)"
                " VALUES ('s',?,?,?,?,'ENEMY')", (tag, stamp(when), victim, killer))

    async def publish(self, game_id='555'):
        with patch.dict(os.environ, {'GAME_LEADERBOARD_CHANNEL_ID': game_id}), \
             patch('bot.discord.match_results.SETTLE', 0):
            await self.results.publish('OYB Classic', START, END)

    async def test_posts_only_the_kills_inside_the_match_window(self):
        self.kill('before', START - timedelta(minutes=5), OTHER, self.identity)
        self.kill('during', START + timedelta(minutes=10), OTHER, self.identity)
        self.kill('after', END + timedelta(minutes=5), OTHER, self.identity)
        await self.publish()
        embed = self.channel.send.await_args.kwargs['embed']
        # One kill, not three: the earlier and later ones belong to other matches.
        self.assertRegex(embed.description, r'Test Player\s+1\s+0')

    async def test_quiet_match_posts_nothing(self):
        await self.publish()
        self.channel.send.assert_not_awaited()

    async def test_ai_and_suicide_deaths_do_not_make_a_board(self):
        with self.links.db:
            self.links.db.execute(
                "INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation)"
                " VALUES ('s','ai',?,?,NULL,'ENEMY')", (stamp(START + timedelta(minutes=1)), self.identity))
        self.kill('self', START + timedelta(minutes=2), self.identity, self.identity)
        await self.publish()
        self.channel.send.assert_not_awaited()

    async def test_the_match_channel_is_configured_without_a_dotenv_entry(self):
        from bot.config import MATCH_LEADERBOARD_CHANNEL, game_leaderboard_channel_id
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('GAME_LEADERBOARD_CHANNEL_ID', None)
            self.assertEqual(game_leaderboard_channel_id(), MATCH_LEADERBOARD_CHANNEL)
        with patch.dict(os.environ, {'GAME_LEADERBOARD_CHANNEL_ID': '42'}):
            self.assertEqual(game_leaderboard_channel_id(), 42)  # a guild can still override

    async def test_unusable_channel_is_reported_not_raised(self):
        self.kill('during', START + timedelta(minutes=10), OTHER, self.identity)
        self.guild.get_channel = lambda _id: None
        self.guild.fetch_channel = AsyncMock(side_effect=discord.HTTPException(MagicMock(), 'nope'))
        await self.publish()
        self.channel.send.assert_not_awaited()

    async def test_a_send_failure_never_escapes(self):
        self.kill('during', START + timedelta(minutes=10), OTHER, self.identity)
        self.channel.send.side_effect = discord.HTTPException(MagicMock(), 'boom')
        await self.publish()  # must not raise

    async def test_schedule_does_not_block_the_caller(self):
        self.kill('during', START + timedelta(minutes=10), OTHER, self.identity)
        with patch.dict(os.environ, {'GAME_LEADERBOARD_CHANNEL_ID': '555'}), \
             patch('bot.discord.match_results.SETTLE', 0):
            self.results.schedule('one', 'OYB Classic', START, END)
            self.assertEqual(len(self.results._tasks), 1)
            # The span is stored immediately, not after the settle delay.
            self.assertEqual(self.links.db.execute(
                'SELECT server,name FROM combat_matches').fetchone(), ('one', 'OYB Classic'))
            await self.results.close()  # cancels cleanly on shutdown
        self.assertFalse(self.results._tasks)


if __name__ == '__main__':
    unittest.main()
