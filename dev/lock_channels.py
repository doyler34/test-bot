#!/usr/bin/env python3
"""Put every channel behind the member role, leaving the way in open.

Denies @everyone View Channel everywhere, allows the member role instead, and
leaves the channels you name visible to everyone so a new member can read the
rules and press the button. Lists what it would change and does nothing until
--apply.

    cd ~/Arma-bot && .venv/bin/python dev/lock_channels.py --open 123,456
    cd ~/Arma-bot && .venv/bin/python dev/lock_channels.py --open 123,456 --apply

--open takes the channel ids that stay public (rules, start here). Everything
else goes behind the member role. Nothing else about a channel is touched: its
other role overwrites, its category and its own settings are left alone.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
flags = [a for a in sys.argv[1:] if a.startswith('--')]
apply = '--apply' in flags
force = '--force' in flags
opened = set()
for index, arg in enumerate(sys.argv):
    if arg == '--open' and index + 1 < len(sys.argv):
        opened = {int(v) for v in sys.argv[index + 1].replace(' ', '').split(',') if v}

role_name = os.getenv('MEMBER_ROLE_NAME', 'OYB Member')
token, guild_id = os.getenv('DISCORD_BOT_TOKEN'), os.getenv('GUILD_ID')
if not token or not guild_id:
    print('DISCORD_BOT_TOKEN and GUILD_ID must be set in .env')
    raise SystemExit(1)
if not opened:
    print('Give --open with the ids that stay public, e.g. --open <rules>,<start-here>.')
    print('Without one, nobody who joins could ever read the rules or verify.')
    raise SystemExit(1)

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    try:
        guild = client.get_guild(int(guild_id))
        if guild is None:
            print('The bot is not in that guild.')
            return
        matches = [r for r in guild.roles if r.name == role_name]
        if len(matches) != 1:
            print(f'Need exactly one role named {role_name!r}; found {len(matches)}.')
            return
        member = matches[0]

        # Locking the door before anyone holds a key blinds the whole server.
        holders = sum(1 for m in guild.members if member in m.roles)
        humans = sum(1 for m in guild.members if not m.bot)
        print(f'role      : {member.name} ({member.id})')
        print(f'holding it: {holders} of {humans} members')
        if humans and holders < humans * 0.9 and not force:
            print(f'\nSTOPPING. Only {holders} of {humans} hold {member.name!r}. Locking the '
                  'server now would shut the rest out.\nRun dev/grant_role.py first, or pass '
                  '--force if you meant it.')
            return

        everyone = guild.default_role
        planned, blocked = [], []
        targets = list(guild.categories) + [c for c in guild.channels if c.category is None]
        for channel in targets:
            public = channel.id in opened
            now = channel.overwrites_for(everyone)
            want_everyone = True if public else False
            need = now.view_channel is not want_everyone
            if public:
                need = need or not now.read_messages or now.send_messages is not False
            if not channel.permissions_for(guild.me).manage_permissions:
                blocked.append(channel)
                continue
            if need or (not public and channel.overwrites_for(member).view_channel is not True):
                planned.append((channel, public))

        print(f'\n{len(planned)} to change, {len(blocked)} the bot cannot touch\n')
        for channel, public in planned:
            kind = 'category' if isinstance(channel, discord.CategoryChannel) else 'channel'
            print(f'  {"OPEN TO ALL" if public else "members only":14} {kind:9} {channel.name}')
        for channel in blocked:
            print(f'  {"NO ACCESS":14} {channel.name}')
        if not apply:
            print('\nNothing changed. Re-run with --apply to do it.')
            return

        done = 0
        for channel, public in planned:
            overwrite = channel.overwrites_for(everyone)
            if public:
                overwrite.update(view_channel=True, read_messages=True, send_messages=False)
            else:
                overwrite.update(view_channel=False)
            try:
                await channel.set_permissions(everyone, overwrite=overwrite,
                                              reason='OYB gate: members only')
                if not public:
                    allow = channel.overwrites_for(member)
                    allow.update(view_channel=True)
                    await channel.set_permissions(member, overwrite=allow,
                                                  reason='OYB gate: members see this')
                done += 1
            except discord.HTTPException as error:
                print(f'  failed on {channel.name}: {error}')
        print(f'\nchanged {done} of {len(planned)}')
        print('Channels inside a category follow it only if they are synced to it. '
              'Check any channel with its own permissions.')
    finally:
        await client.close()


try:
    client.run(token, log_handler=None)
except discord.PrivilegedIntentsRequired:
    print('Turn on the Server Members Intent for this bot in the Developer Portal.')
