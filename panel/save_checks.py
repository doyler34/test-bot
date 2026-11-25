"""Exploit checks that compare one autosave with the one before it.

Each check gives staff alerts as (title, once, text): `once` is what it's about (a player, a storage, a base),
so the same thing is reported once a game. They only alert; nothing here kicks or bans."""
from math import hypot

# A friendly player this close to a structure when it went is "near" it.
GRIEF_REACH = 75
# A wreck this close to its own side's main base counts as wrecked at home.
HOME_REACH = 300
# A mine this close to a base HQ is inside the base, where its own side walks; further out it may be defending it.
MINE_REACH = 75
# Two supply changes this close in size look like the same supplies moving.
SAME_AMOUNT = 0.15
# Below this height a player on foot is under the map (the sea is at 0, and there's no diving).
UNDER_MAP = -2
MINE_SIDES = (("PMN", "USSR"), ("TM62", "USSR"), ("M14", "US"), ("M15", "US"))


def mine_side(name):
    return next((side for start, side in MINE_SIDES if name.startswith(start)), "")


def far(a, b):
    return hypot(a["x"] - b["x"], a["z"] - b["z"])


def placed(thing):
    return thing.get("x") is not None


def nearest_base(bases, thing, reach):
    near = [b for b in bases if placed(b) and far(b, thing) <= reach]
    return min(near, key=lambda b: far(b, thing)) if near else None


def where(storage, bases):
    if not placed(storage):
        return "a map depot (the save doesn't say where)"
    base = nearest_base(bases, storage, HOME_REACH)
    if base and base.get("name"):
        return f"a storage at {base['name']}"
    return f"a storage at {storage['x']:.0f}, {storage['z']:.0f}"


def minutes(seconds):
    return f"{max(round(seconds / 60), 1)} minute{'' if round(seconds / 60) == 1 else 's'}"


def xp_farming(before, after, gap, kills, s, name):
    for identity, p in after.get("player", {}).items():
        old = before.get("player", {}).get(identity)
        if not old or p.get("xp") is None or old.get("xp") is None:
            continue
        gain = p["xp"] - old["xp"]
        if gain >= s["xp_gain"] and not kills.get(identity):
            yield ("XP farming", ("xp", identity),
                   f"**{name(identity)}** gained {gain} game XP in {minutes(gap)} between two autosaves without a "
                   f"kill (from {old['xp']} to {p['xp']}).")


def supplies(before, after, gap, s):
    old, new = before.get("storage", {}), after.get("storage", {})
    both = old.keys() & new.keys()
    changes = {k: new[k]["supplies"] - old[k]["supplies"] for k in both}
    # Only storages in both saves count: one at its starting amount is left out of the save, so one that
    # reappears hasn't jumped from nothing.
    rise = sum(changes.values())
    cargo_same = before.get("cargo", {}) == after.get("cargo", {})
    bases = list(after.get("base", {}).values())
    for key, gain in sorted(changes.items(), key=lambda kv: -kv[1]):
        if gain < s["supply_gain"]:
            break
        here = where({**new[key], "id": key}, bases)
        if rise >= s["supply_gain"]:
            yield ("Supply jump", ("supply", key),
                   f"{here.capitalize()} gained {gain} supplies in {minutes(gap)} between two autosaves, and the "
                   f"supplies on the map went up by {rise} overall, so they didn't come from anywhere else. "
                   "That's more than a base makes on its own.")
            continue
        source = next((k for k, change in changes.items() if k != key and change < 0
                       and abs(-change - gain) <= gain * SAME_AMOUNT), None)
        if source and cargo_same:
            yield ("Supply transfer", ("supply", key),
                   f"{here.capitalize()} gained {gain} supplies while {where({**new[source], 'id': source}, bases)} "
                   f"lost {-changes[source]} in {minutes(gap)} between two autosaves, and no truck's load changed.")


