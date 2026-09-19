"""One self-updating results board per running match.

The board is posted when a match starts, edits itself while the match runs and
stands as the final result once it ends - one message per match, not a live
message followed by a second results post.
"""
import asyncio
from datetime import datetime
import logging
import time

import discord

from bot.config import live_board_channel_id
from bot.discord.leaderboard_command import table
from bot.discord.leaderboard_display import retry_delay
from bot.discord.match_results import CHUNK, duration
from bot.storage.combat_store import window_standings

LOG = logging.getLogger('reforger.liveboard')
POLL = 30
# Wait for the ingestor's closing pass before the board is called final.
SETTLE = 45
MARKER = 'OYB-LIVE'
# A Discord message allows 6000 characters across its embeds. A board row is 38
# characters and a Reforger server tops out at 128 slots, so even a full server
# fits inside two embeds in a single message with room to spare.


def board_embeds(server_name, rows, start, end=None):
    """The board as embeds for one message: live while `end` is None."""
    blocks, position = [], 0
    while position < len(rows):
        size = len(rows) - position
        while size > 1 and len(table(rows[position:position + size], position)) > CHUNK:
            size -= 1
        blocks.append(table(rows[position:position + size], position))
        position += size
    embeds = []
    for index, block in enumerate(blocks or ['```text\nNo linked players yet\n```']):
        embed = discord.Embed(colour=0xA9BC8C if end else 0xC0504D, description=block)
        if index == 0:
            embed.title = (f'🏁 Match results — {server_name}' if end
                           else f'🔴 LIVE — {server_name}')[:256]
        embeds.append(embed)
    closed = end or start
    span = duration(start, end or datetime.now())
    embeds[-1].set_footer(text=(
        f'{MARKER}\n{len(rows)} players • {span} • '
        f'{start:%d %b %H:%M}' + (f'–{closed:%H:%M}' if end else ' • updates every 30s') + '\n'
        'Player kills only • Counts toward the weekly board'))
    return embeds


def ours(message, bot_id, server_name):
    return message.author.id == bot_id and any(
        str(e.footer.text).startswith(MARKER) and server_name in str(e.title)
        for e in message.embeds)


class LiveBoard:
    def __init__(self, bot):
        self.bot = bot
        self.matches = {}
        self.lock = asyncio.Lock()
        self.retry_at = 0
        self.failures = 0
        self._tasks = set()

    def start(self, server_id, server_name, started):
        """A match opened. The board itself is posted on the next tick, once a
        linked player has actually taken the field."""
        if live_board_channel_id() is None:
            return
        self.matches[server_id] = dict(name=server_name, started=started,
                                       message=None, rows=None, searched=False)

    def finish(self, server_id):
        if server_id not in self.matches:
            return
        task = asyncio.create_task(self._finalise(server_id, time.time()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finalise(self, server_id, ended):
        try:
            await asyncio.sleep(SETTLE)
            match = self.matches.pop(server_id, None)
            if match is None or match['message'] is None:
                return
            async with self.lock:
                guild = self.bot.get_guild(self.bot.config.guild_id)
                if guild is None:
                    return
                rows = self.rows(guild, server_id, match['started'], datetime.fromtimestamp(ended))
                await match['message'].edit(
                    embeds=board_embeds(match['name'], rows, match['started'],
                                        datetime.fromtimestamp(ended)),
                    allowed_mentions=discord.AllowedMentions.none())
            LOG.info('Live board finalised for %s (%s players)', match['name'], len(rows))
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Could not finalise the live board for %s', server_id)

    def rows(self, guild, server_id, started, ended=None):
        result = []
        for member_id, kills, deaths, name in window_standings(
                self.bot.account_links.db, guild.id, started, ended, server=server_id):
            member = guild.get_member(member_id)
            result.append((name or (member.display_name if member else f'Member {member_id}'),
                           kills, deaths))
        return result

    async def channel(self, guild):
        pinned = live_board_channel_id()
        if not pinned:
            return None
        channel = guild.get_channel(pinned)
        if not isinstance(channel, discord.TextChannel):
            try:
                channel = await guild.fetch_channel(pinned)
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            LOG.warning('LIVE_BOARD_CHANNEL_ID %s is not a text channel I can see', pinned)
            return None
        return channel

    async def adopt(self, channel, match):
        """After a restart the message id is gone; take the board back over
        rather than leaving one stuck on LIVE and posting a second."""
        match['searched'] = True
        async for message in channel.history(limit=50):
            if ours(message, self.bot.user.id, match['name']):
                LOG.info('Live board adopted after restart: %s', message.id)
                return message
        return None

    async def tick(self):
        async with self.lock:
            now = time.time()
            if now < self.retry_at or not self.matches:
                return
            try:
                guild = self.bot.get_guild(self.bot.config.guild_id)
                if guild is None:
                    return
                channel = await self.channel(guild)
                if channel is None:
                    return
                for server_id, match in list(self.matches.items()):
                    rows = self.rows(guild, server_id, match['started'])
                    if not rows or rows == match['rows']:
                        continue
                    embeds = board_embeds(match['name'], rows, match['started'])
                    if match['message'] is None and not match['searched']:
                        match['message'] = await self.adopt(channel, match)
                    if match['message'] is None:
                        match['message'] = await channel.send(
                            embeds=embeds, silent=True,
                            allowed_mentions=discord.AllowedMentions.none())
                        LOG.info('Live board opened for %s: %s', match['name'], match['message'].id)
                    else:
                        await match['message'].edit(
                            embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
                    match['rows'] = rows
                self.failures = 0
                self.retry_at = 0
            except Exception as error:
                self.failures += 1
                self.retry_at = time.time() + retry_delay(error, self.failures)
                LOG.warning('Live board update failed; retrying later',
                            exc_info=(type(error), error, error.__traceback__))

    async def run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self.tick()
            await asyncio.sleep(POLL)

    async def close(self):
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
