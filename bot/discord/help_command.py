"""Guild /help command: a page for every command, with Previous and Next."""
import logging

import discord
from discord import app_commands

from bot.config import linking_channel

LOG = logging.getLogger("reforger.help")
COLOUR = 0xA9BC8C
TIMEOUT = 300


def explanations():
    link = f"Link your Reforger account first: open {linking_channel()}, press **Link Reforger account** and submit your in-game name. An admin approves it."
    return {
        "rank": ("Shows your OYB rank card as a picture: your rank and XP, your time played and your faction.\n\n" + link +
                 "\n\nYou can use it once every 10 seconds, and the card clears itself after a few minutes."),
        "stats": ("This week's combat stats: player kills, deaths, K/D, longest kill and teamkills. Only player-vs-player "
                  "counts: deaths to AI and suicides are left out. The week starts again every Monday.\n\n"
                  "Pick a member in the **user** box to look at someone else's. The **Last 10 games** button under the "
                  "card shows your last ten games, privately, so only you see it.\n\n" + link),
        "lifetime": ("The same card as /stats, for all time: every game recorded on OYB servers, with no weekly reset.\n\n"
                     "Pick a member in the **user** box to look at someone else's.\n\n" + link),
        "mortar": ("Works out a mortar firing solution between two grids. When the mortar map is up it gives you a "
                   "private link: tap your mortar, then the target. Otherwise it asks you to type the two grids.\n\n"
                   "Only you see the answer. If more than one mortar tube is set up, you can name one in the **tube** box."),
        "help": "This list. Use Next and Previous to go through the commands.",
    }


def pages(commands):
    """The overview, then one page per command, from what is registered right now."""
    detail = explanations()
    order = [name for name in detail if name in commands] + sorted(set(commands) - set(detail))
    total = len(order) + 1
    overview = discord.Embed(title="OYB BOT COMMANDS", colour=COLOUR, description="\n".join(
        f"**/{name}**: {commands[name]}" for name in order))
    overview.set_footer(text=f"O.Y.B • Page 1/{total} • Tap Next to go through each command")
    result = [overview]
    for number, name in enumerate(order, start=2):
        embed = discord.Embed(title=f"/{name}", colour=COLOUR, description=detail.get(name) or commands[name])
        embed.set_footer(text=f"O.Y.B • Page {number}/{total}")
        result.append(embed)
    return result


class HelpView(discord.ui.View):
    def __init__(self, embeds):
        super().__init__(timeout=TIMEOUT)
        self.embeds = embeds
        self.index = 0
        self.refresh()

    def refresh(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.embeds) - 1

    async def turn(self, interaction, step):
        self.index = min(max(self.index + step, 0), len(self.embeds) - 1)
        self.refresh()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction, button):
        await self.turn(interaction, 1)


class HelpCommand:
    def __init__(self, bot):
        self.bot = bot
        self.command = app_commands.Command(name="help", description="Show every OYB bot command and what it does",
                                            callback=self.show)
        app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))(self.command)
        self.command.error(self.error)
        bot.rank_command.tree.add_command(self.command, guild=bot.rank_command.guild)

    def commands(self):
        tree, guild = self.bot.rank_command.tree, self.bot.rank_command.guild
        return {command.name: command.description for command in tree.get_commands(guild=guild)}

    async def error(self, interaction, error):
        text = (f"Try /help again in {error.retry_after:.0f} seconds."
                if isinstance(error, app_commands.CommandOnCooldown)
                else "The command list could not be loaded. Please try again shortly.")
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def show(self, interaction: discord.Interaction):
        embeds = pages(self.commands())
        await interaction.response.send_message(embed=embeds[0], view=HelpView(embeds), ephemeral=True)
