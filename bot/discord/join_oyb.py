"""Read-only account-linking channel with private submissions and admin review."""
import logging
import os
from pathlib import Path
import sqlite3
import uuid
from contextlib import closing
import discord
from bot.storage.account_links import LinkConflict

LOG = logging.getLogger("reforger.join_oyb")
MARKER = "OYB • Verified Reforger account linking"


def can_review(interaction, guild_id):
    return (interaction.guild_id == guild_id and
            (interaction.permissions.manage_guild or interaction.permissions.administrator))


def find_identity(path, name):
    if not Path(path).is_file():
        raise ValueError("Join an OYB game server first so the tracker can see your name, then try again.")
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT DISTINCT identity,name FROM totals").fetchall()
    matches = {identity for identity, known in rows if known.casefold() == name.strip().casefold()}
    if not matches:
        raise ValueError("Name not found. Join an OYB game server, then enter your exact in-game name.")
    if len(matches) != 1:
        raise ValueError("More than one player has that name. Enter your identity ID instead — "
                         "it is on your Reforger profile page.")
    return matches.pop()


def find_by_identity(path, text):
    """Accept a pasted identity ID, but only one the tracker has actually seen.

    A typo would otherwise create a request against an account that has never
    played here, which no admin could sensibly judge.
    """
    identity = str(uuid.UUID(text.strip()))
    if not Path(path).is_file():
        raise ValueError("Join an OYB game server first so the tracker can see you, then try again.")
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        seen = db.execute("SELECT 1 FROM totals WHERE identity=? LIMIT 1", (identity,)).fetchone()
    if not seen:
        raise ValueError("That identity ID has never played on an OYB server. Check it and try again.")
    return identity


def resolve_identity(path, text):
    """Take whichever the member typed: an identity ID, or their in-game name."""
    try:
        uuid.UUID(text.strip())
    except (ValueError, AttributeError):
        return find_identity(path, text)
    return find_by_identity(path, text)


def known_name(path, identity):
    """The most-played name the tracker has for an identity, so a request made
    by identity ID still shows an admin who they are looking at."""
    if not Path(path).is_file():
        return None
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT name FROM totals WHERE identity=? GROUP BY name "
                         "ORDER BY SUM(seconds) DESC LIMIT 1", (identity,)).fetchone()
    return row[0] if row else None


def find_candidates(path, name):
    """Every game account seen under a name, with playtime and servers so an
    admin can tell duplicate names apart. Ordered most-played first."""
    if not Path(path).is_file():
        return []
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT identity, SUM(seconds), GROUP_CONCAT(DISTINCT server) FROM totals "
            "WHERE name = ? COLLATE NOCASE GROUP BY identity ORDER BY SUM(seconds) DESC",
            (name.strip(),)).fetchall()
    return [dict(identity=r[0], seconds=r[1] or 0, servers=r[2] or "") for r in rows]


