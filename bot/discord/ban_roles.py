"""A role naming the ban length on linked members while an OYB Control ban is
active, taken away when the ban ends. The panel owns the bans; this only reads
its database, and leaves every role alone when that database can't be read."""
import asyncio
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3
import time

import discord

LOG = logging.getLogger("reforger.ban_roles")
PREFIX = "Banned · "
LENGTHS = ((3600, "1 hour"), (86400, "1 day"), (604800, "7 days"), (2592000, "30 days"))
INTERVAL = 60


def length_label(created, expires):
    if expires is None:
        return "Permanent"
    span = max(int(expires) - int(created), 1)
    seconds, name = min(LENGTHS, key=lambda item: abs(item[0] - span))
    if abs(seconds - span) <= seconds // 20:
        return name
    days = round(span / 86400)
    return f"{days} days" if days > 1 else f"{max(round(span / 3600), 1)} hours"


def active_bans(path):
    """identity -> length label for every active ban, or None if unreadable."""
    if not Path(path).is_file():
        return None
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute("SELECT identity, created_at, expires_at FROM bans WHERE removed_at IS NULL"
                              " AND (expires_at IS NULL OR expires_at > ?) ORDER BY id",
                              (int(time.time()),)).fetchall()
    except sqlite3.Error:
        return None
    return {identity: length_label(created, expires) for identity, created, expires in rows}


def rank(label):
    """Longest ban wins when one member has several banned accounts."""
    if label == "Permanent":
        return float("inf")
    for seconds, name in LENGTHS:
        if name == label:
            return seconds
    return 0


class BanRoles:
    def __init__(self, bot):
        self.bot = bot
        self.path = os.getenv("PANEL_DB", "data/panel.sqlite3")
        self.announced = False
        self.noted = set()

    async def run(self):
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                LOG.exception("Ban role sync failed; retrying")
            await asyncio.sleep(INTERVAL)

    async def tick(self):
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        if not guild.me.guild_permissions.manage_roles:
            self._note("perm", "Ban roles need the bot to have Manage Roles")
            return
        bans = await asyncio.to_thread(active_bans, self.path)
        if bans is None:
            return
        if not self.announced:
            LOG.info("Ban roles follow OYB Control bans in %s", self.path)
            self.announced = True
        wanted = {}
        for identity, label in bans.items():
            member_id = self.bot.account_links.owner(guild.id, identity)
            if not member_id:
                self._note(identity, f"Banned player {identity} has no linked Discord account, so no ban role")
            elif rank(label) > rank(wanted.get(member_id, "")):
                wanted[member_id] = label
        roles = {role.name[len(PREFIX):]: role for role in guild.roles if role.name.startswith(PREFIX)}
        for member_id, label in wanted.items():
            member = guild.get_member(member_id)
            if member is None:
                self._note(member_id, f"Linked member {member_id} isn't in the Discord server, so no ban role")
                continue
            role = roles.get(label)
            if role is None:
                role = await guild.create_role(name=PREFIX + label, colour=discord.Colour(0x6E2F29),
                                               permissions=discord.Permissions.none(),
                                               mentionable=False, reason="OYB Control ban")
                roles[label] = role
            if role not in member.roles:
                await self._change(member.add_roles, role, member)
        for label, role in roles.items():
            for member in list(role.members):
                if wanted.get(member.id) != label:
                    await self._change(member.remove_roles, role, member)

    def _note(self, key, message):
        if key not in self.noted:
            self.noted.add(key)
            LOG.info(message)

    async def _change(self, action, role, member):
        try:
            await action(role, reason="OYB Control ban")
        except discord.HTTPException:
            LOG.warning("Could not update %s on %s; check the bot's role sits above it", role.name, member.id)
