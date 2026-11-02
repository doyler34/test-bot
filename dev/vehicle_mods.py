"""What every mod on a server sets for its vehicles: the costs and budgets in each
vehicle prefab, read straight out of the mods' .pak files.

    python3 dev/vehicle_mods.py [addons folder] [name filter]

The addons folder defaults to the first server under /home/amp/.ampdata that has
one. Only prefabs that set a cost or budget of their own are printed; the rest
of a mod's vehicles are counted. A budget type shown as "?" is left to the
prefab it comes from.
"""
import re
import struct
import sys
import zlib
from pathlib import Path

LISTS = ("m_EntityBudgetCost", "m_EntityChildrenBudgetCost", "m_aCrewEntityBudgetCost", "m_aPassengerEntityBudgetCost")
SINGLE = re.compile(r"^\s*(m_iSupplyCost|m_iBuildingCost|m_eEntityType|m_EntityType)\s+(\S+)", re.M)
PARENT = re.compile(r'^\w+\s*:\s*"\{[0-9A-F]+\}([^"]+)"')


def entries(pak):
    """(path, offset, packed size, compressed) for every file in a PAC1 pak."""
    found = []

    def walk(buf, pos, prefix):
        kind, size = buf[pos], buf[pos + 1]
        name = buf[pos + 2:pos + 2 + size].decode("utf-8", "replace")
        pos += 2 + size
        path = f"{prefix}/{name}" if prefix and name else (prefix or name)
        if kind == 0:
            count = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
            for _ in range(count):
                pos = walk(buf, pos, path)
            return pos
        offset, packed, _ = struct.unpack_from("<III", buf, pos)
        found.append((path, offset, packed, struct.unpack_from(">I", buf, pos + 16)[0] != 0))
        return pos + 24

    with open(pak, "rb") as f:
        if f.read(12)[8:12] != b"PAC1":
            return found
        while len(head := f.read(8)) == 8:
            tag, length = head[:4], struct.unpack(">I", head[4:])[0]
            if tag == b"FILE":
                walk(f.read(length), 0, "")
            else:
                f.seek(length, 1)
    return found


def read(pak, entry):
    with open(pak, "rb") as f:
        f.seek(entry[1])
        raw = f.read(entry[2])
    return zlib.decompress(raw) if entry[3] else raw


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


def budgets(inside):
    """'PROPS 14000, CAMPAIGN 1375' from a budget list; a type the prefab leaves to its parent shows as '?'."""
    out = []
    for entry in re.findall(r'SCR_EntityBudgetValue\s*(?:"\{[0-9A-F]+\}")?\s*\{([^{}]*)\}', inside):
        kind = re.search(r"m_BudgetType\s+(\w+)", entry)
        value = re.search(r"m_Value\s+(-?\d+)", entry)
        out.append(f"{kind[1] if kind else '?'} {value[1] if value else '?'}")
    return ", ".join(out)


def describe(text):
    parts = []
    for key in LISTS:
        inside = block(text, key)
        if inside is not None:
            parts.append(f"{key.replace('m_a', '').replace('m_', '')}: [{budgets(inside)}]")
    parts += [f"{key[3:]} {value}" for key, value in SINGLE.findall(text) if "Cost" in key]
    return parts


def default_addons():
    for folder in sorted(Path("/home/amp/.ampdata/instances").glob("*/arma-reforger/*/AReforgerMaster/addons")):
        if any(folder.glob("*/*.pak")):
            return folder
    return None


def main():
    addons = Path(sys.argv[1]) if len(sys.argv) > 1 else default_addons()
    want = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    if not addons or not addons.is_dir():
        sys.exit("No addons folder found; give it as the first argument.")
    print(f"Mods in {addons}")
    for mod in sorted(p for p in addons.iterdir() if p.is_dir()):
        shown, quiet = [], 0
        for pak in sorted(mod.glob("*.pak")):
            for entry in entries(pak):
                path = entry[0]
                if not path.endswith(".et") or "/vehicles/" not in path.lower() or want not in path.lower():
                    continue
                raw = read(pak, entry)
                if raw[:4] in (b"FORM", b"EBIN"):
                    continue
                text = raw.decode("utf-8", "replace")
                parts = describe(text)
                if not parts:
                    quiet += 1
                    continue
                parent = PARENT.match(text)
                shown.append(f"  {path}" + (f"  (from {parent[1].rsplit('/', 1)[-1]})" if parent else "")
                             + "\n    " + " | ".join(parts))
        if shown or quiet:
            print(f"\n== {mod.name.rsplit('_', 1)[0]}: {len(shown)} vehicle prefabs set costs, {quiet} don't")
            print("\n".join(shown))


if __name__ == "__main__":
    main()
