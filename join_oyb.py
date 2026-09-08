"""Read-only account-linking channel with private submissions and admin review."""
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import discord
from account_links import LinkConflict

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
        raise ValueError("More than one player has that name. Ask an admin to help identify your account before linking.")
    return matches.pop()


class LinkModal(discord.ui.Modal, title="Link your Reforger account"):
    name_input = discord.ui.TextInput(label="Your exact Reforger in-game name", min_length=1, max_length=100)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message("Use this in the OYB server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            name = str(self.name_input).strip()
            identity = find_identity(os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"), name)
            self.bot.account_links.submit(interaction.guild_id, interaction.user.id, identity, name)
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
            discord.SelectOption(label=r[3][:100], description=f"Discord ID: {r[1]}", value=r[0]) for r in rows])

        async def selected(interaction):
            if not can_review(interaction, bot.config.guild_id) or interaction.user.id != owner:
                await interaction.response.send_message("Admin access required.", ephemeral=True)
                return
            token = select.values[0]
            _, member, identity, name = self.rows[token]
            text = (f"Discord account: <@{member}> (`{member}`)\n"
                    f"Reforger name: {discord.utils.escape_markdown(name)}\n"
                    f"Game identity: `{identity}`\n\n"
                    "Confirm ownership with the player in-game before approving. A matching name alone is not verification.")
            await interaction.response.edit_message(content=text, view=ReviewDecision(bot, token, owner),
                                                    allowed_mentions=discord.AllowedMentions.none())
        select.callback = selected
        self.add_item(select)


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

    @discord.ui.button(label="Admin: review requests", custom_id="oyb:link-review")
    async def review(self, interaction, button):
        if not can_review(interaction, self.bot.config.guild_id):
            await interaction.response.send_message("Only admins with Manage Server can review requests.", ephemeral=True)
            return
        rows = self.bot.account_links.pending(interaction.guild_id)
        await interaction.response.send_message("Pending requests (up to 25; reopen after reviewing for more)." if rows else "No pending requests.",
                                                view=ReviewList(self.bot, rows, interaction.user.id) if rows else None,
                                                ephemeral=True)


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
        channel = matches[0] if matches else await guild.create_text_channel(
            "join-oyb", topic=MARKER, overwrites=overwrites, reason="OYB account linking")
    else:
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
        "Ranks begin at **OYB Renegade**. Earn **1 XP per 10 tracked minutes** across OYB servers; partial time is saved. "
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
