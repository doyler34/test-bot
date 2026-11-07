"""Read-only: list the Conflict bases on Everon with where each one is, from the game's own .pak files.

    python3 dev/everon_bases.py /home/amp/.ampdata/instances/OGEveron01/arma-reforger/1874900/addons/data

It finds the .layer and .ent files of the Everon Conflict world inside the paks, unpacks them in memory and
prints one line per base: name, east, north, and the entity ID the save uses. Nothing is written."""
import re
import struct
import sys
import zlib
from pathlib import Path

WANT = re.compile(rb"[\x01]([\x05-\x7f])")
NAME = re.compile(rb"[A-Za-z0-9_.\-]+\.(?:layer|ent)")


def chunks(data):
    at = 12
    while at + 8 <= len(data):
        tag, size = data[at:at + 4], struct.unpack(">I", data[at + 4:at + 8])[0]
        yield tag, at + 8, size
        at += 8 + size


def unpack(raw, size):
    for wbits in (15, -15):
        try:
            out = zlib.decompress(raw, wbits)
            if len(out) == size:
                return out
        except zlib.error:
            pass
    return raw if len(raw) == size else None


def world_files(pak, raw=False):
    data = pak.read_bytes()
    if data[:4] != b"FORM" or data[8:12] != b"PAC1":
        return
    table = next(((start, size) for tag, start, size in chunks(data) if tag == b"FILE"), None)
    if not table:
        return
    start, size = table
    files = data[start:start + size]
    for hit in WANT.finditer(files):
        length = hit.group(1)[0]
        name = files[hit.end():hit.end() + length]
        if not NAME.fullmatch(name):
            continue
        after = hit.end() + length
        if after + 12 > len(files):
            continue
        offset, packed, real = struct.unpack_from("<III", files, after)
        if not (0 < packed <= len(data) and 0 < real < 200_000_000 and offset + packed <= len(data)):
            continue
        body = unpack(data[offset:offset + packed], real)
        if body and b"CampaignMilitaryBase" in body:
            yield name.decode(), body if raw else body.decode("utf-8", "replace")


ENTITY = re.compile(r'^\s*(?:\$grp\s+)?\w+\s*:\s*"[^"]*"\s*\{', re.M)


def bases(text):
    """(name, east, north, id) for each entity block that names a Conflict map location."""
    starts = [m.start() for m in ENTITY.finditer(text)] + [len(text)]
    for begin, end in zip(starts, starts[1:]):
        block = text[begin:end]
        place = re.search(r"MapLocation_([A-Za-z0-9_]+)", block)
        coords = re.search(r"coords\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)", block)
        if not place or not coords:
            continue
        ident = re.search(r'\bID\s+"([0-9A-Fa-f]{16})"', block)
        yield place.group(1), float(coords.group(1)), float(coords.group(3)), ident.group(1) if ident else ""


def names_in(pak):
    """Every file name in a pak's table, as (name, offset, packed size, real size)."""
    data = pak.read_bytes()
    if data[:4] != b"FORM" or data[8:12] != b"PAC1":
        return
    table = next(((start, size) for tag, start, size in chunks(data) if tag == b"FILE"), None)
    if not table:
        return
    files = data[table[0]:table[0] + table[1]]
    for hit in re.finditer(rb"[\x01]([\x03-\x7f])", files):
        length = hit.group(1)[0]
        name = files[hit.end():hit.end() + length]
        if re.fullmatch(rb"[A-Za-z0-9_.\- ]+\.[A-Za-z0-9]{1,8}", name) and hit.end() + length + 12 <= len(files):
            yield (name.decode(), *struct.unpack_from("<III", files, hit.end() + length))


def survey(folder):
    """What kinds of files the paks hold, and any that could map prefab IDs to names."""
    import collections
    kinds = collections.Counter()
    for pak in sorted(Path(folder).glob("*.pak")):
        for name, offset, packed, real in names_in(pak):
            kind = name.rsplit(".", 1)[-1].lower()
            kinds[kind] += 1
            if kind in ("rdb", "meta", "conf") and ("resource" in name.lower() or kind == "rdb"):
                print(f"{pak.name}: {name} ({real} bytes)")
    print(dict(kinds.most_common(25)))


def entry(folder, wanted="DefaultPlayerControllerMP_Campaign.et", guid="D39FCAB93FC4FB02"):
    """Print the table bytes around one file's name, and where a known prefab ID shows up in the tables."""
    raw = bytes.fromhex(guid)
    for pak in sorted(Path(folder).glob("*.pak")):
        data = pak.read_bytes()
        table = next(((start, size) for tag, start, size in chunks(data) if tag == b"FILE"), None)
        if not table:
            continue
        files = data[table[0]:table[0] + table[1]]
        at = files.find(wanted.encode())
        if at >= 0:
            print(f"{pak.name}: name at {at}: {files[at - 4:at + len(wanted) + 48].hex(' ')}")
        for label, key in (("as written", raw), ("reversed", raw[::-1])):
            spot = files.find(key)
            if spot >= 0:
                print(f"{pak.name}: ID {label} in the table at {spot}, name {at}")
            spot = data.find(key)
            if spot >= 0:
                print(f"{pak.name}: ID {label} in the pak at {spot}")


REF = re.compile(rb"\{([0-9A-F]{16})\}([A-Za-z0-9_/ .\-]+?\.et)\b")


