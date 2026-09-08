"""Additive migrations and durable global time / XP accounting."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import time
from rank_rules import xp_from_seconds


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
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_wallet_v2'").fetchone():
            return
        backup_before(db, "rank-v2")
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute('''CREATE TABLE rank_wallet_v2(guild INTEGER, member INTEGER, identity TEXT,
                credit INTEGER, baseline INTEGER, milliseconds INTEGER, PRIMARY KEY(guild,member))''')
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_progress'").fetchone()
            if legacy:
                # Preserve the exact previously awarded test XP and partial minute.
                db.execute('''INSERT INTO rank_wallet_v2 SELECT guild,member,identity,
                    CAST(seconds/60 AS INTEGER)*10,NULL,CAST(ROUND((seconds-CAST(seconds/60 AS INTEGER)*60)*1000) AS INTEGER)
                    FROM rank_progress''')

    def read(self, guild, member, identity, path, ready):
        row = self.db.execute("SELECT identity,credit,baseline,milliseconds FROM rank_wallet_v2 WHERE guild=? AND member=?", (guild,member)).fetchone()
        if row and row[0] != identity:
            raise ValueError("Linked identity changed; admin review required")
        credit, baseline, earned = (row[1], row[2], row[3]) if row else (0, 0, 0)
        if ready:
            try:
                with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True)) as source:
                    total = source.execute("SELECT milliseconds FROM global_time WHERE identity=?", (identity,)).fetchone()
                    legacy = source.execute("SELECT milliseconds FROM global_time_legacy WHERE identity=?", (identity,)).fetchone() if baseline is None else None
                if total:
                    if baseline is None:
                        baseline = (legacy[0] if legacy else 0) - earned
                    earned = max(earned, total[0]-baseline)
            except sqlite3.Error:
                pass  # Keep the last durable balance when tracking is unavailable.
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO rank_wallet_v2 VALUES (?,?,?,?,?,?)", (guild, member, identity, credit, baseline, earned))
        return credit + xp_from_seconds(earned / 1000)

    def cached(self, guild, member):
        row = self.db.execute("SELECT credit,milliseconds FROM rank_wallet_v2 WHERE guild=? AND member=?", (guild,member)).fetchone()
        return row[0] + xp_from_seconds(row[1]/1000) if row else 0
