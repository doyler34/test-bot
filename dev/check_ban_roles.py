"""Walk through every step the ban roles depend on and say which one fails.

Run from the bot's folder:  .venv/bin/python dev/check_ban_roles.py
"""

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.discord.ban_roles import PREFIX, active_bans  # noqa: E402

API = "https://discord.com/api/v10"


def links_db():
    state = os.getenv("NOTIFICATION_STATE_DB", "data/notifications.sqlite3")
    return os.getenv("ACCOUNT_LINKS_DB", str(Path(state).with_name("account_links.sqlite3")))


async def main():
    load_dotenv()
    panel = os.getenv("PANEL_DB", "data/panel.sqlite3")
    guild = os.getenv("GUILD_ID", "")
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    print(f"Panel database: {panel} ({'found' if Path(panel).is_file() else 'MISSING'})")
    bans = active_bans(panel)
    if bans is None:
        print("-> The bot can't read the panel's bans. Fix PANEL_DB in .env.")
        return
    if not bans:
        print("-> There are no active bans in the panel, so there's nothing to give a role for.")
        return
    print(f"Active bans: {len(bans)}")
    links = links_db()
    print(f"Links database: {links} ({'found' if Path(links).is_file() else 'MISSING'})")
    linked = {}
    with sqlite3.connect(links) as db:
        for identity, label in bans.items():
            row = db.execute("SELECT discord_id FROM account_links WHERE identity = ?", (identity,)).fetchone()
            print(f"  {identity}  {PREFIX}{label}  ->  " + (f"Discord {row[0]}" if row else "NOT LINKED"))
            if row:
                linked[row[0]] = label
    if not linked:
        print("-> None of the banned players has a linked Discord account. Link them through Join OYB first.")
        return
    headers = {"Authorization": f"Bot {token}"}
    async with aiohttp.ClientSession(headers=headers) as http:
        async with http.get(f"{API}/guilds/{guild}/roles") as r:
            if r.status != 200:
                print(f"-> Discord refused to list roles ({r.status}). Check GUILD_ID and the token.")
                return
            roles = {role["id"]: role for role in await r.json()}
        async with http.get(f"{API}/users/@me") as r:
            me = (await r.json())["id"]
        async with http.get(f"{API}/guilds/{guild}/members/{me}") as r:
            mine = await r.json()
        top = max((roles[i]["position"] for i in mine.get("roles", []) if i in roles), default=0)
        manage = any(int(roles[i]["permissions"]) & (0x10000000 | 0x8) for i in mine.get("roles", []) if i in roles)
        print(f"Bot: Manage Roles {'yes' if manage else 'NO'}, highest role position {top}")
        ban_roles = {r["name"]: r for r in roles.values() if r["name"].startswith(PREFIX)}
        print("Ban roles in the server: " + (", ".join(f"{n} (position {r['position']})" for n, r in ban_roles.items()) or "none yet"))
        for member_id, label in linked.items():
            async with http.get(f"{API}/guilds/{guild}/members/{member_id}") as r:
                if r.status != 200:
                    print(f"  Discord {member_id}: NOT IN THE SERVER")
                    continue
                member = await r.json()
            has = [roles[i]["name"] for i in member.get("roles", []) if i in roles and roles[i]["name"].startswith(PREFIX)]
            print(f"  Discord {member_id} ({member['user']['username']}): has {has or 'no ban role'}, should have {PREFIX}{label}")
    log = Path("bot.log")
    if log.is_file():
        lines = [line for line in log.read_text(errors="replace").splitlines() if "ban_roles" in line or "Ban role" in line]
        print("Bot log:\n  " + "\n  ".join(lines[-6:] or ["(nothing about ban roles)"]))


asyncio.run(main())
