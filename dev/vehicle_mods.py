"""What every mod on a server sets for its vehicles, and every supply cost it sets,
read straight out of the mods' .pak files.

    python3 dev/vehicle_mods.py [addons folder] [filter]            vehicles' budgets
    python3 dev/vehicle_mods.py --prices [addons folder] [filter]   every supply cost

The addons folder defaults to the first server under /home/amp/.ampdata that has
one; the game's own files are found next to it, to work out what a prefab
takes from the one it's based on. A budget entry with no type of its own is
looked up there; one with no type anywhere is the game's default, building
space (PROPS), and is marked "no type set". The filter keeps files whose path
has that word in it.
"""
import re
import struct
import sys
import zlib
from pathlib import Path

LISTS = ("m_EntityBudgetCost", "m_EntityChildrenBudgetCost", "m_aCrewEntityBudgetCost", "m_aPassengerEntityBudgetCost")
LIST_NAMES = {"m_EntityBudgetCost": "own", "m_EntityChildrenBudgetCost": "children",
              "m_aCrewEntityBudgetCost": "crew", "m_aPassengerEntityBudgetCost": "passengers"}
ENTRY = re.compile(r'SCR_EntityBudgetValue\s*(?:"\{([0-9A-F]+)\}")?\s*\{([^{}]*)\}')
PARENT = re.compile(r'^\w+\s*:\s*"\{[0-9A-F]+\}([^"]+)"')
PREFAB = re.compile(r'm_sEntityPrefab\s+"\{[0-9A-F]+\}([^"]+)"')
COST = re.compile(r"\b(m_iSupplyCost|m_fSpawnSupplyCost|m_iSupplyRefundAmount)\s+(-?[\d.]+)")


class Pak:
    def __init__(self, path):
        self.path, self.files = path, {}
        with open(path, "rb") as f:
            if f.read(12)[8:12] != b"PAC1":
                return
            while len(head := f.read(8)) == 8:
                tag, length = head[:4], struct.unpack(">I", head[4:])[0]
                if tag == b"FILE":
                    self.walk(f.read(length), 0, "")
                else:
                    f.seek(length, 1)

    def walk(self, buf, pos, prefix):
        kind, size = buf[pos], buf[pos + 1]
        name = buf[pos + 2:pos + 2 + size].decode("utf-8", "replace")
        pos += 2 + size
        path = f"{prefix}/{name}" if prefix and name else (prefix or name)
        if kind == 0:
            count = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
            for _ in range(count):
                pos = self.walk(buf, pos, path)
            return pos
        offset, packed, _ = struct.unpack_from("<III", buf, pos)
        self.files[path] = (offset, packed, struct.unpack_from(">I", buf, pos + 16)[0] != 0)
        return pos + 24

    def text(self, path):
        offset, packed, compressed = self.files[path]
        with open(self.path, "rb") as f:
            f.seek(offset)
            raw = f.read(packed)
        raw = zlib.decompress(raw) if compressed else raw
        return None if raw[:4] in (b"FORM", b"EBIN") else raw.decode("utf-8", "replace")


class Library:
    """Every prefab by path, the mods' over the game's, read when first asked for."""

    def __init__(self, paks):
        self.where, self.cache = {}, {}
        for pak in paks:
            for path in pak.files:
                if path.endswith(".et"):
                    self.where[path.lower()] = (pak, path)

    def text(self, path):
        key = path.lower()
        if key not in self.cache:
            found = self.where.get(key)
            self.cache[key] = found[0].text(found[1]) if found else None
        return self.cache[key]


def block(text, key):
    """The text inside 'key { ... }', braces matched."""
    at = re.search(r"\b" + key + r"\s*\{", text)
    if not at:
        return None
    depth, i = 1, at.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[at.end():i - 1]


def entries(text, key):
    """[(id, type or None, value or None)] in one budget list, or None without the list."""
    inside = block(text, key)
    if inside is None:
        return None
    out = []
    for ident, body in ENTRY.findall(inside):
        kind = re.search(r"m_BudgetType\s+(\w+)", body)
        value = re.search(r"m_Value\s+(-?\d+)", body)
        out.append((ident, kind[1] if kind else None, int(value[1]) if value else None))
    return out


