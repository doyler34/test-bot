"""Paged, read-only combat standings from existing approved OYB links."""
import logging
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
                          'Player kills ↓ · deaths ↑ • Updates automatically')
    return embed


class LeaderboardCommand:
    def __init__(self, bot):
        self.bot = bot
        self.command = app_commands.Command(name='leaderboard', description='Find the permanent OYB leaderboard', callback=self.show)
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
        try:
            saved = self.bot.store.leaderboard(interaction.guild_id)
            target = f"<#{saved['channel']}>" if saved['channel'] else '**===OYB-LeaderBoard===** (being prepared)'
            text = f'View the permanent leaderboard in {target}. Use its Previous/Next buttons to browse.'
        except Exception:
            LOG.exception('Could not read leaderboard channel state')
            text = 'The leaderboard is temporarily unavailable; please try again shortly.'
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
