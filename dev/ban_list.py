"""Ask each game server for its own ban list and compare it with the panel's.

Run from the panel's folder, where panel.local.json is:

    .venv/bin/python dev/ban_list.py [server-id]

It only reads. Nothing is banned or unbanned.
"""

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panel.config import load_config  # noqa: E402
from panel.rcon import RconClient, RconError  # noqa: E402


def panel_bans(database):
    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    rows = db.execute("SELECT identity, name FROM bans WHERE removed_at IS NULL"
                      " AND (expires_at IS NULL OR expires_at > ?)", (int(time.time()),)).fetchall()
    db.close()
    return rows


async def ask(server, bans):
    client = RconClient(server.rcon_host, server.rcon_port, server.rcon_password)
    try:
        await client.connect()
        reply = await client.command("#ban list")
    except RconError as exc:
        print(f"{server.id}: could not ask: {exc}")
        return
    finally:
        client.close()
    print(f"\n=== {server.id} ===\n{reply.strip() or '(empty reply)'}\n")
    missing = [(identity, name) for identity, name in bans if identity.lower() not in reply.lower()]
    print(f"{len(bans) - len(missing)} of the panel's {len(bans)} active bans are in this server's list.")
    for identity, name in missing:
        print(f"  not in the game's list: {name} {identity}")


async def main():
    config = load_config()
    wanted = sys.argv[1:]
    bans = panel_bans(config.database)
    for server in config.servers:
        if server.configured and (not wanted or server.id in wanted):
            await ask(server, bans)


asyncio.run(main())
