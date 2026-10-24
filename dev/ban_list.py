"""Ask each game server for its own ban list and compare it with the panel's.

Run from the panel's folder, where panel.local.json is:

    .venv/bin/python dev/ban_list.py [server-id ...] [find=part-of-a-name-or-identity]

It only reads. Nothing is banned or unbanned. The game lists bans a page at a time,
so this asks for every page, one every half second.
"""

import asyncio
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panel.config import load_config  # noqa: E402
from panel.rcon import RconClient, RconError  # noqa: E402

PAGES = re.compile(r"Page:\s*(\d+)\s*/\s*(\d+)")
TOTAL = re.compile(r"Total bans:\s*(\d+)")
IDENTITY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHOWN = 15
MATCHES = 10


def panel_view(database):
    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    bans = db.execute("SELECT id, identity, name, expires_at, created_by, created_at FROM bans"
                      " WHERE removed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
                      (int(time.time()),)).fetchall()
    sent, lifted = {}, {}
    for ban_id, server_id, action in db.execute("SELECT ban_id, server_id, action FROM ban_sync"):
        (sent if action == "ban" else lifted).setdefault(server_id, set()).add(ban_id)
    db.close()
    return bans, sent, lifted


async def game_list(client):
    identities, total, page, pages = set(), None, 1, 1
    while page <= pages and page <= 100:
        reply = await client.command(f"#ban list {page}")
        identities |= set(IDENTITY.findall(reply.lower()))
        found = TOTAL.search(reply)
        total = int(found.group(1)) if found else total
        numbers = PAGES.search(reply)
        pages = int(numbers.group(2)) if numbers else 1
        page += 1
        await asyncio.sleep(0.5)
    return identities, total, pages


async def ask(server, bans, sent, lifted, find=""):
    client = RconClient(server.rcon_host, server.rcon_port, server.rcon_password)
    try:
        await client.connect()
        listed, total, pages = await game_list(client)
    except RconError as exc:
        print(f"{server.id}: could not ask: {exc}")
        return
    finally:
        client.close()
    gone = [ban for ban in bans if ban[1].lower() not in listed]
    missing = [(ban[1], ban[2]) for ban in gone]
    ours = {ban[1].lower() for ban in bans}
    unsent = sum(1 for ban in bans if ban[0] not in sent.get(server.id, set()))
    print(f"\n=== {server.id} ===")
    print(f"The game says it has {total} bans ({pages} pages read, {len(listed)} identities seen).")
    print(f"The panel has {len(bans)} active bans: {len(bans) - len(missing)} are in the game's list, "
          f"{len(missing)} are not.")
    print(f"The panel has not marked {unsent} of its active bans as sent to this server.")
    print(f"{len(listed - ours)} bans in the game's list are not active bans in the panel.")
    if gone:
        permanent = sum(1 for ban in gone if ban[3] is None)
        stamp = lambda at: time.strftime("%d %b %Y", time.gmtime(at))
        print(f"Of those {len(gone)}: {permanent} permanent, {len(gone) - permanent} with a length. "
              f"Made between {stamp(min(b[5] for b in gone))} and {stamp(max(b[5] for b in gone))}.")
        makers = {}
        for ban in gone:
            makers[ban[4]] = makers.get(ban[4], 0) + 1
        print("By who: " + ", ".join(f"{who} {n}" for who, n in sorted(makers.items(), key=lambda kv: -kv[1])[:6]))
        lifted_here = sum(1 for ban in gone if ban[0] in lifted.get(server.id, set()))
        print(f"The panel also sent an unban to this server for {lifted_here} of them.")
    print("Newest bans:")
    for ban in sorted(bans, key=lambda b: -b[5])[:5]:
        made = time.strftime("%d %b %H:%M", time.gmtime(ban[5]))
        print(f"  {made} UTC by {ban[4]}: {ban[2]} {ban[1]}: "
              f"{'in' if ban[1].lower() in listed else 'NOT in'} the game's list")
    if find:
        print(f"Bans matching '{find}' by name, identity or who made them, newest first:")
        found = [ban for ban in bans if find in f"{ban[2]} {ban[1]} {ban[4]}".lower()]
        for ban in sorted(found, key=lambda b: -b[5])[:MATCHES]:
            made = time.strftime("%d %b %H:%M", time.gmtime(ban[5]))
            print(f"  {made} UTC by {ban[4]}: {ban[2]} {ban[1]}: "
                  f"{'in' if ban[1].lower() in listed else 'NOT in'} the game's list")
    for identity, name in missing[:SHOWN]:
        print(f"  not in the game's list: {name} {identity}")
    if len(missing) > SHOWN:
        print(f"  ... and {len(missing) - SHOWN} more")


async def main():
    config = load_config()
    find = next((a[5:].lower() for a in sys.argv[1:] if a.startswith("find=")), "")
    wanted = [a for a in sys.argv[1:] if not a.startswith("find=")]
    bans, sent, lifted = panel_view(config.database)
    for server in config.servers:
        if server.configured and (not wanted or server.id in wanted):
            await ask(server, bans, sent, lifted, find)


if __name__ == "__main__":
    asyncio.run(main())
