"""Shared combat-standings query and embed for the pinned channel leaderboard."""
import unicodedata

import discord

from bot.storage.combat_store import week_start, window_standings

PAGE_SIZE = 15


def standings(db, guild, start=None, end=None):
    """One snapshot for the current week; never write XP or stats."""
    return window_standings(db, guild, week_start() if start is None else start, end)


def clean_name(value):
    # Keep player text inside its table cell, including malicious Markdown names.
    value = ''.join(' ' if ch.isspace() else ch for ch in str(value)
                    if not unicodedata.category(ch).startswith('C'))
    value = ' '.join(value.replace('`', "'").split()) or 'Unknown player'
    return value[:17] + '…' if len(value) > 18 else value


def table(selected, offset=0):
    """Fixed-width standings block shared by the weekly and per-match boards."""
    number_width = max(2, len(str(offset + len(selected))))
    kills_width = max(5, max(len(str(row[1])) for row in selected))
    deaths_width = max(6, max(len(str(row[2])) for row in selected))
    lines = [f"{'#':>{number_width}}  {'Name':18}  {'Kills':>{kills_width}}  {'Deaths':>{deaths_width}}"]
    for position, (name, kills, deaths) in enumerate(selected, offset + 1):
        lines.append(f'{position:>{number_width}}  {clean_name(name):18}  {kills:>{kills_width}}  {deaths:>{deaths_width}}')
    return '```text\n' + '\n'.join(lines) + '\n```'


def leaderboard_embed(rows, page, start=None):
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    embed = discord.Embed(title='OYB LEADERBOARD', colour=0xA9BC8C)
    if rows:
        offset = page * PAGE_SIZE
        embed.description = table(rows[offset:offset + PAGE_SIZE], offset)
    else:
        embed.description = 'No combat recorded this week yet. Link your account in **#join-oyb** and get stuck in — the board resets every Monday.'
    week = f'Week of {start:%d %b}' if start else 'This week'
    embed.set_footer(text=f'Page {page + 1}/{pages} • {len(rows)} players • {week}\n'
                          'Player kills ↓ · deaths ↑ • Resets Monday')
    return embed
