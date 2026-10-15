"""In-game numbers for a player from the bot's databases: time played, kills,
recent games and the Discord account they linked. Opened read-only, so the
panel can never change them.
"""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from bot.storage.combat_store import recent_matches, week_start, window_standings

CACHE_SECONDS = 60


def _open(path: Path):
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None


def _one(db, sql, *args):
    try:
        return db.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None


class OybStats:
    def __init__(self, data_dir: str = "data"):
        self.data = Path(data_dir)
        self._cache: dict[str, tuple[float, dict | None]] = {}
        self._played: dict[str, tuple[float, int | None]] = {}
        self._board: tuple[float, dict | None] | None = None

    @property
    def available(self) -> bool:
        return (self.data / "playtime.sqlite3").is_file() or (self.data / "account_links.sqlite3").is_file()

    def player(self, identity: str) -> dict | None:
        hit = self._cache.get(identity)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        result = self._read(identity)
        self._cache[identity] = (time.monotonic(), result)
        return result

    def playtimes(self, identities) -> dict[str, int]:
        """Time played for a whole list of players at once, for the player lists."""
        now, result, wanted = time.monotonic(), {}, []
        for identity in dict.fromkeys(identities):
            hit = self._played.get(identity)
            if hit and now - hit[0] < CACHE_SECONDS:
                if hit[1] is not None:
                    result[identity] = hit[1]
            else:
                wanted.append(identity)
        playtime = _open(self.data / "playtime.sqlite3") if wanted else None
        found = {}
        if playtime:
            with closing(playtime):
                for start in range(0, len(wanted), 500):
                    chunk = wanted[start:start + 500]
                    marks = ",".join("?" * len(chunk))
                    try:
                        found.update((i, int(sec)) for i, sec in playtime.execute(
                            f"SELECT identity, SUM(seconds) FROM totals WHERE identity IN ({marks}) GROUP BY identity", chunk))
                        found.update((i, int(ms / 1000)) for i, ms in playtime.execute(
                            f"SELECT identity, milliseconds FROM global_time WHERE identity IN ({marks})", chunk))
                    except sqlite3.Error:
                        pass
        for identity in wanted:
            self._played[identity] = (now, found.get(identity))
            if identity in found:
                result[identity] = found[identity]
        return result

    def _read(self, identity: str) -> dict | None:
        found = False
        stats = {"name": "", "playtime": 0, "servers": [], "kills": 0, "deaths": 0, "teamkills": 0, "matches": [],
                 "discord": None}
        playtime = _open(self.data / "playtime.sqlite3")
        if playtime:
            with closing(playtime):
                rows = []
                try:
                    rows = playtime.execute("SELECT server, seconds, name FROM totals WHERE identity = ? ORDER BY seconds DESC",
                                            (identity,)).fetchall()
                except sqlite3.Error:
                    pass
                stats["servers"] = [{"server": s, "seconds": int(sec)} for s, sec, _ in rows]
                stats["name"] = next((n for _, _, n in rows if n), "")
                union = _one(playtime, "SELECT milliseconds FROM global_time WHERE identity = ?", identity)
                stats["playtime"] = int(union[0] / 1000) if union else sum(r["seconds"] for r in stats["servers"])
                found = found or bool(rows)
        links = _open(self.data / "account_links.sqlite3")
        if links:
            with closing(links):
                combat = _one(links, "SELECT player_kills, deaths, teamkills FROM combat_totals WHERE identity = ?", identity)
                if combat:
                    stats["kills"], stats["deaths"], stats["teamkills"] = combat
                    found = True
                try:
                    stats["matches"] = recent_matches(links, identity, limit=10)
                except sqlite3.Error:
                    pass
                link = _one(links, "SELECT discord_id FROM account_links WHERE identity = ?", identity)
                if link:
                    stats["discord"] = link[0]
                    found = True
        return stats if found else None

    def health(self, identity: str = "") -> dict:
        """What the panel finds in the bot's data folder, errors and all, for when
        the numbers look wrong: the panel's own pages hide a failed read."""
        report = {"folder": str(self.data.resolve()), "files": [], "player": None}
        for name in ("playtime.sqlite3", "account_links.sqlite3"):
            path = self.data / name
            entry = {"name": name, "exists": path.is_file(), "size": 0, "modified": 0, "wal": 0,
                     "check": "", "tables": [], "error": ""}
            report["files"].append(entry)
            if not entry["exists"]:
                continue
            stat = path.stat()
            entry["size"], entry["modified"] = stat.st_size, int(stat.st_mtime)
            wal = path.with_name(name + "-wal")
            entry["wal"] = wal.stat().st_size if wal.is_file() else 0
            try:
                db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            except sqlite3.Error as exc:
                entry["error"] = str(exc)
                continue
            with closing(db):
                try:
                    entry["check"] = "; ".join(r[0] for r in db.execute("PRAGMA quick_check").fetchall()[:5])
                    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
                        entry["tables"].append((table, db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]))
                except sqlite3.Error as exc:
                    entry["error"] = str(exc)
        if identity:
            found = {}
            for name, sql in (("playtime.sqlite3", "SELECT server, seconds, name FROM totals WHERE identity = ?"),
                              ("playtime.sqlite3", "SELECT milliseconds FROM global_time WHERE identity = ?"),
                              ("account_links.sqlite3",
                               "SELECT player_kills, deaths, teamkills FROM combat_totals WHERE identity = ?"),
                              ("account_links.sqlite3", "SELECT discord_id FROM account_links WHERE identity = ?")):
                db = _open(self.data / name)
                if db is None:
                    found[sql] = "file missing"
                    continue
                with closing(db):
                    try:
                        found[sql] = repr(db.execute(sql, (identity,)).fetchall())
                    except sqlite3.Error as exc:
                        found[sql] = f"error: {exc}"
            report["player"] = found
        return report

    def weekly_board(self, limit: int = 10) -> dict | None:
        """The same weekly standings the bot pins in Discord: linked players only,
        kills first, reset every Monday. None when the bot's data isn't here."""
        if self._board and time.monotonic() - self._board[0] < CACHE_SECONDS:
            return self._board[1]
        board = None
        links = _open(self.data / "account_links.sqlite3")
        if links:
            with closing(links):
                try:
                    guild = _one(links, "SELECT guild FROM account_links GROUP BY guild ORDER BY COUNT(*) DESC LIMIT 1")
                    start = week_start()
                    rows = window_standings(links, guild[0], start) if guild else []
                    board = {"week": f"{start:%d %b}", "players": len(rows), "rows": [
                        {"name": name or self._game_name(links, guild[0], member), "kills": kills, "deaths": deaths}
                        for member, kills, deaths, name in rows[:limit]]}
                except sqlite3.Error:
                    board = None
        self._board = (time.monotonic(), board)
        return board

    def _game_name(self, links, guild, member) -> str:
        identities = [r[0] for r in links.execute(
            "SELECT identity FROM account_links WHERE guild = ? AND discord_id = ?", (guild, member))]
        playtime = _open(self.data / "playtime.sqlite3") if identities else None
        if playtime:
            with closing(playtime):
                marks = ",".join("?" * len(identities))
                found = _one(playtime, f"SELECT name FROM totals WHERE identity IN ({marks}) AND name <> ''"
                                       " ORDER BY seconds DESC LIMIT 1", *identities)
                if found:
                    return found[0]
        return "Unknown player"

    def search(self, text: str, limit: int = 50) -> list[dict]:
        """Players the bot has recorded, by name or identity, most played first."""
        playtime = _open(self.data / "playtime.sqlite3")
        if not playtime:
            return []
        like = f"%{text}%"
        with closing(playtime):
            try:
                rows = playtime.execute(
                    "SELECT identity, MAX(name), SUM(seconds) AS total FROM totals"
                    " WHERE name LIKE ? OR identity LIKE ? GROUP BY identity ORDER BY total DESC LIMIT ?",
                    (like, like, limit)).fetchall()
            except sqlite3.Error:
                return []
        return [{"identity": i, "name": n or i} for i, n, _ in rows]