def inherited(library, text, key, ident, want):
    """What the prefab's ancestors give an entry: want 1 for its type, 2 for its value."""
    for _ in range(30):
        parent = PARENT.match(text)
        text = library.text(parent[1]) if parent else None
        if not text:
            return None
        for entry in entries(text, key) or []:
            if entry[0] == ident and entry[want] is not None:
                return entry[want]
    return None


def describe(library, text, unknown):
    parts = []
    for key in LISTS:
        found = entries(text, key)
        if found is None:
            continue
        shown = []
        for ident, kind, value in found:
            kind = kind or (inherited(library, text, key, ident, 1) if ident else None)
            if value is None and ident:
                value = inherited(library, text, key, ident, 2)
            shown.append(f"{kind or unknown} {value if value is not None else '?'}")
        parts.append(f"{LIST_NAMES[key]}: {', '.join(shown)}")
    return parts


def default_addons():
    for folder in sorted(Path("/home/amp/.ampdata/instances").glob("*/arma-reforger/*/AReforgerMaster/addons")):
        if any(folder.glob("*/*.pak")):
            return folder
    return None


def game_paks(addons):
    """The game's own paks, found near the mods (a server keeps them in addons/data)."""
    for folder in list(addons.parents)[:4]:
        for data in [folder / "addons" / "data", *folder.glob("*/addons/data"), *folder.glob("*/*/addons/data")]:
            paks = sorted(data.glob("data*.pak"))
            if paks:
                return data, [Pak(p) for p in paks]
    return None, []


def mods(addons):
    for mod in sorted(p for p in addons.iterdir() if p.is_dir()):
        paks = [Pak(p) for p in sorted(mod.glob("*.pak"))]
        if paks:
            yield mod.name.rsplit("_", 1)[0], paks


def vehicles(addons, want):
    every = list(mods(addons))
    data, game = game_paks(addons)
    library = Library(game + [pak for _, paks in every for pak in paks])
    print(f"Mods in {addons}")
    if game:
        print(f"The game's own files: {data}")
        unknown = "PROPS (no type set)"
    else:
        print("THE GAME'S OWN FILES WEREN'T FOUND: a type left to them shows as 'type unknown'.")
        unknown = "type unknown"
    for name, paks in every:
        shown, quiet = [], 0
        for pak in paks:
            for path in sorted(pak.files):
                if not path.endswith(".et") or "/vehicles/" not in path.lower() or want not in path.lower():
                    continue
                text = pak.text(path)
                parts = describe(library, text, unknown) if text else []
                if not parts:
                    quiet += 1
                    continue
                parent = PARENT.match(text)
                shown.append(f"  {path}" + (f"  (from {parent[1].rsplit('/', 1)[-1]})" if parent else "")
                             + "\n    " + " | ".join(parts))
        if shown or quiet:
            print(f"\n== {name}: {len(shown)} vehicle prefabs set budgets, {quiet} don't")
            if shown:
                print("\n".join(shown))


def prices(addons, want):
    print(f"Supply costs set by the mods in {addons}")
    for name, paks in mods(addons):
        lines = []
        for pak in paks:
            for path in sorted(pak.files):
                if not path.endswith((".conf", ".et")) or want not in path.lower():
                    continue
                text = pak.text(path)
                if not text or not COST.search(text):
                    continue
                # A catalogue's costs belong to the entry prefab named before them.
                marks = [(m.start(), m[1].rsplit("/", 1)[-1]) for m in PREFAB.finditer(text)]
                for cost in COST.finditer(text):
                    owner = next((label for at, label in reversed(marks) if at < cost.start()), "")
                    lines.append(f"  {path.rsplit('/', 1)[-1]}  {owner + '  ' if owner else ''}{cost[1][3:]} {cost[2]}")
        if lines:
            print(f"\n== {name}: {len(lines)} costs")
            print("\n".join(lines))


def main():
    args = sys.argv[1:]
    mode = vehicles
    if args and args[0] == "--prices":
        mode, args = prices, args[1:]
    addons = Path(args[0]) if args else default_addons()
    want = args[1].lower() if len(args) > 1 else ""
    if not addons or not addons.is_dir():
        sys.exit("No addons folder found; give it as the first argument.")
    try:
        mode(addons, want)
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
