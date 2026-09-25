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
CREATE TABLE IF NOT EXISTS server_events (
    id INTEGER PRIMARY KEY,
    server TEXT NOT NULL,
    at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS server_events_at ON server_events(server, at);
CREATE TABLE IF NOT EXISTS memory_samples (
    server TEXT NOT NULL,
    at INTEGER NOT NULL,
    rss INTEGER NOT NULL,
    PRIMARY KEY (server, at)
);
CREATE TABLE IF NOT EXISTS connections (
    identity TEXT NOT NULL,
    ip TEXT NOT NULL,
    guid TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    server TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    times INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (identity, ip)
);
CREATE INDEX IF NOT EXISTS connections_ip ON connections(ip);
CREATE TABLE IF NOT EXISTS log_positions (
    server TEXT NOT NULL,
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (server, path)
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

    def saw_player(self, identity, name, server_id, at=None, commit=True):
        t = at or now()
        self.db.execute(
            "INSERT INTO players (identity, name, first_seen, last_seen, last_server) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(identity) DO UPDATE SET"
            " name = CASE WHEN excluded.last_seen >= last_seen THEN excluded.name ELSE name END,"
            " last_server = CASE WHEN excluded.last_seen >= last_seen THEN excluded.last_server ELSE last_server END,"
            " first_seen = MIN(first_seen, excluded.first_seen), last_seen = MAX(last_seen, excluded.last_seen)",
            (identity, name, t, t, server_id))
        self.db.execute(
            "INSERT INTO player_names (identity, name, last_seen) VALUES (?, ?, ?)"
            " ON CONFLICT(identity, name) DO UPDATE SET last_seen = MAX(last_seen, excluded.last_seen)",
            (identity, name, t))
        if commit:
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

    # health

    def add_event(self, server, kind, detail="", at=None):
        self.write("INSERT INTO server_events (server, at, kind, detail) VALUES (?, ?, ?, ?)",
                   server, at or now(), kind, detail)

    def events(self, server, limit=20, kinds=None):
        sql = "SELECT * FROM server_events WHERE server = ?"
        args = [server]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        return self.all(sql + " ORDER BY at DESC, id DESC LIMIT ?", *args, limit)

    def uptime(self, server, since) -> float | None:
        """Share of time since `since` the server answered RCON, from its online/offline history."""
        rows = self.all("SELECT at, kind FROM server_events WHERE server = ? AND kind IN ('online', 'offline')"
                        " AND at >= ? ORDER BY at, id", server, since)
        before = self.one("SELECT kind FROM server_events WHERE server = ? AND kind IN ('online', 'offline')"
                          " AND at < ? ORDER BY at DESC, id DESC LIMIT 1", server, since)
        if not rows and not before:
            return None
        state = before["kind"] if before else None
        start = since if before else rows[0]["at"]
        cursor, up = start, 0
        for row in rows:
            if state == "online":
                up += row["at"] - cursor
            state, cursor = row["kind"], row["at"]
        if state == "online":
            up += now() - cursor
        span = now() - start
        return up / span if span > 0 else (1.0 if state == "online" else 0.0)

    def add_memory(self, server, rss, at=None):
        t = at or now()
        self.write("INSERT OR REPLACE INTO memory_samples (server, at, rss) VALUES (?, ?, ?)", server, t, rss)
        self.write("DELETE FROM memory_samples WHERE at < ?", t - 7 * 86400)

    def memory(self, server, since):
        return self.all("SELECT at, rss FROM memory_samples WHERE server = ? AND at >= ? ORDER BY at", server, since)

    # connections

    def add_connections(self, server, events, positions):
        for e in events:
            self.saw_player(e["identity"], e["name"], server, at=e["at"], commit=False)
            if e["ip"]:
                self.db.execute(
                    "INSERT INTO connections (identity, ip, guid, name, server, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(identity, ip) DO UPDATE SET times = times + 1,"
                    " guid = CASE WHEN excluded.guid != '' THEN excluded.guid ELSE guid END,"
                    " name = CASE WHEN excluded.last_seen >= last_seen THEN excluded.name ELSE name END,"
                    " server = CASE WHEN excluded.last_seen >= last_seen THEN excluded.server ELSE server END,"
                    " first_seen = MIN(first_seen, excluded.first_seen), last_seen = MAX(last_seen, excluded.last_seen)",
                    (e["identity"], e["ip"], e["guid"], e["name"], server, e["at"], e["at"]))
        for path, position in positions.items():
            self.db.execute("INSERT OR REPLACE INTO log_positions (server, path, position) VALUES (?, ?, ?)",
                            (server, path, position))
        self.db.commit()

    def log_positions(self, server) -> dict[str, int]:
        return {r["path"]: r["position"] for r in self.all("SELECT path, position FROM log_positions WHERE server = ?", server)}

    def ips(self, identity):
        return self.all("SELECT * FROM connections WHERE identity = ? ORDER BY last_seen DESC", identity)

    def alts(self, identity):
        """Other accounts that connected from any address this one used."""
        return self.all(
            "SELECT other.identity, COALESCE(p.name, other.name) AS name, MAX(other.last_seen) AS last_seen,"
            " GROUP_CONCAT(DISTINCT other.ip) AS shared,"
            " EXISTS (SELECT 1 FROM bans b WHERE b.identity = other.identity AND b.removed_at IS NULL"
            "   AND (b.expires_at IS NULL OR b.expires_at > ?)) AS banned"
            " FROM connections mine JOIN connections other ON other.ip = mine.ip AND other.identity != mine.identity"
            " LEFT JOIN players p ON p.identity = other.identity"
            " WHERE mine.identity = ? GROUP BY other.identity ORDER BY last_seen DESC", now(), identity)

    def alt_summary(self, identities) -> dict[str, dict]:
        """For the live list: how many other accounts share an address, and whether one is banned."""
        result = {}
        for identity in identities:
            rows = self.alts(identity)
            if rows:
                result[identity] = {"count": len(rows), "banned": [r["name"] for r in rows if r["banned"]]}
        return result

    def search_ip(self, text, limit=100):
        return self.all(
            "SELECT p.* FROM players p WHERE p.identity IN (SELECT identity FROM connections WHERE ip LIKE ?)"
            " ORDER BY p.last_seen DESC LIMIT ?", f"{text}%", limit)

    def prune_connections(self, days=180):
        self.write("DELETE FROM connections WHERE last_seen < ?", now() - days * 86400)

    # notes

    def add_note(self, identity, author, body) -> int:
        return self.write("INSERT INTO notes (identity, author, body, created_at) VALUES (?, ?, ?, ?)",
                          identity, author, body, now())

    def notes(self, identity: str):
        return self.all("SELECT * FROM notes WHERE identity = ? ORDER BY id DESC", identity)
