"""Member-chosen faction: coloured roles plus a picker in the register channel.

The picked faction is cosmetic — it colours the member's name and (later) brands
their /rank card. It is independent of which side they actually played, which is
tracked separately from the game logs.
"""
import logging

import discord

from bot.discord.interactions import ack, say

LOG = logging.getLogger("reforger.factions")

PICKER_MARKER = "OYB • Faction picker"

# (key, role colour, picker emoji). Colours confirmed with the community.
FACTIONS = (("US", 0x3B5B8C, "🇺🇸"), ("USSR", 0xB23A32, "🇷🇺"), ("FIA", 0x8A7B3F, "🏳️"))
FACTION_NAMES = tuple(name for name, _, _ in FACTIONS)
# Names, colours and emojis published from OYB Control, by key. The keys never
# change: ranks and the rank card go by them.
LOOK = {}


def factions():
    """(key, role colour, emoji) with what OYB Control set laid over the defaults."""
    found = []
    for key, colour, emoji in FACTIONS:
        look = LOOK.get(key) or {}
        if look.get("colour"):
            colour = int(look["colour"][1:], 16)
        found.append((key, colour, look.get("emoji", emoji)))
    return found


def label(key):
    """What a faction is called in Discord."""
    return (LOOK.get(key) or {}).get("name") or key


def current_faction(bot, guild, member):
    """The faction role the member actually holds, if any. Read from roles rather
    than the saved pick so an admin lifting the lock is just removing the role."""
    for name, _, _ in factions():
        role_id = bot.account_links.faction_role(guild.id, name)
        role = guild.get_role(role_id) if role_id else None
        if role is not None and role in member.roles:
            return name
    return None


def held_faction(bot, guild, member_id):
    """The member's faction when only their id is to hand. Their role is the
    truth; the saved pick covers a member the gateway cache has not loaded."""
    member = guild.get_member(member_id)
    if member is not None:
        return current_faction(bot, guild, member)
    return bot.account_links.faction(guild.id, member_id)


async def ensure_faction_roles(bot, guild):
    """Create or adopt the three coloured faction roles and remember their ids.
    Returns what couldn't be done, for OYB Control."""
    links, problems = bot.account_links, []
    for name, colour, _ in factions():
        wanted = label(name)
        role_id = links.faction_role(guild.id, name)
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            role = discord.utils.get(guild.roles, name=wanted) or discord.utils.get(guild.roles, name=name)
        if role is None:
            role = await guild.create_role(name=wanted, colour=discord.Colour(colour),
                                           permissions=discord.Permissions.none(),
                                           mentionable=False, reason="OYB faction role")
        elif role.colour.value != colour or role.name != wanted:
            if role.managed or role >= guild.me.top_role:
                problems.append(f"The {role.name} role sits above the bot's role, so it can't be renamed "
                                "or recoloured; move the bot's role higher.")
            else:
                try:
                    await role.edit(name=wanted, colour=discord.Colour(colour), reason="OYB faction look")
                except discord.HTTPException:
                    LOG.warning("Could not update faction role %s", name)
                    problems.append(f"Couldn't update the {role.name} role; the bot needs Manage Roles.")
        links.save_faction_role(guild.id, name, role.id)
    return problems


async def apply_faction(bot, guild, member, faction):
    """Give the member the chosen faction role, drop the others, and save the pick.

    Returns the role applied, or None when there is no such role to give. The
    caller has to say which, because saving a pick the member cannot see any
    sign of is worse than telling them it did not work.
    """
    links = bot.account_links
    chosen, remove = None, []
    for name, _, _ in factions():
        role_id = links.faction_role(guild.id, name)
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            continue
        if name == faction:
            chosen = role
        elif role in member.roles:
            remove.append(role)
    if chosen is None:
        LOG.warning("No %s role on this server; not saving the pick", faction)
        return None
    if remove:
        await member.remove_roles(*remove, reason="OYB faction change")
    if chosen not in member.roles:
        await member.add_roles(chosen, reason="OYB faction pick")
    links.set_faction(guild.id, member.id, faction)
    return chosen


class FactionView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        for name, _, emoji in factions():
            self.add_item(self._button(name, emoji))

    def _button(self, name, emoji):
        button = discord.ui.Button(label=label(name), emoji=emoji or None, style=discord.ButtonStyle.secondary,
                                   custom_id=f"oyb:faction:{name}")

        async def pick(interaction):
            if interaction.guild_id != self.bot.config.guild_id:
                await say(interaction, "Use this in the OYB server.")
                return
            # Two role calls follow; acknowledge before making them.
            await ack(interaction)
            held = current_faction(self.bot, interaction.guild, interaction.user)
            if held is not None:
                await say(interaction,
                          f"You're locked to **{label(held)}**. Ask an admin if you need to switch sides.")
                return
            try:
                if await apply_faction(self.bot, interaction.guild, interaction.user, name) is None:
                    await say(interaction, f"The **{label(name)}** role is missing on this server. "
                                           'Ask an admin to restart the bot so it can make it.')
                    return
                text = (f"You're **{label(name)}** on Discord now, and locked to it here. You've got "
                        f"access to the {label(name)} channels. Play whatever side you like in game.")
            except discord.Forbidden:
                text = "I need Manage Roles, and my role must sit above the faction roles. Ask an admin."
            await say(interaction, text)

        button.callback = pick
        return button


async def prepare_faction_picker(bot, guild, channel):
    """Ensure the faction roles exist and post/refresh the picker in the channel."""
    await ensure_faction_roles(bot, guild)
    embed = discord.Embed(title="Faction roles — optional, Discord only", colour=0x5865F2, description=(
        "⚠️ **THIS DOES NOT LOCK YOUR FACTION IN GAME.** Play US, USSR or FIA on the "
        "servers whenever you like. This is a Discord role and nothing more.\n\n"
        "⚠️ **It is completely optional.** Skip it and nothing is closed off to you.\n\n"
        "If you do want one, it colours your name here, brands your **/rank** card and "
        "gets you into that faction's channels.\n\n"
        "You are locked to it **on Discord** once you pick — ask an admin if you want to "
        "switch later.\n\n"
        "Welcome to the fight. 🫡"))
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


async def refresh_picker(bot, guild):
    """Redraw the faction picker wherever it was posted, so new names and
    emojis show. Returns whether one was found."""
    from bot.config import faction_channel_id, onboarding_channel_id
    row = bot.account_links.db.execute("SELECT channel FROM join_channel WHERE guild=?", (guild.id,)).fetchone()
    for channel_id in (faction_channel_id(), row[0] if row else None, onboarding_channel_id()):
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            continue
        async for message in channel.history(limit=50):
            if message.author.id == bot.user.id and any(
                    e.footer and e.footer.text == PICKER_MARKER for e in message.embeds):
                await message.edit(view=FactionView(bot), allowed_mentions=discord.AllowedMentions.none())
                return True
    return False
