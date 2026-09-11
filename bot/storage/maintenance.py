"""Periodic retention sweep, run on a worker thread with its own connections."""
from __future__ import annotations

import asyncio
from contextlib import closing
import logging
import os
from pathlib import Path
import sqlite3
import time

from bot.config import configure_connection
import bot.storage.retention as retention

LOG = logging.getLogger("reforger.retention")


class Maintenance:
    def __init__(self, bot):
        self.bot = bot
        self.links_path = bot.account_links.db.execute("PRAGMA database_list").fetchone()[2]
        self.notif_path = bot.store.db.execute("PRAGMA database_list").fetchone()[2]
        self.playtime_path = os.getenv("PLAYTIME_DB", "data/playtime.sqlite3")
        try:
            self.interval = max(60, int(os.getenv("MAINTENANCE_INTERVAL_SECONDS",
                                retention.DEFAULTS["MAINTENANCE_INTERVAL_SECONDS"])))
        except ValueError:
            self.interval = retention.DEFAULTS["MAINTENANCE_INTERVAL_SECONDS"]

    def sweep(self):
        now = time.time()
        if self.links_path:
            try:
                with closing(sqlite3.connect(self.links_path, timeout=30)) as db:
                    configure_connection(db)
                    posts = retention.prune_post_events(
                        db, now - retention._days("POST_EVENT_RETENTION_DAYS") * 86400)
                    combat = retention.prune_combat_events(
                        db, retention.combat_cutoff_iso(now, retention._days("COMBAT_EVENT_RETENTION_DAYS")))
                if posts or combat:
                    LOG.info("Pruned %s post-XP and %s combat dedup rows (totals unchanged)", posts, combat)
            except sqlite3.Error:
                LOG.exception("Account-links retention sweep failed; will retry")
        if self.notif_path:
            try:
                with closing(sqlite3.connect(self.notif_path, timeout=30)) as db:
                    configure_connection(db)
                    done = retention.prune_finished_announcements(
                        db, now - retention._days("ANNOUNCEMENT_RETENTION_DAYS") * 86400)
                if done:
                    LOG.info("Pruned %s finished announcement rows", done)
            except sqlite3.Error:
                LOG.exception("Announcement retention sweep failed; will retry")
        if self.playtime_path and Path(self.playtime_path).is_file():
            try:
                with closing(sqlite3.connect(self.playtime_path, timeout=30)) as db:
                    configure_connection(db)
                    cutoff_ms = round(now * 1000) - retention._days("PLAYTIME_INTERVAL_RETENTION_DAYS") * 86400 * 1000
                    intervals = retention.compact_intervals(db, cutoff_ms)
                if intervals:
                    LOG.info("Compacted %s old playtime interval rows (combined XP unchanged)", intervals)
            except sqlite3.Error:
                LOG.exception("Playtime interval compaction failed; will retry")

    async def run(self):
        LOG.info("Database retention active; totals, links and checkpoints are preserved")
        while not self.bot.is_closed():
            try:
                await asyncio.to_thread(self.sweep)
            except Exception:
                LOG.exception("Retention sweep raised; will retry")
            await asyncio.sleep(self.interval)
