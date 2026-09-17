"""Additive migrations and durable global time / XP accounting."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import sqlite3
import time
from bot.ranks.rank_rules import xp_from_seconds, POSTS_PER_DAY, XP_PER_POST

_UNSET = object()


def backup_before(db, version):
    raw = db.execute("PRAGMA database_list").fetchone()[2]
    if not raw:
        return
    dest = Path(raw + f".before-{version}.sqlite3")
    if dest.exists():
        return
    temp = dest.with_suffix(".tmp")
    with closing(sqlite3.connect(temp)) as copy:
        db.backup(copy)
    os.chmod(temp, 0o600)
    os.replace(temp, dest)


def migrate_time(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='global_time'").fetchone():
        return
    backup_before(db, "global-time-v2")
    has_history = db.execute("SELECT 1 FROM totals LIMIT 1").fetchone()
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE global_time(identity TEXT PRIMARY KEY, milliseconds INTEGER NOT NULL)")
        db.execute("CREATE TABLE global_intervals(identity TEXT, start INTEGER, end INTEGER, PRIMARY KEY(identity,start))")
        db.execute("CREATE TABLE global_time_meta(cutover INTEGER NOT NULL)")
        db.execute("INSERT INTO global_time_meta VALUES (?)", (round(time.time()*1000) if has_history else 0,))
        db.execute("INSERT INTO global_time SELECT identity,CAST(ROUND(SUM(seconds)*1000) AS INTEGER) FROM totals GROUP BY identity")
        db.execute("CREATE TABLE global_time_legacy AS SELECT * FROM global_time")


def record_interval(db, identity, start, end):
    """Union intervals in the caller's cursor transaction, including cross-server overlaps."""
    cutover = db.execute("SELECT cutover FROM global_time_meta").fetchone()[0]
    start, end = max(cutover, round(start*1000)), round(end*1000)
    if end <= start:
        return
    rows = db.execute("SELECT start,end FROM global_intervals WHERE identity=? AND start<=? AND end>=?", (identity, end, start)).fetchall()
    a = min([start] + [r[0] for r in rows])
    b = max([end] + [r[1] for r in rows])
    added = b-a-sum(y-x for x, y in rows)
    db.execute("DELETE FROM global_intervals WHERE identity=? AND start<=? AND end>=?", (identity, end, start))
    db.execute("INSERT INTO global_intervals VALUES (?,?,?)", (identity, a, b))
    db.execute("INSERT INTO global_time VALUES (?,?) ON CONFLICT(identity) DO UPDATE SET milliseconds=milliseconds+excluded.milliseconds", (identity, added))


