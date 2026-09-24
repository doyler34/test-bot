"""One panel that takes a new member from arriving to playing.

Three steps in one message: accept the rules and become a member, pick a side,
link a Reforger account. Each button reports where the member stands rather
than silently doing nothing, and My progress shows all three at once.
"""
import logging

import discord

from bot.config import member_role_name, unverified_role_name
from bot.discord.factions import FACTIONS, apply_faction, current_faction, ensure_faction_roles
from bot.discord.interactions import ack, say
from bot.discord.join_oyb import LinkModal

LOG = logging.getLogger('reforger.onboarding')
MARKER = 'OYB • Start here'
# Recorded when somebody chooses to stay Renegade, so the checklist can tell a
# deliberate choice apart from a step nobody has got round to yet.
NO_FACTION = 'NONE'


def by_name(guild, name):
    if not name:
        return None
    matches = [r for r in guild.roles if r.name == name]
    if len(matches) > 1:
        LOG.warning('%s roles named %r; onboarding will not guess', len(matches), name)
        return None
    return matches[0] if matches else None


def grantable(guild, role):
    """Manage Roles alone is not enough: the role has to sit below our own."""
    me = guild.me
    return (role is not None and me is not None and me.guild_permissions.manage_roles
            and not role.managed and role < me.top_role)


async def ensure_member_role(guild):
    """Create the member role if it is missing, the way the rank and faction
    roles are already handled. A new role lands at the bottom of the list,
    which is below the bot's own, so granting it works without anyone having
    to reorder anything by hand.
    """
    name = member_role_name()
    existing = by_name(guild, name)
    if existing is not None:
        return existing
    if any(r.name == name for r in guild.roles):
        return None  # Duplicates: by_name already warned, do not add a third.
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        LOG.warning('Cannot create %r without Manage Roles', name)
        return None
    role = await guild.create_role(name=name, permissions=discord.Permissions.none(),
                                   mentionable=False, reason='OYB onboarding: member role')
    LOG.info('Created the %r role; point your channel permissions at it', name)
    return role


async def ensure_unverified_role(guild):
    """Create the label role if it is wanted and missing. Nothing gates on it,
    so a server that leaves UNVERIFIED_ROLE_NAME blank simply has none."""
    name = unverified_role_name()
    if not name:
        return None
    existing = by_name(guild, name)
    if existing is not None:
        return existing
    if any(r.name == name for r in guild.roles):
        return None
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        LOG.warning('Cannot create %r without Manage Roles', name)
        return None
    return await guild.create_role(name=name, permissions=discord.Permissions.none(),
                                   mentionable=False, reason='OYB onboarding: unverified label')


async def mark_unverified(bot, guild, member):
    """Label somebody who has arrived but not linked yet.

    Only a label. Channel access hangs off the member role instead, because a
    role handed out on join is simply absent while the bot is down, and a gate
    built on an absent role lets everyone through.
    """
    if member.bot:
        return False
    if by_name(guild, member_role_name()) in member.roles:
        return False
    role = by_name(guild, unverified_role_name())
    if not grantable(guild, role) or role in member.roles:
        return False
    try:
        await member.add_roles(role, reason='OYB onboarding: not linked yet')
        return True
    except discord.HTTPException:
        LOG.warning('Could not label %s unverified', member.id)
        return False


async def verify(bot, guild, member):
    """Give the member role, take the unverified one. Returns a message to show."""
    member_role = by_name(guild, member_role_name())
    if member_role is None:
        return ("The member role isn't set up yet, so I can't let you in. "
                "Give an admin a shout.")
    if member_role in member.roles:
        return "You're already verified. Carry on to step 2."
    if not grantable(guild, member_role):
        return ("I can't hand out that role — my own role needs to sit above it. "
                "Give an admin a shout.")
    await member.add_roles(member_role, reason='OYB onboarding: rules accepted')
    unverified = by_name(guild, unverified_role_name())
    if unverified is not None and unverified in member.roles and grantable(guild, unverified):
        try:
            await member.remove_roles(unverified, reason='OYB onboarding: verified')
        except discord.HTTPException:
            LOG.warning('Could not remove %s from %s', unverified, member.id)
    return "You're in. Step 2 — pick your faction."


def progress(bot, guild, member):
    links = bot.account_links
    member_role = by_name(guild, member_role_name())
    verified = member_role is not None and member_role in member.roles
    faction = current_faction(bot, guild, member)
    identities = links.identities(guild.id, member.id)
    chose = links.faction(guild.id, member.id) == NO_FACTION
    if identities:
        lines = [f'✅ **1.** Reforger account linked ({len(identities)})']
    else:
        lines = ['⬜ **1.** Reforger account — ' +
                 links.status(guild.id, member.id).split('.')[0].lower()]
    if faction:
        lines.append(f'✅ **2.** Side — **{faction}**')
    elif chose:
        lines.append('✅ **2.** Side — none, by choice')
    else:
        lines.append('⬜ **2.** Side — not picked')
    if identities and verified:
        lines.append('\nYou are in. ' + bot.rank_sync.status(member.id))
    elif identities and not verified:
        lines.append('\nYour link is in and waiting on an admin.')
    if not faction:
        lines.append('You stay **OYB Renegade** without a side.')
    return '\n'.join(lines)


