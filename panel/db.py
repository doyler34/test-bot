import sqlite3
import time
from pathlib import Path

FEED_SHOWS = {"joins": ("join", "leave", "side"), "kills": ("kill", "teamkill"), "sus": ("sus",),
              "rcon": ("rcon", "server"), "admin": ()}

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
CREATE TABLE IF NOT EXISTS ip_bans (
    ban_id INTEGER NOT NULL REFERENCES bans(id),
    ip TEXT NOT NULL,
    PRIMARY KEY (ban_id, ip)
);
CREATE INDEX IF NOT EXISTS ip_bans_ip ON ip_bans(ip);
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

    def add_memory(self, server, rss, at=None):
        t = at or now()
        self.write("INSERT OR REPLACE INTO memory_samples (server, at, rss) VALUES (?, ?, ?)", server, t, rss)
        self.write("DELETE FROM memory_samples WHERE at < ?", t - 7 * 86400)

    def memory(self, server, since):
        return self.all("SELECT at, rss FROM memory_samples WHERE server = ? AND at >= ? ORDER BY at", server, since)

    # connections

    def add_connections(self, server, events, positions):
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
        events = ("SELECT id, at, kind, text, ip, 0 AS src FROM feed WHERE server = ?"
                  + (f" AND kind IN ({', '.join('?' * len(kinds))})" if kinds else ""))
        actions = ("SELECT id, at, 'admin' AS kind,"
                   " username || ': ' || action || CASE WHEN target != '' THEN ' ' || target ELSE '' END"
                   " || CASE WHEN detail != '' THEN ' (' || detail || ')' ELSE '' END AS text, '' AS ip, 1 AS src"
                   " FROM audit WHERE server = ?")
        if show == "admin":
            parts, args = [actions], [server]
        elif kinds:
            parts, args = [events], [server, *kinds]
        else:
            parts, args = [events, actions], [server, server]
        return self.all(f"SELECT at, kind, text, ip FROM ({' UNION ALL '.join(parts)})"
                        " ORDER BY at DESC, src, id DESC LIMIT ?", *args, limit)

    def prune_feed(self, days=7, sus_days=180):
        self.write("DELETE FROM feed WHERE at < ? AND (kind != 'sus' OR at < ?)",
                   now() - days * 86400, now() - sus_days * 86400)

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

    # Discord messages the panel edits (read by the bot)

    def discord_doc(self, key):
        return self.one("SELECT * FROM discord_docs WHERE key = ?", key)

    def save_discord_draft(self, key, text, by):
        self.write("INSERT INTO discord_docs (key, draft, draft_by, draft_at) VALUES (?, ?, ?, ?)"
                   " ON CONFLICT(key) DO UPDATE SET draft = excluded.draft, draft_by = excluded.draft_by,"
                   " draft_at = excluded.draft_at", key, text, by, now())

    def publish_discord_doc(self, key, text, by):
        self.write("INSERT INTO discord_docs (key, published, published_by, published_at, version)"
                   " VALUES (?, ?, ?, ?, 1) ON CONFLICT(key) DO UPDATE SET published = excluded.published,"
                   " published_by = excluded.published_by, published_at = excluded.published_at,"
                   " version = version + 1, draft = NULL, draft_by = NULL, draft_at = NULL",
                   key, text, by, now())

    def discard_discord_draft(self, key):
        self.write("UPDATE discord_docs SET draft = NULL, draft_by = NULL, draft_at = NULL WHERE key = ?", key)

    def add_link_action(self, kind, target, identity, by) -> int:
        return self.write("INSERT INTO link_actions (kind, target, identity, by, at) VALUES (?, ?, ?, ?, ?)",
                          kind, target, identity, by, now())

    def link_actions(self, limit=30):
        return self.all("SELECT * FROM link_actions ORDER BY id DESC LIMIT ?", limit)

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

    # every kill, for the kill log on a player's page

    def add_kills(self, server, rows, folder=None):
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO kills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                [(server, *row) for row in rows])
            if folder:
                self.db.execute("INSERT OR IGNORE INTO kills_read VALUES (?, ?)", (server, folder))

    def unread_kill_games(self, server, limit=20):
        return self.all("SELECT * FROM log_archive a WHERE server = ? AND NOT EXISTS (SELECT 1 FROM kills_read r"
                        " WHERE r.server = a.server AND r.folder = a.folder) ORDER BY started DESC LIMIT ?",
                        server, limit)

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

    def revenge_for(self, row, window=600):
        """The teamkill this one may have been paying back: the victim
        teamkilling the killer shortly before, on the same server."""
        if row["kind"] != "teamkill" or not row["killer"]:
            return None
        return self.one("SELECT * FROM kills WHERE server = ? AND kind = 'teamkill' AND killer = ? AND victim = ?"
                        " AND at <= ? AND at >= ? ORDER BY at DESC LIMIT 1",
                        row["server"], row["victim"], row["killer"], row["at"], row["at"] - window)

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

    def unscored_games(self, server, limit=20):
        return self.all("SELECT * FROM log_archive a WHERE server = ? AND NOT EXISTS (SELECT 1 FROM scored_games s"
                        " WHERE s.server = a.server AND s.folder = a.folder) ORDER BY started DESC LIMIT ?",
                        server, limit)

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

    def repeat_suspects(self, incident, days=30):
        """Players present at this incident who were also present at earlier ones, on any server."""
        return self.all(
            "SELECT ip.identity, ip.name, COUNT(*) AS times FROM incident_players ip"
            " JOIN incidents i ON i.id = ip.incident"
            " WHERE ip.identity IN (SELECT identity FROM incident_players WHERE incident = ?)"
            " AND i.at >= ? GROUP BY ip.identity HAVING times > 1 ORDER BY times DESC, ip.name",
            incident, now() - days * 86400)

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