class XPStore:
    def __init__(self, db):
        self.db = db
        self._migrate_posts()
        with db:
            db.execute('CREATE INDEX IF NOT EXISTS discord_post_events_day ON discord_post_events(guild,member,created)')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_wallet_v2'").fetchone():
            self._widen_wallet()
            return
        backup_before(db, "rank-v2")
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute('''CREATE TABLE rank_wallet_v2(guild INTEGER, member INTEGER, identity TEXT,
                credit INTEGER, baseline INTEGER, milliseconds INTEGER, PRIMARY KEY(guild,member,identity))''')
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_progress'").fetchone()
            if legacy:
                # Preserve the exact previously awarded test XP and partial minute.
                db.execute('''INSERT INTO rank_wallet_v2 SELECT guild,member,identity,
                    CAST(seconds/60 AS INTEGER)*10,NULL,CAST(ROUND((seconds-CAST(seconds/60 AS INTEGER)*60)*1000) AS INTEGER)
                    FROM rank_progress''')

    def _widen_wallet(self):
        """One wallet row per game account, not per member, so a player on two
        platforms keeps a separate baseline for each. Existing rows are already
        one per member, so the rebuild carries them over untouched."""
        sql = self.db.execute("SELECT sql FROM sqlite_master WHERE name='rank_wallet_v2'").fetchone()
        if not sql or "PRIMARYKEY(guild,member,identity)" in sql[0].replace(" ", ""):
            return
        backup_before(self.db, "rank-wallet-multi")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute('''CREATE TABLE rank_wallet_v3(guild INTEGER, member INTEGER, identity TEXT,
                credit INTEGER, baseline INTEGER, milliseconds INTEGER, PRIMARY KEY(guild,member,identity))''')
            self.db.execute("INSERT INTO rank_wallet_v3 SELECT guild,member,identity,credit,baseline,milliseconds FROM rank_wallet_v2")
            self.db.execute("DROP TABLE rank_wallet_v2")
            self.db.execute("ALTER TABLE rank_wallet_v3 RENAME TO rank_wallet_v2")

    def snapshot(self, path):
        """One batched read-only load of every identity's tracked/legacy time.

        The rank sync builds this once per tick and passes it to read(), so a
        tick makes a single read-only connection instead of one per player.
        Returns None when tracking is unavailable, so callers keep the last
        durable balance exactly as read()'s per-connection fallback does.
        """
        try:
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as source:
                source.execute("PRAGMA busy_timeout=5000")
                total = dict(source.execute("SELECT identity,milliseconds FROM global_time").fetchall())
                try:
                    legacy = dict(source.execute("SELECT identity,milliseconds FROM global_time_legacy").fetchall())
                except sqlite3.Error:
                    legacy = {}
        except sqlite3.Error:
            return None
        return {identity: (ms, legacy.get(identity)) for identity, ms in total.items()}

    def total(self, guild, member, identities, path, ready, snapshot=_UNSET):
        """Combined XP for every game account the member holds.

        Playtime is summed in milliseconds before converting, so a player split
        across two platforms keeps the partial minutes each one contributes
        instead of having both rounded down.
        """
        credit = earned = 0
        for identity in identities:
            one, milliseconds = self.wallet(guild, member, identity, path, ready, snapshot)
            credit += one
            earned += milliseconds
        return (credit + xp_from_seconds(earned / 1000) + self.post_xp(guild, member)
                + sum(self.combat_xp(i) for i in identities))

    def read(self, guild, member, identity, path, ready, snapshot=_UNSET):
        """One identity's XP. total() is what the rank loop uses."""
        credit, earned = self.wallet(guild, member, identity, path, ready, snapshot)
        return (credit + xp_from_seconds(earned / 1000) + self.post_xp(guild, member)
                + self.combat_xp(identity))

    def wallet(self, guild, member, identity, path, ready, snapshot=_UNSET):
        """Durable credit and tracked milliseconds for a single game account."""
        row = self.db.execute("SELECT credit,baseline,milliseconds FROM rank_wallet_v2 WHERE guild=? AND member=? AND identity=?",
                              (guild,member,identity)).fetchone()
        credit, baseline, earned = row if row else (0, 0, 0)
        if ready:
            total_ms = legacy_ms = None
            if snapshot is _UNSET:
                # No batch supplied (a direct/status read): open one connection here.
                try:
                    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)) as source:
                        total = source.execute("SELECT milliseconds FROM global_time WHERE identity=?", (identity,)).fetchone()
                        total_ms = total[0] if total else None
                        if baseline is None:
                            legacy = source.execute("SELECT milliseconds FROM global_time_legacy WHERE identity=?", (identity,)).fetchone()
                            legacy_ms = legacy[0] if legacy else None
                except sqlite3.Error:
                    total_ms = None  # Keep the last durable balance when tracking is unavailable.
            elif snapshot is not None:
                data = snapshot.get(identity)
                if data is not None:
                    total_ms, legacy_ms = data
            # snapshot is None -> tracking unavailable this tick; keep the balance.
            if total_ms is not None:
                if baseline is None:
                    baseline = (legacy_ms if legacy_ms is not None else 0) - earned
                earned = max(earned, total_ms - baseline)
        # Write only when the durable values actually changed; a stable rank
        # must not rewrite rank_wallet_v2 on every sync.
        if (row if row else None) != (credit, baseline, earned):
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO rank_wallet_v2 VALUES (?,?,?,?,?,?)", (guild, member, identity, credit, baseline, earned))
        return credit, earned

    def played(self, guild, member):
        """Tracked server time in milliseconds across every linked account.
        total() refreshes it, so call this afterwards for the current figure."""
        row = self.db.execute("SELECT SUM(milliseconds) FROM rank_wallet_v2 WHERE guild=? AND member=?", (guild,member)).fetchone()
        return (row[0] or 0) if row else 0

    def cached(self, guild, member):
        rows = self.db.execute("SELECT credit,milliseconds,identity FROM rank_wallet_v2 WHERE guild=? AND member=?", (guild,member)).fetchall()
        banked = (sum(r[0] for r in rows) + xp_from_seconds(sum(r[1] for r in rows) / 1000)
                  + sum(self.combat_xp(r[2]) for r in rows))
        return banked + self.post_xp(guild, member)

    def combat_xp(self, identity):
        # Imported here because combat_store needs backup_before from this module.
        from bot.storage.combat_store import combat_xp
        try:
            return combat_xp(self.db, identity)
        except sqlite3.Error:
            return 0  # Combat tables absent on a links-only database.

    def _migrate_posts(self):
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='discord_post_events'").fetchone():
            return
        backup_before(self.db, 'discord-post-xp-v1')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('''CREATE TABLE discord_post_events (
                message INTEGER PRIMARY KEY, guild INTEGER NOT NULL, member INTEGER NOT NULL,
                identity TEXT NOT NULL, xp INTEGER NOT NULL, created REAL NOT NULL)''')
            self.db.execute('''CREATE TABLE discord_post_totals (
                guild INTEGER, member INTEGER, identity TEXT NOT NULL, xp INTEGER NOT NULL,
                PRIMARY KEY(guild,member))''')

    def posts_on_day(self, guild, member, created):
        """Posts already credited on the same day as this one. The day boundary
        is the community's, the same one the weekly leaderboard turns over on,
        and it follows the post rather than the clock so a replayed backlog is
        still capped against the day it was written."""
        from bot.storage.combat_store import day_start
        midnight = day_start(datetime.fromtimestamp(created, timezone.utc))
        return self.db.execute('''SELECT COUNT(*) FROM discord_post_events
            WHERE guild=? AND member=? AND created>=? AND created<?''',
            (guild, member, midnight.timestamp(),
             (midnight + timedelta(days=1)).timestamp())).fetchone()[0]

    def post_xp(self, guild, member):
        row = self.db.execute('SELECT xp FROM discord_post_totals WHERE guild=? AND member=?',
                              (guild,member)).fetchone()
        return row[0] if row else 0

    def award_post(self, guild, member, message, created):
        """Credit a newly observed post once, in the existing account-links DB."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            link = self.db.execute('SELECT identity,linked_at FROM account_links WHERE guild=? AND discord_id=?',
                                   (guild,member)).fetchone()
            if not link or created < link[1]:
                return 0
            identity = link[0]
            previous = self.db.execute('SELECT identity FROM discord_post_totals WHERE guild=? AND member=?',
                                       (guild,member)).fetchone()
            if previous and previous[0] != identity:
                raise ValueError('Linked identity changed; admin review required')
            if self.posts_on_day(guild, member, created) >= POSTS_PER_DAY:
                return 0
            inserted = self.db.execute('INSERT OR IGNORE INTO discord_post_events VALUES (?,?,?,?,?,?)',
                                        (message,guild,member,identity,XP_PER_POST,created))
            if not inserted.rowcount:
                return 0
            self.db.execute('''INSERT INTO discord_post_totals VALUES (?,?,?,?)
                ON CONFLICT(guild,member) DO UPDATE SET xp=xp+excluded.xp''',
                (guild,member,identity,XP_PER_POST))
        return XP_PER_POST