class OnboardingView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        for name, _, emoji in FACTIONS:
            self.add_item(self._faction(name, emoji))
        self.add_item(self._no_faction())

    async def interaction_check(self, interaction):
        if interaction.guild_id == self.bot.config.guild_id:
            return True
        await say(interaction, 'Use this in the OYB server.')
        return False

    def _faction(self, name, emoji):
        button = discord.ui.Button(label=name, emoji=emoji, row=1,
                                   style=discord.ButtonStyle.secondary,
                                   custom_id=f'oyb:onboard:faction:{name}')

        async def pick(interaction):
            await ack(interaction)
            held = current_faction(self.bot, interaction.guild, interaction.user)
            if held is not None:
                await say(interaction,
                          f"You're locked to **{held}**. Ask an admin if you need to switch sides.")
                return
            try:
                if await apply_faction(self.bot, interaction.guild, interaction.user, name) is None:
                    await say(interaction, f'The **{name}** role is missing on this server. '
                                           'Ask an admin to restart the bot so it can make it.')
                    return
                text = (f"You're **{name}** on Discord now, and locked to it here. That's you "
                        "off Renegade — play whatever side you like in game.")
            except discord.Forbidden:
                text = 'I need Manage Roles, and my role must sit above the faction roles. Ask an admin.'
            await say(interaction, text)

        button.callback = pick
        return button

    def _no_faction(self):
        """Staying Renegade on purpose is a choice, not an unfinished step."""
        button = discord.ui.Button(label='No faction', row=1,
                                   style=discord.ButtonStyle.secondary,
                                   custom_id='oyb:onboard:faction:none')

        async def skip(interaction):
            await ack(interaction)
            held = current_faction(self.bot, interaction.guild, interaction.user)
            if held is not None:
                await say(interaction,
                          f"You're already **{held}**. Ask an admin if you want that removed.")
                return
            self.bot.account_links.set_faction(interaction.guild_id, interaction.user.id, NO_FACTION)
            await say(interaction, 'No side for you then. You stay **OYB Renegade** — press a '
                                   'faction any time you change your mind.')

        button.callback = skip
        return button

    @discord.ui.button(label='1. Link Reforger account', row=0, style=discord.ButtonStyle.success,
                       custom_id='oyb:onboard:link')
    async def link(self, interaction, button):
        await interaction.response.send_modal(LinkModal(self.bot))

    @discord.ui.button(label='My progress', row=0, custom_id='oyb:onboard:progress')
    async def mine(self, interaction, button):
        await ack(interaction)
        await say(interaction, progress(self.bot, interaction.guild, interaction.user))


def panel_embed():
    return discord.Embed(title='Start here', colour=0xA9BC8C, description=(
        '**1. Link your Reforger account.** Play a round on one of our servers first so we can '
        'find you, then press the button and put in your in-game name or your player ID.\n\n'
        'If the name is yours and nobody has claimed it, you are in straight away. If we cannot '
        'match it, it goes to an admin to sort out.\n\n'
        '**This is what opens up the rest of the server**, and it starts your kills, deaths and '
        'playtime counting towards your rank and the leaderboards.\n\n'
        '**2. Pick your side** — US, USSR or FIA. A Discord role only: it colours your name, '
        'gets you into that side\'s channels and takes you off **OYB Renegade**. It does not '
        'pick your faction in game, so play whatever you like on the servers. You are locked '
        'to it here once you pick, so go with the one your mates are on. Not fussed? Press '
        '**No faction** and stay Renegade.\n\n'
        'Stuck? Press **My progress** to see what you still need.')
    ).set_footer(text=MARKER)


async def prepare_onboarding(bot, guild, channel):
    """Post or refresh the panel. Edits our own message rather than piling up."""
    await ensure_member_role(guild)
    await ensure_unverified_role(guild)
    # The panel offers the faction buttons, so it owns making sure the roles
    # they hand out exist. They used to be created only by the separate faction
    # picker, which a server without one configured never sets up.
    await ensure_faction_roles(bot, guild)
    embed, view = panel_embed(), OnboardingView(bot)
    async for message in channel.history(limit=50):
        if message.author.id == bot.user.id and any(
                e.footer and e.footer.text == MARKER for e in message.embeds):
            await message.edit(embed=embed, view=view,
                               allowed_mentions=discord.AllowedMentions.none())
            return message
    return await channel.send(embed=embed, view=view, silent=True,
                              allowed_mentions=discord.AllowedMentions.none())
