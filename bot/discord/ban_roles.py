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
RECHECK_SECONDS = 3600
ENDED_WINDOW = 7 * 86400


def length_label(created, expires):
    if expires is None:
        return "Permanent"
    span = max(int(expires) - int(created), 1)
    seconds, name = min(LENGTHS, key=lambda item: abs(item[0] - span))
    if abs(seconds - span) <= seconds // 20:
        return name
    days = round(span / 86400)
    return f"{days} days" if days > 1 else f"{max(round(span / 3600), 1)} hours"


def read_bans(path, ended_since):
    """(identity -> length label for active bans, identities whose ban ended
    since `ended_since` and have no other active ban), or None if unreadable."""
    if not Path(path).is_file():
        return None
    now = int(time.time())
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute("SELECT identity, created_at, expires_at FROM bans WHERE removed_at IS NULL"
                              " AND (expires_at IS NULL OR expires_at > ?) ORDER BY id", (now,)).fetchall()
            ended = db.execute("SELECT identity FROM bans WHERE removed_at >= ?"
                               " OR (removed_at IS NULL AND expires_at BETWEEN ? AND ?)",
                               (ended_since, ended_since, now)).fetchall()
    except sqlite3.Error:
        return None
    active = {identity: length_label(created, expires) for identity, created, expires in rows}
    return active, {identity for (identity,) in ended} - set(active)


def active_bans(path):
    read = read_bans(path, int(time.time()))
    return read[0] if read else None


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
        # Members already set right, so each minute doesn't ask Discord again.
        # The bot may not cache members, so everything else is fetched.
        self.applied: dict[int, str] = {}
        self.cleared: set[int] = set()
        self.checked = 0

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
        if time.monotonic() - self.checked > RECHECK_SECONDS:
            self.checked = time.monotonic()
            self.applied.clear()
            self.cleared.clear()
        read = await asyncio.to_thread(read_bans, self.path, int(time.time()) - ENDED_WINDOW)
        if read is None:
            return
        bans, ended = read
        if not self.announced:
            LOG.info("Ban roles follow OYB Control bans in %s", self.path)
            self.announced = True
        owner = lambda identity: self.bot.account_links.owner(guild.id, identity)
        wanted = {}
        for identity, label in bans.items():
            member_id = owner(identity)
            if not member_id:
                self._note(identity, f"Banned player {identity} has no linked Discord account, so no ban role")
            elif rank(label) > rank(wanted.get(member_id, "")):
                wanted[member_id] = label
        roles = {role.name[len(PREFIX):]: role for role in guild.roles if role.name.startswith(PREFIX)}
        for member_id, label in wanted.items():
            self.cleared.discard(member_id)
            if self.applied.get(member_id) == label:
                continue
            member = await self._member(guild, member_id)
            if member is None:
                self._note(member_id, f"Linked member {member_id} isn't in the Discord server, so no ban role")
                continue
            role = roles.get(label)
            if role is None:
                role = await guild.create_role(name=PREFIX + label, colour=discord.Colour(0x6E2F29),
                                               permissions=discord.Permissions.none(),
                                               mentionable=False, reason="OYB Control ban")
                roles[label] = role
            done = True
            if role not in member.roles:
                done = await self._change(member.add_roles, role, member)
            for other in [r for r in member.roles if r.name.startswith(PREFIX) and r != role]:
                done = await self._change(member.remove_roles, other, member) and done
            if done:
                self.applied[member_id] = label
        stale = set(self.applied) | {m for m in map(owner, ended) if m}
        stale |= {m.id for role in roles.values() for m in role.members}
        for member_id in stale - set(wanted) - self.cleared:
            self.applied.pop(member_id, None)
            member = await self._member(guild, member_id)
            done = True
            for role in [r for r in member.roles if r.name.startswith(PREFIX)] if member else []:
                done = await self._change(member.remove_roles, role, member) and done
            if done:
                self.cleared.add(member_id)

    async def _member(self, guild, member_id):
        member = guild.get_member(member_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(member_id)
        except discord.NotFound:
            return None

    def _note(self, key, message):
        if key not in self.noted:
            self.noted.add(key)
            LOG.info(message)

    async def _change(self, action, role, member):
        try:
            await action(role, reason="OYB Control ban")
            return True
        except discord.HTTPException:
            LOG.warning("Could not update %s on %s; check the bot's role sits above it", role.name, member.id)
            return False
