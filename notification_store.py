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
        """)

    def channel(self, server):
        return self.db.execute("SELECT * FROM channels WHERE server=?", (server,)).fetchone()

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
