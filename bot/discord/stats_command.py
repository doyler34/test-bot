"""Clean combat-only embeds using the existing approved account links."""
import logging
import discord
from discord import app_commands
from bot.config import command_auto_clear_seconds
from bot.storage.combat_store import week_start, window_totals

LOG = logging.getLogger('reforger.stats')


def kd(kills, deaths):
    return f'{kills / deaths if deaths else kills:.2f}'


def metres(value):
    return f'{value:.0f} m' if value else '—'


def stats_embed(name, data, start=None):
    embed = discord.Embed(title='OYB PLAYER STATS',description=discord.utils.escape_markdown(name)[:256],colour=0xA9BC8C)
    if data is None:
        embed.add_field(name='No recorded data',value='No Reforger combat has been recorded for this player this week.',inline=False)
    else:
        for label,value in [('Player Kills',data['player_kills']),('Deaths',data['deaths']),
                            ('K/D',kd(data['player_kills'],data['deaths'])),
                            ('Longest Kill',metres(data.get('longest_kill'))),
                            ('AI Kills','Unavailable'),('Teamkills',data['teamkills'])]:
            embed.add_field(name=label,value=str(value),inline=True)
        embed.add_field(name='Coverage',value='Player-vs-player only. Deaths to AI and suicides are not counted, '
            'and vanilla logs do not report AI kills. Longest kill counts gunfire only.',inline=False)
    week = f"Week of {start:%d %b}" if start else 'This week'
    embed.set_footer(text=f'O.Y.B • {week} • Resets Monday')
    return embed


class StatsCommand:
    def __init__(self, bot):
        self.bot = bot
        self.command = app_commands.Command(name='stats',description='Show recorded OYB Reforger combat statistics',callback=self.show)
        app_commands.checks.cooldown(1,5,key=lambda i:(i.guild_id,i.user.id))(self.command)
        self.command.error(self.error)
        bot.rank_command.tree.add_command(self.command,guild=bot.rank_command.guild)

    async def error(self, interaction, error):
        text = (f'Try /stats again in {error.retry_after:.0f} seconds.' if isinstance(error,app_commands.CommandOnCooldown)
                else 'Combat stats could not be loaded. Please try again shortly.')
        if interaction.response.is_done():
            await interaction.followup.send(text,ephemeral=True)
        else:
            await interaction.response.send_message(text,ephemeral=True)

    async def show(self, interaction: discord.Interaction, user: discord.Member | None = None):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message('Use /stats in the OYB Discord server.',ephemeral=True)
            return
        member = user or interaction.user
        identity = self.bot.account_links.lookup(interaction.guild_id,member.id)
        if not identity:
            text = ('Open **#join-oyb**, press **Link Reforger account**, and submit your in-game name for admin approval.'
                    if member.id == interaction.user.id else 'That member does not have an approved Reforger account link.')
            await interaction.response.send_message(text,ephemeral=True)
            return
        if not interaction.app_permissions.embed_links:
            await interaction.response.send_message('The bot needs **Embed Links** permission in this channel.',ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            start = week_start()
            data = window_totals(self.bot.account_links.db,identity,start)
            sent = await interaction.followup.send(embed=stats_embed(member.display_name,data,start),allowed_mentions=discord.AllowedMentions.none())
            clear = command_auto_clear_seconds()
            if clear:
                await sent.delete(delay=clear)
        except Exception:
            LOG.exception('Could not load combat stats')
            await interaction.followup.send('Combat stats could not be loaded. Please try again shortly.',ephemeral=True)
