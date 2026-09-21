#!/usr/bin/env python3
"""Why did somebody not get the member role?

Checks every reason the grant can fail, in one pass. Read-only. Run on the
bot host, optionally with the Discord id of the member who got stuck:

    cd ~/Arma-bot && .venv/bin/python dev/diagnose_onboarding.py
    cd ~/Arma-bot && .venv/bin/python dev/diagnose_onboarding.py 123456789012345678
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
who = next((int(a) for a in sys.argv[1:] if a.isdigit()), None)
token, guild_id = os.getenv("DISCORD_BOT_TOKEN"), os.getenv("GUILD_ID")
if not token or not guild_id:
    print("DISCORD_BOT_TOKEN and GUILD_ID must be set in .env")
    raise SystemExit(1)

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


def report(guild, label, name):
    matches = [r for r in guild.roles if r.name == name]
    me = guild.me
    if not name:
        print(f"  {label:12} not configured")
        return None
    if not matches:
        print(f"  {label:12} MISSING - no role called {name!r}")
        return None
    if len(matches) > 1:
        print(f"  {label:12} {len(matches)} ROLES SHARE THIS NAME - the bot refuses to guess")
        for r in matches:
            print(f"               id={r.id} position={r.position}")
        return None
    role = matches[0]
    flags = []
    if role.managed:
        flags.append("MANAGED by an integration")
    if role >= me.top_role:
        flags.append(f"ABOVE the bot's own role ({me.top_role.name} at {me.top_role.position})")
    if role.permissions.value != 0:
        flags.append("has permissions set")
    state = "ok" if not flags else "; ".join(flags)
    print(f"  {label:12} {name} (id={role.id} position={role.position}) -> {state}")
    return role


@client.event
async def on_ready():
    try:
        guild = client.get_guild(int(guild_id))
        if guild is None:
            print("The bot is not in that guild, or GUILD_ID is wrong.")
            return
        me = guild.me
        print(f"guild        : {guild.name} ({guild.id}), {guild.member_count} members")
        print(f"bot role     : {me.top_role.name} at position {me.top_role.position}")
        print(f"manage roles : {me.guild_permissions.manage_roles}")
        print(f"onboarding   : {os.getenv('ONBOARDING_CHANNEL_ID') or 'NOT SET - panel is off'}")
        print(f"members intent: {os.getenv('ENABLE_MEMBERS_INTENT') or 'NOT SET - joins are not seen'}")
        print("\nroles the panel needs:")
        member_role = report(guild, "member", os.getenv("MEMBER_ROLE_NAME", "OYB Member"))
        report(guild, "unverified", os.getenv("UNVERIFIED_ROLE_NAME", "Unverified"))
        for faction in ("US", "USSR", "FIA"):
            report(guild, "faction", faction)

        path = os.getenv("ACCOUNT_LINKS_DB") or "data/account_links.sqlite3"
        if who and Path(path).is_file():
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            links = db.execute("SELECT identity,verified_by FROM account_links WHERE guild=? AND discord_id=?",
                               (guild.id, who)).fetchall()
            reqs = db.execute("SELECT name,status,identity FROM link_requests WHERE guild=? AND discord_id=?"
                              " ORDER BY created DESC LIMIT 5", (guild.id, who)).fetchall()
            member = guild.get_member(who) or await guild.fetch_member(who)
            print(f"\nmember {member} ({who}):")
            print(f"  roles      : {', '.join(r.name for r in member.roles) or 'none'}")
            print(f"  has member role: {member_role in member.roles if member_role else 'cannot tell'}")
            print(f"  links      : {links or 'none'}")
            for name, status, identity in reqs:
                print(f"  request    : {name!r} {status} identity={identity}")
            db.close()
        elif who:
            print(f"\nNo database at {path}; skipping the member check.")
    finally:
        await client.close()


try:
    client.run(token, log_handler=None)
except discord.PrivilegedIntentsRequired:
    print("Turn on the Server Members Intent in the Developer Portal.")
