"""Where every player is, read from the game's own autosave.

The save is the vanilla persistence blob: sections of records, each record a bitstream read lowest bit first.
A record is the entity's id, a block naming its persistence config, a block with its prefab and position, then
one block per component that isn't at its default. Field names aren't stored, so the parts are told apart by
their shape. Numbers are packed: a flag bit, then 7 bits, or a 3-bit size and that many bytes plus one. Ints
add a sign bit in front. A block is a 1 bit, a 32-bit length, then its contents.

A Player record holds the account's identity, faction and XP, and points at the character it plays; the
character's record has the position and its hit zones ("Health" at 0 is a dead body)."""
import json
import struct
import uuid
from pathlib import Path

PREFABS = Path(__file__).resolve().parent.parent / "assets" / "save" / "prefabs.json"
_prefabs = None
_cache = {}


class Bad(Exception):
    pass


class Bits:
    def __init__(self, value, at, end):
        self.v, self.at, self.end = value, at, end

    def take(self, n):
        if self.at + n > self.end:
            raise Bad()
        out = (self.v >> self.at) & ((1 << n) - 1)
        self.at += n
        return out

    def flag(self):
        return self.take(1)

    def count(self):
        if not self.take(1):
            return self.take(7)
        return self.take((self.take(3) + 1) * 8)

    def int(self):
        negative = self.take(1)
        value = self.count()
        return -value if negative else value

    def float(self):
        return struct.unpack("<f", struct.pack("<I", self.take(32)))[0]

    def text(self):
        n = self.count()
        if n * 8 > self.end - self.at:
            raise Bad()
        return self.take(n * 8).to_bytes(n, "little").decode("utf-8", "replace") if n else ""

    def uuid(self):
        return str(uuid.UUID(bytes=self.take(128).to_bytes(16, "little")))

    def block(self):
        if not self.take(1):
            raise Bad()
        size = self.take(32)
        start = self.at
        if not size or start + size > self.end:
            raise Bad()
        self.at = start + size
        return start, start + size


def blocks(value, start, end):
    """The blocks filling [start, end) end to end, or None if it isn't laid out that way."""
    parts, r = [], Bits(value, start, end)
    try:
        while r.at < end:
            parts.append(r.block())
    except Bad:
        return None
    return parts


def prefab_name(guid):
    global _prefabs
    if _prefabs is None:
        try:
            _prefabs = json.loads(PREFABS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _prefabs = {}
    return _prefabs.get(guid, "")


def entity(value, start, end):
    """The generic entity block: version, prefab, position, rotation, prefab children."""
    for masked in (False, True):
        r = Bits(value, start, end)
        try:
            r.int()
            prefab = f"{r.count():016X}" if r.flag() else ""
            pos = (r.float(), r.float(), r.float()) if r.flag() else None
            if not masked and r.flag():
                r.float(), r.float(), r.float()
            if r.flag() and r.flag():
                for _ in range(r.count()):
                    r.text(), r.flag()
            if r.at == end:
                return {"prefab": prefab, "pos": pos}
        except Bad:
            pass
    return None


def exactly(read, value, start, end):
    r = Bits(value, start, end)
    try:
        out = read(r)
        return out if r.at == end else None
    except Bad:
        return None


def faction(r):
    r.int()
    side = r.text()
    if side not in ("US", "USSR", "FIA") and not (side.isalnum() and side.isupper() and len(side) <= 6):
        raise Bad()
    return {"faction": side}


def xp(r):
    r.int()
    return {"xp": r.int(), "xp_since_spawn": r.int(), "survival": r.int()}


def hit_zones(r):
    r.int()
    if not r.flag():
        raise Bad()
    zones = {}
    for _ in range(r.count()):
        start, end = r.block()
        z = Bits(r.v, start, end)
        name = z.text()
        zones[name] = z.float()
        if z.at != end:
            raise Bad()
    if not zones:
        raise Bad()
    return {"zones": zones}


def player_links(r):
    r.int()
    body = r.uuid()
    current = r.uuid() if r.at < r.end else body
    return {"body": body, "current": current}


def records(data):
    """(section name, record bytes) for every record in the blob."""
    at = 1
    sections = struct.unpack_from("<I", data, at)[0]
    at += 4
    for _ in range(sections):
        size = struct.unpack_from("<I", data, at)[0]
        name = data[at + 4:at + 4 + size].decode("ascii", "replace")
        at += 4 + size
        total = struct.unpack_from("<I", data, at)[0]
        at += 4
        for _ in range(total):
            length = struct.unpack_from("<I", data, at)[0]
            yield name, data[at + 4:at + 4 + length]
            at += 4 + length


def split(record):
    """(id, value, [blocks after the config block]) for one record."""
    value = int.from_bytes(record, "little")
    end = value & 0xFFFFFFFF
    r = Bits(value, 64, end)
    rid = r.uuid()
    r.block()
    return rid, value, blocks(value, r.at, end) or []


def components(value, part):
    return blocks(value, *part) or []


def read(path):
    """Players and the characters they play, from one save file; None if it can't be read."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    players, bodies = [], {}
    try:
        for section, record in records(data):
            if section not in ("Player", "Character"):
                continue
            rid, value, parts = split(record)
            if section == "Player":
                p = {"identity": rid}
                for part in parts:
                    if Bits(value, part[0], part[1]).take(1) == 0 and "body" not in p:
                        links = exactly(player_links, value, *part)
                        if links:
                            p.update(links)
                            continue
                    for comp in components(value, part):
                        for reader in (xp, faction):
                            got = exactly(reader, value, *comp)
                            if got:
                                p.update(got)
                                break
                players.append(p)
            else:
                body = {}
                for part in parts:
                    first = Bits(value, part[0], part[1]).take(1)
                    if first == 0 and "pos" not in body:
                        e = entity(value, *part)
                        if e:
                            body.update(e)
                            continue
                    for comp in components(value, part):
                        got = exactly(hit_zones, value, *comp) or exactly(faction, value, *comp)
                        if got:
                            body.update(got)
                if body.get("pos"):
                    bodies[rid] = body
    except (struct.error, IndexError, Bad, ValueError):
        return None
    out = []
    for p in players:
        body = bodies.get(p.get("current")) or bodies.get(p.get("body"))
        if not body:
            continue
        x, y, z = body["pos"]
        zones = body.get("zones") or {}
        out.append({"identity": p["identity"], "faction": p.get("faction") or body.get("faction") or "",
                    "xp": p.get("xp"), "x": round(x, 1), "z": round(z, 1),
                    "dead": zones.get("Health", 1) <= 0, "kind": prefab_name(body.get("prefab", "")).rsplit("/", 1)[-1][:-3]})
    return out


def players(path):
    """read(), kept until the file changes."""
    try:
        stamp = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    got = read(path)
    _cache.clear()
    _cache[str(path)] = (stamp, got)
    return got
