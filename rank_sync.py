"""Test ranks from durable, post-link connected time; no gameplay privileges."""
import asyncio
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3
import time

import discord

LOG = logging.getLogger("reforger.ranks")
RANKS = tuple("OYB " + name for name in (
    "Recruit", "Private", "Corporal", "Sergeant", "Lieutenant",
    "Captain", "Major", "Colonel"))


class RankSync:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.account_links.db
        self.roles = []
        self.applied = {}
        self.lock = asyncio.Lock()
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS rank_progress (
                guild INTEGER, member INTEGER, identity TEXT,
                baseline REAL, seconds REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(guild,member));
            CREATE TABLE IF NOT EXISTS rank_roles (
                guild INTEGER, tier INTEGER, role INTEGER,
                PRIMARY KEY(guild,tier));
        ''')

    async def prepare(self, guild):
        if not guild.me.guild_permissions.manage_roles:
            raise RuntimeError("Ranks need Manage Roles permission")
        roles = await guild.fetch_roles()
        prepared = []
        for tier, name in enumerate(RANKS):
            row = self.db.execute("SELECT role FROM rank_roles WHERE guild=? AND tier=?",
                                  (guild.id, tier)).fetchone()
            role = next((r for r in roles if row and r.id == row[0]), None)
            if role is None:
                matches = [r for r in roles if r.name == name]
                if len(matches) > 1:
                    raise RuntimeError(f"Multiple roles named {name}; resolve duplicates")
                role = matches[0] if matches else await guild.create_role(
                    name=name, permissions=discord.Permissions.none(),
                    hoist=True, mentionable=False, reason="OYB test ranks")
            if (role.managed or role.is_default() or role.permissions.value != 0
                    or role >= guild.me.top_role):
                raise RuntimeError(f"{name} must have no permissions and be below the bot role")
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO rank_roles VALUES (?,?,?)",
                                (guild.id, tier, role.id))
            prepared.append(role)
        self.roles = prepared
        LOG.info("Test ranks ready: Recruit on approved link; 10 XP per minute, 10 XP per rank")

    def progress(self, member, identity):
        guild = self.bot.config.guild_id
        row = self.db.execute("SELECT identity,baseline,seconds FROM rank_progress WHERE guild=? AND member=?",
                              (guild, member)).fetchone()
        if row and row[0] != identity:
            raise RuntimeError("Linked identity changed; rank progress needs admin review")
        baseline, seconds = (row[1], row[2]) if row else (None, 0)
        # Wait for historical log import before starting a new XP baseline.
        ready = bool(self.bot._trackers) and all(
            t.initialized and t.caught_up for t in self.bot._trackers)
        if ready:
            path = Path(os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"))
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
                    total = source.execute("SELECT SUM(seconds) FROM totals WHERE identity=?", (identity,)).fetchone()[0]
                if total is not None:
                    if baseline is None:
                        baseline = total
                    # A lost/reset tracker DB must never subtract earned XP.
                    seconds = max(seconds, total - baseline)
            except sqlite3.Error:
                LOG.warning("Playtime database unavailable; keeping earned XP")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO rank_progress VALUES (?,?,?,?,?)",
                            (guild, member, identity, baseline, seconds))
        return int(seconds // 60) * 10

    def status(self, member):
        row = self.db.execute("SELECT seconds FROM rank_progress WHERE guild=? AND member=?",
                              (self.bot.config.guild_id, member)).fetchone()
        xp = int(row[0] // 60) * 10 if row else 0
        return f"Rank: **{RANKS[min(xp // 10, len(RANKS)-1)]}** · **{xp} XP** (10 XP per tracked minute)."

    async def tick(self):
        async with self.lock:
            guild = self.bot.get_guild(self.bot.config.guild_id)
            if guild is None:
                return
            if not self.roles:
                await self.prepare(guild)
            rows = self.db.execute("SELECT discord_id,identity FROM account_links WHERE guild=?",
                                   (guild.id,)).fetchall()
            for member_id, identity in rows:
                try:
                    xp = self.progress(member_id, identity)
                    tier = min(xp // 10, len(RANKS)-1)
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
                    obsolete = [r for r in self.roles if r.id in current and r.id != target.id]
                    if obsolete:
                        await member.remove_roles(*obsolete, reason="OYB rank promotion", atomic=True)
                    self.applied[member_id] = (target.id, time.monotonic())
                    LOG.info("Rank synced for Discord %s: %s (%s XP)", member_id, RANKS[tier], xp)
                except discord.NotFound:
                    self.applied[member_id] = (self.roles[tier].id, time.monotonic())
                except Exception:
                    LOG.exception("Rank update failed for %s; retrying", member_id)

    async def run(self):
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                self.roles = []
                LOG.exception("Rank setup failed; retrying. Check Manage Roles and role order")
            await asyncio.sleep(15)
