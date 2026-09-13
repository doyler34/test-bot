"""Member-chosen faction: coloured roles plus a picker in the register channel.

The picked faction is cosmetic — it colours the member's name and (later) brands
their /rank card. It is independent of which side they actually played, which is
tracked separately from the game logs.
"""
import logging

import discord

LOG = logging.getLogger("reforger.factions")

PICKER_MARKER = "OYB • Faction picker"

# (name, role colour, picker emoji). Colours confirmed with the community.
FACTIONS = (("US", 0x3B5B8C, "🇺🇸"), ("USSR", 0xB23A32, "🚩"), ("FIA", 0x8A7B3F, "🏴"))
FACTION_NAMES = tuple(name for name, _, _ in FACTIONS)


async def ensure_faction_roles(bot, guild):
    """Create or adopt the three coloured faction roles and remember their ids."""
    links = bot.account_links
    for name, colour, _ in FACTIONS:
        role_id = links.faction_role(guild.id, name)
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            role = discord.utils.get(guild.roles, name=name)
        if role is None:
            role = await guild.create_role(name=name, colour=discord.Colour(colour),
                                           permissions=discord.Permissions.none(),
                                           mentionable=False, reason="OYB faction role")
        elif (role.colour.value != colour and not role.managed
              and role < guild.me.top_role):
            try:
                await role.edit(colour=discord.Colour(colour), reason="OYB faction colour")
            except discord.HTTPException:
                LOG.warning("Could not recolour faction role %s", name)
        links.save_faction_role(guild.id, name, role.id)


async def apply_faction(bot, guild, member, faction):
    """Give the member the chosen faction role, drop the others, and save the pick."""
    links = bot.account_links
    chosen, remove = None, []
    for name, _, _ in FACTIONS:
        role_id = links.faction_role(guild.id, name)
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            continue
        if name == faction:
            chosen = role
        elif role in member.roles:
            remove.append(role)
    if remove:
        await member.remove_roles(*remove, reason="OYB faction change")
    if chosen is not None and chosen not in member.roles:
        await member.add_roles(chosen, reason="OYB faction pick")
    links.set_faction(guild.id, member.id, faction)


class FactionView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        for name, _, emoji in FACTIONS:
            self.add_item(self._button(name, emoji))

    def _button(self, name, emoji):
        button = discord.ui.Button(label=name, emoji=emoji, style=discord.ButtonStyle.secondary,
                                   custom_id=f"oyb:faction:{name}")

        async def pick(interaction):
            if interaction.guild_id != self.bot.config.guild_id:
                await interaction.response.send_message("Use this in the OYB server.", ephemeral=True)
                return
            try:
                await apply_faction(self.bot, interaction.guild, interaction.user, name)
                text = f"You're now flying **{name}** colours. Switch anytime — your stats and XP are unaffected."
            except discord.Forbidden:
                text = "I need Manage Roles, and my role must sit above the faction roles. Ask an admin."
            await interaction.response.send_message(text, ephemeral=True)

        button.callback = pick
        return button


async def prepare_faction_picker(bot, guild, channel):
    """Ensure the faction roles exist and post/refresh the picker in the channel."""
    await ensure_faction_roles(bot, guild)
    embed = discord.Embed(title="Pick your faction", colour=0x5865F2, description=(
        "Choose the side you fight for. This colours your name and brands your **/rank** card.\n\n"
        "You can switch anytime, and it doesn't affect your stats or XP."))
    embed.set_footer(text=PICKER_MARKER)
    existing = None
    async for message in channel.history(limit=50):
        if message.author.id == bot.user.id and any(
                e.footer and e.footer.text == PICKER_MARKER for e in message.embeds):
            existing = message
            break
    if existing is None:
        await channel.send(embed=embed, view=FactionView(bot), silent=True,
                           allowed_mentions=discord.AllowedMentions.none())
    else:
        await existing.edit(embed=embed, view=FactionView(bot),
                            allowed_mentions=discord.AllowedMentions.none())
