"""Locked voice 'stat' channels showing live server, player and VC counts.

A read-only "SERVER STATS" category holds one voice channel per counter, named
with the current value (Discord shows the voice-channel name verbatim). Nobody
can join them. Renames are paced at five minutes per channel because Discord
rate-limits channel renames to about two per ten minutes each — the same budget
the category timers already respect.

Counters:
  * one per configured server — linked players currently in-game on it
  * Playing ArmA — linked players in-game across all servers
  * Users in VC — non-bot members currently in voice
  * Admins (optional) — members holding ADMIN_ROLE_ID; drop-in and only created
    when that env var is set. With the presences intent enabled and
    ADMIN_STATS_ONLINE_ONLY=true it becomes a live "Admins Online" count.

"Playing ArmA" and the per-server counts use the playtime tracker's live
connection state intersected with approved account links, so they reflect real
tracked players rather than Discord rich-presence.
"""
import asyncio
import logging
import os
import time

import discord

LOG = logging.getLogger("reforger.stats")

CATEGORY_NAME = "📊 SERVER STATS 📊"
RENAME_INTERVAL = 300  # Discord allows ~2 channel renames per 10 minutes each.
POLL = 30
TRUTHY = ("1", "true", "yes", "on")


def stat_overwrites(guild):
    # Visible to everyone, joinable by no one: a display-only counter.
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
    }


class ServerStats:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.store.db
        self.category = None
        self.channels = {}  # key -> voice channel
        self.db.execute("CREATE TABLE IF NOT EXISTS stat_channels ("
                        "key TEXT PRIMARY KEY, channel INTEGER NOT NULL, updated REAL NOT NULL DEFAULT 0)")
        self.db.commit()
        raw = os.getenv("ADMIN_ROLE_ID", "").strip()
        self.admin_role_id = int(raw) if raw.isdigit() else None

    def stat_keys(self):
        keys = [server.id for server in self.bot.config.servers]
        if self.admin_role_id:
            keys.append("admins")
        keys += ["arma", "vc"]
        return keys

    # --- setup ---------------------------------------------------------------

    async def prepare(self, guild):
        self.category = await self._ensure_category(guild)
        for key in self.stat_keys():
            await self._ensure_channel(guild, key)

    async def _ensure_category(self, guild):
        row = self.db.execute("SELECT channel FROM stat_channels WHERE key='category'").fetchone()
        category = guild.get_channel(row[0]) if row else None
        if not isinstance(category, discord.CategoryChannel):
            category = next((c for c in guild.categories if c.name == CATEGORY_NAME), None)
        if category is None:
            category = await guild.create_category(CATEGORY_NAME, reason="OYB live server stats")
        if row is None or row[0] != category.id:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO stat_channels VALUES ('category',?,0)", (category.id,))
        return category

    async def _ensure_channel(self, guild, key):
        row = self.db.execute("SELECT channel FROM stat_channels WHERE key=?", (key,)).fetchone()
        channel = guild.get_channel(row[0]) if row else None
        if not isinstance(channel, discord.VoiceChannel):
            channel = None
        if channel is None:
            name = self._desired_name(guild, key, self._in_game()) or key
            channel = await guild.create_voice_channel(
                name, category=self.category, overwrites=stat_overwrites(guild),
                reason="OYB live stat channel")
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO stat_channels VALUES (?,?,?)",
                                (key, channel.id, time.time()))
        self.channels[key] = channel

    # --- data ----------------------------------------------------------------

    def _linked(self):
        return {identity for (identity,) in self.bot.account_links.db.execute(
            "SELECT identity FROM account_links WHERE guild=?", (self.bot.config.guild_id,))}

    def _in_game(self):
        """Linked players currently connected, per server id."""
        linked = self._linked()
        trackers = {t.server: t for t in getattr(self.bot, "_trackers", [])}
        counts = {}
        for server in self.bot.config.servers:
            tracker = trackers.get(server.id)
            live = server.id in getattr(self.bot, "match_times", {})
            active = tracker.active_identities if (tracker and live) else set()
            counts[server.id] = len(active & linked)
        return counts

    def _desired_name(self, guild, key, counts):
        if key in counts:
            server = next(s for s in self.bot.config.servers if s.id == key)
            if not server.enabled:
                return self._cap(f"🔴 {server.name}: soon")
            icon = "🟢" if key in getattr(self.bot, "match_times", {}) else "⚪"
            return self._cap(f"{icon} {server.name}: {counts[key]}")
        if key == "arma":
            return self._cap(f"🎮 Playing ArmA: {sum(counts.values())}")
        if key == "vc":
            people = sum(1 for vc in guild.voice_channels for m in vc.members if not m.bot)
            return self._cap(f"🔊 Users in VC: {people}")
        if key == "admins":
            role = guild.get_role(self.admin_role_id) if self.admin_role_id else None
            if role is None:
                return None
            members = role.members
            if os.getenv("ADMIN_STATS_ONLINE_ONLY", "").strip().lower() in TRUTHY:
                online = [m for m in members if str(getattr(m, "status", "offline")) != "offline"]
                return self._cap(f"🛡️ Admins Online: {len(online)}")
            return self._cap(f"🛡️ Admins: {len(members)}")
        return None

    @staticmethod
    def _cap(name):
        return name[:100]

    # --- loop ----------------------------------------------------------------

    async def tick(self):
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None or self.category is None:
            return
        counts = self._in_game()
        now = time.time()
        for key, channel in list(self.channels.items()):
            desired = self._desired_name(guild, key, counts)
            if desired is None or channel.name == desired:
                continue
            row = self.db.execute("SELECT updated FROM stat_channels WHERE key=?", (key,)).fetchone()
            if row and now - row[0] < RENAME_INTERVAL:
                continue
            # Reserve the rename budget before sending so retries obey it too.
            with self.db:
                self.db.execute("UPDATE stat_channels SET updated=? WHERE key=?", (now, key))
            try:
                self.channels[key] = await channel.edit(name=desired, reason="OYB live stats")
            except discord.HTTPException:
                LOG.exception("Stat channel update failed for %s; retry after cooldown", key)

    async def run(self):
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                LOG.exception("Server stats tick failed; retrying")
            await asyncio.sleep(POLL)
