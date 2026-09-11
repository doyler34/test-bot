"""One durable public leaderboard, using the existing combat query and bot state."""
import asyncio
import json
import logging
import math
import time

import discord
from bot.config import staging_enabled
from bot.discord.leaderboard_command import PAGE_SIZE, leaderboard_embed, standings

LOG = logging.getLogger('reforger.leaderboard')
CHANNEL_NAME = '===OYB-LeaderBoard==='
MARKER = 'OYB • Permanent leaderboard'
POLL = 15
DEBOUNCE = 30
RECONCILE = 300


def privileged(target):
    permissions = getattr(target, 'guild_permissions', None) or getattr(target, 'permissions', None)
    return permissions is not None and any(getattr(permissions, flag, False) for flag in
        ('administrator', 'manage_guild', 'manage_channels', 'manage_messages', 'moderate_members'))


def overwrites(guild, existing=None):
    result = dict(existing or {})
    # Explicit ordinary-role/member grants can override @everyone denies.
    for target in set(result) | {guild.default_role, guild.me}:
        overwrite = discord.PermissionOverwrite.from_pair(*result.get(target, discord.PermissionOverwrite()).pair())
        if target == guild.me:
            overwrite.update(view_channel=True, read_message_history=True, send_messages=True,
                             embed_links=True, manage_messages=True, pin_messages=True)
        elif target == guild.default_role or not privileged(target):
            overwrite.update(view_channel=not staging_enabled(), read_message_history=True, send_messages=False,
                create_public_threads=False, create_private_threads=False, send_messages_in_threads=False,
                add_reactions=False, use_external_stickers=False, use_external_apps=False,
                use_application_commands=False, send_polls=False, send_voice_messages=False)
        result[target] = overwrite
    return result


def owned(message, bot_id):
    return message.author.id == bot_id and any(
        e.title == 'OYB LEADERBOARD' and
        (str(e.footer.text).startswith(MARKER) or
         ('Player kills' in str(e.footer.text) and 'All servers' in str(e.footer.text)))
        for e in message.embeds)


def retry_delay(error, failures):
    values = [getattr(error, 'retry_after', None)]
    headers = getattr(getattr(error, 'response', None), 'headers', {}) or {}
    values += [headers.get('Retry-After'), headers.get('X-RateLimit-Reset-After')]
    try:
        values.append(json.loads(getattr(error, 'text', '')).get('retry_after'))
    except (ValueError, TypeError, AttributeError):
        pass
    delays = []
    for value in values:
        try:
            delay = float(value)
            if math.isfinite(delay) and delay >= 0:
                delays.append(delay)
        except (TypeError, ValueError):
            pass
    return max([min(900, 30 * 2 ** min(failures - 1, 5))] + delays)


def signature(embed, components):
    buttons = [(b.get('custom_id'), b.get('label'), b.get('style'), bool(b.get('disabled')))
               for row in components for b in row.get('components', [])]
    return (embed.title, embed.description, embed.colour.value if embed.colour else None,
            embed.footer.text, buttons)


