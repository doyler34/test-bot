"""One self-updating board per running server, kept in a fixed order.

Each server that is up owns one message in the live channel, edited in place:
server A on top, B under it, and so on. While a match runs its message is the live
board; between matches it says so, with a line about the last game. A server that
is down has no board, and gets one back, in its place in the order, when it comes
up. A finished match's own results go to the results channel, so nothing piles up
here.
"""
import asyncio
from datetime import datetime
import logging
import time

import discord

from bot.config import live_board_channel_id
from bot.discord.leaderboard_command import table
from bot.discord.leaderboard_display import retry_delay
from bot.discord.match_results import CHUNK, duration, match_tag, server_letter, titled
from bot.storage.combat_store import match_number, window_standings

LOG = logging.getLogger('reforger.liveboard')
POLL = 30
# A running match is green so it reads as on air at a glance; a server between
# matches is grey.
LIVE = 0x3BA55D
IDLE = 0x747F8D
# Wait for the ingestor's closing pass before the last game is summed up.
SETTLE = 45
# A server has to read as down this many ticks running before its board is taken away,
# so a log that goes quiet for a moment doesn't make the boards jump about.
DOWN_TICKS = 3
MARKER = 'OYB-LIVE'
# A Discord message allows 6000 characters across its embeds. A board row is 38
# characters and a Reforger server tops out at 128 slots, so even a full server
# fits inside two embeds in a single message with room to spare.


def footer_head(server_id):
    """The first footer line: it says the message is ours and which server's board it is."""
    return f'{MARKER}:{server_id}' if server_id else MARKER


def board_embeds(server_name, rows, start, tag=None, server_id=None):
    """A running match's board as the embeds of one message."""
    blocks, position = [], 0
    while position < len(rows):
        size = len(rows) - position
        while size > 1 and len(table(rows[position:position + size], position)) > CHUNK:
            size -= 1
        blocks.append(table(rows[position:position + size], position))
        position += size
    embeds = []
    for index, block in enumerate(blocks or ['```text\nNo linked players yet\n```']):
        embed = discord.Embed(colour=LIVE, description=block)
        if index == 0:
            embed.title = titled('🔴 LIVE', tag, server_name)
        embeds.append(embed)
    embeds[-1].set_footer(text=(
        f'{footer_head(server_id)}\n{len(rows)} players • {duration(start, datetime.now())} • '
        f'{start:%d %b %H:%M} • updates every 30s\n'
        'Player kills only • Counts toward the weekly board'))
    return embeds


def idle_embed(server_id, server_name, last=''):
    """What a server's board says between matches."""
    lines = ['No game running. This fills in when the next one starts.']
    if last:
        lines.append(f'Last game: {last}')
    embed = discord.Embed(colour=IDLE, title=f'⚪ {server_letter(server_id)} — {server_name}'[:256],
                          description='\n'.join(lines))
    embed.set_footer(text=f'{footer_head(server_id)}\nUpdates every 30s while a game runs')
    return embed


def slot_server(message, bot_id):
    """The server a message of ours is the board for, or None. Boards from before
    the servers had a message each have no server in their footer and are left alone."""
    if message.author.id != bot_id:
        return None
    for embed in message.embeds:
        head = str(embed.footer.text).split('\n', 1)[0]
        if head.startswith(MARKER + ':'):
            return head[len(MARKER) + 1:]
    return None


