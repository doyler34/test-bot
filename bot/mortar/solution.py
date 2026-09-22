"""Firing solution from two map grids: azimuth, range and tube elevation.

Azimuth and range fall out of the grid difference. Elevation does not - it is
read from the game's own range tables in assets/mortar/tables.json, one block
per ring, and only ever interpolated between two rows that are actually in the
table. Past a ring's first or last row that ring simply does not reach; when
no ring reaches, the answer is OUT OF RANGE rather than a guess.

Mil circles differ by tube: the M252 sight reads 6400 to the circle, the 2B14
reads 6000, so azimuth is always worked out with the tube's own circle.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import math

TABLES = Path(__file__).resolve().parents[2] / 'assets/mortar/tables.json'
OUT_OF_RANGE = 'OUT OF RANGE'
NATO_MILS = 6400


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


def bearing(gun, target, circle=NATO_MILS):
    """Azimuth in the tube's mils, and range in metres, north-up."""
    east, north = target[0] - gun[0], target[1] - gun[1]
    distance = math.hypot(east, north)
    mils = math.atan2(east, north) * circle / (2 * math.pi) % circle
    return mils, distance


@lru_cache(maxsize=4)
def tables(path=None):
    try:
        data = json.loads(Path(path or TABLES).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def tube(name, path=None):
    return tables(path).get(name) or {}


def tube_names(path=None):
    return {key: value.get('name', key) for key, value in tables(path).items()}


def mil_circle(name, path=None):
    return tube(name, path).get('mils') or NATO_MILS


def has_table(name, path=None):
    return any(len(block.get('rows') or []) >= 2
               for block in (tube(name, path).get('rings') or {}).values())


@dataclass(frozen=True)
class Ring:
    ring: str
    elevation: float
    flight: float
    dispersion: float
    correction: float = 0.0


def between(rows, distance, column):
    """One column read off the table at this range.

    Only ever between two rows the table actually holds; outside that span the
    ring does not reach and there is nothing to read.
    """
    if len(rows) < 2 or not rows[0][0] <= distance <= rows[-1][0]:
        return None
    for low, high in zip(rows, rows[1:]):
        if low[0] <= distance <= high[0]:
            if high[0] == low[0]:
                return low[column]
            share = (distance - low[0]) / (high[0] - low[0])
            return low[column] + (high[column] - low[column]) * share
    return None


def rings(name, distance, climb=0.0, path=None):
    """Every ring that reaches this range, lowest ring first.

    ``climb`` is how much higher the target sits than the gun, in metres. A
    target above the gun is met earlier in the shell's fall, so the tube has to
    throw further and the elevation comes down; the table's own mils-per-100m
    column is what that is worked out from.
    """
    blocks = tube(name, path).get('rings') or {}
    found = []
    for key in sorted(blocks, key=lambda k: (len(k), k)):
        rows = sorted(blocks[key].get('rows') or [])
        elevation = between(rows, distance, 1)
        if elevation is None:
            continue
        per_100 = between(rows, distance, 3) or 0.0
        correction = -climb * per_100 / 100
        found.append(Ring(key, elevation + correction, between(rows, distance, 2),
                          blocks[key].get('dispersion'), correction))
    return found


def solution(name, distance, climb=0.0, path=None):
    """The rings that reach, and the one to use: the lowest that reaches, which
    is the tightest grouping and the shortest time of flight."""
    found = rings(name, distance, climb, path)
    return found[0] if found else None, found
