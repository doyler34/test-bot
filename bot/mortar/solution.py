"""Firing solution from two map grids: azimuth, range and tube elevation.

Azimuth and range are exact - they fall out of the grid difference. Elevation
does not: it is read from a range table measured in game, so a tube with no
table yet still gives a bearing and a distance and says so.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import math

TABLES = Path(__file__).resolve().parents[2] / 'assets/mortar/tables.json'
MILS = 6400


def parse_grid(text):
    """A map grid as metres east and north.

    Takes the usual shapes - "0428 1183", "04281183", "042118" - with 4, 6, 8
    or 10 digits. The digit count sets the precision, the same way the map
    does: 6 digits is 100m, 8 is 10m, 10 is 1m.
    """
    digits = ''.join(c for c in str(text) if c.isdigit())
    if len(digits) not in (4, 6, 8, 10) or len(digits) % 2:
        raise ValueError('A grid is 4, 6, 8 or 10 digits, half easting and half northing.')
    half = len(digits) // 2
    step = 10 ** (5 - half)
    return int(digits[:half]) * step, int(digits[half:]) * step


def bearing(gun, target):
    """Azimuth in mils and metres between two points, north-up."""
    east, north = target[0] - gun[0], target[1] - gun[1]
    distance = math.hypot(east, north)
    mils = math.atan2(east, north) * MILS / (2 * math.pi) % MILS
    return mils, distance


@dataclass(frozen=True)
class Charge:
    charge: str
    elevation: float | None
    note: str = ''


@lru_cache(maxsize=1)
def tables(path=None):
    try:
        data = json.loads(Path(path or TABLES).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def tube_names(path=None):
    return {key: value.get('name', key) for key, value in tables(path).items()}


def interpolate(rows, distance):
    """Elevation for a range that sits between two table rows.

    Range tables step in 25-50m, so the row either side is what a gunner reads
    off and splits; anything outside the table's own span is out of range for
    that charge rather than extrapolated into a guess.
    """
    ordered = sorted((float(r), float(mils)) for r, mils in rows)
    if len(ordered) < 2 or not ordered[0][0] <= distance <= ordered[-1][0]:
        return None
    for (low, low_mils), (high, high_mils) in zip(ordered, ordered[1:]):
        if low <= distance <= high:
            if high == low:
                return low_mils
            return low_mils + (high_mils - low_mils) * (distance - low) / (high - low)
    return None


def solution(tube, distance, path=None):
    """Every charge that reaches, nearest the middle of its band first."""
    data = tables(path).get(tube)
    if not data:
        return []
    charges = data.get('charges') or {}
    result = []
    for name in sorted(charges, key=lambda n: (len(n), n)):
        rows = charges[name] or []
        if len(rows) < 2:
            result.append(Charge(name, None, 'no table'))
            continue
        mils = interpolate(rows, distance)
        span = sorted(float(r) for r, _ in rows)
        result.append(Charge(name, mils, '' if mils is not None else
                             f'out of range ({span[0]:.0f}-{span[-1]:.0f} m)'))
    return result


def has_table(tube, path=None):
    charges = (tables(path).get(tube) or {}).get('charges') or {}
    return any(len(rows or []) >= 2 for rows in charges.values())
