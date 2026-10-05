"""When the weekly leaderboard resets, post last week's top 3."""
import asyncio
import logging
from datetime import timedelta

import discord

from bot.config import leaderboard_channel_id, weekly_winners_channel_id
from bot.storage.combat_store import week_start, window_standings

LOG = logging.getLogger('reforger.weekly_winners')
POLL = 60
MEDALS = ('🥇', '🥈', '🥉')


def winners_embed(rows, guild, start):
    lines = []
    for medal, (member_id, kills, deaths, name) in zip(MEDALS, rows):
        member = guild.get_member(member_id)
        who = member.mention if member else (name or f'Member {member_id}')
        lines.append(f'{medal} {who} — **{kills}** kills, {deaths} deaths')
    embed = discord.Embed(title=f'🏆 Top 3 for the week of {start:%d %b}', colour=0xD9A441,
                          description='The weekly leaderboard has reset. Last week\'s best:\n\n'
                                      + '\n'.join(lines) + '\n\nNew week, clean slate. Good luck.')
    return embed


class WeeklyWinners:
    def __init__(self, bot):
        self.bot = bot

    def channel(self, guild):
        for channel_id in (weekly_winners_channel_id(), leaderboard_channel_id(),
                           self.bot.store.leaderboard(guild.id)['channel']):
            channel = guild.get_channel(channel_id) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                return channel
        return None

    async def tick(self, now=None):
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        start = week_start(now)
        week = start.date().isoformat()
        last = self.bot.store.weekly_announced(guild.id)
        if last is None:
            # First run: start counting from here rather than announcing a week nobody expected.
            self.bot.store.save_weekly_announced(guild.id, week)
            return
        if last >= week:
            return
        previous = start - timedelta(days=7)
        rows = [r for r in window_standings(self.bot.account_links.db, guild.id, previous, start) if r[1] > 0][:3]
        if rows:
            channel = self.channel(guild)
            if channel is None:
                LOG.warning('No channel for the weekly top 3; set WEEKLY_WINNERS_CHANNEL_ID')
                return
            await channel.send(embed=winners_embed(rows, guild, previous),
                               allowed_mentions=discord.AllowedMentions.none())
            LOG.info('Posted the weekly top 3 for %s in %s', previous.date(), channel.id)
        self.bot.store.save_weekly_announced(guild.id, week)

    async def run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                LOG.exception('Weekly top 3 failed; trying again shortly')
            await asyncio.sleep(POLL)
