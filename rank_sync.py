"""Discord roles from durable combined OYB XP; no gameplay privileges."""
import asyncio
import logging
import os
import time

import discord

LOG = logging.getLogger("reforger.ranks")
from rank_rules import RANKS as DEFINITIONS, rank_for_xp
from rank_persistence import XPStore
RANKS = tuple(rank.role_name for rank in DEFINITIONS)
_NO_SNAPSHOT = object()


class RankSync:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.account_links.db
        self.roles = []
        self.applied = {}
        self.lock = asyncio.Lock()
        self.wallet = XPStore(self.db)
        self.db.execute("CREATE TABLE IF NOT EXISTS rank_roles_v2 (guild INTEGER, tier INTEGER, role INTEGER, PRIMARY KEY(guild,tier))")
        self.db.commit()
        from rank_announcements import RankAnnouncements
        self.announcements = RankAnnouncements(bot)

    async def prepare(self, guild):
        if not guild.me.guild_permissions.manage_roles:
            raise RuntimeError("Ranks need Manage Roles permission")
        roles = await guild.fetch_roles()
        prepared = []
        for tier, name in enumerate(RANKS):
            row = self.db.execute("SELECT role FROM rank_roles_v2 WHERE guild=? AND tier=?",
                                  (guild.id, tier)).fetchone()
            role = next((r for r in roles if row and r.id == row[0]), None)
            if role is None:
                matches = [r for r in roles if r.name == name]
                if len(matches) > 1:
                    raise RuntimeError(f"Multiple roles named {name}; resolve duplicates")
                role = matches[0] if matches else await guild.create_role(
                    name=name, permissions=discord.Permissions.none(),
                    hoist=True, mentionable=False, reason="OYB community ranks")
            if (role.managed or role.is_default() or role.permissions.value != 0
                    or role >= guild.me.top_role):
                raise RuntimeError(f"{name} must have no permissions and be below the bot role")
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO rank_roles_v2 VALUES (?,?,?)",
                                (guild.id, tier, role.id))
            prepared.append(role)
        self.roles = prepared
        LOG.info("OYB ranks ready: Renegade to Major; playtime XP plus Discord post XP")

    def _ready(self):
        return bool(self.bot._trackers) and all(t.initialized and t.caught_up for t in self.bot._trackers)

    def progress(self, member, identity, snapshot=_NO_SNAPSHOT):
        # snapshot is the per-tick batched playtime read. When omitted (direct
        # reads/tests) read() opens its own connection for backward compatibility;
        # an explicit dict (or None, when the batch failed) is forwarded so a
        # tick never opens one connection per player.
        path = os.getenv("PLAYTIME_DB", "data/playtime.sqlite3")
        if snapshot is _NO_SNAPSHOT:
            return self.wallet.read(self.bot.config.guild_id, member, identity, path, self._ready())
        return self.wallet.read(self.bot.config.guild_id, member, identity, path, self._ready(), snapshot=snapshot)

    def status(self, member):
        xp = self.wallet.cached(self.bot.config.guild_id, member)
        return f"Rank: **{rank_for_xp(xp).current.role_name}** · **{xp} XP**. Use /rank for your card."

    async def tick(self):
        async with self.lock:
            guild = self.bot.get_guild(self.bot.config.guild_id)
            if guild is None:
                return
            if not self.roles:
                await self.prepare(guild)
            rows = self.db.execute("SELECT discord_id,identity FROM account_links WHERE guild=?",
                                   (guild.id,)).fetchall()
            # One batched read-only load of playtime for the whole tick, instead
            # of opening a connection per linked member.
            snapshot = self.wallet.snapshot(os.getenv("PLAYTIME_DB", "data/playtime.sqlite3")) if self._ready() else None
            for member_id, identity in rows:
                try:
                    xp = self.progress(member_id, identity, snapshot)
                    tier = rank_for_xp(xp).tier
                    target = self.roles[tier]
                    previous = self.applied.get(member_id)
                    if previous and previous[0] == target.id and time.monotonic() - previous[1] < 300:
                        continue
                    # Recheck gateway state in case an admin changed/deleted a role.
                    current_target = guild.get_role(target.id)
                    if (current_target is None or current_target.managed
                            or current_target.permissions.value != 0
                            or current_target >= guild.me.top_role):
                        self.roles = []
                        raise RuntimeError("Rank role changed; check permissions and hierarchy")
                    target = current_target
                    member = await guild.fetch_member(member_id)
                    current = {r.id for r in member.roles}
                    if target.id not in current:
                        await member.add_roles(target, reason=f"OYB rank: {xp} XP", atomic=True)
                    managed = {r.id: r for r in self.roles}
                    if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_roles'").fetchone():
                        for old_id, in self.db.execute("SELECT role FROM rank_roles WHERE guild=?", (guild.id,)):
                            legacy = guild.get_role(old_id)
                            if legacy and not legacy.managed and legacy.permissions.value == 0 and legacy < guild.me.top_role:
                                managed[legacy.id] = legacy
                    obsolete = [r for r in managed.values() if r.id in current and r.id != target.id]
                    if obsolete:
                        await member.remove_roles(*obsolete, reason="OYB rank promotion", atomic=True)
                    self.announcements.record(guild.id, member_id, tier, xp)
                    self.applied[member_id] = (target.id, time.monotonic())
                    LOG.info("Rank synced for Discord %s: %s (%s XP)", member_id, RANKS[tier], xp)
                except discord.NotFound:
                    self.applied[member_id] = (self.roles[tier].id, time.monotonic())
                except Exception:
                    LOG.exception("Rank update failed for %s; retrying", member_id)
            await self.announcements.flush(guild)

    async def run(self):
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                self.roles = []
                LOG.exception("Rank setup failed; retrying. Check Manage Roles and role order")
            await asyncio.sleep(15)
