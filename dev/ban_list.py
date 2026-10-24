"""Ask each game server for its own ban list and compare it with the panel's.

Run from the panel's folder, where panel.local.json is:

    .venv/bin/python dev/ban_list.py [server-id ...]

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


def panel_view(database):
    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    bans = db.execute("SELECT id, identity, name FROM bans WHERE removed_at IS NULL"
                      " AND (expires_at IS NULL OR expires_at > ?)", (int(time.time()),)).fetchall()
    sent = {}
    for ban_id, server_id in db.execute("SELECT ban_id, server_id FROM ban_sync WHERE action = 'ban'"):
        sent.setdefault(server_id, set()).add(ban_id)
    db.close()
    return bans, sent


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


async def ask(server, bans, sent):
    client = RconClient(server.rcon_host, server.rcon_port, server.rcon_password)
    try:
        await client.connect()
        listed, total, pages = await game_list(client)
    except RconError as exc:
        print(f"{server.id}: could not ask: {exc}")
        return
    finally:
        client.close()
    missing = [(identity, name) for _, identity, name in bans if identity.lower() not in listed]
    ours = {identity.lower() for _, identity, _ in bans}
    unsent = sum(1 for ban_id, _, _ in bans if ban_id not in sent.get(server.id, set()))
    print(f"\n=== {server.id} ===")
    print(f"The game says it has {total} bans ({pages} pages read, {len(listed)} identities seen).")
    print(f"The panel has {len(bans)} active bans: {len(bans) - len(missing)} are in the game's list, "
          f"{len(missing)} are not.")
    print(f"The panel has not marked {unsent} of its active bans as sent to this server.")
    print(f"{len(listed - ours)} bans in the game's list are not active bans in the panel.")
    for identity, name in missing[:SHOWN]:
        print(f"  not in the game's list: {name} {identity}")
    if len(missing) > SHOWN:
        print(f"  ... and {len(missing) - SHOWN} more")


async def main():
    config = load_config()
    wanted = sys.argv[1:]
    bans, sent = panel_view(config.database)
    for server in config.servers:
        if server.configured and (not wanted or server.id in wanted):
            await ask(server, bans, sent)


asyncio.run(main())