class LiveBoard:
    def __init__(self, bot):
        self.bot = bot
        self.matches = {}
        self.slots = {}
        self.boards = []
        self.down = {}
        self.online = lambda server_id: True
        self.arranged = False
        self.lock = asyncio.Lock()
        self.retry_at = 0
        self.failures = 0
        self._tasks = set()

    def setup(self, servers, online=None):
        """The servers to keep a board for: [(id, name)]. They show in letter order,
        A first, whatever order they're listed in. `online(server_id)` says whether a
        server is up; a server that isn't, and has no match running, gets no board."""
        if live_board_channel_id() is None:
            return
        if online is not None:
            self.online = online
        ordered = sorted(servers, key=lambda s: (server_letter(s[0]), s[0]))
        self.slots = {server_id: dict(name=name, message=None, last='', shown=None)
                      for server_id, name in ordered}
        self.arranged = False

    def start(self, server_id, server_name, started):
        """A match opened. Its board goes live on the next tick, once a linked
        player has actually taken the field."""
        if live_board_channel_id() is None:
            return
        if server_id not in self.slots:
            self.slots[server_id] = dict(name=server_name, message=None, last='', shown=None)
            self.arranged = False
        self.matches[server_id] = dict(name=server_name, started=started, rows=None,
                                       tag=self.tag(server_id, started))

    def tag(self, server_id, started):
        """This match's own name, #0001A upwards per server. A match that cannot
        be numbered still gets a board - it just goes out without the number."""
        try:
            return match_tag(server_id, match_number(self.bot.account_links.db,
                                                     server_id, started))
        except Exception:
            LOG.exception('Could not number the %s match', server_id)
            return None

    def finish(self, server_id):
        if server_id not in self.matches:
            return
        task = asyncio.create_task(self._finalise(server_id, time.time()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finalise(self, server_id, ended):
        """The match is over: its board goes back to waiting, with a line about the game."""
        try:
            await asyncio.sleep(SETTLE)
            match = self.matches.pop(server_id, None)
            slot = self.slots.get(server_id)
            if match is None or slot is None or slot['message'] is None or slot['shown'] != 'live':
                return
            async with self.lock:
                guild = self.bot.get_guild(self.bot.config.guild_id)
                if guild is None:
                    return
                closed = datetime.fromtimestamp(ended)
                rows = self.rows(guild, server_id, match['started'], closed)
                slot['last'] = ' • '.join(part for part in (
                    match['tag'], f'{len(rows)} players', duration(match['started'], closed),
                    f'ended {closed:%d %b %H:%M}') if part)
                await slot['message'].edit(embeds=[idle_embed(server_id, slot['name'], slot['last'])],
                                           allowed_mentions=discord.AllowedMentions.none())
                slot['shown'] = 'idle'
            LOG.info('Live board for %s is waiting for the next game (%s)', match['name'], slot['last'])
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Could not close the live board for %s', server_id)

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

    def up(self, server_id):
        """Whether this server gets a board: it's online or has a match running, and a
        server that was showing keeps its board through a short blip."""
        if server_id in self.matches or self.online(server_id):
            self.down[server_id] = 0
            return True
        self.down[server_id] = self.down.get(server_id, 0) + 1
        return server_id in self.boards and self.down[server_id] < DOWN_TICKS

    async def arrange(self, channel, wanted):
        """Make sure every server that is up has exactly one board, in order. After a
        restart the boards already there are taken back over, if they are all there
        and in order; otherwise ours are removed and posted again, A first."""
        history = [message async for message in channel.history(limit=100)]
        history.reverse()
        found = [(slot_server(message, self.bot.user.id), message) for message in history]
        found = [(server_id, message) for server_id, message in found if server_id in self.slots]
        if [server_id for server_id, _ in found] == wanted:
            for server_id, message in found:
                self.slots[server_id].update(message=message, shown=None)
            self.boards = list(wanted)
            LOG.info('Live boards taken back over: %s', len(found))
            return
        for _, message in found:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        for slot in self.slots.values():
            slot['message'], slot['shown'] = None, None
        for match in self.matches.values():
            match['rows'] = None
        for server_id in wanted:
            slot = self.slots[server_id]
            slot['message'] = await channel.send(
                embed=idle_embed(server_id, slot['name'], slot['last']), silent=True,
                allowed_mentions=discord.AllowedMentions.none())
            slot['shown'] = 'idle'
        self.boards = list(wanted)
        LOG.info('Live boards posted in order: %s', ', '.join(wanted) or 'none, no server is up')

    async def tick(self):
        async with self.lock:
            now = time.time()
            if now < self.retry_at or not self.slots:
                return
            try:
                guild = self.bot.get_guild(self.bot.config.guild_id)
                if guild is None:
                    return
                channel = await self.channel(guild)
                if channel is None:
                    return
                wanted = [server_id for server_id in self.slots if self.up(server_id)]
                if not self.arranged or wanted != self.boards:
                    await self.arrange(channel, wanted)
                    self.arranged = True
                for server_id in self.boards:
                    slot = self.slots[server_id]
                    match = self.matches.get(server_id)
                    rows = self.rows(guild, server_id, match['started']) if match else None
                    if rows:
                        if rows == match['rows']:
                            continue
                        await slot['message'].edit(
                            embeds=board_embeds(match['name'], rows, match['started'],
                                                tag=match['tag'], server_id=server_id),
                            allowed_mentions=discord.AllowedMentions.none())
                        match['rows'], slot['shown'] = rows, 'live'
                    elif slot['shown'] != 'idle':
                        await slot['message'].edit(
                            embeds=[idle_embed(server_id, slot['name'], slot['last'])],
                            allowed_mentions=discord.AllowedMentions.none())
                        slot['shown'] = 'idle'
                self.failures = 0
                self.retry_at = 0
            except discord.NotFound:
                self.arranged = False
                LOG.warning('A live board message was deleted; putting the boards back')
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