def griefing(before, after, s, name):
    gone = [(k, b) for k, b in before.get("build", {}).items()
            if b.get("kind") == "structure" and k not in after.get("build", {}) and placed(b)]
    if not gone:
        return
    people = {}
    for snap in (before, after):
        for identity, p in snap.get("player", {}).items():
            if placed(p) and not p.get("dead"):
                people.setdefault(identity, []).append(p)
    sides = {i: p.get("faction") for i, p in before.get("player", {}).items()}
    near = {}
    for key, b in gone:
        side = sides.get(b["author"])
        if not side:
            continue
        for identity, spots in people.items():
            if identity != b["author"] and sides.get(identity) == side and any(far(p, b) <= GRIEF_REACH for p in spots):
                near.setdefault(identity, []).append(b)
    for identity, builds in near.items():
        if len(builds) >= s["grief_count"]:
            listed = ", ".join(f"{b['name'] or 'a structure'} by {name(b['author'])}" for b in builds[:8])
            more = f" and {len(builds) - 8} more" if len(builds) > 8 else ""
            yield ("Base griefing", ("grief", identity),
                   f"{len(builds)} structures built by their own side went between two autosaves with "
                   f"**{name(identity)}** nearby: {listed}{more}. Check the GM log for who deleted them.")


def vehicles(before, after, s, name, game_vehicles, roadkills, home_wrecks):
    for identity, count in roadkills.items():
        if count >= s["vehicle_roadkills"]:
            yield ("Vehicle griefing", ("roadkill", identity),
                   f"**{name(identity)}** has run over {count} teammates with a vehicle this game.")
    for identity, ids in game_vehicles.items():
        if len(ids) >= s["vehicle_spawns"]:
            yield ("Vehicle griefing", ("spawns", identity),
                   f"**{name(identity)}** has taken {len(ids)} vehicles out this game.")
    homes = [b for b in after.get("base", {}).values() if b.get("hq") and placed(b)]
    for key, v in after.get("build", {}).items():
        old = before.get("build", {}).get(key)
        if v.get("kind") != "vehicle" or not v.get("wreck") or (old and old.get("wreck")) or not placed(v):
            continue
        home = nearest_base(homes, v, HOME_REACH)
        if home and home.get("faction") and home["faction"] == v.get("faction"):
            home_wrecks.append((v["name"], v["author"]))
    if len(home_wrecks) >= s["vehicle_wrecks"]:
        listed = ", ".join(f"{n or 'a vehicle'} (taken out by {name(a)})" for n, a in home_wrecks[:8])
        yield ("Vehicle griefing", ("wrecks", ""),
               f"{len(home_wrecks)} vehicles have been wrecked at their own side's main base this game: {listed}.")


def own_mines(after, s):
    mines = list(after.get("mine", {}).values())
    for key, base in after.get("base", {}).items():
        if not placed(base) or not base.get("faction"):
            continue
        inside = [m for m in mines if placed(m) and mine_side(m["name"]) == base["faction"] and far(m, base) <= MINE_REACH]
        if len(inside) >= s["ownmines"]:
            yield ("Mines in own base", ("mines", key),
                   f"{len(inside)} {base['faction']} mines are inside {base.get('name') or 'a base'}, which "
                   f"{base['faction']} holds. Their own side walks there.")


def movement(before, after, gap, s, name):
    for identity, p in after.get("player", {}).items():
        if not placed(p) or p.get("dead"):
            continue
        if p.get("y") is not None and p["y"] < UNDER_MAP and not p.get("seat"):
            yield ("Under the map", ("under", identity),
                   f"**{name(identity)}** is under the map, {-p['y']:.0f} m below sea level and not in a vehicle.")
        old = before.get("player", {}).get(identity)
        if (not old or not placed(old) or old.get("dead") or old.get("body") != p.get("body")
                or old.get("seat") or p.get("seat")):
            continue
        moved = far(old, p)
        if moved >= s["teleport_km"] * 1000:
            yield ("Teleport", ("teleport", identity),
                   f"**{name(identity)}** moved {moved / 1000:.1f} km in {minutes(gap)} between two autosaves, on foot "
                   "in both, without dying. A vehicle ride in between can do this, so check before acting.")


def run(before, after, gap, s, name, kills, game_vehicles, roadkills, home_wrecks):
    """Every alert this pair of autosaves gives, for the checks switched on."""
    found = []
    if s["xp_on"]:
        found += xp_farming(before, after, gap, kills, s, name)
    if s["supply_on"]:
        found += supplies(before, after, gap, s)
    if s["grief_on"]:
        found += griefing(before, after, s, name)
    if s["vehicle_on"]:
        found += vehicles(before, after, s, name, game_vehicles, roadkills, home_wrecks)
    if s["ownmines_on"]:
        found += own_mines(after, s)
    if s["teleport_on"]:
        found += movement(before, after, gap, s, name)
    return found
