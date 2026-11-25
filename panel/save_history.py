"""What each autosave says about the game, kept so one save can be compared with the next.

From one save: every player (side, game XP, where they are with height, alive or a body, the seat they're in),
every base (side, and where its HQ stands), every supply storage, everything a player built or brought out
(structures and vehicles, with whoever placed it), the supplies trucks carry, and the mines.

A base record holds its side and the ID of its HQ building; the building's record has the position. Supply
storages built during the game (crates) have a position too, so their supplies go to the nearest base. The map's
own depots are placed with the world and the save never writes their position, so their supplies are counted
apart, as "not placed"."""
import struct
from pathlib import Path

from . import mobs
from .save_world import (Bad, Bits, components, entity, exactly, faction, hit_zones, joined, records, side_of,
                         split, character_record, player_record, prefab_name)

NO_ONE = "00000000-0000-0000-0000-000000000000"
# A crate further than this from every base HQ isn't counted towards one.
BASE_REACH = 300
# A base HQ this close to a named base on the map takes its name.
NAME_REACH = 150
BUILT = {"Structure": "structure", "Vehicle": "vehicle", "Turret": "emplacement"}
# Vehicle prefabs that are helicopters, so the bot can tell a heli kill by the killer's seat.
HELIS = ("UH1H", "Mi8")
WANTED = {"Player", "Character", "MilitaryBase", "Storage", "Item", *BUILT}


def supplies(r):
    r.int()
    total = 0.0
    if r.flag():
        for _ in range(r.count()):
            start, end = r.block()
            s = Bits(r.v, start, end)
            kind, value = s.int(), s.float()
            if s.at != end:
                raise Bad()
            if kind == 0:
                total += value
    if r.flag():
        for _ in range(r.count()):
            r.int()
    return {"supplies": total}


def author(r):
    r.int()
    r.uuid()
    r.flag()
    who = r.uuid()
    if r.at < r.end:
        r.int()
    return {"author": who}


def military_base(r):
    r.int()
    hq = bool(r.flag())
    building = r.uuid()
    r.int()
    r.flag()
    r.text()
    return {"building": building, "hq": hq}


def parse(value, parts, readers):
    """The entity block's prefab and position, and whatever `readers` make of the components."""
    out = {}
    for part in parts:
        if "pos" not in out and Bits(value, part[0], part[1]).take(1) == 0:
            found = entity(value, *part)
            if found:
                out.update(found)
                continue
        for comp in components(value, part):
            for reader in readers:
                got = exactly(reader, value, *comp)
                if got:
                    out.update(got)
                    break
    return out


def spot(pos):
    return {"x": round(pos[0], 1), "y": round(pos[1], 1), "z": round(pos[2], 1)} if pos else {"x": None, "y": None, "z": None}


def short(prefab):
    return prefab_name(prefab).rsplit("/", 1)[-1][:-3] if prefab else ""


def read(path):
    """{"players", "bases", "storages", "builds", "mines"} from one save file; None if it can't be read."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    players, bodies, bases, storages, builds, mines, cargo, helis, where = [], {}, [], [], [], [], [], [], {}
    try:
        for section, record in records(data):
            if section not in WANTED:
                continue
            rid, value, parts = split(record)
            if section == "Player":
                players.append(player_record(rid, value, parts))
            elif section == "Character":
                body = character_record(value, parts)
                if body.get("pos"):
                    bodies[rid] = body
            elif section == "MilitaryBase":
                got = parse(value, parts, (faction, military_base))
                if got.get("building"):
                    bases.append({"id": rid, "faction": got.get("faction") or "", "building": got["building"],
                                  "hq": got["hq"]})
            elif section == "Storage":
                got = parse(value, parts, (supplies,))
                if "supplies" in got:
                    storages.append({"id": rid, **spot(got.get("pos")), "supplies": round(got["supplies"])})
            elif section in BUILT:
                got = parse(value, parts, (author, hit_zones, supplies) if section == "Vehicle" else (author,))
                if got.get("pos"):
                    where[rid] = got["pos"]
                name = short(got.get("prefab"))
                if section == "Vehicle" and got.get("supplies"):
                    cargo.append({"id": rid, "supplies": round(got["supplies"])})
                if section == "Vehicle" and name.startswith(HELIS):
                    helis.append({"id": rid, "name": name})
                if got.get("author") and got["author"] != NO_ONE:
                    build = {"id": rid, "kind": BUILT[section], "name": name, "author": got["author"], **spot(got.get("pos"))}
                    if section == "Vehicle":
                        build.update(faction=side_of(name) if name else "",
                                     wreck=(got.get("zones") or {}).get("Hull", 1) <= 0)
                    builds.append(build)
            elif section == "Item":
                got = parse(value, parts, ())
                name = short(got.get("prefab"))
                if name.startswith("Mine_") and got.get("pos"):
                    mines.append({"id": rid, "name": name[5:], **spot(got["pos"])})
    except (struct.error, IndexError, Bad, ValueError):
        return None
    for b in bases:
        pos = where.get(b.pop("building"))
        b["x"], b["z"] = (round(pos[0], 1), round(pos[2], 1)) if pos else (None, None)
    return {"players": joined(players, bodies), "bases": bases, "storages": storages, "builds": builds, "mines": mines,
            "cargo": cargo, "helis": helis}


def base_names(bases):
    """{base id: map name} for each base whose HQ stands at a named base."""
    named = [p for p in mobs.places() if p.get("kind") == "base"]
    out = {}
    for b in bases:
        if b["x"] is None or not named:
            continue
        near = min(named, key=lambda p: (p["x"] - b["x"]) ** 2 + (p["z"] - b["z"]) ** 2)
        if (near["x"] - b["x"]) ** 2 + (near["z"] - b["z"]) ** 2 <= NAME_REACH ** 2:
            out[b["id"]] = near["name"]
    return out


def snapshot(read):
    """(supplies not at a base, {kind: {key: data}}) from read(), as the database keeps it."""
    totals, loose = base_supplies(read["bases"], read["storages"])
    names = base_names(read["bases"])
    keyed = lambda rows, key: {r[key]: {k: v for k, v in r.items() if k != key} for r in rows}
    bases = [{**b, "name": names.get(b["id"], ""), "supplies": totals[b["id"]]} for b in read["bases"]]
    return loose, {"player": keyed(read["players"], "identity"), "base": keyed(bases, "id"),
                   "storage": keyed(read["storages"], "id"), "build": keyed(read["builds"], "id"),
                   "mine": keyed(read["mines"], "id"), "cargo": keyed(read.get("cargo", []), "id"),
                   "heli": keyed(read.get("helis", []), "id")}


def base_supplies(bases, storages):
    """({base id: supplies}, supplies not placed) with each placed storage counted at its nearest base."""
    placed = [b for b in bases if b["x"] is not None]
    totals, loose = {b["id"]: 0 for b in bases}, 0
    for s in storages:
        if s["x"] is None or not placed:
            loose += s["supplies"]
            continue
        near = min(placed, key=lambda b: (b["x"] - s["x"]) ** 2 + (b["z"] - s["z"]) ** 2)
        if (near["x"] - s["x"]) ** 2 + (near["z"] - s["z"]) ** 2 <= BASE_REACH ** 2:
            totals[near["id"]] += s["supplies"]
        else:
            loose += s["supplies"]
    return totals, loose
