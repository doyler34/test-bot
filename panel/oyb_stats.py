"""In-game numbers for a player from the bot's databases: time played, kills,
recent games and the Discord account they linked. Opened read-only, so the
panel can never change them.
"""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from bot.storage.combat_store import recent_matches

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
