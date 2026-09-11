"""Prune old raw dedup/event rows once their totals are aggregated.

Aggregate tables (XP, kills, deaths, playtime, links) are never touched; only
raw rows that can no longer legitimately replay are removed. Deletes are chunked
to keep each write transaction short.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os

LOG = logging.getLogger("reforger.retention")

DEFAULTS = {
    "POST_EVENT_RETENTION_DAYS": 30,
    "COMBAT_EVENT_RETENTION_DAYS": 30,
    "ANNOUNCEMENT_RETENTION_DAYS": 7,
    "PLAYTIME_INTERVAL_RETENTION_DAYS": 3,
    "MAINTENANCE_INTERVAL_SECONDS": 6 * 60 * 60,
}


def _days(name):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return DEFAULTS[name]
    try:
        value = int(raw)
    except ValueError:
        LOG.warning("%s=%r is not an integer; using default %s", name, raw, DEFAULTS[name])
        return DEFAULTS[name]
    return max(1, value)


def _prune(db, table, where, params, chunk=5000):
    removed = 0
    sql = f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} LIMIT ?)"
    while True:
        with db:
            cursor = db.execute(sql, (*params, chunk))
        if not cursor.rowcount:
            return removed
        removed += cursor.rowcount


def prune_post_events(db, cutoff_ts, chunk=5000):
    return _prune(db, "discord_post_events", "created < ?", (cutoff_ts,), chunk)


def prune_combat_events(db, cutoff_iso, chunk=5000):
    return _prune(db, "combat_events", "occurred < ?", (cutoff_iso,), chunk)


def prune_finished_announcements(db, cutoff_ts, chunk=5000):
    return _prune(db, "announcements", "done=1 AND queued < ?", (cutoff_ts,), chunk)


def compact_intervals(db, cutoff_ms, chunk=5000):
    return _prune(db, "global_intervals", "end < ?", (cutoff_ms,), chunk)


def combat_cutoff_iso(now, days):
    return (datetime.fromtimestamp(now, timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000")