def play_summary(candidate):
    minutes = int(candidate.get("seconds", 0) // 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m played" if hours else f"{minutes} min played"


class LinkModal(discord.ui.Modal, title="Link your Reforger account"):
    name_input = discord.ui.TextInput(
        label="In-game name, or your identity ID",
        placeholder="GazLagom    —or—    362be24b-cb0b-4539-bcf2-efb896c767db",
        min_length=1, max_length=100)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message("Use this in the OYB server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            typed = str(self.name_input).strip()
            identity = resolve_identity(os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"), typed)
            name = known_name(os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"), identity) or typed
            token = self.bot.account_links.submit(interaction.guild_id, interaction.user.id, identity, name,
                                                  interaction.user.display_name)
            try:
                from bot.discord.link_review import post_request_alert
                await post_request_alert(self.bot, interaction.guild, token)
            except Exception:
                LOG.exception("Could not post link-request alert for %s", interaction.user.id)
            text = "Request submitted for admin approval. Use My link status to check progress. Your tracked time is preserved."
        except (ValueError, LinkConflict) as exc:
            text = str(exc)
        except sqlite3.Error:
            text = "The playtime tracker is not ready yet. Please try again shortly."
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ReviewDecision(discord.ui.View):
    def __init__(self, bot, token, owner):
        super().__init__(timeout=180)
        self.bot, self.token, self.owner = bot, token, owner

    async def decide(self, interaction, approve):
        if not can_review(interaction, self.bot.config.guild_id) or interaction.user.id != self.owner:
            await interaction.response.send_message("An authorised admin must review this request.", ephemeral=True)
            return
        try:
            self.bot.account_links.review(interaction.guild_id, self.token, interaction.user.id, approve)
            text = "Account link approved." if approve else "Request rejected."
        except LinkConflict as exc:
            text = str(exc)
        await interaction.response.edit_message(content=text, view=None, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Approve verified owner", style=discord.ButtonStyle.success)
    async def approve(self, interaction, button):
        await self.decide(interaction, True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction, button):
        await self.decide(interaction, False)


class ReviewList(discord.ui.View):
    def __init__(self, bot, rows, owner):
        super().__init__(timeout=180)
        self.bot, self.rows, self.owner = bot, {r[0]: r for r in rows}, owner
        select = discord.ui.Select(placeholder="Choose a linking request", options=[
            discord.SelectOption(label=r[3][:100], description=(r[4] or f"Discord ID: {r[1]}")[:100], value=r[0])
            for r in rows])

        async def selected(interaction):
            if not can_review(interaction, bot.config.guild_id) or interaction.user.id != owner:
                await interaction.response.send_message("Admin access required.", ephemeral=True)
                return
            token = select.values[0]
            _, member, identity, name, discord_name = self.rows[token]
            who = discord.utils.escape_markdown(discord_name) + " " if discord_name else ""
            text = (f"Discord account: {who}<@{member}> (`{member}`)\n"
                    f"Reforger name: {discord.utils.escape_markdown(name)}\n"
                    f"Game identity: `{identity}`\n\n"
                    "Confirm ownership with the player in-game before approving. A matching name alone is not verification.")
            await interaction.response.edit_message(content=text, view=ReviewDecision(bot, token, owner),
                                                    allowed_mentions=discord.AllowedMentions.none())
        select.callback = selected
        self.add_item(select)


async def _strip_rank_roles(bot, guild, member_id):
    """Take back any rank roles after an unlink so an abused account loses them."""
    sync = getattr(bot, "rank_sync", None)
    if sync is None:
        return
    sync.applied.pop(member_id, None)
    managed = [r for r in getattr(sync, "roles", []) if r is not None]
    if not managed:
        return
    try:
        member = await guild.fetch_member(member_id)
    except discord.HTTPException:
        return
    held = [r for r in managed if r in member.roles]
    if held:
        try:
            await member.remove_roles(*held, reason="OYB link removed by admin")
        except discord.HTTPException:
            LOG.warning("Could not remove rank roles from %s after unlink", member_id)


class UnlinkView(discord.ui.View):
    """Admin picks a member; a matching link is removed after a confirmation step."""
    def __init__(self, bot, owner):
        super().__init__(timeout=180)
        self.bot, self.owner = bot, owner
        select = discord.ui.UserSelect(placeholder="Choose the member to unlink", min_values=1, max_values=1)

        async def chosen(interaction):
            if not can_review(interaction, bot.config.guild_id) or interaction.user.id != owner:
                await interaction.response.send_message("Admin access required.", ephemeral=True)
                return
            target = select.values[0]
            identity = bot.account_links.lookup(interaction.guild_id, target.id)
            if not identity:
                await interaction.response.edit_message(
                    content=f"{target.mention} has no linked Reforger account.", view=None,
                    allowed_mentions=discord.AllowedMentions.none())
                return
            text = (f"Remove the link for {target.mention} (`{target.id}`)?\n"
                    f"Game identity: `{identity}`\n\n"
                    "Their tracked playtime and XP stay with the game account, so a genuine "
                    "owner can re-link later. Rank roles are removed now.")
            await interaction.response.edit_message(content=text,
                                                    view=ConfirmUnlink(bot, owner, target.id),
                                                    allowed_mentions=discord.AllowedMentions.none())
        select.callback = chosen
        self.add_item(select)


class ConfirmUnlink(discord.ui.View):
    def __init__(self, bot, owner, target_id):
        super().__init__(timeout=180)
        self.bot, self.owner, self.target_id = bot, owner, target_id

    @discord.ui.button(label="Remove link", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if not can_review(interaction, self.bot.config.guild_id) or interaction.user.id != self.owner:
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        identity = self.bot.account_links.unlink(interaction.guild_id, self.target_id)
        if identity is None:
            text = "That link was already removed."
        else:
            await _strip_rank_roles(self.bot, interaction.guild, self.target_id)
            text = f"Removed the link for <@{self.target_id}> (was `{identity}`)."
        await interaction.response.edit_message(content=text, view=None,
                                                allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled. No link was removed.", view=None,
                                                allowed_mentions=discord.AllowedMentions.none())


class ForceLinkChoice(discord.ui.View):
    """Admin picks the correct game account for a Discord member and links it."""
    def __init__(self, bot, owner, discord_id, candidates):
        super().__init__(timeout=180)
        self.bot, self.owner, self.discord_id = bot, owner, discord_id
        options = [discord.SelectOption(
            label=play_summary(c)[:100],
            description=f"{c['servers'] or 'unknown server'} · {c['identity'][:8]}…"[:100],
            value=c["identity"]) for c in candidates[:25]]
        select = discord.ui.Select(placeholder="Pick the correct account", options=options)

        async def chosen(interaction):
            if not can_review(interaction, bot.config.guild_id) or interaction.user.id != owner:
                await interaction.response.send_message("Admin access required.", ephemeral=True)
                return
            identity = select.values[0]
            try:
                bot.account_links.verified_link(interaction.guild_id, self.discord_id, identity,
                                                f"admin:{interaction.user.id}")
                text = f"✅ Linked <@{self.discord_id}> to `{identity}`."
            except LinkConflict as exc:
                text = f"⚠️ {exc}\nUse **Admin: remove a link** first if you need to move it."
            except (ValueError, sqlite3.Error) as exc:
                text = f"Could not link: {exc}"
            await interaction.response.edit_message(content=text, view=None,
                                                    allowed_mentions=discord.AllowedMentions.none())
        select.callback = chosen
        self.add_item(select)


class ForceLinkModal(discord.ui.Modal, title="Force-link a Reforger account"):
    member_id = discord.ui.TextInput(label="Discord user ID", min_length=5, max_length=25)
    name_input = discord.ui.TextInput(label="Exact in-game name", min_length=1, max_length=100)

    def __init__(self, bot, owner):
        super().__init__()
        self.bot, self.owner = bot, owner

    async def on_submit(self, interaction):
        if not can_review(interaction, self.bot.config.guild_id) or interaction.user.id != self.owner:
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        try:
            discord_id = int(str(self.member_id).strip())
        except ValueError:
            await interaction.response.send_message("That Discord ID isn't a number — right-click the user → Copy ID.", ephemeral=True)
            return
        name = str(self.name_input).strip()
        candidates = find_candidates(os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"), name)
        if not candidates:
            await interaction.response.send_message(
                f"No game account found under **{discord.utils.escape_markdown(name)}**. "
                "Make sure they've joined an OYB server so the tracker has seen them.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Force-link <@{discord_id}> (`{discord_id}`) to which **{discord.utils.escape_markdown(name)}**?",
            view=ForceLinkChoice(self.bot, self.owner, discord_id, candidates),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class JoinView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction):
        if interaction.guild_id == self.bot.config.guild_id:
            return True
        await interaction.response.send_message("Use this in the OYB server.", ephemeral=True)
        return False

    @discord.ui.button(label="Link Reforger account", custom_id="oyb:link-account", style=discord.ButtonStyle.primary)
    async def link(self, interaction, button):
        await interaction.response.send_modal(LinkModal(self.bot))

    @discord.ui.button(label="My link status", custom_id="oyb:link-status")
    async def status(self, interaction, button):
        text = self.bot.account_links.status(interaction.guild_id, interaction.user.id)
        if self.bot.account_links.lookup(interaction.guild_id, interaction.user.id):
            text += "\n" + self.bot.rank_sync.status(interaction.user.id)
        await interaction.response.send_message(text, ephemeral=True)

    # Review, force-link and unlink moved to the pinned panel in the staff-only
    # link-request channel; members should not see admin buttons at all.


async def prepare_join_channel(bot, guild, overwrites):
    db = bot.account_links.db
    row = db.execute("SELECT channel,message FROM join_channel WHERE guild=?", (guild.id,)).fetchone()
    channel = guild.get_channel(row[0]) if row else None
    if channel is not None and (not isinstance(channel, discord.TextChannel) or channel.topic != MARKER):
        raise RuntimeError("Saved Join OYB channel is no longer bot-managed")
    if channel is None:
        matches = [c for c in guild.text_channels if c.topic == MARKER]
        if len(matches) > 1:
            raise RuntimeError("Multiple managed Join OYB channels found")
        channel = matches[0] if matches else None
    if channel is None:
        channel = await guild.create_text_channel(
            "join-oyb", topic=MARKER, overwrites=overwrites, reason="OYB account linking")
    else:
        # Re-lock a channel we adopted rather than created.
        await channel.edit(overwrites=overwrites, reason="Keep Join OYB read-only")
    with db:
        db.execute("INSERT OR REPLACE INTO join_channel VALUES (?,?,?)", (guild.id, channel.id, row[1] if row and row[0] == channel.id else None))
    info = None
    if row and row[0] == channel.id and row[1]:
        try:
            info = await channel.fetch_message(row[1])
        except discord.NotFound:
            pass
    def owned(m):
        return m.author.id == bot.user.id and any(e.footer.text == MARKER for e in m.embeds)
    if info is not None and not owned(info):
        info = None
    if info is None:
        async for candidate in channel.history(limit=None):
            if owned(candidate):
                info = candidate
                break
    embed = discord.Embed(title="Join OYB", colour=0x5865F2, description=(
        "Link your Discord account to your Reforger player for community XP ranks.\n\n"
        "**1.** Join an OYB game server so we can find your player.\n"
        "**2.** Press **Link Reforger account** and enter your exact in-game name.\n"
        "**3.** An admin checks ownership and approves your link.\n\n"
        "Your request and link status are private. We store your Discord ID and game identity so name changes won't lose your tracked time. "
        "Ranks begin at **OYB Renegade**. Earn **1 XP per 10 tracked minutes** across OYB servers "
        "plus **1 XP per new Discord post** after your link is approved. Partial playtime is saved. "
        "Previously earned XP is retained. "
        "Roles update automatically, usually within 15 seconds of recorded time. "
        "Use **/rank** to see your card or **My link status** for your XP. Ranks give no gameplay perks."))
    embed.set_footer(text=MARKER)
    if info is None:
        info = await channel.send(embed=embed, view=JoinView(bot), silent=True,
                                  allowed_mentions=discord.AllowedMentions.none())
    else:
        await info.edit(embed=embed, view=JoinView(bot), allowed_mentions=discord.AllowedMentions.none())
    with db:
        db.execute("UPDATE join_channel SET message=? WHERE guild=?", (info.id, guild.id))
