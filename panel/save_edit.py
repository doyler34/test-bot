"""Reading a game autosave into its parts and writing it back, and swapping one player's saved loadout.

Layout, as far as it's known: one version byte, the number of sections (u32), then each section as its name
(u32 length + text), its record count (u32) and its records, each a u32 byte length and then the record. A
record starts with a u32 that grows by 8 for every byte added to it. In a Player record the loadout is JSON,
written at whatever bit it falls on, right after its own length as 16 bits."""
import json
import struct
import uuid

LOADOUT = b'{"faction"'


def parse(data):
    """(version byte, [(section name, [record bytes])]) for a save; raises ValueError if it doesn't add up."""
    version, count = data[0], struct.unpack_from("<I", data, 1)[0]
    at, sections = 5, []
    for _ in range(count):
        size = struct.unpack_from("<I", data, at)[0]
        name = data[at + 4:at + 4 + size].decode("ascii")
        at += 4 + size
        records = []
        total = struct.unpack_from("<I", data, at)[0]
        at += 4
        for _ in range(total):
            length = struct.unpack_from("<I", data, at)[0]
            records.append(data[at + 4:at + 4 + length])
            at += 4 + length
        sections.append((name, records))
    if at != len(data):
        raise ValueError(f"save has {len(data) - at} bytes left over")
    return version, sections


def build(version, sections):
    """The save bytes for its parts: parse() backwards."""
    out = [bytes([version]), struct.pack("<I", len(sections))]
    for name, records in sections:
        out += [struct.pack("<I", len(name)), name.encode("ascii"), struct.pack("<I", len(records))]
        for record in records:
            out += [struct.pack("<I", len(record)), record]
    return b"".join(out)


def find_loadout(record):
    """(bit where the loadout JSON starts, its length in bytes), or None if the record has no loadout."""
    value = int.from_bytes(record, "little")
    for shift in range(8):
        text = (value >> shift).to_bytes(len(record), "little")
        at = text.find(LOADOUT)
        if at >= 2:
            length = struct.unpack_from("<H", text, at - 2)[0]
            try:
                json.loads(text[at:at + length].decode("utf-8"))
            except ValueError:
                continue
            return at * 8 + shift, length
    return None


def identity_of(record):
    return str(uuid.UUID(bytes=record[8:24]))


def with_loadout(record, loadout_json):
    """The record with its loadout JSON swapped for loadout_json (bytes), lengths fixed to match."""
    found = find_loadout(record)
    if not found:
        raise ValueError("record has no loadout to swap")
    start, length = found
    if len(loadout_json) > 0xFFFF:
        raise ValueError("loadout too long")
    value = int.from_bytes(record, "little")
    head = value & ((1 << (start - 16)) - 1)
    tail = value >> (start + 8 * length)
    body = int.from_bytes(struct.pack("<H", len(loadout_json)) + loadout_json, "little")
    grown = len(loadout_json) - length
    new = head | (body << (start - 16)) | (tail << (start + 8 * len(loadout_json)))
    out = bytearray(new.to_bytes(len(record) + grown, "little"))
    struct.pack_into("<I", out, 0, struct.unpack_from("<I", record, 0)[0] + 8 * grown)
    return bytes(out)


def set_loadout(data, identity, loadout_json):
    """The save with one player's saved loadout replaced; ValueError if they aren't in it or have none."""
    version, sections = parse(data)
    for name, records in sections:
        if name != "Player":
            continue
        for i, record in enumerate(records):
            if identity_of(record) == identity:
                records[i] = with_loadout(record, loadout_json)
                return build(version, sections)
    raise ValueError(f"{identity} isn't in this save")
