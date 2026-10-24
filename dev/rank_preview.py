"""What a new rank ladder and a bigger teamkill penalty would do to the people ranked today.

Run from the panel's folder, where panel.local.json is:

    .venv/bin/python dev/rank_preview.py

It works on a copy of the bot's database in a temporary folder and changes nothing real.
Everyone is counted as if they had picked a side, since Renegade has nothing to do with XP.
"""

import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.ranks.rank_rules import RANKS, XP_PER_TEAMKILL  # noqa: E402
from bot.storage.combat_store import SCORED, held  # noqa: E402
from bot.storage.rank_persistence import XPStore  # noqa: E402
from panel.config import load_config  # noqa: E402

PROPOSED = (("Recruit", 150), ("Private", 400), ("Lance Corporal", 750), ("Corporal", 1200), ("Sergeant", 1800),
            ("Staff Sergeant", 2600), ("2nd Lieutenant", 3600), ("Lieutenant", 4900), ("Captain", 6500),
            ("Major", 8500))
NEW_TEAMKILL = -15
SHOWN = 12


def rank_of(xp, ladder):
    name = ladder[0][0]
    for rank, threshold in ladder:
        if xp >= threshold:
            name = rank
    return name


def main():
    source = Path(load_config().oyb_data) / "account_links.sqlite3"
    if not source.is_file():
        sys.exit(f"No bot database at {source}.")
    current = tuple((r.name, r.threshold) for r in RANKS if r.name != "Renegade")
    with tempfile.TemporaryDirectory() as tmp, closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as live:
        copy = sqlite3.connect(Path(tmp) / "copy.sqlite3")
        live.backup(copy)
        guild = copy.execute("SELECT guild FROM account_links GROUP BY guild ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        if not guild:
            sys.exit("Nobody has linked an account.")
        store = XPStore(copy)
        members = {}
        for member, identities in copy.execute("SELECT discord_id, GROUP_CONCAT(identity) FROM account_links WHERE guild=?"
                                              " GROUP BY discord_id", (guild[0],)):
            members[member] = identities.split(",")
        rows = []
        for member, identities in members.items():
            xp = store.cached(guild[0], member)
            marks = ",".join("?" * len(identities))
            kills = copy.execute(f"SELECT COUNT(*) FROM combat_events WHERE killer IN ({marks}) AND relation='TK' AND {SCORED}",
                                 identities).fetchone()[0]
            rows.append((member, xp, kills, xp + kills * (NEW_TEAMKILL - XP_PER_TEAMKILL)))
        copy.close()
    before = Counter(rank_of(xp, current) for _, xp, _, _ in rows)
    after = Counter(rank_of(xp, PROPOSED) for _, _, _, xp in rows)
    order = [name for name, _ in PROPOSED]
    names = [name for name, _ in current]
    print(f"{len(rows)} linked members. Teamkill {XP_PER_TEAMKILL} XP now, {NEW_TEAMKILL} in the proposal.\n")
    print("Now:      " + ", ".join(f"{name} {before.get(name, 0)}" for name in names))
    print("Proposed: " + ", ".join(f"{name} {after.get(name, 0)}" for name in order))
    same = sum(1 for _, xp, _, new in rows if rank_of(xp, current) == rank_of(new, PROPOSED))
    print(f"\nSame rank: {same}. Different rank: {len(rows) - same}.")
    print(f"Members with a teamkill on record: {sum(1 for r in rows if r[2])}. "
          f"XP they would lose to teamkills: {sum(r[2] * (XP_PER_TEAMKILL - NEW_TEAMKILL) for r in rows)} in total.")
    print("\nBiggest teamkill hits (Discord id, XP now, teamkills, XP after):")
    for member, xp, kills, new in sorted(rows, key=lambda r: -r[2])[:SHOWN]:
        if kills:
            print(f"  {member}  {xp}  {kills} TK  {new}")


main()
