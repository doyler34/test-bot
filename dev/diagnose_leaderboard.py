#!/usr/bin/env python3
"""Why does the leaderboard show what it shows?

Prints the week the bot is counting, how many recorded kills fall inside it,
and the standings it would build right now. Run on the bot host:

    cd ~/Arma-bot && python3 dev/diagnose_leaderboard.py
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402
from bot.storage.combat_store import stamp, week_start, window_standings  # noqa: E402

load_dotenv()

path = os.getenv("ACCOUNT_LINKS_DB") or "data/account_links.sqlite3"
if not Path(path).is_file():
    print(f"No database at {path}. Set ACCOUNT_LINKS_DB or run from the bot folder.")
    raise SystemExit(1)

db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
print(f"database : {path}")

tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
weekly = "combat_matches" in tables
print(f"build    : {'weekly (this code is deployed)' if weekly else 'OLD - weekly build is NOT running'}")

start = week_start()
low = stamp(start)
print(f"week from: {start}  (compared as {low})")

total = db.execute("SELECT COUNT(*) FROM combat_events").fetchone()[0]
inside = db.execute("SELECT COUNT(*) FROM combat_events WHERE occurred>=?", (low,)).fetchone()[0]
oldest, newest = db.execute("SELECT MIN(occurred), MAX(occurred) FROM combat_events").fetchone()
print(f"events   : {total} recorded, {inside} inside this week")
print(f"span     : {oldest}  ->  {newest}")

scored = db.execute(
    "SELECT COUNT(*) FROM combat_events WHERE occurred>=? AND killer IS NOT NULL AND killer<>victim",
    (low,)).fetchone()[0]
print(f"scoring  : {scored} of those count (the rest are AI kills or suicides)")

guilds = [r[0] for r in db.execute("SELECT DISTINCT guild FROM account_links")]
for guild in guilds:
    rows = window_standings(db, guild, start)
    print(f"\nguild {guild}: {len(rows)} players on the board")
    for discord_id, kills, deaths, name in rows[:10]:
        print(f"  {kills:>4}k {deaths:>4}d  {name or discord_id}")

if not weekly:
    print("\nThe board you are looking at is the old all-time one. Pull and restart.")
