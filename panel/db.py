import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    must_change INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    last_login INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    at INTEGER NOT NULL,
    username TEXT NOT NULL,
    action TEXT NOT NULL,
    server TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS audit_at ON audit(at);
CREATE TABLE IF NOT EXISTS bans (
    id INTEGER PRIMARY KEY,
    identity TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    removed_by TEXT,
    removed_at INTEGER
);
CREATE TABLE IF NOT EXISTS ban_sync (
    ban_id INTEGER NOT NULL REFERENCES bans(id),
    server_id TEXT NOT NULL,
    action TEXT NOT NULL,
    done_at INTEGER NOT NULL,
    PRIMARY KEY (ban_id, server_id, action)
);
CREATE TABLE IF NOT EXISTS players (
    identity TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    last_server TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS player_names (
    identity TEXT NOT NULL,
    name TEXT NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (identity, name)
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    identity TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""


def now() -> int:
    return int(time.time())


class PanelDB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()

    def all(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def write(self, sql, *args) -> int:
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur.lastrowid

    # users

    def user(self, user_id: int):
        return self.one("SELECT * FROM users WHERE id = ?", user_id)

    def user_by_name(self, username: str):
        return self.one("SELECT * FROM users WHERE username = ?", username)

    def users(self):
        return self.all("SELECT * FROM users ORDER BY username COLLATE NOCASE")

    def add_user(self, username, password_hash, role, must_change=False) -> int:
        return self.write(
            "INSERT INTO users (username, password_hash, role, must_change, created_at) VALUES (?, ?, ?, ?, ?)",
            username, password_hash, role, int(must_change), now())

    def owner_count(self) -> int:
        return self.one("SELECT COUNT(*) FROM users WHERE role = 'owner' AND disabled = 0")[0]

    # audit

    def log(self, username, action, server="", target="", detail="", ok=True):
        self.write("INSERT INTO audit (at, username, action, server, target, detail, ok) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   now(), username, action, server, target, detail, int(ok))

    def audit(self, search="", limit=200, server=""):
        sql = "SELECT * FROM audit WHERE 1 = 1"
        args = []
        if server:
            sql += " AND server = ?"
            args.append(server)
        if search:
            sql += " AND (username LIKE ? OR action LIKE ? OR target LIKE ? OR detail LIKE ?)"
            args += [f"%{search}%"] * 4
        sql += " ORDER BY id DESC LIMIT ?"
        return self.all(sql, *args, limit)

    # bans

    def add_ban(self, identity, name, reason, created_by, expires_at=None) -> int:
        return self.write(
            "INSERT INTO bans (identity, name, reason, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            identity, name, reason, created_by, now(), expires_at)

    def ban(self, ban_id: int):
        return self.one("SELECT * FROM bans WHERE id = ?", ban_id)

    def bans(self, include_old=False):
        sql = "SELECT * FROM bans"
        if not include_old:
            sql += " WHERE removed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
            return self.all(sql + " ORDER BY id DESC", now())
        return self.all(sql + " ORDER BY id DESC")

    def active_ban(self, identity: str):
        return self.one("SELECT * FROM bans WHERE identity = ? AND removed_at IS NULL"
                        " AND (expires_at IS NULL OR expires_at > ?) ORDER BY id DESC", identity, now())

    def remove_ban(self, ban_id: int, removed_by: str):
        self.write("UPDATE bans SET removed_by = ?, removed_at = ? WHERE id = ? AND removed_at IS NULL",
                   removed_by, now(), ban_id)

    def mark_synced(self, ban_id: int, server_id: str, action: str):
        self.write("INSERT OR IGNORE INTO ban_sync (ban_id, server_id, action, done_at) VALUES (?, ?, ?, ?)",
                   ban_id, server_id, action, now())

    def synced(self, ban_id: int) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for row in self.all("SELECT server_id, action FROM ban_sync WHERE ban_id = ?", ban_id):
            result.setdefault(row["server_id"], set()).add(row["action"])
        return result

    def pending_bans(self, server_id: str):
        return self.all(
            "SELECT * FROM bans WHERE removed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
            " AND id NOT IN (SELECT ban_id FROM ban_sync WHERE server_id = ? AND action = 'ban')",
            now(), server_id)

    def pending_unbans(self, server_id: str):
        return self.all(
            "SELECT * FROM bans WHERE removed_at IS NOT NULL"
            " AND id IN (SELECT ban_id FROM ban_sync WHERE server_id = ? AND action = 'ban')"
            " AND id NOT IN (SELECT ban_id FROM ban_sync WHERE server_id = ? AND action = 'unban')",
            server_id, server_id)

    # players

    def saw_player(self, identity, name, server_id):
        t = now()
        self.db.execute(
            "INSERT INTO players (identity, name, first_seen, last_seen, last_server) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(identity) DO UPDATE SET name = excluded.name, last_seen = excluded.last_seen,"
            " last_server = excluded.last_server",
            (identity, name, t, t, server_id))
        self.db.execute(
            "INSERT INTO player_names (identity, name, last_seen) VALUES (?, ?, ?)"
            " ON CONFLICT(identity, name) DO UPDATE SET last_seen = excluded.last_seen",
            (identity, name, t))
        self.db.commit()

    def player(self, identity: str):
        return self.one("SELECT * FROM players WHERE identity = ?", identity)

    def player_names(self, identity: str):
        return self.all("SELECT * FROM player_names WHERE identity = ? ORDER BY last_seen DESC", identity)

    def search_players(self, text: str, limit=100):
        like = f"%{text}%"
        return self.all(
            "SELECT * FROM players WHERE identity LIKE ? OR identity IN"
            " (SELECT identity FROM player_names WHERE name LIKE ?) ORDER BY last_seen DESC LIMIT ?",
            like, like, limit)

    # notes

    def add_note(self, identity, author, body) -> int:
        return self.write("INSERT INTO notes (identity, author, body, created_at) VALUES (?, ?, ?, ?)",
                          identity, author, body, now())

    def notes(self, identity: str):
        return self.all("SELECT * FROM notes WHERE identity = ? ORDER BY id DESC", identity)
