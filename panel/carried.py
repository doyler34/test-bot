"""What each player's character is carrying right now, read from the game's autosave.

Everything a character holds (clothing, what's in it, weapons and their attachments) is stored inside its record
as nested entities: a block holding the item's id, then its 69-bit config block, then its own blocks, the first
being its prefab and position. Rather than walk every kind of storage, the record is searched for that config
block's header, and each hit is read as an item.

An item still exactly as the character spawned with it is saved by id only, with no prefab, so it isn't counted
here; those come from the arsenal loadout the character spawned from (see read_loadouts in servers.py)."""
import struct
from collections import Counter

from . import save_world as sw

# A block marker (1) and its 32-bit length, 69, as they read lowest bit first.
CONFIG_BLOCK = "1" + format(69, "032b")[::-1]


def items(value, end):
    """Counter of prefab GUIDs of the items nested in one record."""
    bits = format(value, "b")[::-1].ljust(end, "0")[:end]
    found = Counter()
    # The record's own id and config block come first; items start after them.
    at = bits.find(CONFIG_BLOCK, 64 + 128 + 33)
    while at >= 0:
        start = at - 128 - 33
        if start >= 0 and bits[start] == "1":
            try:
                block_start, block_end = sw.Bits(value, start, end).block()
                r = sw.Bits(value, block_start, block_end)
                r.uuid()
                r.block()
                for part in sw.blocks(value, r.at, block_end) or []:
                    if sw.Bits(value, *part).take(1) == 0:
                        got = sw.entity(value, *part)
                        if got and got["prefab"]:
                            found[got["prefab"]] += 1
                        break
            except sw.Bad:
                pass
        at = bits.find(CONFIG_BLOCK, at + 1)
    return found


def read(data):
    """{player identity: {"items": Counter of prefab GUIDs, "prefab": character prefab GUID}} for every player
    whose character is alive in this save; None if it can't be read."""
    players, bodies = {}, {}
    try:
        for section, record in sw.records(data):
            if section not in ("Player", "Character"):
                continue
            rid, value, parts = sw.split(record)
            if section == "Player":
                for part in parts:
                    if sw.Bits(value, part[0], part[1]).take(1) == 0:
                        links = sw.exactly(sw.player_links, value, *part)
                        if links:
                            players[rid] = links
                            break
                continue
            body = {"dead": False, "prefab": ""}
            for part in parts:
                if sw.Bits(value, part[0], part[1]).take(1) == 0 and not body["prefab"]:
                    got = sw.entity(value, *part)
                    if got:
                        body["prefab"] = got["prefab"]
                        continue
                for comp in sw.components(value, part):
                    zones = sw.exactly(sw.hit_zones, value, *comp)
                    if zones:
                        body["dead"] = zones["zones"].get("Health", 1) <= 0
            body["items"] = items(value, value & 0xFFFFFFFF)
            bodies[rid] = body
    except (struct.error, IndexError, sw.Bad, ValueError):
        return None
    out = {}
    for identity, links in players.items():
        body = bodies.get(links.get("current")) or bodies.get(links.get("body"))
        if body and not body["dead"]:
            out[identity] = {"items": body["items"], "prefab": body["prefab"]}
    return out
