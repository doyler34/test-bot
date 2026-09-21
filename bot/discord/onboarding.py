"""One panel that takes a new member from arriving to playing.

Three steps in one message: accept the rules and become a member, pick a side,
link a Reforger account. Each button reports where the member stands rather
than silently doing nothing, and My progress shows all three at once.
"""
import logging

import discord

from bot.config import member_role_name, unverified_role_name
from bot.discord.factions import FACTIONS, apply_faction, current_faction
from bot.discord.interactions import ack, say
from bot.discord.join_oyb import LinkModal

LOG = logging.getLogger('reforger.onboarding')
MARKER = 'OYB • Start here'


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
    lines = [f"{'✅' if verified else '⬜'} **1.** Rules accepted",
             f"{'✅' if faction else '⬜'} **2.** Faction" + (f" — **{faction}**" if faction else '')]
    if identities:
        lines.append(f"✅ **3.** Reforger account linked ({len(identities)})")
    else:
        lines.append('⬜ **3.** Reforger account — ' +
                     links.status(guild.id, member.id).split('.')[0].lower())
    if verified and faction and identities:
        lines.append('\nAll done. ' + bot.rank_sync.status(member.id))
    elif not faction:
        lines.append('\nYou stay **OYB Renegade** until you pick a side.')
    return '\n'.join(lines)


class OnboardingView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        for name, _, emoji in FACTIONS:
            self.add_item(self._faction(name, emoji))

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
                await apply_faction(self.bot, interaction.guild, interaction.user, name)
                text = (f"You're **{name}** now, and locked to it. That's you off Renegade — "
                        'step 3, link your game account.')
            except discord.Forbidden:
                text = 'I need Manage Roles, and my role must sit above the faction roles. Ask an admin.'
            await say(interaction, text)

        button.callback = pick
        return button

    @discord.ui.button(label='1. Accept the rules', row=0, style=discord.ButtonStyle.success,
                       custom_id='oyb:onboard:verify')
    async def accept(self, interaction, button):
        await ack(interaction)
        try:
            text = await verify(self.bot, interaction.guild, interaction.user)
        except discord.Forbidden:
            text = 'I need Manage Roles for that, and my role must sit above the member role.'
        await say(interaction, text)

    @discord.ui.button(label='3. Link Reforger account', row=0, style=discord.ButtonStyle.primary,
                       custom_id='oyb:onboard:link')
    async def link(self, interaction, button):
        await interaction.response.send_modal(LinkModal(self.bot))

    @discord.ui.button(label='My progress', row=0, custom_id='oyb:onboard:progress')
    async def mine(self, interaction, button):
        await ack(interaction)
        await say(interaction, progress(self.bot, interaction.guild, interaction.user))


def panel_embed():
    return discord.Embed(title='Start here', colour=0xA9BC8C, description=(
        'Three steps and you\'re playing.\n\n'
        '**1. Accept the rules.** Opens up the rest of the server.\n\n'
        '**2. Pick your faction** — US, USSR or FIA. Colours your name, gets you into that '
        'side\'s channels, and takes you off **OYB Renegade**. You\'re locked to it after, so '
        'pick the one your mates are on.\n\n'
        '**3. Link your Reforger account.** Put in your in-game name or your player ID. An '
        'admin approves it and your kills, deaths and playtime start counting towards your rank '
        'and the leaderboards.\n\n'
        'Stuck? Press **My progress** to see what you still need.')
    ).set_footer(text=MARKER)


async def prepare_onboarding(bot, guild, channel):
    """Post or refresh the panel. Edits our own message rather than piling up."""
    embed, view = panel_embed(), OnboardingView(bot)
    async for message in channel.history(limit=50):
        if message.author.id == bot.user.id and any(
                e.footer and e.footer.text == MARKER for e in message.embeds):
            await message.edit(embed=embed, view=view,
                               allowed_mentions=discord.AllowedMentions.none())
            return message
    return await channel.send(embed=embed, view=view, silent=True,
                              allowed_mentions=discord.AllowedMentions.none())
