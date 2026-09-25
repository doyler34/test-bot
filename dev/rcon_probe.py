"""Send a few RCON commands and print every reply and server message, raw.

    .venv/bin/python dev/rcon_probe.py PORT PASSWORD [command ...]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panel.rcon import RconClient, RconError  # noqa: E402

DEFAULT = ["#players", "players", "#status", "#help"]


async def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    port, password = int(sys.argv[1]), sys.argv[2]
    commands = sys.argv[3:] or DEFAULT
    client = RconClient("127.0.0.1", port, password)
    client.on_message = lambda text: print(f"  [server message] {text!r}")
    await client.connect()
    print("logged in")
    for command in commands:
        print(f"\n>>> {command}")
        try:
            print(f"  [reply] {await client.command(command)!r}")
        except RconError as exc:
            print(f"  [error] {exc}")
        await asyncio.sleep(2)
    client.close()


asyncio.run(main())
