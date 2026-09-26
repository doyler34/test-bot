"""A role naming the ban length on linked members while an OYB Control ban is
active, taken away when the ban ends, and a DM telling them why when the ban
is made. The panel owns the bans; this only reads its database, and leaves
every role alone when that database can't be read."""
import asyncio
from contextlib import closing
import json
import logging
import os
import re
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
# Only bans made this recently get a DM, so turning the bot on doesn't message
# everyone banned in the past.
DM_WINDOW = 3600
SAME_IP = re.compile(r"\s*\(same IP as [^)]*\)$")


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


def new_bans(path, since):
    """Active bans made since `since`: (id, identity, name, reason, created, expires)."""
    if not Path(path).is_file():
        return None
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            return db.execute("SELECT id, identity, name, reason, created_at, expires_at FROM bans"
                              " WHERE created_at >= ? AND removed_at IS NULL"
                              " AND (expires_at IS NULL OR expires_at > ?) ORDER BY id",
                              (since, int(time.time()))).fetchall()
    except sqlite3.Error:
        return None


def ban_message(guild_name, name, reason, created, expires, appeal=""):
    """The DM a linked player gets when they're banned."""
    label = length_label(created, expires)
    length = "permanently" if label == "Permanent" else f"for **{label}**"
    who = f"Your account **{discord.utils.escape_markdown(name)}**" if name else "Your account"
    embed = discord.Embed(title=f"You've been banned from {guild_name}", colour=0x6E2F29,
                          description=f"{who} is banned from all our servers {length}.")
    # An IP ban's reason names the other account on the IP; that may be
    # someone else in their house, so it stays out of the DM.
    embed.add_field(name="Reason", value=SAME_IP.sub("", reason or "")[:1000] or "Not given", inline=False)
    embed.add_field(name="Ends", value=f"<t:{int(expires)}:F> (<t:{int(expires)}:R>)" if expires else "Never",
                    inline=False)
    if appeal:
        embed.add_field(name="Appeal", value=appeal[:1000], inline=False)
    return embed


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
        self.appeal = os.getenv("BAN_APPEAL", "").strip()
        self.dm_state = Path(os.getenv("BAN_DM_STATE", "data/ban_dms.json"))
        self.messaged = self._load_messaged()

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
        await self.send_dms(guild)
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

    async def send_dms(self, guild):
        """Tells each linked player once, when a ban is made, why and for how long."""
        rows = await asyncio.to_thread(new_bans, self.path, int(time.time()) - DM_WINDOW)
        rows = [r for r in rows or [] if r[0] not in self.messaged]
        if not rows:
            return
        # One DM per person, about their longest ban, however many accounts it covered.
        people = {}
        for row in rows:
            member_id = self.bot.account_links.owner(guild.id, row[1])
            if not member_id:
                self.messaged.add(row[0])
                continue
            best = people.get(member_id)
            if best is None or rank(length_label(row[4], row[5])) > rank(length_label(best[0][4], best[0][5])):
                people[member_id] = [row] + (best or [])
            else:
                best.append(row)
        for member_id, bans in people.items():
            _, _, name, reason, created, expires = bans[0]
            member = await self._member(guild, member_id)
            if member is None:
                self.messaged.update(b[0] for b in bans)
                continue
            try:
                await member.send(embed=ban_message(guild.name, name, reason, created, expires, self.appeal))
                LOG.info("Sent ban DM to %s", member_id)
            except discord.Forbidden:
                LOG.info("Couldn't DM %s about their ban; their DMs are closed", member_id)
            except discord.HTTPException:
                LOG.warning("Ban DM to %s failed; will try again", member_id)
                continue
            self.messaged.update(b[0] for b in bans)
        self._save_messaged()

    def _load_messaged(self):
        try:
            return set(json.loads(self.dm_state.read_text()))
        except (OSError, ValueError, TypeError):
            return set()

    def _save_messaged(self):
        try:
            self.dm_state.parent.mkdir(parents=True, exist_ok=True)
            self.dm_state.write_text(json.dumps(sorted(self.messaged)[-1000:]))
        except OSError:
            LOG.warning("Couldn't save %s", self.dm_state)

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
