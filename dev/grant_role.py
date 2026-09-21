#!/usr/bin/env python3
"""Give one role to every member who does not already hold it.

A one-off backfill, not part of the running bot. Lists what it would do and
changes nothing until you pass --apply. Run on the bot host:

    cd ~/Arma-bot && .venv/bin/python dev/grant_role.py "OYB Member"
    cd ~/Arma-bot && .venv/bin/python dev/grant_role.py "OYB Member" --apply

--unless skips anyone who already holds another role, which is how you label
only the members who have not been let in yet:

    .venv/bin/python dev/grant_role.py "Unverified" --unless "OYB Member" --apply

Needs ENABLE_MEMBERS_INTENT=true in .env and the Server Members Intent ticked
in the Developer Portal, otherwise Discord will not hand over the member list.
Bots are skipped unless you pass --bots.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
import os  # noqa: E402

load_dotenv()
args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}
name = args[0] if args else "OYB Member"
apply = "--apply" in flags
include_bots = "--bots" in flags
skip_holders_of = None
for index, arg in enumerate(sys.argv):
    if arg == "--unless" and index + 1 < len(sys.argv):
        skip_holders_of = sys.argv[index + 1]

token = os.getenv("DISCORD_BOT_TOKEN")
guild_id = os.getenv("GUILD_ID")
if not token or not guild_id:
    print("DISCORD_BOT_TOKEN and GUILD_ID must be set in .env")
    raise SystemExit(1)

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    try:
        guild = client.get_guild(int(guild_id)) or await client.fetch_guild(int(guild_id))
        roles = await guild.fetch_roles()
        matches = [r for r in roles if r.name == name]
        if not matches:
            print(f"No role named {name!r}. Roles: {', '.join(sorted(r.name for r in roles))}")
            return
        if len(matches) > 1:
            print(f"{len(matches)} roles named {name!r}; rename or delete the extras first.")
            return
        role = matches[0]
        me = guild.me or await guild.fetch_member(client.user.id)
        if not me.guild_permissions.manage_roles:
            print("The bot needs Manage Roles.")
            return
        if role >= me.top_role:
            print(f"{name!r} sits at or above the bot's own role; move the bot above it first.")
            return

        spared = None
        if skip_holders_of:
            found = [r for r in roles if r.name == skip_holders_of]
            if len(found) != 1:
                print(f"--unless {skip_holders_of!r}: found {len(found)} roles by that name.")
                return
            spared = found[0]

        members = [m async for m in guild.fetch_members(limit=None)]
        missing = [m for m in members if role not in m.roles and (include_bots or not m.bot)
                   and (spared is None or spared not in m.roles)]
        bots = sum(1 for m in members if m.bot)
        print(f"role     : {name} ({role.id})")
        if spared is not None:
            print(f"skipping : anyone with {spared.name}")
        print(f"members  : {len(members)} ({bots} bots{'' if include_bots else ', skipped'})")
        print(f"have it  : {len(members) - len(missing) - (0 if include_bots else bots)}")
        print(f"to add   : {len(missing)}")
        if not apply:
            for m in missing[:20]:
                print(f"   would add to {m} ({m.id})")
            if len(missing) > 20:
                print(f"   ...and {len(missing) - 20} more")
            print("\nNothing changed. Re-run with --apply to do it.")
            return

        done = failed = 0
        for index, member in enumerate(missing, 1):
            try:
                await member.add_roles(role, reason=f"Backfill {name}", atomic=True)
                done += 1
            except discord.HTTPException as error:
                failed += 1
                print(f"   failed for {member} ({member.id}): {error}")
            if index % 25 == 0:
                print(f"   {index}/{len(missing)}...")
        print(f"\nadded {done}, failed {failed}")
    finally:
        await client.close()


try:
    client.run(token, log_handler=None)
except discord.PrivilegedIntentsRequired:
    print("Turn on the Server Members Intent for this bot in the Discord Developer Portal.")
