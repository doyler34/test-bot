"""Private staff channel that alerts on new link requests with inline approve/reject.

Views are persistent (fixed custom_ids plus the request token in the alert
footer), so they keep working across restarts.
"""
from datetime import timedelta
import logging

import discord

from bot.discord.interactions import ack, say, update

from bot.storage.account_links import LinkConflict

LOG = logging.getLogger("reforger.link_review")

REVIEW_MARKER = "OYB • Link request review (staff only)"
CONTROL_MARKER = "OYB • Link request alerts control"
TOKEN_PREFIX = "OYB • Link request • "
HANDLED_MARKER = TOKEN_PREFIX + "handled"
REVIEWER_ROLE_NAME = "OYB Link Reviewer"
STAFF_PERMS = ("administrator", "manage_guild", "manage_channels", "manage_messages", "moderate_members")


def can_review(interaction, guild_id):
    return (interaction.guild_id == guild_id and
            (interaction.permissions.manage_guild or interaction.permissions.administrator))


def _is_staff_role(role):
    return not role.is_default() and any(getattr(role.permissions, flag, False) for flag in STAFF_PERMS)


def _overwrites(guild, reviewer_role=None):
    result = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, read_message_history=True,
                                              send_messages=True, embed_links=True, manage_messages=True),
    }
    for role in guild.roles:
        if _is_staff_role(role):
            result[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
    if reviewer_role is not None:
        result[reviewer_role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True, send_messages=False)
    return result


class AdminPanelView(discord.ui.View):
    """Every admin control in one pinned message, in the staff-only channel.

    These used to sit on the public #join-oyb panel where members could see
    them, so they live here now and that panel keeps only member buttons.
    """

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _guard(self, interaction, what):
        if can_review(interaction, self.bot.config.guild_id):
            return True
        await say(interaction, f"Only staff with Manage Server can {what}.")
        return False

    @discord.ui.button(label="Pending requests", emoji="📋", style=discord.ButtonStyle.primary,
                       custom_id="oyb:admin:pending")
    async def pending(self, interaction, button):
        await ack(interaction)
        if not await self._guard(interaction, "review requests"):
            return
        from bot.discord.join_oyb import ReviewList
        rows = self.bot.account_links.pending(interaction.guild_id)
        await say(interaction,
                  "Pending requests (up to 25; reopen after reviewing for more)." if rows else "No pending requests.",
                  view=ReviewList(self.bot, rows, interaction.user.id) if rows else None)

    @discord.ui.button(label="Force-link", emoji="➕", style=discord.ButtonStyle.secondary,
                       custom_id="oyb:admin:forcelink")
    async def force(self, interaction, button):
        if not await self._guard(interaction, "force-link"):
            return
        from bot.discord.join_oyb import ForceLinkModal
        await interaction.response.send_modal(ForceLinkModal(self.bot, interaction.user.id))

    @discord.ui.button(label="Remove a link", emoji="🗑", style=discord.ButtonStyle.danger,
                       custom_id="oyb:admin:unlink")
    async def unlink(self, interaction, button):
        await ack(interaction)
        if not await self._guard(interaction, "remove links"):
            return
        from bot.discord.join_oyb import UnlinkView
        await say(interaction, "Remove a member's Reforger link (use for abuse or a bad link).",
                  view=UnlinkView(self.bot, interaction.user.id))

    @discord.ui.button(label="Toggle my alerts", emoji="🔔", style=discord.ButtonStyle.secondary,
                       custom_id="oyb:linkalerts:toggle")
    async def toggle(self, interaction, button):
        await ack(interaction)
        if not can_review(interaction, self.bot.config.guild_id):
            await say(interaction, "Only staff with Manage Server can subscribe.")
            return
        cfg = self.bot.account_links.review_settings(interaction.guild_id)
        role = interaction.guild.get_role(cfg["reviewer_role"]) if cfg["reviewer_role"] else None
        if role is None:
            await say(interaction, "Reviewer role is missing; ask an admin to restart the bot.")
            return
        member = interaction.user
        try:
            if role in getattr(member, "roles", []):
                await member.remove_roles(role, reason="OYB link alerts opt-out")
                text = "🔕 You'll no longer be pinged for new link requests."
            else:
                await member.add_roles(role, reason="OYB link alerts opt-in")
                text = "🔔 You'll be pinged here on each new link request."
        except discord.Forbidden:
            text = "I need Manage Roles, and my role must sit above the reviewer role."
        await say(interaction, text)


class ReviewButtons(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    def _token(self, message):
        for embed in message.embeds:
            text = embed.footer.text if embed.footer else None
            if text and text.startswith(TOKEN_PREFIX):
                return text[len(TOKEN_PREFIX):].strip()
        return None

    async def _settle_conflict(self, interaction, token, reason):
        """A request that cannot be approved is finished, so close it here.

        Leaving it pending is what stranded requests before: the queue kept
        showing work nobody could action. The member is told directly, because
        they cannot see this channel.
        """
        links = self.bot.account_links
        request = links.request(interaction.guild_id, token)
        if request and request[3] == "pending":
            try:
                links.review(interaction.guild_id, token, interaction.user.id, False)
            except LinkConflict:
                pass  # settled by someone else in the meantime; nothing to close
            await notify_member(self.bot, interaction.guild, request[0], reason)
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed is not None:
            embed.colour = discord.Colour(0xE74C3C)
            embed.add_field(name="🚫 Auto-rejected", value=reason, inline=False)
            embed.set_footer(text=HANDLED_MARKER)
            await interaction.message.edit(embed=embed, view=None,
                                           allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.message.edit(view=None)

    async def _decide(self, interaction, approve):
        await ack(interaction)
        if not can_review(interaction, self.bot.config.guild_id):
            await say(interaction, "Only staff with Manage Server can review requests.")
            return
        token = self._token(interaction.message)
        if not token:
            await say(interaction, "This request has already been handled.")
            return
        try:
            self.bot.account_links.review(interaction.guild_id, token, interaction.user.id, approve)
        except LinkConflict as exc:
            await say(interaction, str(exc))
            await self._settle_conflict(interaction, token, str(exc))
            return
        embed = interaction.message.embeds[0]
        embed.colour = discord.Colour(0x2ECC71 if approve else 0xE74C3C)
        embed.add_field(name="✅ Approved" if approve else "🚫 Rejected",
                        value=f"by <@{interaction.user.id}>", inline=False)
        embed.set_footer(text="OYB • Link request • handled")
        await update(interaction, embed=embed, view=None,
                     allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="oyb:linkreview:approve")
    async def approve(self, interaction, button):
        await self._decide(interaction, True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="oyb:linkreview:reject")
    async def reject(self, interaction, button):
        await self._decide(interaction, False)


async def notify_member(bot, guild, discord_id, reason):
    """Tell a member their request was closed. DM first; if their DMs are shut,
    say it in the join channel where they asked, so the answer still reaches
    them. Never raises - a failed notice must not break the review."""
    text = (f"Your OYB Reforger link request could not be completed: {reason}\n"
            "Ask an admin if you think that is wrong.")
    member = guild.get_member(discord_id) if guild else None
    if member is not None:
        try:
            await member.send(text)
            return True
        except discord.HTTPException:
            LOG.info("DMs closed for %s; falling back to the join channel", discord_id)
    row = bot.account_links.db.execute(
        "SELECT channel FROM join_channel WHERE guild=?", (guild.id,)).fetchone() if guild else None
    channel = guild.get_channel(row[0]) if row and row[0] else None
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(content=f"<@{discord_id}> {text}", silent=True,
                               allowed_mentions=discord.AllowedMentions(users=True))
            return True
        except discord.HTTPException:
            LOG.warning("Could not tell %s their request was closed", discord_id)
    return False


def admin_panel_embed(links, guild_id):
    """The pinned staff panel. pending() caps at 25, so a full page reads 25+."""
    waiting = len(links.pending(guild_id))
    queue = "nothing waiting" if not waiting else f"**{waiting}{'+' if waiting >= 25 else ''}** waiting for review"
    embed = discord.Embed(title="OYB admin panel", colour=0x5865F2, description=(
        f"Link requests: {queue}.\n\n"
        "📋 **Pending requests** — work through the queue.\n"
        "➕ **Force-link** — link a member yourself when the name lookup can't.\n"
        "🗑 **Remove a link** — unlink an account; tracked playtime is kept.\n"
        "🔔 **Toggle my alerts** — start or stop being pinged on each new request.\n\n"
        "New requests also post below with **Approve / Reject** on them. "
        "Only staff can see this channel."))
    embed.set_footer(text=CONTROL_MARKER)
    return embed


async def prepare_review_channel(bot, guild):
    """Ensure the staff-only review channel, reviewer role and control message exist."""
    links = bot.account_links
    cfg = links.review_settings(guild.id)

    role = guild.get_role(cfg["reviewer_role"]) if cfg["reviewer_role"] else None
    if role is None:
        role = discord.utils.get(guild.roles, name=REVIEWER_ROLE_NAME)
    if role is None:
        # Mentionable so the opt-in ping reliably notifies without Mention-Everyone.
        role = await guild.create_role(name=REVIEWER_ROLE_NAME, permissions=discord.Permissions.none(),
                                       mentionable=True, reason="OYB link-request reviewers (opt-in pings)")

    channel = guild.get_channel(cfg["channel"]) if cfg["channel"] else None
    if not isinstance(channel, discord.TextChannel) or channel.topic != REVIEW_MARKER:
        channel = discord.utils.get(guild.text_channels, topic=REVIEW_MARKER)
    desired = _overwrites(guild, role)
    if channel is None:
        channel = await guild.create_text_channel("oyb-link-requests", topic=REVIEW_MARKER,
                                                  overwrites=desired, reason="OYB private link-request review")
    elif channel.overwrites != desired:
        await channel.edit(overwrites=desired, reason="Keep OYB link-request channel staff-only")
    links.save_review_settings(guild.id, channel=channel.id, reviewer_role=role.id)

    control = None
    if cfg["control"]:
        try:
            control = await channel.fetch_message(cfg["control"])
        except discord.NotFound:
            control = None
    if control is None:
        async for message in channel.history(limit=50):
            if message.author.id == bot.user.id and any(
                    e.footer.text == CONTROL_MARKER for e in message.embeds):
                control = message
                break
    embed = admin_panel_embed(links, guild.id)
    if control is None:
        control = await channel.send(embed=embed, view=AdminPanelView(bot),
                                     allowed_mentions=discord.AllowedMentions.none())
    else:
        await control.edit(embed=embed, view=AdminPanelView(bot),
                           allowed_mentions=discord.AllowedMentions.none())
    if not control.pinned:
        try:
            await control.pin(reason="OYB link-request control")
        except discord.HTTPException:
            pass
    links.save_review_settings(guild.id, control=control.id)
    return channel


async def post_request_alert(bot, guild, token):
    """Announce a new pending link request in the staff channel; safe to fail."""
    links = bot.account_links
    cfg = links.review_settings(guild.id)
    if not cfg["channel"]:
        LOG.warning("No review channel configured yet; request %s not announced", token)
        return
    channel = guild.get_channel(cfg["channel"])
    if not isinstance(channel, discord.TextChannel):
        LOG.warning("Review channel unavailable; request %s not announced", token)
        return
    request = links.request(guild.id, token)
    if not request:
        return
    discord_id, identity, name, _status, discord_name = request
    role = guild.get_role(cfg["reviewer_role"]) if cfg["reviewer_role"] else None
    who = discord.utils.escape_markdown(discord_name) + " " if discord_name else ""
    embed = discord.Embed(title="🔗 New link request", colour=0xF1C40F, description=(
        f"**Member:** {who}<@{discord_id}> (`{discord_id}`)\n"
        f"**Reforger name:** {discord.utils.escape_markdown(name)}\n"
        f"**Game identity:** `{identity}`\n\n"
        "Confirm ownership in-game before approving — a matching name alone is not proof."))
    embed.set_footer(text=TOKEN_PREFIX + token)
    await channel.send(
        content=role.mention if role else None, embed=embed, view=ReviewButtons(bot),
        allowed_mentions=discord.AllowedMentions(everyone=False, users=False,
                                                 roles=[role] if role else False, replied_user=False))


async def prune_handled(bot, guild, older_than=86400):
    """Delete approved/rejected request alerts older than a day so the staff
    channel doesn't clog. Pending requests and the control message are kept."""
    cfg = bot.account_links.review_settings(guild.id)
    channel = guild.get_channel(cfg["channel"]) if cfg["channel"] else None
    if not isinstance(channel, discord.TextChannel):
        return 0
    cutoff = discord.utils.utcnow() - timedelta(seconds=older_than)
    removed = 0
    try:
        async for message in channel.history(limit=200, before=cutoff):
            if message.author.id != bot.user.id or message.id == cfg["control"]:
                continue
            if any(e.footer and e.footer.text == HANDLED_MARKER for e in message.embeds):
                try:
                    await message.delete()
                    removed += 1
                except discord.HTTPException:
                    pass
    except discord.HTTPException:
        LOG.warning("Could not sweep the link-request channel; will retry")
    return removed
