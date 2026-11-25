"""What OYB Control's kept autosaves say about a match: each player's game XP and who held the bases.

Read only, from the panel's database (PANEL_DB). Everything here gives nothing back when that database
or a save of the match isn't there, so the boards just go without. The panel and the bot name their
servers separately, so a match's game is found by its players: the kept game whose saves during the
match hold the most of them."""
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

# Saves land a little either side of a match's start and end.
SLACK = 120


def panel_path():
    return os.getenv("PANEL_DB", "data/panel.sqlite3")


def connect(path=None):
    path = Path(path or panel_path())
    if not path.is_file():
        return None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        db.execute("SELECT 1 FROM save_rows LIMIT 1")
        return db
    except sqlite3.Error:
        return None


def unix(moment):
    return int(moment.timestamp())


def find_game(db, identities, start, end):
    """(panel server, game) kept for this match, or None."""
    identities = list(identities)
    if not identities:
        return None
    marks = ",".join("?" * len(identities))
    row = db.execute(f"SELECT server, game, COUNT(DISTINCT key) AS n FROM save_rows WHERE kind='player'"
                     f" AND saved>=? AND saved<=? AND key IN ({marks}) GROUP BY server, game ORDER BY n DESC LIMIT 1",
                     (unix(start) - SLACK, unix(end) + SLACK, *identities)).fetchone()
    return (row[0], row[1]) if row else None


def xp_gains(db, server, game, start, end):
    """{identity: game XP gained during the match}. Only rises add up, so a reset (a new life or a side
    switch, if the game does that) never takes any off. When the game began with the match, everyone's
    game XP started at nothing, so a player's first save counts in full."""
    gains, last = Counter(), {}
    fresh = game >= unix(start) - SLACK
    for key, saved, data in db.execute(
            "SELECT key, saved, data FROM save_rows WHERE server=? AND game=? AND kind='player' AND saved<=?"
            " ORDER BY key, saved", (server, game, unix(end) + SLACK)):
        xp = json.loads(data).get("xp") if data else None
        if xp is None:
            continue
        before = last.get(key, 0 if fresh else None)
        if before is not None and saved >= unix(start) - SLACK and xp > before:
            gains[key] += xp - before
        last[key] = xp
    return gains


def bases_at(db, server, game, end):
    """How many bases each side held in the last save at or before the end of the match."""
    held = Counter()
    for (data,) in db.execute(
            "SELECT data FROM save_rows r WHERE server=? AND game=? AND kind='base' AND saved=(SELECT MAX(saved)"
            " FROM save_rows WHERE server=r.server AND game=r.game AND kind='base' AND key=r.key AND saved<=?)",
            (server, game, unix(end) + SLACK)):
        faction = json.loads(data).get("faction") if data else None
        if faction:
            held[faction] += 1
    return held


# A save this close to a kill says what the killer was sitting in.
SEAT_WINDOW = 240


def heli_check(spots, path=None):
    """A confirm(killer, unix time) for tracking.heli: True when a save near the kill has the killer seated
    in a helicopter, False when the saves near it have them somewhere else, None with no save near it."""
    db = connect(path)
    if db is None or not spots:
        return None
    from datetime import datetime
    killers = sorted({s[2] for s in spots})
    moments = [datetime.fromisoformat(s[1]).timestamp() for s in spots]
    marks = ",".join("?" * len(killers))
    with closing(db):
        try:
            helis = {row[0] for row in db.execute("SELECT DISTINCT key FROM save_rows WHERE kind='heli'")}
            seats = {}
            for key, saved, data in db.execute(
                    f"SELECT key, saved, data FROM save_rows WHERE kind='player' AND key IN ({marks})"
                    f" AND saved>=? AND saved<=? AND data IS NOT NULL",
                    (*killers, min(moments) - SEAT_WINDOW, max(moments) + SEAT_WINDOW)):
                seats.setdefault(key, []).append((saved, json.loads(data).get("vehicle")))
        except sqlite3.Error:
            return None
    if not helis:
        return None

    def confirm(killer, moment):
        near = [vehicle for saved, vehicle in seats.get(killer, []) if abs(saved - moment) <= SEAT_WINDOW]
        if not near:
            return None
        return any(vehicle in helis for vehicle in near)
    return confirm


def match_summary(identities, start, end, path=None):
    """({identity: game XP gained}, {side: bases held at the end}) for a match; empty when there's no save."""
    db = connect(path)
    if db is None:
        return Counter(), Counter()
    with closing(db):
        try:
            game = find_game(db, identities, start, end)
            if game is None:
                return Counter(), Counter()
            return xp_gains(db, *game, start, end), bases_at(db, *game, end)
        except sqlite3.Error:
            return Counter(), Counter()
