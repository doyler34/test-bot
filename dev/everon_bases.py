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
    if "--copy" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--copy"]
        copy(args[0], args[1])
    elif "--show" in sys.argv:
        show(next(a for a in sys.argv[1:] if a != "--show"))
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else ".")
