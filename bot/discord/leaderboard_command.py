"""Shared combat-standings query and embed for the pinned channel leaderboard."""
import unicodedata

import discord

from bot.config import linking_channel
from bot.storage.combat_store import week_start, window_present, window_sides, window_standings
from bot.storage.panel_saves import match_summary

PAGE_SIZE = 25

# The board is read on phones more than anything else, and a Discord code
# block wraps there at about 37 characters. These widths are fixed rather
# than measured per page so a three-digit position cannot quietly push a
# later page over the edge while the first pages look fine.
NAME = 14
PLACE = 4
SCORE = 4


def standings(db, guild, start=None, end=None):
    """One snapshot for the current week; never write XP or stats."""
    return window_standings(db, guild, week_start() if start is None else start, end)


def clean_name(value, width=NAME):
    # Keep player text inside its table cell, including malicious Markdown names.
    value = ''.join(' ' if ch.isspace() else ch for ch in str(value)
                    if not unicodedata.category(ch).startswith('C'))
    value = ' '.join(value.replace('`', "'").split()) or 'Unknown player'
    return value[:width - 1] + '…' if len(value) > width else value


SIDES = {'US': 'US', 'USSR': 'RU', 'FIA': 'FIA'}


def side_code(faction, switched=False):
    """The short tag a board shows for a side, with a * when they played the other side too.
    Civilians keep their own, and no pick is a dash."""
    if not faction:
        return '-'
    return SIDES.get(faction, faction[:3]) + ('*' if switched else '')


def side_of(sides, member_id):
    """A member's tag from window_sides(), or a dash."""
    side = sides.get(member_id)
    return side_code(*side) if side else '-'


def table(selected, offset=0):
    """Fixed-width standings block shared by the weekly and per-match boards. A row is (name, kills,
    deaths), then optionally the side tag, which adds a Side column."""
    sided = any(len(row) > 3 and row[3] for row in selected)
    lines = [f"{'#':>{PLACE}}  {'Name':{NAME}} {'K':>{SCORE}} {'D':>{SCORE}}" + ('  Side' if sided else '')]
    for position, (name, kills, deaths, *side) in enumerate(selected, offset + 1):
        line = f'{position:>{PLACE}}  {clean_name(name):{NAME}} {kills:>{SCORE}} {deaths:>{SCORE}}'
        lines.append(f'{line}  {side[0] or "-"}' if sided and side else line)
    return '```text\n' + '\n'.join(lines) + '\n```'


def match_rows(links, guild, server_id, start, end=None):
    """One match's board rows: (name, kills, deaths, side)."""
    db = links.db
    sides = window_sides(db, guild.id, start, end, server_id)
    rows = []
    for member_id, kills, deaths, name in window_standings(db, guild.id, start, end, server=server_id):
        member = guild.get_member(member_id)
        rows.append((name or (member.display_name if member else f'Member {member_id}'), kills, deaths,
                     side_of(sides, member_id)))
    return rows


def match_bases(links, server_id, start, end):
    """{side: bases held at the end} from OYB Control's autosaves, and {identity: game XP gained}."""
    gains, bases = match_summary(window_present(links.db, start, end, server_id), start, end)
    return bases, gains


def bases_line(bases):
    """'Bases at the end: US 24 · RU 16', most first; blank without a save."""
    if not bases:
        return ''
    held = ' · '.join(f'{side_code(side)} {count}' for side, count in sorted(bases.items(), key=lambda kv: -kv[1]))
    return f'🏳️ Bases at the end: {held}'


def leaderboard_embed(rows, page, start=None):
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    embed = discord.Embed(title='OYB LEADERBOARD', colour=0xA9BC8C)
    if rows:
        offset = page * PAGE_SIZE
        embed.description = table(rows[offset:offset + PAGE_SIZE], offset)
    else:
        embed.description = (f'No combat recorded this week yet. Link your account in {linking_channel()} '
                             'and get stuck in — the board resets every Monday.')
    week = f'Week of {start:%d %b}' if start else 'This week'
    embed.set_footer(text=f'Page {page + 1}/{pages} • {len(rows)} players • {week}\n'
                          'Player kills ↓ · deaths ↑ • * switched sides • Resets Monday')
    return embed