class LeaderboardView(discord.ui.View):
    def __init__(self, display):
        super().__init__(timeout=None)
        self.display = display
        self.configure(0, 0)

    def configure(self, page, count):
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        self.previous.disabled = page == 0
        self.next.disabled = page >= pages - 1
        self.indicator.label = f'Page {page + 1} / {pages}'

    async def interaction_check(self, interaction):
        if (interaction.guild_id == self.display.bot.config.guild_id and
                self.display.message_id == interaction.message.id):
            return True
        await interaction.response.send_message('This leaderboard has been replaced or is still starting. Please use the pinned message.', ephemeral=True)
        return False

    @discord.ui.button(label='◀ Previous', custom_id='oyb:leaderboard:previous:v1')
    async def previous(self, interaction, button):
        await self.display.turn_page(interaction, -1)

    @discord.ui.button(label='Page 1 / 1', custom_id='oyb:leaderboard:page:v1', disabled=True)
    async def indicator(self, interaction, button):
        await interaction.response.defer()

    @discord.ui.button(label='Next ▶', custom_id='oyb:leaderboard:next:v1', style=discord.ButtonStyle.primary)
    async def next(self, interaction, button):
        await self.display.turn_page(interaction, 1)

    async def on_error(self, interaction, error, item):
        LOG.error('Leaderboard control failed', exc_info=(type(error), error, error.__traceback__))
        text = 'The leaderboard is temporarily unavailable. Please try again shortly.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class LeaderboardDisplay:
    def __init__(self, bot):
        self.bot = bot
        self.view = LeaderboardView(self)
        self.lock = asyncio.Lock()
        self.message_id = None
        self.last_rows = None
        self.dirty_at = None
        self.reconcile_at = 0
        self.retry_at = 0
        self.failures = 0
        self.last_click = float('-inf')
        self.registered = False

    def register(self):
        if not self.registered:
            self.bot.add_view(self.view)
            self.registered = True
            LOG.info('Leaderboard persistent controls registered')

    def rows(self, guild):
        result = []
        for member_id, kills, deaths, name in standings(self.bot.account_links.db, guild.id):
            member = guild.get_member(member_id)
            result.append((name or (member.display_name if member else f'Member {member_id}'), kills, deaths))
        return result

    async def channel(self, guild, state):
        # Steady state: the saved channel is in the gateway cache, so resolve it
        # without a REST channel-list call every refresh.
        channel = None
        if state['channel']:
            cached = guild.get_channel(state['channel'])
            if isinstance(cached, discord.TextChannel):
                channel = cached
        if channel is None:
            # Cache miss/first run: REST inventory avoids duplicate creation when
            # the gateway cache lags.
            channels = [c for c in await guild.fetch_channels() if isinstance(c, discord.TextChannel)]
            channel = next((c for c in channels if c.id == state['channel']), None)
            if channel is None:
                matches = [c for c in channels if c.topic == MARKER or c.name.casefold() == CHANNEL_NAME.casefold()]
                channel = min(matches, key=lambda c: c.id) if matches else None
                if channel:
                    LOG.info('Leaderboard channel found: %s', channel.id)
        if channel is None:
            channel = await guild.create_text_channel(CHANNEL_NAME, topic=MARKER,
                overwrites=overwrites(guild), reason='OYB permanent leaderboard')
            LOG.info('Leaderboard channel created: %s', channel.id)
        else:
            desired = overwrites(guild, channel.overwrites)
            if desired != channel.overwrites:
                channel = await channel.edit(overwrites=desired, reason='Keep OYB leaderboard display-only')
        if state['channel'] != channel.id:
            state.update(channel=channel.id, message=None)
            self.bot.store.save_leaderboard(state)
        return channel

    async def refresh(self, guild, state, rows, reconcile=False):
        channel = await self.channel(guild, state)
        message = None
        if state['message']:
            try:
                candidate = await channel.fetch_message(state['message'])
                if owned(candidate, self.bot.user.id):
                    message = candidate
            except discord.NotFound:
                LOG.info('Leaderboard recovery after deleted message %s', state['message'])
        # Full-channel scan is recovery only (the known message is missing);
        # a routine reconcile with a live message re-asserts it without the scan.
        history = []
        if message is None:
            history = [m async for m in channel.history(limit=None)]
        old_boards = [m for m in history if owned(m, self.bot.user.id)]
        if message is None and old_boards:
            message = min(old_boards, key=lambda m: (not m.pinned, m.id))
            LOG.info('Leaderboard message found: %s', message.id)
        page = max(0, min(state['page'], max(0, (len(rows) - 1) // PAGE_SIZE)))
        state['page'] = page
        self.view.configure(page, len(rows))
        embed = leaderboard_embed(rows, page)
        embed.set_footer(text=MARKER + '\n' + embed.footer.text)
        desired = signature(embed, self.view.to_components())
        if message is None:
            message = await channel.send(embed=embed, view=self.view, silent=True,
                                         allowed_mentions=discord.AllowedMentions.none())
            LOG.info('Leaderboard message created: %s', message.id)
        elif (len(message.embeds) != 1 or message.content or
              signature(message.embeds[0], [c.to_dict() for c in message.components]) != desired):
            message = await message.edit(content=None, embed=embed, view=self.view,
                                         allowed_mentions=discord.AllowedMentions.none())
            LOG.info('Leaderboard refreshed: message %s, page %s', message.id, page + 1)
        else:
            LOG.debug('Leaderboard unchanged; edit skipped')
        state['message'] = message.id
        self.bot.store.save_leaderboard(state)
        self.message_id = message.id
        if message.reactions:
            await message.clear_reactions()
        if not message.pinned:
            await message.pin(reason='OYB permanent leaderboard')
            # Includes the pin system notice; remove only notices this bot generated.
            history = [m async for m in channel.history(limit=None)]
            old_boards = [m for m in history if owned(m, self.bot.user.id)]
        board_ids = {m.id for m in old_boards} | {message.id}
        cleanup = [m for m in history if
            (m.id != message.id and owned(m, self.bot.user.id)) or
            (m.author.id == self.bot.user.id and m.type == discord.MessageType.pins_add and
             m.reference and m.reference.message_id in board_ids)]
        for old in cleanup[:20]:
            try:
                await old.delete()
            except discord.NotFound:
                pass
        if cleanup:
            LOG.info('Cleaned %s old bot leaderboard messages/notices', min(20, len(cleanup)))

    def failed(self, error, state=None):
        self.failures += 1
        delay = retry_delay(error, self.failures)
        self.retry_at = time.time() + delay
        self.reconcile_at = 0
        if state is not None:
            try:
                state['retry_at'] = self.retry_at
                self.bot.store.save_leaderboard(state)
            except Exception:
                LOG.warning('Could not persist leaderboard retry; keeping in memory')
        LOG.warning('Leaderboard refresh failed%s; retry scheduled in %.0fs',
                    ' (rate limited)' if getattr(error, 'status', None) == 429 or isinstance(error, discord.RateLimited) else '',
                    delay, exc_info=(type(error), error, error.__traceback__))

    async def tick(self):
        async with self.lock:
            now = time.time()
            if now < self.retry_at:
                return
            state = None
            try:
                state = self.bot.store.leaderboard(self.bot.config.guild_id)
                self.retry_at = max(self.retry_at, state['retry_at'])
                if now < self.retry_at:
                    return
                guild = self.bot.get_guild(self.bot.config.guild_id)
                if guild is None:
                    raise RuntimeError('Leaderboard guild unavailable')
                rows = self.rows(guild)
                if rows != self.last_rows:
                    self.last_rows = rows
                    if self.dirty_at is None:
                        self.dirty_at = now + DEBOUNCE
                reconcile = now >= self.reconcile_at
                if not reconcile and (self.dirty_at is None or now < self.dirty_at):
                    return
                await self.refresh(guild, state, rows, reconcile=reconcile)
                self.dirty_at = None
                if reconcile:
                    self.reconcile_at = time.time() + RECONCILE
                self.failures = 0
                self.retry_at = 0
                if state['retry_at']:
                    state['retry_at'] = 0
                    self.bot.store.save_leaderboard(state)
            except Exception as error:
                self.failed(error, state)

    async def turn_page(self, interaction, step):
        # Acknowledge before network/SQLite work; everybody shares this page.
        await interaction.response.defer()
        if self.lock.locked():
            await interaction.followup.send('The leaderboard is updating; try again shortly.', ephemeral=True)
            return
        async with self.lock:
            state = None
            try:
                now = time.time()
                state = self.bot.store.leaderboard(self.bot.config.guild_id)
                if interaction.message.id != state['message']:
                    await interaction.followup.send('Please use the current pinned leaderboard.', ephemeral=True)
                    return
                if now < max(self.retry_at, state['retry_at']) or now - self.last_click < 2:
                    await interaction.followup.send('Please wait a moment before changing pages again.', ephemeral=True)
                    return
                self.last_click = now
                guild = interaction.guild
                rows = self.rows(guild)
                state['page'] = max(0, min(state['page'] + step, max(0, (len(rows) - 1) // PAGE_SIZE)))
                # Persist the selected page before Discord, so an interrupted edit retries it.
                self.bot.store.save_leaderboard(state)
                await self.refresh(guild, state, rows)
                self.last_rows = rows
                self.dirty_at = None
            except Exception as error:
                self.failed(error, state)
                await interaction.followup.send('The leaderboard is temporarily unavailable; an automatic retry is scheduled.', ephemeral=True)

    async def run(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self.tick()
            await asyncio.sleep(POLL)
