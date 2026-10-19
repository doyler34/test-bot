"""Build assets/ballistics/rounds.json from the game's AI ballistic tables.

The tables come out of the mortar test mod's dump (the OYB_Manual folder).
Run from the bot's folder:  python3 dev/ballistics_tables.py path/to/OYB_Manual

Each table row is one barrel angle: how far the round goes before it's back at
barrel height, how far that is below the line it was aimed along, and how long
it took. Rifle rounds are so flat the first rows are hundreds of metres apart,
so for bullets a drag curve is fitted to the rows and drop is worked out from
that. Rockets keep burning after launch and don't fit one, so they're read
straight off the table.
"""

import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "ballistics" / "rounds.json"
G = 9.81
ZERO = 100

RIFLE = list(range(100, 900, 100))
MG = list(range(100, 1300, 100))
HMG = list(range(200, 2200, 200))
PISTOL = [25, 50, 75, 100, 150, 200]
GRENADE = list(range(50, 400, 50))

# (group, file, round, used by, ranges, fitted)
ROUNDS = [
    ("Rifles", "556x45_Ball_M855", "5.56 M855", "M16A2, M249", RIFLE, True),
    ("Rifles", "545x39_Ball_7N6", "5.45 7N6", "AK-74, RPK-74", RIFLE, True),
    ("Rifles", "762x39_Ball_57N231", "7.62x39 57-N-231", "AKM", RIFLE, True),
    ("Machine guns and marksman rifles", "762x51_Ball_M80", "7.62x51 M80", "M60, M14", MG, True),
    ("Machine guns and marksman rifles", "762x51_Ball_M118", "7.62x51 M118", "M21", MG, True),
    ("Machine guns and marksman rifles", "762x54r_Ball_57N323S", "7.62x54R 57-N-323S", "PKM", MG, True),
    ("Machine guns and marksman rifles", "762x54r_Ball_7N1", "7.62x54R 7N1", "SVD", MG, True),
    ("Heavy machine guns", "127x99_Ball_M33", "12.7x99 M33", "M2", HMG, True),
    ("Heavy machine guns", "127x108_API_B32", "12.7x108 B-32", "Soviet 12.7 mm", HMG, True),
    ("Heavy machine guns", "145x114_API_57BZ561S", "14.5x114 BZ", "BTR-70", HMG, True),
    ("Pistols", "9x19_Ball_M882", "9x19 M882", "M9", PISTOL, True),
    ("Pistols", "9x18_Ball_57N181", "9x18 57-N-181S", "PM", PISTOL, True),
    ("Launchers", "Grenade_HE_M406", "40 mm M406", "M203", GRENADE, False),
    ("Launchers", "Grenade_HE_VOG25", "40 mm VOG-25", "GP-25", GRENADE + [400, 450, 500], False),
    ("Launchers", "Rocket_M72A3", "66 mm M72A3", "M72 LAW", list(range(50, 450, 50)), False),
    ("Launchers", "Rocket_PG7VM", "PG-7VM", "RPG-7", list(range(50, 550, 50)), False),
    ("Launchers", "Rocket_PG7VL", "PG-7VL", "RPG-7", list(range(50, 550, 50)), False),
    ("Launchers", "Rocket_RPG75", "RPG-75", "RPG-75", list(range(50, 350, 50)), False),
    ("Launchers", "Rocket_PG22", "PG-22", "RPG-22", list(range(50, 450, 50)), False),
]


def read(path):
    """The rows for the round at its normal speed, up to its furthest reach."""
    text = path.read_text()
    for block in text.split('BallisticTable "')[1:]:
        speed = re.search(r"InitSpeedCoefficient ([\d.]+)", block)
        if speed and float(speed.group(1)) == 1:
            rows = [tuple(map(float, line.split()))
                    for line in block.split('"Table data" {')[1].split("}")[0].strip().splitlines()]
            out = []
            for row in rows:
                if out and row[0] <= out[-1][0]:
                    break
                out.append(row)
            return out
    raise ValueError(f"{path.name} has no table at normal speed")


