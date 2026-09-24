#!/usr/bin/env python3
"""Why is the match-notification panel not in the roles channel?

Walks the same path the bot takes at boot and says where it stops. Read-only
unless --fix is given, which posts the panel and creates the roles for real.

    cd ~/Arma-bot && .venv/bin/python dev/diagnose_notifications.py
    cd ~/Arma-bot && .venv/bin/python dev/diagnose_notifications.py --fix
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
fix = "--fix" in sys.argv[1:]
token, guild_id = os.getenv("DISCORD_BOT_TOKEN"), os.getenv("GUILD_ID")
if not token or not guild_id:
    print("DISCORD_BOT_TOKEN and GUILD_ID must be set in .env")
    raise SystemExit(1)

client = discord.Client(intents=discord.Intents.default())


def read_servers():
    """id, name and enabled only - the bits the panel is built from."""
    path = os.getenv("SERVERS_CONFIG", "servers.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(SimpleNamespace(id=e["id"], name=e["name"],
                                 enabled=e.get("enabled", True)) for e in data)


@client.event
async def on_ready():
    try:
        from bot.config import faction_channel_id
        from bot.discord.notification_roles import NAMES, PANEL_MARKER, prepare_notifications
        from bot.discord.server_stats import label_for
        from bot.storage.notification_store import NotificationStore

        guild = client.get_guild(int(guild_id))
        if guild is None:
            print(f"guild {guild_id} not found; is the bot invited?")
            return
        print(f"guild      : {guild.name} ({guild.id})")
        print(f"my top role: {guild.me.top_role.name} position={guild.me.top_role.position}")
        print(f"manage roles: {guild.me.guild_permissions.manage_roles}")

        pinned = faction_channel_id()
        channel = guild.get_channel(pinned) if pinned else None
        print(f"\nfaction channel: {pinned}")
        if not isinstance(channel, discord.TextChannel):
            print("  NOT A TEXT CHANNEL I CAN SEE - this is why nothing posts.")
            print("  Give the bot View Channel on it, or set FACTION_CHANNEL_ID in .env")
            return
        perms = channel.permissions_for(guild.me)
        print(f"  #{channel.name}  view={perms.view_channel} send={perms.send_messages}"
              f" history={perms.read_message_history} embeds={perms.embed_links}")
        if not (perms.view_channel and perms.send_messages and perms.read_message_history):
            print("  MISSING PERMISSIONS - the panel cannot be posted or found.")
            return

        # Only the server list matters here, so read it straight from
        # SERVERS_CONFIG rather than through the full loader - that one also
        # demands log directories this check has no use for.
        servers = read_servers()
        print("\nservers:")
        for server in servers:
            print(f"  {server.id:10} enabled={server.enabled} label={label_for(server)}"
                  f" role={NAMES.get(server.id, server.name + ' Notifications')!r}")
        if not any(s.enabled for s in servers):
            print("  NO SERVER IS ENABLED - the panel has no buttons, so it is skipped.")
            return

        print("\nexisting roles with those names:")
        for server in servers:
            name = NAMES.get(server.id, server.name + " Notifications")
            for role in [r for r in guild.roles if r.name == name]:
                print(f"  {name!r} id={role.id} position={role.position}"
                      f" perms={role.permissions.value} managed={role.managed}"
                      f" mentionable={role.mentionable}"
                      f" below_me={guild.me.top_role > role}")

        found = None
        async for message in channel.history(limit=50):
            if message.author.id == client.user.id and any(
                    e.footer and e.footer.text == PANEL_MARKER for e in message.embeds):
                found = message
                break
        print(f"\npanel already in the channel: {found.jump_url if found else 'no'}")

        if not fix:
            print("\nRead-only. Re-run with --fix to create the roles and post the panel.")
            return

        client.store = NotificationStore(
            os.getenv("NOTIFICATION_STATE_DB", "data/notifications.sqlite3"))
        client.config = SimpleNamespace(guild_id=guild.id, servers=servers)
        client.roles_by_server = {}
        client.notification_role_lock = asyncio.Lock()
        try:
            await prepare_notifications(client, guild, channel)
            print("\nposted. roles:", {k: v.name for k, v in client.roles_by_server.items()})
        finally:
            client.store.close()
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()


client.run(token, log_handler=None)
