"""Paged, read-only combat standings from existing approved OYB links."""
import asyncio
import logging
import time
import unicodedata

import discord
from discord import app_commands

LOG = logging.getLogger('reforger.leaderboard')
PAGE_SIZE = 15


def standings(db, guild):
    """One snapshot, one row per approved identity; never write XP or stats."""
    return db.execute('''SELECT a.discord_id, c.player_kills, c.deaths,
        (SELECT r.name FROM link_requests r
         WHERE r.guild=a.guild AND r.discord_id=a.discord_id
           AND r.identity=a.identity AND r.status='approved'
         ORDER BY r.created DESC, r.token LIMIT 1)
        FROM account_links a JOIN combat_totals c ON c.identity=a.identity
        WHERE a.guild=?
        ORDER BY c.player_kills DESC, c.deaths ASC, a.discord_id ASC''', (guild,)).fetchall()


def clean_name(value):
    # Keep player text inside its table cell, including malicious Markdown names.
    value = ''.join(' ' if ch.isspace() else ch for ch in str(value)
                    if not unicodedata.category(ch).startswith('C'))
    value = ' '.join(value.replace('`', "'").split()) or 'Unknown player'
    return value[:17] + '…' if len(value) > 18 else value


def leaderboard_embed(rows, page):
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    embed = discord.Embed(title='OYB LEADERBOARD', colour=0xA9BC8C)
    if rows:
        start = page * PAGE_SIZE
        selected = rows[start:start + PAGE_SIZE]
        number_width = max(2, len(str(start + len(selected))))
        kills_width = max(5, max(len(str(row[1])) for row in selected))
        deaths_width = max(6, max(len(str(row[2])) for row in selected))
        lines = [f"{'#':>{number_width}}  {'Name':18}  {'Kills':>{kills_width}}  {'Deaths':>{deaths_width}}"]
        for position, (name, kills, deaths) in enumerate(selected, start + 1):
            lines.append(f'{position:>{number_width}}  {clean_name(name):18}  {kills:>{kills_width}}  {deaths:>{deaths_width}}')
        embed.description = '```text\n' + '\n'.join(lines) + '\n```'
    else:
        embed.description = 'No linked players have recorded combat stats yet. Link your account in **#join-oyb** to appear once combat is recorded.'
    embed.set_footer(text=f'Page {page + 1}/{pages} • {len(rows)} players • All servers\n'
                          'Player kills ↓ · deaths ↑ • Snapshot; run /leaderboard to refresh')
    return embed


class LeaderboardView(discord.ui.View):
    def __init__(self, owner, rows):
        super().__init__(timeout=300)
        self.owner = owner
        self.rows = tuple(rows)
        self.page = 0
        self.message = None
        self.lock = asyncio.Lock()
        self.last_click = float('-inf')
        self.update_buttons()

    def update_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * PAGE_SIZE >= len(self.rows)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner:
            await interaction.response.send_message('Run **/leaderboard** to browse your own pages.', ephemeral=True)
            return False
        return True

    async def change_page(self, interaction, step):
        async with self.lock:
            if self.is_finished():
                await interaction.response.send_message('Run **/leaderboard** again to refresh these pages.', ephemeral=True)
                return
            now = time.monotonic()
            if now - self.last_click < 1:
                await interaction.response.defer()
                return
            self.last_click = now
            old = self.page
            last = max(0, (len(self.rows) - 1) // PAGE_SIZE)
            self.page = max(0, min(old + step, last))
            self.update_buttons()
            try:
                await interaction.response.edit_message(embed=leaderboard_embed(self.rows, self.page), view=self,
                                                        allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                self.page = old
                self.update_buttons()
                raise

    @discord.ui.button(label='Previous', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_page(interaction, -1)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_page(interaction, 1)

    async def on_timeout(self):
        async with self.lock:
            for button in self.children:
                button.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    LOG.debug('Could not disable expired leaderboard buttons', exc_info=True)

    async def on_error(self, interaction, error, item):
        LOG.error('Leaderboard page failed', exc_info=(type(error), error, error.__traceback__))
        message = 'That page could not be loaded. Try again or run **/leaderboard**.'
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class LeaderboardCommand:
    def __init__(self, bot):
        self.bot = bot
        self.command = app_commands.Command(name='leaderboard', description='Browse OYB kills and deaths, 15 players per page', callback=self.show)
        app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))(self.command)
        self.command.error(self.error)
        bot.rank_command.tree.add_command(self.command, guild=bot.rank_command.guild)

    async def error(self, interaction, error):
        message = (f'Try /leaderboard again in {error.retry_after:.0f} seconds.'
                   if isinstance(error, app_commands.CommandOnCooldown)
                   else 'The leaderboard could not be loaded. Please try again shortly.')
        if not isinstance(error, app_commands.CommandOnCooldown):
            LOG.error('Leaderboard failed', exc_info=(type(error), error, error.__traceback__))
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def show(self, interaction: discord.Interaction):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message('Use /leaderboard in the OYB Discord server.', ephemeral=True)
            return
        if not interaction.app_permissions.embed_links:
            await interaction.response.send_message('The bot needs **Embed Links** permission in this channel.', ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        rows = []
        for member_id, kills, deaths, name in standings(self.bot.account_links.db, interaction.guild_id):
            member = interaction.guild.get_member(member_id) if interaction.guild else None
            rows.append((name or (member.display_name if member else f'Member {member_id}'), kills, deaths))
        view = LeaderboardView(interaction.user.id, rows) if len(rows) > PAGE_SIZE else None
        try:
            message = await interaction.followup.send(embed=leaderboard_embed(rows, 0),
                                                     allowed_mentions=discord.AllowedMentions.none(), wait=True,
                                                     **({'view': view} if view else {}))
        except Exception:
            if view:
                view.stop()
            raise
        if view:
            view.message = message
