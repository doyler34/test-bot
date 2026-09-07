"""Approximate category elapsed time, with persisted five-minute rename pacing."""
import asyncio
import logging
import time
import re
import discord

LOG = logging.getLogger("reforger.categories")
BASES = {"server-1": "SERVER ONE", "server-2": "SERVER TWO", "server-3": "SERVER THREE"}


def matches_category(category, server):
    base = BASES.get(server.id, server.name.upper())[:65]
    return isinstance(category, discord.CategoryChannel) and bool(re.fullmatch(
        rf"(?:🟢 )?{re.escape(base)} · (?:WAITING|COMING SOON|~\d+ MIN)", category.name))


def category_name(server, match, now):
    base = BASES.get(server.id, server.name.upper())[:65]
    if not server.enabled:
        return f"{base} · COMING SOON"
    if match is None:
        return f"{base} · WAITING"
    minutes = max(0, int((now - match[1]) // 60))
    return f"🟢 {base} · ~{minutes} MIN"


class CategoryTimers:
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.store.db
        self.channels = {}
        self.db.execute("CREATE TABLE IF NOT EXISTS category_timers (server TEXT PRIMARY KEY, channel INTEGER NOT NULL, updated REAL NOT NULL)")
        self.db.commit()

    async def prepare(self, guild, server):
        row = self.db.execute("SELECT channel FROM category_timers WHERE server=?", (server.id,)).fetchone()
        category = guild.get_channel(row[0]) if row else None
        if category is not None and not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("Saved category is no longer a category")
        matches = [c for c in getattr(guild, "categories", []) if matches_category(c, server)]
        with_voice = [c for c in matches if c.voice_channels]
        if with_voice:
            category = min(with_voice, key=lambda c: c.id)
        elif category is None and matches:
            category = min(matches, key=lambda c: c.id)
        if category is None:
            category = await guild.create_category(category_name(server, None, 0),
                                                    reason="OYB server grouping and approximate match time")
        if row is None or row[0] != category.id:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO category_timers VALUES (?,?,0)",
                                (server.id, category.id))
        self.channels[server.id] = category
        return category

    async def tick(self):
        for server in self.bot.config.servers:
            monitor = next((m for sid, m in getattr(self.bot, "monitors", [])
                            if sid == server.id), None)
            if monitor is not None and not monitor.initialized:
                # Do not spend the rename budget on WAITING while startup
                # history is still being read and the match age is unknown.
                continue
            category = self.channels.get(server.id)
            if category is None:
                continue
            now = time.time()
            row = self.db.execute("SELECT updated FROM category_timers WHERE server=?", (server.id,)).fetchone()
            if row and now - row[0] < 300:
                continue
            desired = category_name(server, self.bot.match_times.get(server.id), time.monotonic())
            if category.name == desired:
                continue
            # Reserve before sending: restart/error retries also obey the budget.
            with self.db:
                self.db.execute("UPDATE category_timers SET updated=? WHERE server=?", (now, server.id))
            try:
                self.channels[server.id] = await category.edit(name=desired, reason="OYB approximate match time")
            except discord.HTTPException:
                LOG.exception("Category update failed for %s; will retry after cooldown", server.id)

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(30)