def prefab_names(folder):
    """{ID: prefab path} from every "{ID}Prefabs/....et" written in the game's configs and prefabs."""
    found = {}
    for pak in sorted(Path(folder).glob("*.pak")):
        data = pak.read_bytes()
        table = next(((start, size) for tag, start, size in chunks(data) if tag == b"FILE"), None)
        if data[:4] != b"FORM" or not table:
            continue
        files = data[table[0]:table[0] + table[1]]
        for hit in re.finditer(rb"[\x01]([\x03-\x7f])", files):
            length = hit.group(1)[0]
            name = files[hit.end():hit.end() + length]
            if not (name.endswith(b".conf") or name.endswith(b".et") or name.endswith(b".layer") or name.endswith(b".ent")):
                continue
            if hit.end() + length + 12 > len(files):
                continue
            offset, packed, real = struct.unpack_from("<III", files, hit.end() + length)
            if not (0 < packed and offset + packed <= len(data) and real < 50_000_000):
                continue
            body = unpack(data[offset:offset + packed], real)
            if body:
                for ref in REF.finditer(body):
                    found.setdefault(ref.group(1).decode(), ref.group(2).decode("utf-8", "replace"))
    return found


def items(folder, save):
    """How many of the items in the players' loadouts in `save` the game's files can name."""
    import collections
    import json
    names = prefab_names(folder)
    print(f"{len(names)} prefab IDs named in the game files")
    used = collections.Counter(re.findall(r'"prefab":"([0-9A-F]{16})"', loadouts(Path(save).read_bytes())))
    named = [g for g in used if g in names]
    print(f"loadout items: {len(named)} of {len(used)} kinds named, {sum(used[g] for g in named)} of {sum(used.values())} uses")
    for guid, count in used.most_common(20):
        print(count, guid, names.get(guid, "?"))


def loadouts(blob):
    """Every player's loadout text in a save, joined."""
    start = blob.find(b"\x06\x00\x00\x00Player")
    count, at, out = struct.unpack_from("<I", blob, start + 10)[0], start + 14, []
    for _ in range(count):
        size = struct.unpack_from("<I", blob, at)[0]
        record = blob[at + 4:at + 4 + size]
        at += 4 + size
        value = int.from_bytes(record, "little")
        for s in range(8):
            text = (value >> s).to_bytes(size, "little")
            j = text.find(b'{"faction"')
            if j >= 0:
                out.append(text[j:].decode("utf-8", "replace"))
                break
    return "\n".join(out)


def grep(folder, text, kinds=(b".c", b".conf")):
    """Every line in the game's scripts and configs that mentions `text`, with the file it's in."""
    needle = text.encode()
    for pak in sorted(Path(folder).glob("*.pak")):
        data = pak.read_bytes()
        table = next(((start, size) for tag, start, size in chunks(data) if tag == b"FILE"), None)
        if data[:4] != b"FORM" or not table:
            continue
        files = data[table[0]:table[0] + table[1]]
        for hit in re.finditer(rb"[\x01]([\x03-\x7f])", files):
            length = hit.group(1)[0]
            name = files[hit.end():hit.end() + length]
            if not name.endswith(kinds) or hit.end() + length + 12 > len(files):
                continue
            offset, packed, real = struct.unpack_from("<III", files, hit.end() + length)
            if not (0 < packed and offset + packed <= len(data) and real < 50_000_000):
                continue
            body = unpack(data[offset:offset + packed], real)
            if body and needle.lower() in body.lower():
                for line in body.decode("utf-8", "replace").splitlines():
                    if text.lower() in line.lower():
                        print(f"{name.decode()}: {line.strip()[:200]}")


def cat(folder, wanted):
    """Print one file from the paks, by its name."""
    for pak in sorted(Path(folder).glob("*.pak")):
        for name, offset, packed, real in names_in(pak):
            if name == wanted:
                with open(pak, "rb") as handle:
                    handle.seek(offset)
                    body = unpack(handle.read(packed), real)
                print(body.decode("utf-8", "replace") if body else "couldn't unpack it")
                return
    print("not found")


def copy(folder, out, wanted="CTI_Campaign_Eden.ent"):
    """Write the Everon world file, unpacked, to `out`."""
    for pak in sorted(Path(folder).glob("*.pak")):
        for name, text in world_files(pak, raw=True):
            if name == wanted:
                Path(out).write_bytes(text)
                print(f"wrote {out} ({len(text)} bytes)")
                return
    print("not found")


def show(folder, wanted="CTI_Campaign_Eden.ent"):
    """Print the lines around the first few bases in the Everon file, to see how they're written."""
    for pak in sorted(Path(folder).glob("*.pak")):
        for name, text in world_files(pak):
            if name != wanted:
                continue
            lines = text.splitlines()
            print(f"# {pak.name}: {name}, {len(lines)} lines")
            hits = [i for i, line in enumerate(lines) if "MapLocation_" in line or "CampaignMilitaryBase" in line][:4]
            for i in hits:
                print(f"--- line {i}")
                print("\n".join(lines[max(0, i - 25):i + 8]))
            return


def main(folder):
    paks = sorted(Path(folder).glob("*.pak"))
    print(f"{len(paks)} pak files")
    seen = set()
    for pak in paks:
        for name, text in world_files(pak):
            print(f"# {pak.name}: {name}")
            for row in bases(text):
                if row not in seen:
                    seen.add(row)
                    print(f"{row[0]}\t{row[1]:.0f}\t{row[2]:.0f}\t{row[3]}")
    print(f"{len(seen)} bases")


if __name__ == "__main__":
    if "--cat" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--cat"]
        cat(args[0], args[1])
    elif "--grep" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--grep"]
        grep(args[0], args[1])
    elif "--items" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--items"]
        items(args[0], args[1])
    elif "--entry" in sys.argv:
        entry(next(a for a in sys.argv[1:] if a != "--entry"))
    elif "--survey" in sys.argv:
        survey(next(a for a in sys.argv[1:] if a != "--survey"))
    elif "--copy" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--copy"]
        copy(args[0], args[1])
    elif "--show" in sys.argv:
        show(next(a for a in sys.argv[1:] if a != "--show"))
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else ".")
