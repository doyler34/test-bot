"""One self-updating message with this week's heli kills, and nothing else.

It edits itself every minute. When the week turns over (Monday 00:00 Boston,
the same as the main board) the old message is left as that week's final
result and a new one is started underneath.
"""
import asyncio
from datetime import timedelta
import logging
import time

import discord

from bot.config import heli_board_channel_id
from bot.discord.leaderboard_command import NAME, PLACE, SCORE, clean_name
from bot.discord.leaderboard_display import retry_delay
from bot.mortar import heights
from bot.storage.combat_store import killer_spots, linked_names, week_start
from bot.tracking.combat_ingestor import backfill_spots
from bot.tracking.heli import heli_kills

LOG = logging.getLogger('reforger.heliboard')
POLL = 60
MARKER = 'OYB-HELI'
SHOWN = 25
COLOUR = 0x5B8DB8


def standings(db, guild, ground, start, end=None):
    """[(name, heli kills)] for linked players, most first. `guild` is the Discord guild, so a member
    with no approved name shows under their Discord name, the same as on the main board."""
    counted = heli_kills(killer_spots(db, start, end), ground)
    names = linked_names(db, guild.id)
    members = {}
    for identity, kills in counted.items():
        if identity in names:
            member_id, name = names[identity]
            total, known = members.get(member_id, (0, None))
            members[member_id] = (total + kills, known or name)
    rows = []
    for member_id, (kills, name) in members.items():
        member = guild.get_member(member_id) if not name else None
        rows.append((name or (member.display_name if member else f'Member {member_id}'), kills))
    return sorted(rows, key=lambda r: (-r[1], r[0].lower()))


def week_tag(start):
    return f'{MARKER} {start:%Y-%m-%d}'


def board_embed(rows, start, final=False):
    embed = discord.Embed(title='🚁 HELI KILLS' + (' — FINAL' if final else ' — THIS WEEK'), colour=COLOUR)
    if rows:
        lines = [f"{'#':>{PLACE}}  {'Name':{NAME}} {'Kills':>{SCORE + 1}}"]
        for position, (name, kills) in enumerate(rows[:SHOWN], 1):
            lines.append(f'{position:>{PLACE}}  {clean_name(name):{NAME}} {kills:>{SCORE + 1}}')
        embed.description = '```text\n' + '\n'.join(lines) + '\n```'
    else:
        embed.description = 'No heli kills yet this week.'
    status = 'Final result' if final else 'Updates every minute • Resets Monday'
    embed.set_footer(text=f'{week_tag(start)}\nWeek of {start:%d %b} • {len(rows)} pilots & gunners\n'
                          f'Kills from a helicopter only • {status}')
    return embed


def ours(message, bot_id, start):
    return message.author.id == bot_id and any(
        str(e.footer.text).startswith(week_tag(start)) for e in message.embeds)


class HeliBoard:
    def __init__(self, bot):
        self.bot = bot
        self.message = None
        self.week = None
        self.shown = None
        self.retry_at = 0
        self.failures = 0

    async def channel(self, guild):
        wanted = heli_board_channel_id()
        channel = guild.get_channel(wanted)
        if not isinstance(channel, discord.TextChannel):
            try:
                channel = await guild.fetch_channel(wanted)
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            LOG.warning('HELI_BOARD_CHANNEL_ID %s is not a text channel I can see', wanted)
            return None
        return channel

    async def adopt(self, channel, start):
        """After a restart, take this week's board back over instead of posting a second one."""
        async for message in channel.history(limit=50):
            if ours(message, self.bot.user.id, start):
                return message
        return None

    async def tick(self, now=None):
        if time.time() < self.retry_at:
            return
        ground = heights.load()
        if ground is None:
            await asyncio.to_thread(heights.ensure)
            ground = heights.load()
            if ground is None:
                return
        try:
            guild = self.bot.get_guild(self.bot.config.guild_id)
            if guild is None:
                return
            channel = await self.channel(guild)
            if channel is None:
                return
            db = self.bot.account_links.db
            start = week_start(now)
            if self.week is not None and self.week != start and self.message is not None:
                # The week is over: leave the old board as its final result.
                last = standings(db, guild, ground, self.week, start)
                await self.message.edit(embed=board_embed(last, self.week, final=True),
                                        allowed_mentions=discord.AllowedMentions.none())
                LOG.info('Heli board for the week of %s finalised', self.week.date())
                self.message, self.shown = None, None
            if self.week != start:
                self.week = start
                self.message = await self.adopt(channel, start)
            embed = board_embed(standings(db, guild, ground, start), start)
            if self.message is None:
                self.message = await channel.send(embed=embed, silent=True,
                                                  allowed_mentions=discord.AllowedMentions.none())
                LOG.info('Heli board posted for the week of %s: %s', start.date(), self.message.id)
            elif embed.to_dict() != self.shown:
                await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            self.shown = embed.to_dict()
            self.failures, self.retry_at = 0, 0
        except discord.NotFound:
            # Someone deleted the board; post a fresh one next time.
            self.message, self.shown, self.week = None, None, None
        except Exception as error:
            self.failures += 1
            self.retry_at = time.time() + retry_delay(error, self.failures)
            LOG.warning('Heli board update failed; retrying later',
                        exc_info=(type(error), error, error.__traceback__))

    async def run(self):
        if heli_board_channel_id() is None:
            return
        await self.bot.wait_until_ready()
        await asyncio.to_thread(heights.ensure)
        try:
            since = (week_start() - timedelta(days=1)).date()
            database = self.bot.account_links.db.execute('PRAGMA database_list').fetchone()[2]
            filled = await asyncio.to_thread(backfill_spots, database, self.bot.config.servers, since)
            if filled:
                LOG.info('Added killer positions to %s kills from this week\'s logs', filled)
        except Exception:
            LOG.exception('Could not add positions to this week\'s earlier kills; carrying on')
        while not self.bot.is_closed():
            await self.tick()
            await asyncio.sleep(POLL)
