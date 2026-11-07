"""Each player's saved arsenal loadout, read from the game's autosave, and the class it makes them.

The save's "Player" section has one record per player: their identity (16 bytes), the side they're on, and,
when they've saved one at an arsenal, their loadout as JSON. Items in it are prefab IDs; the names come from
the "{ID}Prefabs/....et" references written all through the game's own configs and prefabs."""
import json
import mmap
import re
import struct
import uuid
import zlib
from pathlib import Path

from .campaign_save import SIDE, shifts

SECTION = b"\x06\x00\x00\x00Player"
LOADOUT = b'{"faction"'
REF = re.compile(rb"\{([0-9A-F]{16})\}([A-Za-z0-9_/ .\-]+?\.et)\b")
WANTED = (b".conf", b".et", b".layer", b".ent")


def players(data):
    """{identity: {"side": "US"/"USSR"/"FIA"/None, "loadout": dict or None}} from a save, or {}."""
    start = data.rfind(SECTION)
    if start < 0:
        return {}
    count, at, found = struct.unpack_from("<I", data, start + len(SECTION))[0], start + len(SECTION) + 4, {}
    decoder = json.JSONDecoder()
    for _ in range(count):
        size = struct.unpack_from("<I", data, at)[0]
        record = data[at + 4:at + 4 + size]
        at += 4 + size
        if len(record) != size or size < 24:
            return {}
        side, loadout = None, None
        for text in shifts(record):
            if side is None:
                hit = SIDE.search(text)
                side = hit.group()[1:].decode() if hit else None
            spot = text.find(LOADOUT)
            if spot >= 0 and loadout is None:
                try:
                    loadout = decoder.raw_decode(text[spot:].decode("utf-8", "replace"))[0]
                except ValueError:
                    loadout = None
        if loadout and loadout.get("faction"):
            side = loadout["faction"]
        found[str(uuid.UUID(bytes=record[8:24]))] = {"side": side, "loadout": loadout}
    return found


# ---- names for prefab IDs, from the game's paks ----

def _chunks(data):
    at = 12
    while at + 8 <= len(data):
        tag, size = data[at:at + 4], struct.unpack(">I", data[at + 4:at + 8])[0]
        yield tag, at + 8, size
        at += 8 + size


def _unpack(raw, size):
    if len(raw) == size:
        return raw
    for wbits in (15, -15):
        try:
            out = zlib.decompress(raw, wbits)
            if len(out) == size:
                return out
        except zlib.error:
            pass
    return None


def read_names(pak_dir):
    """{prefab ID: prefab path} from every config and prefab in the game's paks."""
    found = {}
    for pak in sorted(Path(pak_dir).glob("*.pak")):
        with open(pak, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            _read_pak(data, found)
    return found


def _read_pak(data, found):
    if data[:4] != b"FORM" or data[8:12] != b"PAC1":
        return
    table = next(((start, size) for tag, start, size in _chunks(data) if tag == b"FILE"), None)
    if not table:
        return
    files = data[table[0]:table[0] + table[1]]
    for hit in re.finditer(rb"\x01([\x03-\x7f])", files):
        length = hit.group(1)[0]
        name = files[hit.end():hit.end() + length]
        if not name.endswith(WANTED) or hit.end() + length + 12 > len(files):
            continue
        offset, packed, real = struct.unpack_from("<III", files, hit.end() + length)
        if not (0 < packed and offset + packed <= len(data) and real < 50_000_000):
            continue
        body = _unpack(data[offset:offset + packed], real)
        if body:
            for ref in REF.finditer(body):
                found.setdefault(ref.group(1).decode(), ref.group(2).decode("utf-8", "replace"))


def pak_dir(log_dir):
    """The game's addons/data folder for a server whose logs are in log_dir."""
    return Path(log_dir).parent.parent / "addons" / "data"


def stamp(folder):
    """What the paks are now, so names are read again after a game update."""
    return [[p.name, p.stat().st_size, int(p.stat().st_mtime)] for p in sorted(Path(folder).glob("*.pak"))]


def names(folder, cache):
    """The prefab names, from the cache file while the paks haven't changed; read afresh (slow) when they have."""
    now = stamp(folder)
    if not now:
        return {}
    try:
        kept = json.loads(Path(cache).read_text(encoding="utf-8"))
        if kept.get("paks") == now:
            return kept["names"]
    except (OSError, ValueError, KeyError):
        pass
    found = read_names(folder)
    if found:
        Path(cache).write_text(json.dumps({"paks": now, "names": found}), encoding="utf-8")
    return found


# ---- reading a loadout ----

def label(path):
    """"Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et" -> "Rifle M16A2"."""
    stem = Path(path).stem
    stem = re.sub(r"_0\d$", "", stem)
    return stem.replace("_", " ")


def tree(loadout, prefab_names):
    """The loadout as nested [{"name", "path", "items": [...]}], what's inside each thing under it."""
    def walk(node):
        out = []
        for storage in node.get("storages") or []:
            for _, slot in sorted((storage.get("slots") or {}).items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
                if not isinstance(slot, dict) or not slot.get("prefab"):
                    continue
                path = prefab_names.get(slot["prefab"], "")
                out.append({"name": label(path) if path else slot["prefab"], "path": path, "items": walk(slot)})
        return out
    return walk(loadout.get("arsenalLoadout") or {})


def flat(items):
    for item in items:
        yield item
        yield from flat(item["items"])


# The first rule that matches a loadout names its class. Each is (class, path pattern, how many it takes).
RULES = (
    ("Anti-tank", r"/Weapons/Launchers/", 1),
    ("Machine gunner", r"/Weapons/MachineGuns/", 1),
    ("Marksman", r"/Weapons/(Rifles|Snipers?)/[^/]*/[^/]*(SVD|M21|M24|Sniper|DMR)", 1),
    ("Medic", r"/Items/Medicine/(Saline|MedicalKit|MedicBag|Medkit)", 2),
    ("Grenadier", r"/Weapons/(Rifles|Grenades|Ammo)/.*(M203|GP25|GP-25|M79|M433|M406|VOG|UGL)", 1),
    ("Engineer", r"/Weapons/(Mines|Explosives)/|/Items/Equipment/(Kits|Tools)/.*(Repair|Engineer|Shovel|Entrench)", 1),
    ("Radio operator", r"/Items/Equipment/Radios/.*(PRC77|R148|Backpack|Long)", 1),
    ("Rifleman", r"/Weapons/Rifles/", 1),
)


def classify(items):
    """(class, the item that decided it) for a loadout tree; ("Unarmed", "") when nothing matches."""
    paths = [i["path"] for i in flat(items) if i["path"]]
    for name, pattern, need in RULES:
        hits = [p for p in paths if re.search(pattern, p, re.I)]
        if len(hits) >= need:
            return name, label(hits[0])
    return "Unarmed", ""


def weapons(items):
    """The weapons carried, by name."""
    return [i["name"] for i in flat(items) if re.search(r"/Weapons/(Rifles|MachineGuns|Launchers|Handguns|Snipers?)/", i["path"])]
