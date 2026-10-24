"""Show why someone was banned: their ban records and every audit entry that mentions them.

Run from the panel's folder, where panel.local.json is:

    .venv/bin/python dev/why_banned.py part-of-a-name-or-identity

It only reads.
"""

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panel.config import load_config  # noqa: E402

LIMIT = 25


def stamp(at):
    return time.strftime("%d %b %H:%M", time.gmtime(at)) + " UTC" if at else "-"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    term = f"%{sys.argv[1].lower()}%"
    db = sqlite3.connect(f"file:{load_config().database}?mode=ro", uri=True)
    bans = db.execute("SELECT id, name, identity, reason, created_by, created_at, expires_at, removed_by, removed_at"
                      " FROM bans WHERE lower(name) LIKE ? OR lower(identity) LIKE ? ORDER BY id DESC LIMIT ?",
                      (term, term, LIMIT)).fetchall()
    print(f"Bans matching '{sys.argv[1]}': {len(bans)}")
    for ban_id, name, identity, reason, by, made, expires, removed_by, removed in bans:
        length = f"until {stamp(expires)}" if expires else "permanent"
        print(f"\n#{ban_id} {name} {identity}\n  made {stamp(made)} by {by}, {length}\n  reason: {reason}")
        if removed:
            print(f"  lifted {stamp(removed)} by {removed_by}")
    rows = db.execute("SELECT at, username, action, server, target, detail, ok FROM audit"
                      " WHERE lower(target) LIKE ? OR lower(detail) LIKE ? ORDER BY id DESC LIMIT ?",
                      (term, term, LIMIT)).fetchall()
    print(f"\nAudit log entries mentioning '{sys.argv[1]}' (newest first): {len(rows)}")
    for at, user, action, server, target, detail, ok in rows:
        print(f"  {stamp(at)} {user}: {action}{'' if ok else ' (failed)'} {server} {target} {detail}")
    db.close()


main()
