"""Put bans the game has lost back on one server, a few at a time, and see what that does to its list.

Run from the panel's folder. The list is read a page at a time and takes about half a minute, so this is
two commands:

    .venv/bin/python dev/resend_bans.py server-2 send [how-many]
    .venv/bin/python dev/resend_bans.py server-2 check

"send" reads the game's list, saves it, then sends the first few of the panel's bans that it is missing
(one by default). "check" reads the list again and says what was added and whether anything else went.
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dev.ban_list import game_list  # noqa: E402
from panel.config import load_config  # noqa: E402
from panel.rcon import RconClient, RconError  # noqa: E402
from panel.servers import clean  # noqa: E402


def active_bans(database):
    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    rows = db.execute("SELECT identity, name, reason, expires_at FROM bans WHERE removed_at IS NULL"
                      " AND (expires_at IS NULL OR expires_at > ?) ORDER BY id", (int(time.time()),)).fetchall()
    db.close()
    return rows


async def main():
    if len(sys.argv) < 3 or sys.argv[2] not in ("send", "check"):
        sys.exit(__doc__)
    server_id, mode = sys.argv[1], sys.argv[2]
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    config = load_config()
    server = next((s for s in config.servers if s.id == server_id and s.configured), None)
    if server is None:
        sys.exit(f"No server called {server_id} with RCON set up.")
    saved = Path(f"/tmp/resend_bans_{server_id}.json")
    client = RconClient(server.rcon_host, server.rcon_port, server.rcon_password)
    try:
        await client.connect()
        if mode == "check":
            if not saved.exists():
                sys.exit("Run 'send' first.")
            before = json.loads(saved.read_text())
            listed, total, pages = await game_list(client)
            print(f"{server_id}: the game said {before['total']} bans before, and says {total} now.")
            added = [i for i in before["sent"] if i in listed]
            print(f"Of the {len(before['sent'])} sent, {len(added)} are in the list now.")
            gone = sorted(set(before["listed"]) - listed)
            print(f"{len(gone)} bans that were in the list before are not in it now.")
            for identity in gone[:10]:
                print(f"  gone: {identity}")
            return
        listed, total, pages = await game_list(client)
        missing = [ban for ban in active_bans(config.database) if ban[0].lower() not in listed][:count]
        sent = []
        for identity, name, reason, expires_at in missing:
            seconds = max(expires_at - int(time.time()), 1) if expires_at else 0
            command = server.commands["ban"].format(identity=identity, seconds=seconds, reason=clean(reason) or "Banned")
            print(f"{name} {identity}: {clean(await client.command(command), 120) or '(no reply)'}")
            sent.append(identity.lower())
            await asyncio.sleep(1)
        saved.write_text(json.dumps({"total": total, "listed": sorted(listed), "sent": sent}))
        print(f"Sent {len(sent)}. The game had {total} bans before. Now run 'check'.")
    except RconError as exc:
        print(f"{server_id}: could not ask: {exc}")
    finally:
        client.close()


asyncio.run(main())
