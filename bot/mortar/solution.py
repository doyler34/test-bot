"""The ballistic engine: one shared calculation, one profile per weapon.

Nothing here knows what a mortar is called or how many mils are in its circle.
A tube is a profile loaded from assets/mortar/tables.json - its sight, its
shell, its rings and the ranges each ring covers - and the same code works any
of them out. Adding a tube is a change to that file, not to this one.

Elevation is only ever read between two rows the profile actually holds. Past
a ring's ends that ring does not reach, and when none of them reach the answer
is OUT OF RANGE.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import math

TABLES = Path(__file__).resolve().parents[2] / 'assets/mortar/tables.json'
OUT_OF_RANGE = 'OUT OF RANGE'


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


def bearing(gun, target, circle):
    """Azimuth in the sight's own mils, and range in metres, north-up.

    ``circle`` is how many mils that sight puts in a full turn - 6400 on the
    M252, 6000 on the 2B14 - and it always comes from the profile.
    """
    east, north = target[0] - gun[0], target[1] - gun[1]
    distance = math.hypot(east, north)
    mils = math.atan2(east, north) * circle / (2 * math.pi) % circle
    return mils, distance


@dataclass(frozen=True)
class Ring:
    ring: str
    elevation: float
    flight: float
    dispersion: float
    correction: float = 0.0


@dataclass(frozen=True)
class Profile:
    """One weapon: its sight, its shell and the table it fires off."""
    key: str
    name: str
    faction: str
    shell: str
    mils: int
    rings: tuple

    @property
    def label(self):
        return f'{self.faction} {self.name}' if self.faction else self.name

    @property
    def loaded(self):
        return bool(self.rings)

    @property
    def span(self):
        """The shortest and longest range any of its rings covers."""
        if not self.rings:
            return None
        return (min(rows[0][0] for _, _, rows in self.rings),
                max(rows[-1][0] for _, _, rows in self.rings))


def build(key, entry):
    """One weapon, or None when it is not fit to fire off.

    A sight with no mil circle is the dangerous case: guessing one would put
    every azimuth out by the difference between 6400 and 6000, so such a tube
    is left out of the list entirely rather than quietly assumed to be NATO.
    """
    try:
        mils = int(entry['mils'])
    except (KeyError, TypeError, ValueError):
        return None
    if mils <= 0:
        return None
    rings = []
    for ring in sorted(entry.get('rings') or {}, key=lambda r: (len(r), r)):
        block = entry['rings'][ring]
        rows = sorted(tuple(row) for row in block.get('rows') or [])
        if len(rows) >= 2:
            rings.append((ring, block.get('dispersion'), tuple(rows)))
    return Profile(key=key, name=entry.get('name', key), faction=entry.get('faction', ''),
                   shell=entry.get('shell', ''), mils=mils, rings=tuple(rings))


@lru_cache(maxsize=4)
def profiles(path=None):
    """Every weapon in the table file, in the order it is written."""
    try:
        data = json.loads(Path(path or TABLES).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    built = ((key, build(key, entry)) for key, entry in data.items() if isinstance(entry, dict))
    return {key: weapon for key, weapon in built if weapon is not None}


def profile(key, path=None):
    return profiles(path).get(key)


def between(rows, distance, column):
    """One column read off a ring at this range.

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


def rings(weapon, distance, climb=0.0):
    """Every ring of this weapon that reaches, lowest ring first.

    ``climb`` is how much higher the target sits than the gun, in metres. A
    target above the gun is met earlier in the shell's fall, so the tube has to
    throw further and the elevation comes down; each row's own mils-per-100m
    figure is what that is worked out from.
    """
    if weapon is None:
        return []
    found = []
    for ring, dispersion, rows in weapon.rings:
        elevation = between(rows, distance, 1)
        if elevation is None:
            continue
        correction = -climb * (between(rows, distance, 3) or 0.0) / 100
        found.append(Ring(ring, elevation + correction, between(rows, distance, 2),
                          dispersion, correction))
    return found


def solution(weapon, distance, climb=0.0):
    """The rings that reach, and the one to use: the lowest that reaches, which
    is the tightest grouping and the shortest time of flight."""
    found = rings(weapon, distance, climb)
    return found[0] if found else None, found
