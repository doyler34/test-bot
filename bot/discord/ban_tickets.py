"""When a banned player opens a ticket (Ticket King, or any ticket bot that
makes a channel or thread per ticket), posts their ban into it so staff
don't have to ask. Only reads the panel's database, like the ban roles."""
import asyncio
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3
import time

import discord

from bot.discord.ban_roles import SAME_IP, ban_settings, length_label

LOG = logging.getLogger("reforger.ban_tickets")
# The ticket bot sets up the channel and greets the opener a moment after
# creating it; waiting lets its permissions and first message land first.
SETTLE_SECONDS = 4


def bans_for(path, identities):
    """Active panel bans on any of these accounts, longest first."""
    if not identities or not Path(path).is_file():
        return []
    marks = ",".join("?" * len(identities))
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute(f"SELECT identity, name, reason, created_at, expires_at, created_by FROM bans"
                              f" WHERE identity IN ({marks}) AND removed_at IS NULL"
                              f" AND (expires_at IS NULL OR expires_at > ?)",
                              (*identities, int(time.time()))).fetchall()
    except sqlite3.Error:
        return []
    return sorted(rows, key=lambda r: float("inf") if r[4] is None else r[4] - r[3], reverse=True)


def ban_card(member, bans, panel_url="", title=""):
    embed = discord.Embed(title=title or "This player is banned", colour=0x6E2F29,
                          description=f"{member.mention} opened this ticket while banned on "
                                      f"{'these accounts' if len(bans) > 1 else 'this account'}.")
    for identity, name, reason, created, expires, by in bans[:10]:
        ends = f"<t:{int(expires)}:F> (<t:{int(expires)}:R>)" if expires else "Never"
        lines = [f"**Length:** {length_label(created, expires)}", f"**Ends:** {ends}",
                 f"**Reason:** {SAME_IP.sub('', reason or '')[:500] or 'Not given'}",
                 f"**Banned by:** {(by or 'Unknown')[:100]}", f"`{identity}`"]
        if panel_url:
            lines.append(f"{panel_url.rstrip('/')}/player/{identity}")
        embed.add_field(name=(name or "Unknown name")[:256], value="\n".join(lines),
                        inline=False)
    return embed


class BanTickets:
    def __init__(self, bot):
        self.bot = bot
        self.path = os.getenv("PANEL_DB", "data/panel.sqlite3")
        self._load()

    def _load(self):
        """Settings from OYB Control if published there, else the bot's .env."""
        settings = ban_settings(self.path)
        self.on = settings["tickets_enabled"]
        self.panel_channel = settings["ticket_channel"]
        # Ticket King puts tickets in the panel's category, and each option can
        # have its own, so this can list several.
        self.categories = set(settings["ticket_categories"])
        self.panel_url = settings["panel_url"]
        self.title = settings["ticket_title"]
        # A private thread only that role is added to, so the player never sees the card.
        self.private = bool(settings.get("ticket_private"))
        self.role = settings.get("ticket_role") or ""

    @property
    def enabled(self):
        return self.on and bool(self.panel_channel or self.categories)

    def _categories(self, guild):
        panel = guild.get_channel(self.panel_channel) if self.panel_channel else None
        return self.categories | ({panel.category_id} if getattr(panel, "category_id", None) else set())

    async def channel_created(self, channel):
        """A ticket made as its own channel: the opener is the member it was shared with."""
        self._load()
        if not self.enabled or channel.guild.id != self.bot.config.guild_id:
            return
        if not isinstance(channel, discord.TextChannel) or channel.id == self.panel_channel:
            return
        watched = self._categories(channel.guild)
        if channel.category_id not in watched:
            LOG.info("New channel #%s isn't in a ticket category (it's in %s, watching %s)",
                     channel.name, channel.category_id, ", ".join(map(str, watched)) or "none")
            return
        await asyncio.sleep(SETTLE_SECONDS)
        ids = {target.id for target in channel.overwrites if not isinstance(target, discord.Role)}
        LOG.info("New ticket #%s; checking %d member(s) for bans", channel.name, len(ids))
        await self._check(channel, ids)

    async def thread_created(self, thread):
        """A ticket made as a thread off the ticket channel."""
        self._load()
        if not self.enabled or thread.guild.id != self.bot.config.guild_id:
            return
        if thread.parent_id != self.panel_channel:
            return
        await asyncio.sleep(SETTLE_SECONDS)
        ids = set()
        try:
            ids |= {m.id for m in await thread.fetch_members()}
        except discord.HTTPException:
            pass
        try:
            async for message in thread.history(limit=5, oldest_first=True):
                ids |= {m.id for m in message.mentions}
        except discord.HTTPException:
            pass
        await self._check(thread, ids - {thread.owner_id})

    async def _check(self, where, member_ids):
        guild = where.guild
        me = guild.me.id if guild.me else None
        for member_id in member_ids - {me}:
            identities = self.bot.account_links.identities(guild.id, member_id)
            bans = await asyncio.to_thread(bans_for, self.path, identities)
            if not bans:
                continue
            member = guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except discord.HTTPException:
                    continue
            if member.bot:
                continue
            card = ban_card(member, bans, self.panel_url, self.title)
            if self.private:
                await self._private(where, member, card)
                continue
            try:
                await where.send(embed=card, allowed_mentions=discord.AllowedMentions.none())
                LOG.info("Posted the ban for %s into ticket %s", member_id, where.id)
            except discord.HTTPException:
                LOG.warning("Couldn't post into ticket %s; give the bot access to the ticket channels", where.id)

    async def _private(self, where, member, card):
        """Post the card in a private thread off the ticket and add the role's members to it.
        If that can't be done the card isn't posted at all, rather than shown to the player."""
        if not isinstance(where, discord.TextChannel):
            LOG.warning("Ticket %s is itself a thread, so no private thread can go in it; ban card not posted", where.id)
            return
        from bot.discord.onboarding import by_name
        role = by_name(where.guild, self.role)
        if role is None:
            LOG.warning("No role called %r for the private ban card; ban card not posted", self.role)
            return
        try:
            thread = await where.create_thread(name="🔒 Ban info", type=discord.ChannelType.private_thread,
                                               invitable=False, auto_archive_duration=10080,
                                               reason="Ban card for staff only")
            await thread.send(embed=card, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            LOG.warning("Couldn't make the private ban thread in ticket %s (%s); the bot needs Create Private "
                        "Threads there. Ban card not posted", where.id, exc.text or exc.status)
            return
        added = 0
        for staff in role.members:
            if staff.id == member.id or staff.bot:
                continue
            try:
                await thread.add_user(staff)
                added += 1
            except discord.HTTPException:
                pass
        LOG.info("Posted the ban for %s in a private thread in ticket %s for %d %s", member.id, where.id,
                 added, role.name)


def _id(value):
    try:
        return int(value.strip()) if value and value.strip() else None
    except ValueError:
        return None
