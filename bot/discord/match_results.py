"""Post one results board per finished match to a pinned channel."""
import asyncio
import logging

import discord

from bot.config import game_leaderboard_channel_id, live_board_channel_id
from bot.discord.leaderboard_command import table
from bot.storage.combat_store import record_match, window_standings

LOG = logging.getLogger('reforger.match')
# The log ingestor polls every 15s, so wait a few cycles for the closing kills
# to land before counting the match.
SETTLE = 45
# A Discord embed description tops out at 4096 characters; a full 128-slot
# match is past that, so the board runs on across as many embeds as it needs.
CHUNK = 3800


def duration(start, end):
    minutes = max(0, int((end - start).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes}m' if hours else f'{minutes}m'


def pages(rows):
    """Split the board so each block fits one embed. Everyone who fought is
    listed - an end-of-game board that stops at the top 25 is half a board."""
    result, start = [], 0
    while start < len(rows):
        size = len(rows) - start
        while size > 1 and len(table(rows[start:start + size], start)) > CHUNK:
            size -= 1
        result.append(table(rows[start:start + size], start))
        start += size
    return result


def match_embeds(server_name, rows, start, end):
    blocks = pages(rows)
    closed = f'{end:%H:%M}' if start.date() == end.date() else f'{end:%d %b %H:%M}'
    embeds = []
    for index, block in enumerate(blocks):
        embed = discord.Embed(colour=0xA9BC8C, description=block)
        if index == 0:
            embed.title = f'🏁 Match results — {server_name}'[:256]
        if index == len(blocks) - 1:
            embed.set_footer(text=f'{len(rows)} players • {duration(start, end)} • '
                                  f'{start:%d %b %H:%M}–{closed}\n'
                                  'Player kills only • Counts toward the weekly board')
        embeds.append(embed)
    return embeds


class MatchResults:
    def __init__(self, bot):
        self.bot = bot
        self._tasks = set()

    def schedule(self, server_id, server_name, start, end):
        """Called from the match-end callback; never blocks it. The match span is
        stored straight away so /stats can break it out even if posting fails."""
        try:
            record_match(self.bot.account_links.db, server_id, server_name, start, end)
        except Exception:
            LOG.exception('Could not record the %s match span', server_name)
        task = asyncio.create_task(self.publish(server_id, server_name, start, end))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def rows(self, guild, start, end, server_id):
        result = []
        for member_id, kills, deaths, name in window_standings(
                self.bot.account_links.db, guild.id, start, end, server=server_id):
            member = guild.get_member(member_id)
            result.append((name or (member.display_name if member else f'Member {member_id}'), kills, deaths))
        return result

    async def channel(self, guild):
        pinned = game_leaderboard_channel_id()
        if not pinned:
            return None
        channel = guild.get_channel(pinned)
        if not isinstance(channel, discord.TextChannel):
            try:
                channel = await guild.fetch_channel(pinned)
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            LOG.warning('Match results channel %s is not a text channel I can see', pinned)
            return None
        return channel

    async def publish(self, server_id, server_name, start, end):
        try:
            await asyncio.sleep(SETTLE)
            guild = self.bot.get_guild(self.bot.config.guild_id)
            if guild is None:
                return
            channel = await self.channel(guild)
            if channel is None:
                return
            if live_board_channel_id() == channel.id:
                # The live board already stands in this channel as the final
                # result; a second post would just repeat it.
                LOG.info('Live board owns %s; skipping the results post', channel.id)
                return
            rows = self.rows(guild, start, end, server_id)
            if not rows:
                # An empty board after every quiet match would just be noise.
                LOG.info('No linked-player kills in the %s match; nothing to post', server_name)
                return
            # One message per block: several embeds in a single message share a
            # 6000-character budget that a full server would blow through.
            for embed in match_embeds(server_name, rows, start, end):
                await channel.send(embed=embed, silent=True,
                                   allowed_mentions=discord.AllowedMentions.none())
            LOG.info('Posted match results for %s (%s players)', server_name, len(rows))
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Could not post match results for %s', server_name)

    async def close(self):
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
