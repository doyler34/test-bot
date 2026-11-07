"""Who holds each Conflict base, read from the game's own autosave.

The save is the game's bit-packed format. Its last section, "MilitaryBase", has one record per base and the
owning side's key ("US", "USSR", "FIA") sits in the record as a short string. A base's name isn't in its record;
it only shows up in an open "Seize" task, next to that base's ID, so names are learned as tasks come and go."""
import json
import re
import struct
from pathlib import Path

SECTION = b"\x0c\x00\x00\x00MilitaryBase"
SECTIONS_AFTER_TASKS = b"\x06\x00\x00\x00Player"
SIDE = re.compile(rb"\x08USSR|\x04US|\x06FIA")
TASK = re.compile(rb"(US|USSR|FIA)_SeizeCampaignMilitaryBase_\d+")
PLACE = re.compile(rb"MapLocation_([A-Za-z_]+)")
_cache = {}


def newest(log_dir):
    """The newest autosave of the game, as (blob path, saved at unix time), or None."""
    root = Path(log_dir).parent / "profile" / ".save" / "game"
    best = None
    for meta in root.glob("*Campaign*/playthrough*/savepoint*/meta-info.json"):
        try:
            saved = int(json.loads(meta.read_text(encoding="utf-8"))["m_iSavedAtUnix"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        blobs = sorted((meta.parent / "WorldState").glob("*.blob"))
        if blobs and (best is None or saved > best[1]):
            best = (blobs[0], saved)
    return best


def shifts(data):
    """The bytes as they read starting at each of the 8 bit offsets."""
    value = int.from_bytes(data, "little")
    return [(value >> s).to_bytes(len(data), "little") for s in range(8)]


def bases(data):
    """[(base id, side or None)] from the MilitaryBase section, or None if the section isn't as expected."""
    start = data.rfind(SECTION)
    if start < 0:
        return None
    count = struct.unpack_from("<I", data, start + len(SECTION))[0]
    at = start + len(SECTION) + 4
    found = []
    for _ in range(count):
        size = struct.unpack_from("<I", data, at)[0]
        record = data[at + 4:at + 4 + size]
        at += 4 + size
        if len(record) != size or size < 25:
            return None
        side = None
        for text in shifts(record):
            hit = SIDE.search(text)
            if hit:
                side = hit.group()[1:].decode()
                break
        found.append((record[17:25].hex(), side))
    return found if at == len(data) else None


def names(data, ids):
    """{base id: name} for the bases that have a Seize task open: the task, then its place, then the base ID."""
    end = data.find(SECTIONS_AFTER_TASKS)
    head = data[:end if end > 0 else 300_000]
    keys = {bytes.fromhex(i)[1:7]: i for i in ids}
    events = []
    for offset, text in enumerate(shifts(head)):
        for key, base in keys.items():
            events += [(m.start() * 8 + offset, "id", base) for m in re.finditer(re.escape(key), text)]
        events += [(m.start() * 8 + offset, "task", "") for m in TASK.finditer(text)]
        events += [(m.start() * 8 + offset, "place", m.group(1).decode()) for m in PLACE.finditer(text)]
    learned, task, place = {}, False, None
    for _, kind, value in sorted(events):
        if kind == "task":
            task, place = True, None
        elif kind == "place" and task:
            place = value
        elif kind == "id" and task and place:
            learned[value] = place
            task, place = False, None
    return learned


def label(place):
    """"TillersFind" -> "Tillers Find", "Coastal_Chotain" -> "Coastal Chotain"."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", place).replace("_", " ").strip()


def read(path):
    """{"bases": [(id, side)], "names": {id: name}} for a save file, cached until the file changes; None if it
    can't be read."""
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    key = str(path)
    if key in _cache and _cache[key][0] == stamp:
        return _cache[key][1]
    try:
        data = path.read_bytes()
        found = bases(data)
        result = {"bases": found, "names": names(data, [b for b, _ in found])} if found else None
    except (OSError, struct.error, ValueError):
        result = None
    if len(_cache) > 16:
        _cache.clear()
    _cache[key] = (stamp, result)
    return result