def fly(v0, k, angle, until=None):
    """Quadratic drag. Lands back at barrel height, or stops at x = until;
    gives (x, y, t) at that point."""
    vx, vy = v0 * math.cos(angle), v0 * math.sin(angle)
    x = y = t = 0.0
    dt = 0.0005 if until and until < 300 else 0.002
    while t < 60:
        v = math.hypot(vx, vy)
        nx, ny = x + vx * dt, y + vy * dt
        vx -= k * v * vx * dt
        vy -= (k * v * vy + G) * dt
        if until is not None and nx >= until:
            f = (until - x) / (nx - x)
            return until, y + f * (ny - y), t + f * dt
        if until is None and ny < 0 < t:
            f = y / (y - ny)
            return x + f * (nx - x), 0.0, t + f * dt
        x, y, t = nx, ny, t + dt
    return x, y, t


def misfit(v0, k, rows):
    total = 0.0
    for x, drop, t in rows:
        fx, _, ft = fly(v0, k, math.atan2(drop, x))
        total += ((fx - x) / x) ** 2 + ((ft - t) / t) ** 2
    return total


def fit(rows):
    rows = [r for r in rows if r[0] > 0][:12]
    v0, k = rows[0][0] / rows[0][2] * 1.1, 1e-3
    best = misfit(v0, k, rows)
    dv, dk = v0 * 0.05, k * 0.5
    while dv > 0.05:
        for nv, nk in ((v0 + dv, k), (v0 - dv, k), (v0, k + dk), (v0, max(1e-6, k - dk))):
            score = misfit(nv, nk, rows)
            if score < best:
                best, v0, k = score, nv, nk
                break
        else:
            dv, dk = dv / 2, dk / 2
    return v0, k, math.sqrt(best / len(rows) / 2)


def zero_angle(v0, k, zero):
    low, high = 0.0, 0.05
    for _ in range(40):
        mid = (low + high) / 2
        if fly(v0, k, mid, zero)[1] < 0:
            low = mid
        else:
            high = mid
    return low


def from_fit(rows, ranges):
    v0, k, error = fit(rows)
    angle = zero_angle(v0, k, ZERO)
    out = []
    for r in ranges:
        _, y, t = fly(v0, k, angle, r)
        out.append([r, round(-y, 2), round(t, 2)])
    return out, {"speed": round(v0), "fit": round(error, 4)}


def on_table(rows, x):
    for p, q in zip(rows, rows[1:]):
        if p[0] <= x <= q[0]:
            f = (x - p[0]) / (q[0] - p[0])
            return p[1] + f * (q[1] - p[1]), p[2] + f * (q[2] - p[2])
    return None


def from_table(rows, ranges):
    zero_drop = on_table(rows, ZERO)[0]
    out = []
    for r in ranges:
        found = on_table(rows, r)
        if found:
            out.append([r, round(found[0] - zero_drop * r / ZERO, 2), round(found[1], 2)])
    return out, {}


def main(folder):
    folder = Path(folder)
    rounds = []
    for group, name, label, used_by, ranges, fitted in ROUNDS:
        rows = read(folder / f"Configs_Weapons_AIBallisticTables_AIBT_{name}.conf")
        table, extra = (from_fit if fitted else from_table)(rows, ranges)
        # Grenades go up in an arc; past 100 m nobody aims "above" one, they
        # use the range marks on the sight, so only the flight time is kept.
        lobbed = name.startswith("Grenade")
        rounds.append({"group": group, "round": label, "used_by": used_by,
                       "reach": round(rows[-1][0]), "lobbed": lobbed,
                       "rows": [[r, None, t] for r, _, t in table] if lobbed else table, **extra})
        print(f"{label:22} {extra or 'from the table'}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "Arma Reforger AI ballistic tables (Configs/Weapons/AIBallisticTables), normal muzzle speed.",
        "zero": ZERO,
        "columns": "range m, aim this many metres above the target when zeroed at 100 m, flight time s",
        "rounds": rounds}, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
