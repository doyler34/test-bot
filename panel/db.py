import ipaddress
import json
import sqlite3
import time
from pathlib import Path

FEED_SHOWS = {"joins": ("join", "leave", "side"), "kills": ("kill", "teamkill"), "tks": ("teamkill",), "sus": ("sus",),
              "rcon": ("rcon", "server"), "admin": ()}

# Columns added after their table first shipped: older databases get them on start.
ADDED_COLUMNS = (("server_samples", "sock_drops", "REAL"), ("notes", "edited_by", "TEXT"), ("notes", "edited_at", "INTEGER"), ("warnings", "used_ban", "INTEGER"), ("warnings", "edited_by", "TEXT"), ("warnings", "edited_at", "INTEGER"), ("users", "toured", "INTEGER NOT NULL DEFAULT 0"), ("users", "manage_toured", "INTEGER NOT NULL DEFAULT 0"),
                 *(("kills", c, "REAL") for c in ("victim_x", "victim_y", "killer_x", "killer_y")),
                 *(("box_samples", c, "REAL") for c in ("disk_pct", "disk_read", "disk_write", "temp", "conntrack",
                                                        "tcp_retrans", "softnet_drop", "nic_errors")))

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
CREATE TABLE IF NOT EXISTS user_grants (
    user_id INTEGER NOT NULL REFERENCES users(id),
    grant_key TEXT NOT NULL,
    PRIMARY KEY (user_id, grant_key)
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
CREATE INDEX IF NOT EXISTS audit_server ON audit(server, at);
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
CREATE TABLE IF NOT EXISTS bm_bans (
    ban_id INTEGER PRIMARY KEY REFERENCES bans(id),
    bm_id TEXT NOT NULL,
    source TEXT NOT NULL,
    reason_sent TEXT NOT NULL DEFAULT '',
    removed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS bm_bans_bm ON bm_bans(bm_id);
CREATE TABLE IF NOT EXISTS bm_players (
    player TEXT PRIMARY KEY,
    identities TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bm_player_names (
    player TEXT PRIMARY KEY,
    name TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS login_attempts (
    at INTEGER NOT NULL,
    ip TEXT NOT NULL,
    source TEXT NOT NULL,
    user TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS login_attempts_at ON login_attempts (at);
CREATE INDEX IF NOT EXISTS login_attempts_ip ON login_attempts (ip, at);
CREATE TABLE IF NOT EXISTS trusted_ips (
    ip TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    added_by TEXT NOT NULL DEFAULT '',
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS box_samples (
    at INTEGER PRIMARY KEY,
    cpu REAL, load1 REAL, mem_used INTEGER, mem_total INTEGER, swap_used INTEGER,
    rx_mbps REAL, tx_mbps REAL, rx_pps REAL, tx_pps REAL,
    rx_drop REAL, udp_errors REAL, udp_rcvbuf REAL, udp_sndbuf REAL
);
CREATE TABLE IF NOT EXISTS server_samples (
    server TEXT NOT NULL,
    at INTEGER NOT NULL,
    players INTEGER, proc_cpu REAL, rss INTEGER, threads INTEGER, fps REAL,
    query_ok INTEGER, query_ms REAL, disconnects INTEGER, errors INTEGER, reasons TEXT, sock_drops REAL,
    PRIMARY KEY (server, at)
);
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
CREATE TABLE IF NOT EXISTS feed (
    id INTEGER PRIMARY KEY,
    server TEXT NOT NULL,
    at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS feed_at ON feed(server, at);
CREATE INDEX IF NOT EXISTS feed_kind ON feed(server, kind, at);
-- The sus flags on every server at once, for the bar along the top of each page.
CREATE INDEX IF NOT EXISTS feed_sus ON feed(kind, at);
CREATE TABLE IF NOT EXISTS ip_bans (
    ban_id INTEGER NOT NULL REFERENCES bans(id),
    ip TEXT NOT NULL,
    PRIMARY KEY (ban_id, ip)
);
CREATE INDEX IF NOT EXISTS ip_bans_ip ON ip_bans(ip);
CREATE INDEX IF NOT EXISTS bans_identity ON bans(identity);
CREATE TABLE IF NOT EXISTS log_positions (
    server TEXT NOT NULL,
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (server, path)
);
CREATE TABLE IF NOT EXISTS discord_docs (
    key TEXT PRIMARY KEY,
    draft TEXT,
    draft_by TEXT,
    draft_at INTEGER,
    published TEXT,
    published_by TEXT,
    published_at INTEGER,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS link_actions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    identity TEXT,
    by TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS staff (
    identity TEXT PRIMARY KEY,
    added_by TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_alerts (
    id INTEGER PRIMARY KEY,
    server TEXT NOT NULL,
    at INTEGER NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_links (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    server TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    uses INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS baselines (
    server TEXT PRIMARY KEY,
    pid INTEGER NOT NULL,
    started INTEGER NOT NULL,
    rss INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS log_archive (
    server TEXT NOT NULL,
    folder TEXT NOT NULL,
    path TEXT NOT NULL,
    started INTEGER NOT NULL,
    ended INTEGER NOT NULL,
    size INTEGER NOT NULL,
    players INTEGER NOT NULL,
    kills INTEGER NOT NULL,
    flags INTEGER NOT NULL,
    PRIMARY KEY (server, folder)
);
CREATE TABLE IF NOT EXISTS game_players (
    server TEXT NOT NULL,
    folder TEXT NOT NULL,
    identity TEXT NOT NULL,
    name TEXT NOT NULL,
    started INTEGER NOT NULL,
    seconds INTEGER NOT NULL,
    kills INTEGER NOT NULL,
    deaths INTEGER NOT NULL,
    rifle INTEGER NOT NULL,
    heads INTEGER NOT NULL,
    long INTEGER NOT NULL,
    metres REAL NOT NULL,
    measured INTEGER NOT NULL,
    PRIMARY KEY (server, folder, identity)
);
CREATE INDEX IF NOT EXISTS game_players_started ON game_players(started);
-- Each player's latest name for the Unusual and Investigate comparisons.
CREATE INDEX IF NOT EXISTS game_players_identity ON game_players(identity, started);
CREATE TABLE IF NOT EXISTS scored_games (
    server TEXT NOT NULL,
    folder TEXT NOT NULL,
    PRIMARY KEY (server, folder)
);
CREATE TABLE IF NOT EXISTS kills (
    server TEXT NOT NULL,
    game INTEGER NOT NULL,
    at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    killer TEXT NOT NULL,
    killer_name TEXT NOT NULL,
    victim TEXT NOT NULL,
    victim_name TEXT NOT NULL,
    damage TEXT NOT NULL,
    distance REAL,
    zone TEXT NOT NULL,
    UNIQUE (server, at, kind, killer, victim)
);
CREATE INDEX IF NOT EXISTS kills_killer ON kills(killer, at);
CREATE INDEX IF NOT EXISTS kills_victim ON kills(victim, at);
CREATE INDEX IF NOT EXISTS kills_server ON kills(server, at);
CREATE INDEX IF NOT EXISTS kills_at ON kills(at);
CREATE TABLE IF NOT EXISTS platforms (
    identity TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    steam_id TEXT NOT NULL DEFAULT '',
    last_seen INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS platforms_steam ON platforms(steam_id);
CREATE TABLE IF NOT EXISTS platforms_read (
    server TEXT NOT NULL,
    folder TEXT NOT NULL,
    PRIMARY KEY (server, folder)
);
CREATE TABLE IF NOT EXISTS kill_places_read (
    server TEXT NOT NULL,
    folder TEXT NOT NULL,
    PRIMARY KEY (server, folder)
);
CREATE TABLE IF NOT EXISTS kills_read (
    server TEXT NOT NULL,
    folder TEXT NOT NULL,
    PRIMARY KEY (server, folder)
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    server TEXT NOT NULL,
    at INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_players (
    incident INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    identity TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (incident, identity)
);
CREATE INDEX IF NOT EXISTS incident_players_identity ON incident_players(identity);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    identity TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY,
    identity TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    used_at INTEGER,
    used_for TEXT,
    withdrawn_by TEXT,
    withdrawn_at INTEGER
);
CREATE INDEX IF NOT EXISTS warnings_identity ON warnings (identity);
"""


def now() -> int:
    return int(time.time())


class PanelDB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        if path != ":memory:":
            # The bot reads this file too. In WAL mode its reads and the panel's writes don't wait on each other.
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        for table, column, kind in ADDED_COLUMNS:
            if column not in {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self.db.commit()

    def close(self):
        self.db.close()

    def reader(self) -> "PanelDB":
        """A read-only connection of its own, for a slow page to read from in another thread."""
        other = PanelDB.__new__(PanelDB)
        other.path = self.path
        other.db = sqlite3.connect(Path(self.path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        other.db.row_factory = sqlite3.Row
        return other

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

    def first_manager(self, username):
        """Make this senior admin the manager, until there's one already."""
        if not self.one("SELECT COUNT(*) FROM users WHERE role = 'manager'")[0]:
            self.write("UPDATE users SET role = 'manager' WHERE username = ? COLLATE NOCASE AND role = 'owner'", username)

    def grants(self, user_id) -> set[str]:
        return {r["grant_key"] for r in self.all("SELECT grant_key FROM user_grants WHERE user_id = ?", user_id)}

    def all_grants(self) -> dict[int, set[str]]:
        found = {}
        for r in self.all("SELECT user_id, grant_key FROM user_grants"):
            found.setdefault(r["user_id"], set()).add(r["grant_key"])
        return found

    def set_grants(self, user_id, grants):
        self.db.execute("DELETE FROM user_grants WHERE user_id = ?", (user_id,))
        self.db.executemany("INSERT INTO user_grants (user_id, grant_key) VALUES (?, ?)",
                            [(user_id, g) for g in sorted(grants)])
        self.db.commit()

    def users(self):
        return self.all("SELECT * FROM users ORDER BY username COLLATE NOCASE")

    def add_user(self, username, password_hash, role, must_change=False) -> int:
        return self.write(
            "INSERT INTO users (username, password_hash, role, must_change, created_at) VALUES (?, ?, ?, ?, ?)",
            username, password_hash, role, int(must_change), now())

    def owner_count(self) -> int:
        return self.one("SELECT COUNT(*) FROM users WHERE role IN ('owner', 'manager') AND disabled = 0")[0]

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
        sql += " ORDER BY at DESC, id DESC LIMIT ?" if server and not search else " ORDER BY id DESC LIMIT ?"
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

    def set_ban_expiry(self, ban_id: int, expires_at):
        self.write("UPDATE bans SET expires_at = ? WHERE id = ?", expires_at, ban_id)
        self.write("UPDATE bm_bans SET reason_sent = '' WHERE ban_id = ?", ban_id)

    def clear_synced(self, ban_id: int):
        self.write("DELETE FROM ban_sync WHERE ban_id = ?", ban_id)

    def set_ban_reason(self, ban_id: int, reason: str):
        self.write("UPDATE bans SET reason = ? WHERE id = ?", reason, ban_id)

    def remove_ban(self, ban_id: int, removed_by: str):
        self.write("UPDATE bans SET removed_by = ?, removed_at = ? WHERE id = ? AND removed_at IS NULL",
                   removed_by, now(), ban_id)

    # BattleMetrics: which panel ban is which BattleMetrics ban

    def link_bm(self, ban_id, bm_id, source):
        self.write("INSERT OR REPLACE INTO bm_bans (ban_id, bm_id, source, reason_sent)"
                   " SELECT id, ?, ?, reason FROM bans WHERE id = ?", bm_id, source, ban_id)

    def bm_ban(self, ban_id):
        return self.one("SELECT * FROM bm_bans WHERE ban_id = ?", ban_id)

    def bm_link(self, bm_id):
        return self.one("SELECT * FROM bm_bans WHERE bm_id = ?", bm_id)

    def bm_links_active(self):
        return self.all("SELECT m.ban_id, m.bm_id, b.expires_at FROM bm_bans m JOIN bans b ON b.id = m.ban_id"
                        " WHERE m.removed = 0 AND b.removed_at IS NULL")

    def bans_not_on_bm(self):
        return self.all("SELECT * FROM bans WHERE removed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
                        " AND id NOT IN (SELECT ban_id FROM bm_bans) ORDER BY id", now())

    def bm_reasons_changed(self):
        return self.all("SELECT m.ban_id, m.bm_id, b.reason, b.expires_at FROM bm_bans m JOIN bans b ON b.id = m.ban_id"
                        " WHERE m.removed = 0 AND b.removed_at IS NULL AND b.reason != m.reason_sent")

    def mark_bm_reason_sent(self, ban_id):
        self.write("UPDATE bm_bans SET reason_sent = (SELECT reason FROM bans WHERE id = ?) WHERE ban_id = ?",
                   ban_id, ban_id)

    def bm_unbans_to_send(self):
        return self.all("SELECT m.ban_id, m.bm_id FROM bm_bans m JOIN bans b ON b.id = m.ban_id"
                        " WHERE m.removed = 0 AND b.removed_at IS NOT NULL")

    def mark_bm_removed(self, ban_id):
        self.write("UPDATE bm_bans SET removed = 1 WHERE ban_id = ?", ban_id)

    def name_bm_bans(self, bm_id, name):
        """Bans that came in before their player's name was known."""
        self.write("UPDATE bans SET name = ? WHERE name = '' AND id IN (SELECT ban_id FROM bm_bans WHERE bm_id = ?)",
                   name, bm_id)

    def bm_player(self, player):
        row = self.one("SELECT p.*, n.name, n.player IS NOT NULL AS named FROM bm_players p"
                       " LEFT JOIN bm_player_names n ON n.player = p.player WHERE p.player = ?", player)
        if row is None:
            return None
        return {"identities": json.loads(row["identities"]), "name": row["name"] or "", "named": bool(row["named"]),
                "at": row["at"]}

    def save_bm_player(self, player, identities, name=""):
        self.write("INSERT OR REPLACE INTO bm_players (player, identities, at) VALUES (?, ?, ?)",
                   player, json.dumps(identities), now())
        self.write("INSERT OR REPLACE INTO bm_player_names (player, name) VALUES (?, ?)", player, name)

    def bm_sources(self) -> dict[int, str]:
        return {r["ban_id"]: r["source"] for r in self.all("SELECT ban_id, source FROM bm_bans")}

    def add_ip_bans(self, ban_id: int, ips):
        self.db.executemany("INSERT OR IGNORE INTO ip_bans (ban_id, ip) VALUES (?, ?)", [(ban_id, ip) for ip in ips])
        self.db.commit()

    def ban_ips(self, ban_id: int) -> list[str]:
        return [r["ip"] for r in self.all("SELECT ip FROM ip_bans WHERE ban_id = ? ORDER BY ip", ban_id)]

    def accounts_on_ips(self, ips, exclude=""):
        """Every other account seen on any of these addresses, newest first."""
        ips = list(ips)
        if not ips:
            return []
        marks = ",".join("?" * len(ips))
        return self.all(
            f"SELECT c.identity, COALESCE(p.name, MAX(c.name)) AS name FROM connections c"
            f" LEFT JOIN players p ON p.identity = c.identity"
            f" WHERE c.ip IN ({marks}) AND c.identity != ? GROUP BY c.identity ORDER BY MAX(c.last_seen) DESC",
            *ips, exclude)

    def banned_ips(self) -> set[str]:
        return {r["ip"] for r in self.all(
            "SELECT i.ip FROM ip_bans i JOIN bans b ON b.id = i.ban_id WHERE b.removed_at IS NULL"
            " AND (b.expires_at IS NULL OR b.expires_at > ?)", now())}

    def ip_ban_hit(self, identity: str, within=12 * 3600):
        """Another player's active IP ban on an address this identity connected from recently."""
        t = now()
        return self.one(
            "SELECT b.*, c.ip AS hit_ip FROM connections c JOIN ip_bans i ON i.ip = c.ip JOIN bans b ON b.id = i.ban_id"
            " WHERE c.identity = ? AND c.last_seen >= ? AND b.identity != ? AND b.removed_at IS NULL"
            " AND (b.expires_at IS NULL OR b.expires_at > ?) ORDER BY b.id DESC LIMIT 1",
            identity, t - within, identity, t)

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

    def by_name(self, name: str):
        """Everyone who has gone by exactly this name, ignoring case."""
        return self.all(
            "SELECT * FROM players WHERE name = ? COLLATE NOCASE OR identity IN"
            " (SELECT identity FROM player_names WHERE name = ? COLLATE NOCASE) ORDER BY last_seen DESC",
            name, name)

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

    def save_baseline(self, server, pid, started, rss):
        self.write("INSERT OR REPLACE INTO baselines (server, pid, started, rss) VALUES (?, ?, ?, ?)",
                   server, pid, int(started), rss)

    def prune_events(self, days=180):
        self.write("DELETE FROM server_events WHERE at < ?", now() - days * 86400)

    def baseline(self, server):
        return self.one("SELECT * FROM baselines WHERE server = ?", server)

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

    def trusted_ips(self):
        return self.all("SELECT ip, name, added_by, at FROM trusted_ips ORDER BY name, ip")

    def is_trusted(self, ip, networks=None):
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if networks is None:
            networks = [ipaddress.ip_network(r["ip"]) for r in self.trusted_ips()]
        return any(address.version == n.version and address in n for n in networks)

    def trust_ip(self, network, name, added_by):
        self.db.execute("INSERT OR REPLACE INTO trusted_ips (ip, name, added_by, at) VALUES (?, ?, ?, ?)",
                        (network, name, added_by, now()))
        networks = [ipaddress.ip_network(network)]
        gone = [r["ip"] for r in self.all("SELECT DISTINCT ip FROM login_attempts") if self.is_trusted(r["ip"], networks)]
        self.db.executemany("DELETE FROM login_attempts WHERE ip = ?", [(ip,) for ip in gone])
        self.db.commit()

    def untrust_ip(self, network):
        self.write("DELETE FROM trusted_ips WHERE ip = ?", network)

    def add_attempts(self, attempts, keep_days=30):
        networks = [ipaddress.ip_network(r["ip"]) for r in self.trusted_ips()]
        attempts = [a for a in attempts if not self.is_trusted(a["ip"], networks)]
        self.db.executemany("INSERT INTO login_attempts (at, ip, source, user) VALUES (?, ?, ?, ?)",
                            [(int(a["at"]), a["ip"], a["source"], a.get("user", "")) for a in attempts])
        self.db.execute("DELETE FROM login_attempts WHERE at < ?", (now() - keep_days * 86400,))
        self.db.commit()

    def attempts_from(self, ip, since):
        return self.one("SELECT COUNT(*) AS n, GROUP_CONCAT(DISTINCT source) AS sources,"
                        " GROUP_CONCAT(DISTINCT user) AS users FROM login_attempts WHERE ip = ? AND at >= ?", ip, since)

    def attack_sources(self, since):
        return self.all("SELECT source, COUNT(*) AS n, COUNT(DISTINCT ip) AS addresses FROM login_attempts"
                        " WHERE at >= ? GROUP BY source ORDER BY n DESC", since)

    def attackers(self, since, limit=50):
        return self.all("SELECT ip, COUNT(*) AS n, GROUP_CONCAT(DISTINCT source) AS sources,"
                        " GROUP_CONCAT(DISTINCT user) AS users, MIN(at) AS first, MAX(at) AS last"
                        " FROM login_attempts WHERE at >= ? GROUP BY ip ORDER BY n DESC LIMIT ?", since, limit)

    BOX_KEYS = ("cpu", "load1", "mem_used", "mem_total", "swap_used", "rx_mbps", "tx_mbps", "rx_pps", "tx_pps",
                "rx_drop", "udp_errors", "udp_rcvbuf", "udp_sndbuf", "disk_pct", "disk_read", "disk_write", "temp",
                "conntrack", "tcp_retrans", "softnet_drop", "nic_errors")
    SERVER_KEYS = ("players", "proc_cpu", "rss", "threads", "fps", "query_ok", "query_ms", "disconnects",
                   "errors", "reasons", "sock_drops")

    def add_box_sample(self, row, at=None, keep_days=7):
        t = int(at or now())
        keys = [k for k in self.BOX_KEYS if k in row]
        self.write(f"INSERT OR REPLACE INTO box_samples (at, {', '.join(keys)}) VALUES (?{', ?' * len(keys)})",
                   t, *(row[k] for k in keys))
        self.write("DELETE FROM box_samples WHERE at < ?", t - keep_days * 86400)

    def add_server_sample(self, server, row, at=None, keep_days=7):
        t = int(at or now())
        keys = [k for k in self.SERVER_KEYS if k in row]
        self.write(f"INSERT OR REPLACE INTO server_samples (server, at, {', '.join(keys)})"
                   f" VALUES (?, ?{', ?' * len(keys)})", server, t, *(row[k] for k in keys))
        self.write("DELETE FROM server_samples WHERE server = ? AND at < ?", server, t - keep_days * 86400)

    def box_samples(self, since):
        return self.all("SELECT * FROM box_samples WHERE at >= ? ORDER BY at", since)

    def server_samples(self, server, since):
        return self.all("SELECT * FROM server_samples WHERE server = ? AND at >= ? ORDER BY at", server, since)

    def add_memory(self, server, rss, at=None):
        t = at or now()
        self.write("INSERT OR REPLACE INTO memory_samples (server, at, rss) VALUES (?, ?, ?)", server, t, rss)
        self.write("DELETE FROM memory_samples WHERE server = ? AND at < ?", server, t - 7 * 86400)

    def prune_memory(self, days=7):
        """Also clears the readings of servers that have since left the panel."""
        self.write("DELETE FROM memory_samples WHERE at < ?", now() - days * 86400)

    def memory(self, server, since):
        return self.all("SELECT at, rss FROM memory_samples WHERE server = ? AND at >= ? ORDER BY at", server, since)

    # connections

    def add_connections(self, server, events, positions):
        self.add_platforms([e for e in events if e.get("kind") == "platform"], commit=False)
        for e in events:
            if e.get("kind", "identity") != "identity":
                if e.get("text") and e["kind"] != "fps":
                    self.db.execute("INSERT INTO feed (server, at, kind, text, ip) VALUES (?, ?, ?, ?, ?)",
                                    (server, e["at"], e["kind"], e["text"][:300], e.get("ip", "")))
                continue
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

    def add_feed(self, server, kind, text, at=None) -> int:
        return self.write("INSERT INTO feed (server, at, kind, text) VALUES (?, ?, ?, ?)", server, at or now(), kind,
                          text[:2000 if kind == "sus" else 300])

    def update_feed(self, row, text, at):
        self.write("UPDATE feed SET text = ?, at = ? WHERE id = ?", text[:2000], at, row)

    def feed(self, server, limit=200, show="all"):
        """Log events, RCON messages and admin actions for one server, newest first.
        show picks one of FEED_SHOWS, so a rare kind isn't buried under the last 200 kills."""
        kinds = FEED_SHOWS.get(show)
        # Each half takes only its own newest rows, so neither table is read from end to end.
        events = ("SELECT * FROM (SELECT id, at, kind, text, ip, 0 AS src FROM feed WHERE server = ?"
                  + (f" AND kind IN ({', '.join('?' * len(kinds))})" if kinds else "")
                  + " ORDER BY at DESC, id DESC LIMIT ?)")
        actions = ("SELECT * FROM (SELECT id, at, 'admin' AS kind,"
                   " username || ': ' || action || CASE WHEN target != '' THEN ' ' || target ELSE '' END"
                   " || CASE WHEN detail != '' THEN ' (' || detail || ')' ELSE '' END AS text, '' AS ip, 1 AS src"
                   " FROM audit WHERE server = ? ORDER BY at DESC, id DESC LIMIT ?)")
        if show == "admin":
            parts, args = [actions], [server, limit]
        elif kinds:
            parts, args = [events], [server, *kinds, limit]
        else:
            parts, args = [events, actions], [server, limit, server, limit]
        return self.all(f"SELECT at, kind, text, ip FROM ({' UNION ALL '.join(parts)})"
                        " ORDER BY at DESC, src, id DESC LIMIT ?", *args, limit)

    def prune_feed(self, days=7, sus_days=180):
        self.write("DELETE FROM feed WHERE at < ? AND (kind != 'sus' OR at < ?)",
                   now() - days * 86400, now() - sus_days * 86400)

    def forget_positions(self, server, paths):
        self.db.executemany("DELETE FROM log_positions WHERE server = ? AND path = ?", [(server, p) for p in paths])
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
        identities = list(dict.fromkeys(identities))
        result = {}
        for start in range(0, len(identities), 500):
            chunk = identities[start:start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.all(
                "SELECT mine.identity AS me, other.identity, COALESCE(p.name, MAX(other.name)) AS name,"
                " EXISTS (SELECT 1 FROM bans b WHERE b.identity = other.identity AND b.removed_at IS NULL"
                "   AND (b.expires_at IS NULL OR b.expires_at > ?)) AS banned"
                " FROM connections mine JOIN connections other ON other.ip = mine.ip AND other.identity != mine.identity"
                " LEFT JOIN players p ON p.identity = other.identity"
                f" WHERE mine.identity IN ({marks}) GROUP BY mine.identity, other.identity"
                " ORDER BY MAX(other.last_seen) DESC", now(), *chunk)
            for r in rows:
                entry = result.setdefault(r["me"], {"count": 0, "banned": []})
                entry["count"] += 1
                if r["banned"]:
                    entry["banned"].append(r["name"])
        return result

    def ip_accounts(self, ip):
        """Every account seen on this one address, most recent first."""
        return self.all(
            "SELECT c.identity, COALESCE(p.name, MAX(c.name)) AS name, SUM(c.times) AS times,"
            " MIN(c.first_seen) AS first_seen, MAX(c.last_seen) AS last_seen,"
            " EXISTS (SELECT 1 FROM bans b WHERE b.identity = c.identity AND b.removed_at IS NULL"
            "   AND (b.expires_at IS NULL OR b.expires_at > ?)) AS banned"
            " FROM connections c LEFT JOIN players p ON p.identity = c.identity"
            " WHERE c.ip = ? GROUP BY c.identity ORDER BY last_seen DESC", now(), ip)

    def search_ip(self, text, limit=100):
        return self.all(
            "SELECT p.* FROM players p WHERE p.identity IN (SELECT identity FROM connections WHERE ip LIKE ?)"
            " ORDER BY p.last_seen DESC LIMIT ?", f"{text}%", limit)

    def prune_connections(self, days=180):
        self.write("DELETE FROM connections WHERE last_seen < ?", now() - days * 86400)

    # Discord messages the panel edits (read by the bot)

    def discord_doc(self, key):
        return self.one("SELECT * FROM discord_docs WHERE key = ?", key)

    def save_discord_draft(self, key, text, by):
        self.write("INSERT INTO discord_docs (key, draft, draft_by, draft_at) VALUES (?, ?, ?, ?)"
                   " ON CONFLICT(key) DO UPDATE SET draft = excluded.draft, draft_by = excluded.draft_by,"
                   " draft_at = excluded.draft_at", key, text, by, now())

    def publish_discord_doc(self, key, text, by, keep_draft=False):
        self.write("INSERT INTO discord_docs (key, published, published_by, published_at, version)"
                   " VALUES (?, ?, ?, ?, 1) ON CONFLICT(key) DO UPDATE SET published = excluded.published,"
                   " published_by = excluded.published_by, published_at = excluded.published_at,"
                   " version = version + 1"
                   + ("" if keep_draft else ", draft = NULL, draft_by = NULL, draft_at = NULL"),
                   key, text, by, now())

    def discard_discord_draft(self, key):
        self.write("UPDATE discord_docs SET draft = NULL, draft_by = NULL, draft_at = NULL WHERE key = ?", key)

    def add_link_action(self, kind, target, identity, by) -> int:
        return self.write("INSERT INTO link_actions (kind, target, identity, by, at) VALUES (?, ?, ?, ?, ?)",
                          kind, target, identity, by, now())

    def link_actions(self, limit=30):
        return self.all("SELECT * FROM link_actions ORDER BY id DESC LIMIT ?", limit)

    # staff: players who are OYB admins in game

    def staff_ids(self) -> set[str]:
        return {r["identity"] for r in self.all("SELECT identity FROM staff")}

    def set_staff(self, identity, by, on=True):
        if on:
            self.write("INSERT OR IGNORE INTO staff (identity, added_by, at) VALUES (?, ?, ?)", identity, by, now())
        else:
            self.write("DELETE FROM staff WHERE identity = ?", identity)

    # alerts the bot posts to the staff channel

    def add_staff_alert(self, server, at, title, text) -> int:
        return self.write("INSERT INTO staff_alerts (server, at, title, text, created) VALUES (?, ?, ?, ?, ?)",
                          server, at, title, text, now())

    def prune_staff_alerts(self, days=90):
        self.write("DELETE FROM staff_alerts WHERE created < ?", now() - days * 86400)

    def staff_alerts(self, limit=20):
        return self.all("SELECT * FROM staff_alerts ORDER BY id DESC LIMIT ?", limit)

    # upload links

    def add_upload_link(self, token_hash, server, by, hours=24) -> int:
        return self.write("INSERT INTO upload_links (token_hash, server, created_by, created_at, expires_at)"
                          " VALUES (?, ?, ?, ?, ?)", token_hash, server, by, now(), now() + hours * 3600)

    def upload_link(self, token_hash):
        return self.one("SELECT * FROM upload_links WHERE token_hash = ? AND revoked = 0 AND expires_at > ?",
                        token_hash, now())

    def upload_links(self, server):
        return self.all("SELECT * FROM upload_links WHERE server = ? AND revoked = 0 AND expires_at > ?"
                        " ORDER BY id DESC", server, now())

    def used_upload_link(self, link_id):
        self.write("UPDATE upload_links SET uses = uses + 1 WHERE id = ?", link_id)

    def revoke_upload_link(self, link_id, server):
        self.write("UPDATE upload_links SET revoked = 1 WHERE id = ? AND server = ?", link_id, server)

    # archived games

    def archived(self, server) -> set[str]:
        return {r["folder"] for r in self.all("SELECT folder FROM log_archive WHERE server = ?", server)}

    def add_archive(self, server, folder, path, size, game):
        self.write("INSERT OR REPLACE INTO log_archive (server, folder, path, started, ended, size, players, kills, flags)"
                   " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", server, folder, path, int(game["started"]),
                   int(game["ended"]), size, game["players"], game["kills"], game["flags"])
        if "stats" in game:
            self.add_game_players(server, folder, int(game["started"]), game["stats"])
        if "kill_rows" in game:
            self.add_kills(server, game["kill_rows"], folder)
        if "platforms" in game:
            self.add_platforms(game["platforms"], server, folder)

    def old_archives(self, before):
        """Archived games whose saved log is old enough to delete."""
        return self.all("SELECT * FROM log_archive WHERE started < ? AND path != ''", before)

    def archive_deleted(self, server, folder):
        """The game stays listed with its numbers; only its saved log is gone."""
        self.write("UPDATE log_archive SET path = '' WHERE server = ? AND folder = ?", server, folder)

    def unread_games(self, server, limit=20):
        """Games archived before the panel kept per-player numbers, kills, kill places or
        platforms, each with what it still lacks."""
        return self.all(
            "SELECT * FROM (SELECT a.*,"
            " NOT EXISTS (SELECT 1 FROM scored_games r WHERE r.server = a.server AND r.folder = a.folder) AS no_scores,"
            " NOT EXISTS (SELECT 1 FROM kills_read r WHERE r.server = a.server AND r.folder = a.folder) AS no_kills,"
            " NOT EXISTS (SELECT 1 FROM kill_places_read r WHERE r.server = a.server AND r.folder = a.folder) AS no_places,"
            " NOT EXISTS (SELECT 1 FROM platforms_read r WHERE r.server = a.server AND r.folder = a.folder) AS no_platforms"
            " FROM log_archive a WHERE server = ? AND path != '')"
            " WHERE no_scores OR no_kills OR no_places OR no_platforms ORDER BY started DESC LIMIT ?", server, limit)

    # every kill, for the kill log on a player's page

    def add_kills(self, server, rows, folder=None):
        """Rows from archive.kill_rows. A kill saved before positions were kept
        gets them filled in when its game is read again."""
        with self.db:
            self.db.executemany(
                "INSERT INTO kills (server, game, at, kind, killer, killer_name, victim, victim_name, damage, distance,"
                " zone, victim_x, victim_y, killer_x, killer_y) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (server, at, kind, killer, victim) DO UPDATE SET victim_x = excluded.victim_x,"
                " victim_y = excluded.victim_y, killer_x = excluded.killer_x, killer_y = excluded.killer_y"
                " WHERE kills.victim_x IS NULL", [(server, *row) for row in rows])
            if folder:
                self.db.execute("INSERT OR IGNORE INTO kills_read VALUES (?, ?)", (server, folder))
                self.db.execute("INSERT OR IGNORE INTO kill_places_read VALUES (?, ?)", (server, folder))

    def map_kills(self, server, since, limit=3000):
        """Kills with a place on the map, newest first."""
        return self.all("SELECT * FROM kills WHERE server = ? AND at >= ? AND victim_x IS NOT NULL"
                        " ORDER BY at DESC LIMIT ?", server, since, limit)

    def add_platforms(self, events, server=None, folder=None, commit=True):
        """PC on Steam (with the Steam ID) or console, per account, as the log said
        when they last joined."""
        self.db.executemany(
            "INSERT INTO platforms (identity, platform, steam_id, last_seen) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(identity) DO UPDATE SET platform = excluded.platform, steam_id = excluded.steam_id,"
            " last_seen = excluded.last_seen WHERE excluded.last_seen >= last_seen",
            [(e["identity"], e["platform"], e["steam"], e["at"]) for e in events])
        if folder:
            self.db.execute("INSERT OR IGNORE INTO platforms_read VALUES (?, ?)", (server, folder))
        if commit:
            self.db.commit()

    def platform(self, identity):
        return self.one("SELECT * FROM platforms WHERE identity = ?", identity)

    def same_steam(self, steam_id, identity=""):
        """Every other account that joined from this Steam account."""
        if not steam_id:
            return []
        return self.all("SELECT p.identity, COALESCE(pl.name, p.identity) AS name, p.last_seen FROM platforms p"
                        " LEFT JOIN players pl ON pl.identity = p.identity"
                        " WHERE p.steam_id = ? AND p.identity != ? ORDER BY p.last_seen DESC", steam_id, identity)

    def combat(self, identity):
        """A player's kills and deaths from every saved game: totals, longest
        kill, and which body part took the killing bullet, both ways."""
        mine = self.one("SELECT COUNT(*) AS kills, SUM(damage = 'KINETIC') AS bullets,"
                        " SUM(damage = 'KINETIC' AND zone = 'Head') AS heads, MAX(distance) AS longest"
                        " FROM kills WHERE killer = ? AND kind = 'kill'", identity)
        deaths = self.one("SELECT COUNT(*) FROM kills WHERE victim = ?", identity)[0]
        zones = lambda who: {r["zone"]: r["n"] for r in self.all(
            f"SELECT zone, COUNT(*) AS n FROM kills WHERE {who} = ? AND damage = 'KINETIC' AND kind = 'kill'"
            " GROUP BY zone", identity)}
        return {"kills": mine["kills"], "bullets": mine["bullets"] or 0, "heads": mine["heads"] or 0,
                "longest": mine["longest"], "deaths": deaths, "hit": zones("killer"), "hit_by": zones("victim")}

    def combat_average(self, at_least=20):
        """The typical K/D and headshot rate among players with a fair number of kills."""
        rows = self.all("SELECT killer, COUNT(*) AS k, SUM(damage = 'KINETIC') AS b,"
                        " SUM(damage = 'KINETIC' AND zone = 'Head') AS h FROM kills"
                        " WHERE kind = 'kill' AND killer != '' GROUP BY killer HAVING k >= ?", at_least)
        if not rows:
            return None
        died = {r["victim"]: r["n"] for r in self.all("SELECT victim, COUNT(*) AS n FROM kills GROUP BY victim")}
        kd = [r["k"] / max(died.get(r["killer"], 0), 1) for r in rows]
        hs = [r["h"] / r["b"] for r in rows if r["b"]]
        return {"kd": sum(kd) / len(kd), "hs": sum(hs) / len(hs) if hs else None, "players": len(rows)}

    def kill_log(self, identity, mode, since=0, until=None, limit=500):
        """A player's kills, deaths or teamkills, oldest first. 'last' is the
        last game they were in, on whichever server that was."""
        who = {"tk_by": "kind = 'teamkill' AND killer = ?", "tk_on": "kind = 'teamkill' AND victim = ?",
               "all": "(killer = ? OR victim = ?)"}[mode]
        args = [identity] * who.count("?")
        if since == "last":
            last = self.one("SELECT server, game FROM kills WHERE killer = ? OR victim = ? ORDER BY at DESC LIMIT 1",
                            identity, identity)
            if last is None:
                return []
            where, more = "server = ? AND game = ?", [last["server"], last["game"]]
        else:
            where, more = "at >= ? AND at < ?", [since, until or now() + 86400]
        rows = self.all(f"SELECT * FROM kills WHERE {who} AND {where} ORDER BY at DESC LIMIT ?", *args, *more, limit)
        return list(reversed(rows))

    def revenges(self, rows, window=600) -> list:
        """For each row, the teamkill it may have been paying back: the victim
        teamkilling the killer shortly before, on the same server. None where
        there's none. All read at once rather than a read per row."""
        teamkills = [r for r in rows if r["kind"] == "teamkill" and r["killer"]]
        if not teamkills:
            return [None] * len(rows)
        victims = sorted({r["victim"] for r in teamkills})
        earlier = {}
        for start in range(0, len(victims), 500):
            chunk = victims[start:start + 500]
            for found in self.all(f"SELECT * FROM kills WHERE kind = 'teamkill' AND killer IN ({','.join('?' * len(chunk))})"
                                  " AND at >= ? AND at <= ? ORDER BY at DESC", *chunk,
                                  min(r["at"] for r in teamkills) - window, max(r["at"] for r in teamkills)):
                earlier.setdefault((found["server"], found["killer"], found["victim"]), []).append(found)
        return [next((f for f in earlier.get((r["server"], r["victim"], r["killer"]), ())
                      if r["at"] - window <= f["at"] <= r["at"]), None)
                if r["kind"] == "teamkill" and r["killer"] else None for r in rows]

    def prune_kills(self, days=180):
        self.write("DELETE FROM kills WHERE at < ?", now() - days * 86400)

    def add_game_players(self, server, folder, started, players):
        with self.db:
            self.db.execute("DELETE FROM game_players WHERE server = ? AND folder = ?", (server, folder))
            self.db.executemany(
                "INSERT INTO game_players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(server, folder, p["identity"], p["name"], started, max(0, int(p["last"] - p["first"])), p["kills"],
                  p["deaths"], p["rifle"], p["heads"], p["long"], p["metres"], p["measured"]) for p in players])
            self.db.execute("INSERT OR IGNORE INTO scored_games VALUES (?, ?)", (server, folder))

    def player_totals(self, since):
        """Each player's numbers added up over every archived game since then."""
        return self.all(
            "SELECT identity, (SELECT name FROM game_players l WHERE l.identity = g.identity ORDER BY started DESC LIMIT 1)"
            " AS name, COUNT(*) AS games, SUM(seconds) AS seconds, SUM(kills) AS kills, SUM(deaths) AS deaths,"
            " SUM(rifle) AS rifle, SUM(heads) AS heads, SUM(long) AS long, SUM(metres) AS metres,"
            " SUM(measured) AS measured FROM game_players g WHERE started >= ? GROUP BY identity", since)

    def games(self, server, since, until):
        return self.all("SELECT * FROM log_archive WHERE server = ? AND started >= ? AND started < ?"
                        " ORDER BY started DESC", server, since, until)

    def archived_game(self, server, folder):
        return self.one("SELECT * FROM log_archive WHERE server = ? AND folder = ?", server, folder)

    def audit_between(self, server, since, until):
        return self.all("SELECT * FROM audit WHERE server = ? AND at >= ? AND at <= ? ORDER BY at", server, since, until)

    # incidents

    def add_incident(self, server, at, text, players) -> int:
        """Saves a suspicious burst and everyone connected to the server at the time."""
        incident = self.db.execute("INSERT INTO incidents (server, at, text) VALUES (?, ?, ?)",
                                   (server, at, text[:500])).lastrowid
        self.db.executemany("INSERT OR IGNORE INTO incident_players (incident, identity, name) VALUES (?, ?, ?)",
                            [(incident, p["identity"], p["name"]) for p in players if p.get("identity")])
        self.db.commit()
        return incident

    def repeat_suspects(self, incident, days=30, skip=(), min_times=3, min_lift=2.0):
        """Players at this incident who keep turning up at incidents more than
        their playtime explains. Someone on 10 hours a day is at most of them
        by chance; they only count here if they're at more than min_lift times
        the incidents of an average player with the same hours."""
        since = now() - days * 86400
        seen = self.all(
            "SELECT ip.identity, ip.name, COUNT(*) AS times FROM incident_players ip"
            " JOIN incidents i ON i.id = ip.incident"
            " WHERE ip.identity IN (SELECT identity FROM incident_players WHERE incident = ?)"
            " AND i.at >= ? GROUP BY ip.identity HAVING times >= ?", incident, since, min_times)
        seen = [r for r in seen if r["identity"] not in skip]
        if not seen:
            return []
        totals = self.one(
            "SELECT (SELECT COUNT(*) FROM incident_players ip JOIN incidents i ON i.id = ip.incident WHERE i.at >= ?)"
            " AS presences, (SELECT SUM(seconds) FROM game_players WHERE started >= ?) / 3600.0 AS hours", since, since)
        hours = {r["identity"]: r["hours"] for r in self.all(
            "SELECT identity, SUM(seconds) / 3600.0 AS hours FROM game_players WHERE started >= ?"
            f" AND identity IN ({','.join('?' * len(seen))}) GROUP BY identity", since, *[r["identity"] for r in seen])}
        rate = totals["presences"] / totals["hours"] if totals["hours"] else None
        found = []
        for r in seen:
            played = max(hours.get(r["identity"]) or 0, 1)
            lift = r["times"] / (played * rate) if rate else None
            if lift is None or lift >= min_lift:
                found.append({"identity": r["identity"], "name": r["name"], "times": r["times"],
                              "hours": played, "lift": lift})
        found.sort(key=lambda f: (-(f["lift"] or 0), -f["times"], f["name"]))
        return found

    def incidents_for(self, identity, limit=50):
        return self.all(
            "SELECT i.* FROM incidents i JOIN incident_players ip ON ip.incident = i.id"
            " WHERE ip.identity = ? ORDER BY i.at DESC LIMIT ?", identity, limit)

    def incident_counts(self, since):
        return {r["identity"]: r["times"] for r in self.all(
            "SELECT ip.identity, COUNT(*) AS times FROM incident_players ip JOIN incidents i ON i.id = ip.incident"
            " WHERE i.at >= ? GROUP BY ip.identity", since)}

    def prune_incidents(self, days=180):
        self.write("DELETE FROM incidents WHERE at < ?", now() - days * 86400)

    # notes

    def add_note(self, identity, author, body) -> int:
        return self.write("INSERT INTO notes (identity, author, body, created_at) VALUES (?, ?, ?, ?)",
                          identity, author, body, now())

    def notes(self, identity: str):
        return self.all("SELECT * FROM notes WHERE identity = ? ORDER BY id DESC", identity)

    # warnings: the next close-range teamkill after one earns an automatic ban

    def add_warning(self, identity, name, author, reason) -> int:
        return self.write("INSERT INTO warnings (identity, name, author, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                          identity, name, author, reason, now())

    def warnings(self, identity: str):
        return self.all("SELECT * FROM warnings WHERE identity = ? ORDER BY id DESC", identity)

    def warning(self, warning_id: int):
        return self.one("SELECT * FROM warnings WHERE id = ?", warning_id)

    def open_warning(self, identity: str):
        return self.one("SELECT * FROM warnings WHERE identity = ? AND used_at IS NULL AND withdrawn_at IS NULL"
                        " ORDER BY id LIMIT 1", identity)

    def use_warning(self, warning_id: int, used_for: str) -> bool:
        """Spends a warning. False when something else already did, so one teamkill bans once."""
        cur = self.db.execute("UPDATE warnings SET used_at = ?, used_for = ? WHERE id = ?"
                              " AND used_at IS NULL AND withdrawn_at IS NULL", (now(), used_for, warning_id))
        self.db.commit()
        return cur.rowcount == 1

    def set_warning_ban(self, warning_id: int, ban_id: int):
        self.write("UPDATE warnings SET used_ban = ? WHERE id = ?", ban_id, warning_id)

    def visible_warnings(self, identity: str):
        """A warning that earned a ban is shown as used while that ban lasts, and goes once they're unbanned."""
        shown = []
        for w in self.warnings(identity):
            if w["used_at"]:
                ban = self.ban(w["used_ban"]) if w["used_ban"] else self.one(
                    "SELECT * FROM bans WHERE identity = ? AND created_by = 'Automatic' AND reason LIKE 'Teamkill after a warning%'"
                    " AND created_at BETWEEN ? AND ? ORDER BY id LIMIT 1", identity, w["used_at"] - 30, w["used_at"] + 120)
                if ban and (ban["removed_at"] or (ban["expires_at"] and ban["expires_at"] <= now())):
                    continue
            shown.append(w)
        return shown

    def edit_warning(self, warning_id: int, reason: str, editor: str):
        self.write("UPDATE warnings SET reason = ?, edited_by = ?, edited_at = ? WHERE id = ?",
                   reason, editor, now(), warning_id)

    def delete_warning(self, warning_id: int):
        self.write("DELETE FROM warnings WHERE id = ?", warning_id)

    def withdraw_warning(self, warning_id: int, by: str) -> bool:
        cur = self.db.execute("UPDATE warnings SET withdrawn_by = ?, withdrawn_at = ? WHERE id = ?"
                              " AND used_at IS NULL AND withdrawn_at IS NULL", (by, now(), warning_id))
        self.db.commit()
        return cur.rowcount == 1

    def note(self, note_id: int):
        return self.one("SELECT * FROM notes WHERE id = ?", note_id)

    def edit_note(self, note_id: int, body: str, editor: str):
        self.write("UPDATE notes SET body = ?, edited_by = ?, edited_at = ? WHERE id = ?", body, editor, now(), note_id)

    def delete_note(self, note_id: int):
        self.write("DELETE FROM notes WHERE id = ?", note_id)
