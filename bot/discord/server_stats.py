"""Locked voice "stat" channels that display live server, player and VC counts."""
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

LABELS = {"server-1": "Classic", "server-2": "3x Everon", "server-3": "Arland"}


def label_for(server):
    return os.getenv(f"STAT_LABEL_{server.id.replace('-', '_').upper()}", "").strip() \
        or LABELS.get(server.id, server.name)


def stat_overwrites(guild):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
    }


class ServerStats:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.store.db
        self.category = None
        self.channels = {}
        self.db.execute("CREATE TABLE IF NOT EXISTS stat_channels ("
                        "key TEXT PRIMARY KEY, channel INTEGER NOT NULL, updated REAL NOT NULL DEFAULT 0)")
        self.db.commit()
        raw = os.getenv("ADMIN_ROLE_ID", "").strip()
        self.admin_role_id = int(raw) if raw.isdigit() else None
        self._applied_state = {}

    def stat_keys(self):
        keys = [server.id for server in self.bot.config.servers]
        if self.admin_role_id:
            keys.append("admins")
        keys += ["arma", "vc"]
        return keys

    async def prepare(self, guild):
        self.category = await self._ensure_category(guild)
        # Snapshot the tiles already sitting in the category so a wiped or stale id
        # never spawns a second set; whatever is left unclaimed is a duplicate.
        existing = [c for c in guild.voice_channels
                    if getattr(c, "category", None) is not None and c.category.id == self.category.id]
        claimed = set()
        for key in self.stat_keys():
            await self._ensure_channel(guild, key, existing, claimed)
        await self._remove_duplicates(existing, claimed)

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
        # move() reliably reorders a category; edit(position=0) does not.
        try:
            await category.move(beginning=True, reason="Keep OYB stats at the top")
        except (discord.HTTPException, TypeError):
            LOG.warning("Could not move SERVER STATS to the top; check Manage Channels and role position")
        return category

    def _key_for_name(self, name):
        """Which stat key an existing tile's name belongs to, if any."""
        for server in self.bot.config.servers:
            if name == server.id or label_for(server) in name:
                return server.id
        if name == "arma" or "Playing ArmA" in name:
            return "arma"
        if name == "vc" or "Users in VC" in name:
            return "vc"
        if name == "admins" or "Admins" in name:
            return "admins"
        return None

    async def _ensure_channel(self, guild, key, existing, claimed):
        row = self.db.execute("SELECT channel FROM stat_channels WHERE key=?", (key,)).fetchone()
        channel = guild.get_channel(row[0]) if row else None
        if not isinstance(channel, discord.VoiceChannel):
            channel = None
        if channel is None:
            # Reuse the tile already in the category before making a new one.
            channel = next((c for c in existing
                            if c.id not in claimed and self._key_for_name(c.name) == key), None)
            if channel is not None:
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO stat_channels VALUES (?,?,0)",
                                    (key, channel.id))
        if channel is None:
            name = self._desired_name(guild, key, self._in_game()) or key
            channel = await guild.create_voice_channel(
                name, category=self.category, overwrites=stat_overwrites(guild),
                reason="OYB live stat channel")
            # updated=0 lets the first tick correct a startup guess without waiting.
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO stat_channels VALUES (?,?,0)",
                                (key, channel.id))
        claimed.add(channel.id)
        self.channels[key] = channel
        server = next((s for s in self.bot.config.servers if s.id == key), None)
        if server is not None:
            self._applied_state[key] = self._state_token(server)

    async def _remove_duplicates(self, existing, claimed):
        """Delete leftover stat tiles in the category that no key adopted."""
        for channel in existing:
            if channel.id in claimed or self._key_for_name(channel.name) is None:
                continue  # keep claimed tiles and any unrelated channel dropped in here
            try:
                await channel.delete(reason="OYB removing duplicate stat channel")
                LOG.info("Removed duplicate stat channel %r", channel.name)
            except discord.HTTPException:
                LOG.warning("Could not remove duplicate stat channel %r", channel.name)

    def _linked(self):
        return {identity for (identity,) in self.bot.account_links.db.execute(
            "SELECT identity FROM account_links WHERE guild=?", (self.bot.config.guild_id,))}

    def _in_game(self):
        linked = self._linked()
        trackers = {t.server: t for t in getattr(self.bot, "_trackers", [])}
        counts = {}
        for server in self.bot.config.servers:
            tracker = trackers.get(server.id)
            live = server.id in getattr(self.bot, "match_times", {})
            active = tracker.active_identities if (tracker and live) else set()
            counts[server.id] = len(active & linked)
        return counts

    def _state_token(self, server):
        if not server.enabled:
            return "soon"
        if server.id in getattr(self.bot, "match_times", {}):
            return "live"
        monitor = next((m for sid, m in getattr(self.bot, "monitors", []) if sid == server.id), None)
        return "waiting" if (monitor is not None and getattr(monitor, "online", False)) else "offline"

    def _server_status(self, server):
        label = label_for(server)
        if not server.enabled:
            return f"⚫ {label} · Coming soon"
        match = getattr(self.bot, "match_times", {}).get(server.id)
        if match is not None:
            minutes = max(0, int((time.monotonic() - match[1]) // 60))
            hours, rem = divmod(minutes, 60)
            elapsed = f"{hours}h {rem:02d}m" if hours else f"{minutes} min"
            return f"🟢 {label} · {elapsed}"
        monitor = next((m for sid, m in getattr(self.bot, "monitors", []) if sid == server.id), None)
        if monitor is not None and getattr(monitor, "online", False):
            return f"🟡 {label} · Waiting for match"
        return f"🔴 {label} · Offline"

    def _desired_name(self, guild, key, counts):
        if key in counts:
            server = next(s for s in self.bot.config.servers if s.id == key)
            return self._cap(self._server_status(server))
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

    async def tick(self):
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None or self.category is None:
            return
        counts = self._in_game()
        servers = {s.id: s for s in self.bot.config.servers}
        now = time.time()
        for key, channel in list(self.channels.items()):
            desired = self._desired_name(guild, key, counts)
            if desired is None or channel.name == desired:
                continue
            # A match state change renames now; only the minute count is paced.
            forced = key in servers and self._state_token(servers[key]) != self._applied_state.get(key)
            if not forced:
                row = self.db.execute("SELECT updated FROM stat_channels WHERE key=?", (key,)).fetchone()
                if row and now - row[0] < RENAME_INTERVAL:
                    continue
            with self.db:
                self.db.execute("UPDATE stat_channels SET updated=? WHERE key=?", (now, key))
            try:
                self.channels[key] = await channel.edit(name=desired, reason="OYB live stats")
                if key in servers:
                    self._applied_state[key] = self._state_token(servers[key])
            except discord.HTTPException:
                LOG.exception("Stat channel update failed for %s; retry after cooldown", key)

    async def run(self):
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                LOG.exception("Server stats tick failed; retrying")
            await asyncio.sleep(POLL)
