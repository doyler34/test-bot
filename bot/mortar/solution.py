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

from bot.mortar.wind import (CROSS_MRAD, PARALLEL_M, Table, Wind, build_table,
                             components, sight_mils)

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
    """One weapon loaded with one round: the sight, and the table it fires off.

    A tube firing smoke is a different profile from the same tube firing HE -
    different rings, different reach - so the engine is handed the pairing and
    never has to know which weapon or which round it is working on.
    """
    key: str
    weapon: str
    round_key: str
    name: str
    faction: str
    shell: str
    mils: int
    rings: tuple
    winds: tuple = ()      # (ring, Table) - Reforger's own wind samples

    def wind_table(self, ring):
        """That ring's Reforger wind table, or an empty one.

        The ring is what sets the charge, and the charge is what sets the
        projectile's initSpeedCoef, so the table belongs to the ring.
        """
        return next((table for name, table in self.winds if name == ring), Table())

    @property
    def windy(self):
        """Does this shell have wind samples at all?"""
        return any(table.loaded for _, table in self.winds)

    @property
    def label(self):
        return f'{self.faction} {self.name}' if self.faction else self.name

    @property
    def span(self):
        """The shortest and longest range any of its rings covers. A profile is
        only ever built with at least one usable ring, so this always answers."""
        return (min(rows[0][0] for _, _, rows in self.rings),
                max(rows[-1][0] for _, _, rows in self.rings))


def ring_rows(block):
    rings, winds = [], []
    for ring in sorted(block or {}, key=lambda r: (len(r), r)):
        rows = sorted(tuple(row) for row in block[ring].get('rows') or [])
        if len(rows) < 2:
            continue
        rings.append((ring, block[ring].get('dispersion'), tuple(rows)))
        table = build_table(block[ring].get('wind'))
        if table.loaded:
            winds.append((ring, table))
    return tuple(rings), tuple(winds)


def build(weapon, entry):
    """Every round this weapon carries, or nothing when it is not fit to fire.

    A sight with no mil circle is the dangerous case: guessing one would put
    every azimuth out by the difference between 6400 and 6000, so such a tube
    is left out of the list entirely rather than quietly assumed to be NATO.
    """
    try:
        mils = int(entry['mils'])
    except (KeyError, TypeError, ValueError):
        return
    if mils <= 0:
        return
    for round_key, shell in (entry.get('shells') or {}).items():
        rings, winds = ring_rows(shell.get('rings'))
        if not rings:
            continue
        yield Profile(key=f'{weapon}:{round_key}', weapon=weapon, round_key=round_key,
                      name=entry.get('name', weapon), faction=entry.get('faction', ''),
                      shell=shell.get('name', round_key), mils=mils, rings=rings,
                      winds=winds)


@lru_cache(maxsize=4)
def profiles(path=None):
    """Every weapon-and-round pairing in the table file, in the order written.

    Keyed "<weapon>:<round>", so "m252:smoke" is the M252 loaded with smoke.
    """
    try:
        data = json.loads(Path(path or TABLES).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    found = {}
    for weapon, entry in data.items():
        if isinstance(entry, dict):
            found.update({p.key: p for p in build(weapon, entry)})
    return found


def profile(key, path=None):
    return profiles(path).get(key)


def weapons(path=None):
    """The rounds each weapon carries, keyed by weapon."""
    found = {}
    for loaded in profiles(path).values():
        found.setdefault(loaded.weapon, []).append(loaded)
    return found


def swap(current, weapon=None, round_key=None, path=None):
    """The profile you get by changing one half of the pairing.

    Changing tube keeps the round in hand where that tube carries it, so
    picking the 2B14 while holding smoke does not silently hand back HE.
    """
    loaded = profiles(path)
    wanted = weapon or (current.weapon if current else None)
    shell = round_key or (current.round_key if current else None)
    if f'{wanted}:{shell}' in loaded:
        return loaded[f'{wanted}:{shell}']
    for candidate in loaded.values():
        if candidate.weapon == wanted:
            return candidate
    return next(iter(loaded.values()), None)


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


@dataclass(frozen=True)
class Corrected:
    """A firing solution with the wind in it, and the plain one beside it."""
    base: Ring                  # the ring the still-air range asks for
    final: Ring                 # the ring to actually fire, or None
    every: tuple = ()           # every ring reaching the corrected range
    parallel: float = 0.0       # m/s, + tailwind
    crosswind: float = 0.0      # m/s, + towards the shooter's right
    crosswind_mrad: float = 0.0  # Reforger's own figure, signed, milliradians
    azimuth_mils: float = 0.0   # the same angle on this sight, to be ADDED
    range_m: float = 0.0        # metres the wind carries the round, signed
    effective_range: float = 0.0
    has_data: bool = False      # this ring has Reforger samples
    measured: bool = True       # ...and they cover this range and wind speed

    @property
    def applied(self):
        return bool(self.has_data and self.measured
                    and (self.azimuth_mils or self.range_m))


def apply_wind(weapon, distance, bearing_degrees, wind=None, climb=0.0):
    """The solution to fire, once Reforger's wind table is accounted for.

    Crosswind comes back from the table as an azimuth correction in
    milliradians and is converted to this sight's own mils; head or tail wind
    comes back as metres of range, and the elevation for it is read from the
    range table we already have. No elevation is invented here.

    With no wind, or no samples for this ring, the base solution is handed
    back untouched.
    """
    base, every = solution(weapon, distance, climb)
    wind = wind or Wind()
    # The split is geometry and always holds, so it is reported even when
    # there is nothing measured to correct with.
    split = components(wind, bearing_degrees)
    table = weapon.wind_table(base.ring) if base is not None else Table()
    plain = Corrected(base=base, final=base, every=tuple(every),
                      parallel=split.parallel, crosswind=split.crosswind,
                      effective_range=distance, has_data=table.loaded)
    if base is None or wind.calm or not table.loaded:
        return plain

    # Each component is looked up at its own strength, in the game's units.
    mrad = table.at(distance, abs(split.crosswind), CROSS_MRAD)
    carry = table.at(distance, abs(split.parallel), PARALLEL_M)
    if mrad is None or carry is None:
        # The samples do not cover this range or this wind; say so rather
        # than reaching past the ends of what was measured.
        return Corrected(**{**plain.__dict__, 'measured': False})

    carry = math.copysign(carry, split.parallel)
    effective = distance - carry          # a tailwind carries it long
    final, reaching = solution(weapon, effective, climb)
    mrad = math.copysign(mrad, split.crosswind)
    azimuth = -sight_mils(mrad, weapon.mils)   # pushed right, traverse left

    return Corrected(base=base, final=final, every=tuple(reaching),
                     parallel=split.parallel, crosswind=split.crosswind,
                     crosswind_mrad=mrad, azimuth_mils=azimuth, range_m=carry,
                     effective_range=effective, has_data=True)
