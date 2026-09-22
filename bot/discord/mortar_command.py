"""/mortar - two grids in, a firing solution out.

Azimuth and range are worked out from the grids; elevation comes from the
range tables in assets/mortar. A tube whose table is still empty answers with
the bearing and the distance and says so, because a made-up elevation is worse
than none.
"""
import asyncio
from contextlib import closing
from io import BytesIO
import logging

import discord
from discord import app_commands

from bot.mortar.plot import render
from bot.mortar.solution import bearing, parse_grid, solution, tube_names

LOG = logging.getLogger('reforger.mortar')
HELP = ('Grids are 4, 6, 8 or 10 digits - half easting, half northing. '
        '`0428 1183` and `04281183` are the same place.')


def charge_lines(tube, distance):
    """One line per charge, nearest first, or why there is nothing to show."""
    charges = solution(tube, distance)
    if not charges:
        return None
    lines = []
    for charge in charges:
        if charge.elevation is None:
            lines.append(f'`Charge {charge.charge}`  — {charge.note}')
        else:
            lines.append(f'`Charge {charge.charge}`  **{charge.elevation:.0f}** mils')
    return '\n'.join(lines)


def solution_embed(tube, name, gun, target, mils, distance):
    embed = discord.Embed(title='MORTAR FIRING SOLUTION', colour=0xD9A441)
    embed.add_field(name='Azimuth', value=f'**{mils:.0f}** mils\n{mils * 360 / 6400:.1f}°')
    embed.add_field(name='Range', value=f'**{distance:.0f}** m')
    embed.add_field(name='Tube', value=name)
    elevation = charge_lines(tube, distance)
    embed.add_field(name='Elevation', value=elevation or
                    f'No range table for the {name} yet, so azimuth and range only. '
                    'See `assets/mortar/README.md`.', inline=False)
    embed.add_field(name='Gun', value=f'`{gun[0]:05d} {gun[1]:05d}`')
    embed.add_field(name='Target', value=f'`{target[0]:05d} {target[1]:05d}`')
    embed.set_footer(text='Flat ground: height difference is not corrected for. '
                          'Check your own first round and adjust.')
    return embed


class TubeSelect(discord.ui.Select):
    def __init__(self, chosen):
        super().__init__(placeholder='Change tube', options=[
            discord.SelectOption(label=name, value=key, default=key == chosen)
            for key, name in tube_names().items()][:25])

    async def callback(self, interaction):
        # Take the click before rendering; Discord allows three seconds.
        await interaction.response.defer()
        await self.view.show(interaction, self.values[0], edit=True)


class MortarView(discord.ui.View):
    def __init__(self, tube, gun, target):
        super().__init__(timeout=600)
        self.tube, self.gun, self.target = tube, gun, target
        if len(tube_names()) > 1:
            self.add_item(TubeSelect(tube))

    async def show(self, interaction, tube, edit=False):
        """Render and deliver the solution. The caller has already acknowledged
        the interaction, so this goes out as a followup or an edit."""
        self.tube = tube
        name = tube_names().get(tube, tube)
        mils, distance = bearing(self.gun, self.target)
        embed = solution_embed(tube, name, self.gun, self.target, mils, distance)
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                for option in item.options:
                    option.default = option.value == tube
        picture = None
        if interaction.app_permissions.attach_files:
            picture = await asyncio.to_thread(render, self.gun, self.target, mils, distance, name)
        if picture is None:
            if edit:
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.followup.send(embed=embed, view=self, ephemeral=True)
            return
        embed.set_image(url='attachment://mortar.png')
        with BytesIO(picture) as buffer:
            with closing(discord.File(buffer, filename='mortar.png')) as attachment:
                if edit:
                    await interaction.edit_original_response(
                        embed=embed, view=self, attachments=[attachment])
                else:
                    await interaction.followup.send(
                        embed=embed, view=self, file=attachment, ephemeral=True)

    async def on_error(self, interaction, error, item):
        LOG.error('Mortar solution failed', exc_info=(type(error), error, error.__traceback__))
        text = 'That solution could not be worked out. Please try again shortly.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class MortarModal(discord.ui.Modal, title='Mortar firing solution'):
    gun_input = discord.ui.TextInput(label='Your grid (the gun)', placeholder='0428 1183',
                                     min_length=4, max_length=13)
    target_input = discord.ui.TextInput(label='Target grid', placeholder='0512 1096',
                                        min_length=4, max_length=13)

    def __init__(self, tube):
        super().__init__()
        self.tube = tube

    async def on_submit(self, interaction):
        try:
            gun = parse_grid(self.gun_input.value)
            target = parse_grid(self.target_input.value)
        except ValueError as exc:
            await interaction.response.send_message(f'{exc} {HELP}', ephemeral=True)
            return
        if gun == target:
            await interaction.response.send_message(
                'The gun and the target are the same grid.', ephemeral=True)
            return
        # Both grids read cleanly; the render that follows needs the click taken.
        await interaction.response.defer(thinking=True, ephemeral=True)
        await MortarView(self.tube, gun, target).show(interaction, self.tube)

    async def on_error(self, interaction, error):
        LOG.error('Mortar solution failed', exc_info=(type(error), error, error.__traceback__))
        text = 'That solution could not be worked out. Please try again shortly.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class MortarCommand:
    def __init__(self, bot):
        self.bot = bot
        self.command = app_commands.Command(
            name='mortar', description='Work out a mortar bearing and range between two grids',
            callback=self.show)
        app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))(self.command)
        self.command.error(self.error)
        bot.rank_command.tree.add_command(self.command, guild=bot.rank_command.guild)

    async def error(self, interaction, error):
        text = (f'Try /mortar again in {error.retry_after:.0f} seconds.'
                if isinstance(error, app_commands.CommandOnCooldown)
                else 'That solution could not be worked out. Please try again shortly.')
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def show(self, interaction: discord.Interaction):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message('Use /mortar in the OYB Discord server.',
                                                    ephemeral=True)
            return
        tubes = tube_names()
        if not tubes:
            await interaction.response.send_message(
                'No mortar tubes are configured. See `assets/mortar/README.md`.', ephemeral=True)
            return
        await interaction.response.send_modal(MortarModal(next(iter(tubes))))
