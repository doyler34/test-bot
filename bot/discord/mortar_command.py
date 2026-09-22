"""/mortar - two grids in, a firing solution out.

The command holds no ballistics of its own. Picking a tube loads that weapon's
profile - sight, shell, rings and the ranges they cover - and the engine works
the rest out, so a new tube is a line in assets/mortar/tables.json.
"""
import asyncio
from contextlib import closing
from io import BytesIO
import logging

import discord
from discord import app_commands

from bot.mortar.plot import render
from bot.mortar.solution import OUT_OF_RANGE, bearing, parse_grid, profile, profiles, solution

LOG = logging.getLogger('reforger.mortar')
HELP = ('Grids are 4, 6, 8 or 10 digits - half easting, half northing. '
        '`0428 1183` and `04281183` are the same place.')


def height(text):
    """An altitude box: blank is fine, anything else has to be a number."""
    raw = (text or '').strip()
    if not raw:
        return 0.0
    try:
        return float(raw.replace(',', '.'))
    except ValueError:
        raise ValueError('Altitudes are in metres, as numbers.')


def elevation_field(weapon, best, every):
    if best is None:
        if not weapon.loaded:
            return f'**{OUT_OF_RANGE}** — no range table is loaded for this tube.'
        low, high = weapon.span
        return (f'**{OUT_OF_RANGE}** — no ring reaches that far. '
                f'This tube covers {low:.0f}-{high:.0f} m.')
    lines = [f'**{best.elevation:.0f}** mils on **ring {best.ring}**',
             f'Flight {best.flight:.0f}s · dispersion {best.dispersion:.0f} m']
    others = [r for r in every if r.ring != best.ring]
    if others:
        lines.append('')
        lines.extend(f'`ring {r.ring}`  {r.elevation:.0f} mils · {r.flight:.0f}s · '
                     f'{r.dispersion:.0f} m' for r in others)
    return '\n'.join(lines)


def solution_embed(weapon, gun, target, mils, distance, climb=0.0):
    best, every = solution(weapon, distance, climb)
    embed = discord.Embed(title='MORTAR FIRING SOLUTION',
                          colour=0xC0504D if best is None else 0xD9A441)
    embed.add_field(name='Azimuth', value=f'**{mils:.0f}** mils\n{mils * 360 / weapon.mils:.1f}°')
    embed.add_field(name='Range', value=f'**{distance:.0f}** m')
    embed.add_field(name='Tube', value=f'{weapon.label}\n{weapon.mils} mil sight')
    embed.add_field(name='Elevation', value=elevation_field(weapon, best, every), inline=False)
    embed.add_field(name='Gun', value=f'`{gun[0]:05d} {gun[1]:05d}`')
    embed.add_field(name='Target', value=f'`{target[0]:05d} {target[1]:05d}`')
    if climb:
        correction = f'\n{best.correction:+.0f} mils' if best else ''
        embed.add_field(name='Height', value=f'Target {climb:+.0f} m{correction}')
    span = weapon.span
    reach = f' · {span[0]:.0f}-{span[1]:.0f} m' if span else ''
    embed.set_footer(text=f'{weapon.shell}{reach} · in-game range table'.strip(' ·'))
    return embed


class TubeSelect(discord.ui.Select):
    def __init__(self, chosen):
        super().__init__(placeholder='Change tube', options=[
            discord.SelectOption(label=weapon.label, value=key, description=weapon.shell or None,
                                 default=key == chosen)
            for key, weapon in profiles().items()][:25])

    async def callback(self, interaction):
        # Take the click before rendering; Discord allows three seconds.
        await interaction.response.defer()
        await self.view.show(interaction, self.values[0], edit=True)


class MortarView(discord.ui.View):
    def __init__(self, tube, gun, target, climb=0.0):
        super().__init__(timeout=600)
        self.tube, self.gun, self.target, self.climb = tube, gun, target, climb
        if len(profiles()) > 1:
            self.add_item(TubeSelect(tube))

    async def show(self, interaction, tube, edit=False):
        """Render and deliver the solution. The caller has already acknowledged
        the interaction, so this goes out as a followup or an edit."""
        self.tube = tube
        weapon = profile(tube)
        mils, distance = bearing(self.gun, self.target, weapon.mils)
        embed = solution_embed(weapon, self.gun, self.target, mils, distance, self.climb)
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                for option in item.options:
                    option.default = option.value == tube
        picture = None
        if interaction.app_permissions.attach_files:
            picture = await asyncio.to_thread(render, self.gun, self.target, mils,
                                              distance, weapon.label, weapon.mils)
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
    gun_height = discord.ui.TextInput(label='Your altitude (m)', placeholder='Optional',
                                      required=False, max_length=6)
    target_height = discord.ui.TextInput(label='Target altitude (m)', placeholder='Optional',
                                         required=False, max_length=6)

    def __init__(self, tube):
        super().__init__()
        self.tube = tube

    async def on_submit(self, interaction):
        try:
            gun = parse_grid(self.gun_input.value)
            target = parse_grid(self.target_input.value)
            climb = height(self.target_height.value) - height(self.gun_height.value)
        except ValueError as exc:
            await interaction.response.send_message(f'{exc} {HELP}', ephemeral=True)
            return
        if gun == target:
            await interaction.response.send_message(
                'The gun and the target are the same grid.', ephemeral=True)
            return
        # Both grids read cleanly; the render that follows needs the click taken.
        await interaction.response.defer(thinking=True, ephemeral=True)
        await MortarView(self.tube, gun, target, climb).show(interaction, self.tube)

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
            name='mortar', description='Work out a mortar firing solution between two grids',
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

    async def show(self, interaction: discord.Interaction, tube: str | None = None):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message('Use /mortar in the OYB Discord server.',
                                                    ephemeral=True)
            return
        loaded = profiles()
        if not loaded:
            await interaction.response.send_message(
                'No mortar tubes are configured. See `assets/mortar/README.md`.', ephemeral=True)
            return
        await interaction.response.send_modal(
            MortarModal(tube if tube in loaded else next(iter(loaded))))
