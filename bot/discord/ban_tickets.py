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

from bot.discord.ban_roles import SAME_IP, length_label

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
            rows = db.execute(f"SELECT identity, name, reason, created_at, expires_at FROM bans"
                              f" WHERE identity IN ({marks}) AND removed_at IS NULL"
                              f" AND (expires_at IS NULL OR expires_at > ?)",
                              (*identities, int(time.time()))).fetchall()
    except sqlite3.Error:
        return []
    return sorted(rows, key=lambda r: float("inf") if r[4] is None else r[4] - r[3], reverse=True)


def ban_card(member, bans, panel_url=""):
    embed = discord.Embed(title="This player is banned", colour=0x6E2F29,
                          description=f"{member.mention} opened this ticket while banned on "
                                      f"{'these accounts' if len(bans) > 1 else 'this account'}.")
    for identity, name, reason, created, expires in bans[:10]:
        ends = f"<t:{int(expires)}:F> (<t:{int(expires)}:R>)" if expires else "Never"
        lines = [f"**Length:** {length_label(created, expires)}", f"**Ends:** {ends}",
                 f"**Reason:** {SAME_IP.sub('', reason or '')[:500] or 'Not given'}", f"`{identity}`"]
        if panel_url:
            lines.append(f"{panel_url.rstrip('/')}/player/{identity}")
        embed.add_field(name=(name or "Unknown name")[:256], value="\n".join(lines),
                        inline=False)
    return embed


class BanTickets:
    def __init__(self, bot):
        self.bot = bot
        self.path = os.getenv("PANEL_DB", "data/panel.sqlite3")
        self.panel_channel = _id(os.getenv("BAN_TICKET_CHANNEL"))
        # Ticket King puts tickets in the panel's category, and each option can
        # have its own, so this can list several.
        self.categories = {i for i in map(_id, os.getenv("BAN_TICKET_CATEGORY", "").split(",")) if i}
        self.panel_url = os.getenv("PANEL_URL", "").strip()

    @property
    def enabled(self):
        return bool(self.panel_channel or self.categories)

    def _categories(self, guild):
        panel = guild.get_channel(self.panel_channel) if self.panel_channel else None
        return self.categories | ({panel.category_id} if getattr(panel, "category_id", None) else set())

    async def channel_created(self, channel):
        """A ticket made as its own channel: the opener is the member it was shared with."""
        if not self.enabled or channel.guild.id != self.bot.config.guild_id:
            return
        if not isinstance(channel, discord.TextChannel) or channel.id == self.panel_channel:
            return
        if channel.category_id not in self._categories(channel.guild):
            return
        await asyncio.sleep(SETTLE_SECONDS)
        ids = {target.id for target in channel.overwrites if not isinstance(target, discord.Role)}
        await self._check(channel, ids)

    async def thread_created(self, thread):
        """A ticket made as a thread off the ticket channel."""
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
            try:
                await where.send(embed=ban_card(member, bans, self.panel_url),
                                 allowed_mentions=discord.AllowedMentions.none())
                LOG.info("Posted the ban for %s into ticket %s", member_id, where.id)
            except discord.HTTPException:
                LOG.warning("Couldn't post into ticket %s; give the bot access to the ticket channels", where.id)


def _id(value):
    try:
        return int(value.strip()) if value and value.strip() else None
    except ValueError:
        return None
