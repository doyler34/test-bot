"""Durable announcement queue; restart does not cancel scheduled deletion."""
import sqlite3
from pathlib import Path


class NotificationStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                server TEXT PRIMARY KEY, channel INTEGER NOT NULL, info INTEGER
            );
            CREATE TABLE IF NOT EXISTS announcements (
                server TEXT NOT NULL, session TEXT NOT NULL,
                channel INTEGER NOT NULL, name TEXT NOT NULL,
                started REAL NOT NULL, queued REAL NOT NULL,
                message INTEGER, expires REAL, done INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (server, session)
            );
            CREATE TABLE IF NOT EXISTS leaderboard_display (
                guild INTEGER PRIMARY KEY, channel INTEGER, message INTEGER,
                page INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0
            );
        """)

    def channel(self, server):
        return self.db.execute("SELECT * FROM channels WHERE server=?", (server,)).fetchone()

    def leaderboard(self, guild):
        row = self.db.execute('SELECT * FROM leaderboard_display WHERE guild=?', (guild,)).fetchone()
        return dict(row) if row else dict(guild=guild, channel=None, message=None, page=0, retry_at=0)

    def save_leaderboard(self, state):
        with self.db:
            self.db.execute('''INSERT INTO leaderboard_display VALUES (:guild,:channel,:message,:page,:retry_at)
                ON CONFLICT(guild) DO UPDATE SET channel=excluded.channel,
                message=excluded.message,page=excluded.page,retry_at=excluded.retry_at''', state)

    def save_channel(self, server, channel, info=None):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO channels VALUES (?, ?, ?)",
                            (server, channel, info))

    def enqueue(self, server, session, channel, name, started, now):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO announcements "
                "(server, session, channel, name, started, queued) VALUES (?, ?, ?, ?, ?, ?)",
                (server, session, channel, name, started, now))

    def pending(self):
        return self.db.execute("SELECT * FROM announcements WHERE done=0").fetchall()

    def sent(self, row, message, expires):
        with self.db:
            self.db.execute("UPDATE announcements SET message=?, expires=? "
                            "WHERE server=? AND session=?",
                            (message, expires, row["server"], row["session"]))

    def finish(self, row):
        with self.db:
            self.db.execute("UPDATE announcements SET done=1 WHERE server=? AND session=?",
                            (row["server"], row["session"]))

    def close(self):
        self.db.close()
