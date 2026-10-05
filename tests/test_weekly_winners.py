from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock
import uuid

import discord

from bot.discord.weekly_winners import WeeklyWinners
from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate, stamp, week_start
from bot.storage.notification_store import NotificationStore

OTHER = '99999999-9999-9999-9999-999999999999'


class Channel(discord.TextChannel):
    def __init__(self, id):
        self.id = id
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class Guild:
    def __init__(self, channel):
        self.id = 1
        self.channel = channel
        self.members = {}

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None

    def get_member(self, member_id):
        return self.members.get(member_id)


class WeeklyWinnersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / 'links.db')
        migrate(self.links.db)
        self.store = NotificationStore(Path(self.tmp.name) / 'notify.db')
        self.channel = Channel(500)
        self.guild = Guild(self.channel)
        self.bot = SimpleNamespace(config=SimpleNamespace(guild_id=1), account_links=self.links,
                                   store=self.store, get_guild=lambda guild_id: self.guild)
        self.winners = WeeklyWinners(self.bot)
        self.seq = 0
        self.monday = week_start()
        self.last_week = self.monday - timedelta(days=7)

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def player(self, member, name, kills, deaths=0, when=None):
        identity = str(uuid.UUID(int=member))
        token = self.links.submit(1, member, identity, name)
        self.links.review(1, token, 999, True)
        sql = ("INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation)"
               " VALUES ('s',?,?,?,?,'ENEMY')")
        moment = stamp((when or self.last_week) + timedelta(hours=20))
        with self.links.db:
            for victim, killer in [(OTHER, identity)] * kills + [(identity, OTHER)] * deaths:
                self.seq += 1
                self.links.db.execute(sql, (f'e{self.seq}', moment, victim, killer))

    async def tick(self, at):
        with mock.patch.dict('os.environ', {'WEEKLY_WINNERS_CHANNEL_ID': '500'}):
            await self.winners.tick(at)

    async def test_first_run_only_remembers_the_week(self):
        self.player(10, 'Hubcaps', 30)
        await self.tick(self.monday + timedelta(hours=1))
        self.assertEqual(self.channel.sent, [])

    async def test_posts_last_weeks_top_three_once(self):
        for member, name, kills in [(10, 'Hubcaps', 30), (11, 'Duxillec', 25), (12, 'Roastbeeff', 40),
                                    (13, 'Fourth', 5), (14, 'Nobody', 0)]:
            self.player(member, name, kills, deaths=3)
        self.player(15, 'ThisWeek', 99, when=self.monday)
        self.store.save_weekly_announced(1, self.last_week.date().isoformat())
        await self.tick(self.monday + timedelta(hours=1))
        await self.tick(self.monday + timedelta(hours=2))
        self.assertEqual(len(self.channel.sent), 1)
        text = self.channel.sent[0]['embed'].description
        self.assertEqual([line.split(' — ')[0] for line in text.splitlines() if line and line[0] in '🥇🥈🥉'],
                         ['🥇 Roastbeeff', '🥈 Hubcaps', '🥉 Duxillec'])
        self.assertNotIn('Fourth', text)
        self.assertNotIn('ThisWeek', text)
        self.assertIn(f'{self.last_week:%d %b}', self.channel.sent[0]['embed'].title)

    async def test_a_quiet_week_posts_nothing(self):
        self.store.save_weekly_announced(1, self.last_week.date().isoformat())
        await self.tick(self.monday + timedelta(hours=1))
        self.assertEqual(self.channel.sent, [])
        self.assertEqual(self.store.weekly_announced(1), self.monday.date().isoformat())


if __name__ == '__main__':
    unittest.main()
