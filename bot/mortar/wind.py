"""Wind: which way it blows, and what Reforger says that does.

Two halves, kept apart on purpose.

The first half is geometry and is exact: turning the wind a spotter reports
into components along and across the line of fire. It owes the game nothing.

The second half is the game's own data. Reforger keeps wind correction per
projectile, not as one formula, and hands it back through
SCR_ProjectileWindTable.GetDataByDistance() as six figures per sample:

    [0] firing angle                              radians
    [1] distance                                  metres
    [2] peak altitude                             metres
    [3] crosswind azimuth correction              MILLIRADIANS
    [4] parallel wind range correction            METRES
    [5] impact angle                              radians

That shape is what is stored, unconverted, keyed by wind speed and by the
ring whose charge sets the projectile's initSpeedCoef. Milliradians are not
either sight's mils, so the one conversion lives in `sight_mils()` below and
nowhere else.

A ring with no samples reports that it has none. A made-up drift figure is
worse than no correction at all, because it looks like an answer.

CONVENTIONS, fixed here and nowhere else
----------------------------------------
Direction is meteorological - the bearing the wind comes FROM - so the air
travels towards `from + 180`.

Decomposed against the gun-target line:

    parallel  + = tailwind, air moving from gun towards target
              - = headwind, air moving from target towards gun

    crosswind + = air moving towards the shooter's RIGHT
              - = air moving towards the shooter's LEFT

The round is pushed the way the air moves, so a crosswind to the right puts
it right of the target and the gun traverses LEFT: the azimuth correction
carries the opposite sign to the crosswind. A tailwind carries the round
long, so the gun is laid for a SHORTER range.

The stored table holds magnitudes for a wind of a given speed; the signs
above are applied to them here. When real samples arrive, confirm the sign of
column [3] against one live shot before trusting the direction.
"""
from dataclasses import dataclass
import math

# A gale in Reforger is a few metres a second; a bound, not a forecast.
MAX_SPEED = 60.0
# Column numbers, named once, in GetDataByDistance order.
ANGLE, DISTANCE, PEAK, CROSS_MRAD, PARALLEL_M, IMPACT = range(6)


class BadWind(Exception):
    """The wind as given cannot be used."""


def sight_mils(milliradians, circle):
    """Reforger's milliradians as the mils on a particular sight.

    A full circle is 2000*pi milliradians. The M252's sight divides the circle
    into 6400 and the 2B14's into 6000, so the same angle reads differently on
    each and a correction can never be carried from one to the other. This is
    the only place the two ever meet.
    """
    return milliradians / 1000.0 * circle / (2 * math.pi)


@dataclass(frozen=True)
class Wind:
    """What the spotter reported, once it has been checked."""
    speed: float = 0.0
    bearing: float = 0.0     # degrees the wind comes FROM

    @property
    def calm(self):
        return self.speed == 0.0

    @property
    def travelling(self):
        """The bearing the air is moving TOWARDS."""
        return (self.bearing + 180.0) % 360.0


@dataclass(frozen=True)
class Components:
    """The wind split along and across the line of fire, in m/s."""
    parallel: float          # + tailwind, - headwind
    crosswind: float         # + towards the shooter's right


def read(entry):
    """A wind block from a request, or calm when there is none."""
    if entry is None:
        return Wind()
    if not isinstance(entry, dict):
        raise BadWind('Wind must be given as a speed and a direction.')
    speed, bearing = entry.get('speed_mps', 0), entry.get('from_degrees', 0)
    for value in (speed, bearing):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BadWind('Wind speed and direction must be numbers.')
        if value != value or value in (float('inf'), float('-inf')):
            raise BadWind('Wind speed and direction must be numbers.')
    speed = float(speed)
    if speed < 0:
        raise BadWind('Wind speed cannot be negative.')
    if speed > MAX_SPEED:
        raise BadWind(f'Wind speed above {MAX_SPEED:.0f} m/s is not a wind, it is a typo.')
    return Wind(speed=speed, bearing=float(bearing) % 360.0)


def components(wind, bearing_degrees):
    """Split the wind against a line of fire. See the module docstring."""
    if wind.calm:
        return Components(0.0, 0.0)
    offset = math.radians(wind.travelling - bearing_degrees)
    return Components(parallel=wind.speed * math.cos(offset),
                      crosswind=wind.speed * math.sin(offset))


@dataclass(frozen=True)
class Table:
    """One ring's wind data, as Reforger hands it over."""
    init_speed_coef: float = None
    source: str = ''
    # ((wind speed m/s, ((six figures per sample), ...)), ...), speeds ascending
    speeds: tuple = ()

    @property
    def loaded(self):
        return bool(self.speeds)

    @property
    def fastest(self):
        return self.speeds[-1][0] if self.speeds else 0.0

    def at(self, distance, speed, column):
        """One column, read at a distance and a wind speed.

        Interpolated between the samples either side on both axes, and never
        past them - except downwards to nothing, because no wind is no
        correction and that is certain rather than assumed.
        """
        if not self.speeds or speed <= 0:
            return 0.0
        if speed > self.fastest:
            return None          # beyond what was measured; do not guess
        below, above = (0.0, None), None
        for listed, samples in self.speeds:
            if listed <= speed:
                below = (listed, samples)
            elif above is None:
                above = (listed, samples)
        low = 0.0 if below[1] is None else by_distance(below[1], distance, column)
        if low is None:
            return None
        if below[0] == speed or above is None:
            return low if below[0] == speed else None
        high = by_distance(above[1], distance, column)
        if high is None:
            return None
        share = (speed - below[0]) / (above[0] - below[0])
        return low + (high - low) * share


def by_distance(samples, distance, column):
    """A column read off one wind speed's samples, by range.

    This is GetDataByDistance's job: find the samples either side of the range
    and split between them. Outside their span there is nothing to read.
    """
    if len(samples) < 2 or not samples[0][DISTANCE] <= distance <= samples[-1][DISTANCE]:
        return None
    for low, high in zip(samples, samples[1:]):
        if low[DISTANCE] <= distance <= high[DISTANCE]:
            span = high[DISTANCE] - low[DISTANCE]
            if span == 0:
                return low[column]
            share = (distance - low[DISTANCE]) / span
            return low[column] + (high[column] - low[column]) * share
    return None


def build_table(block):
    """A ring's wind block from the table file, or an empty table."""
    if not isinstance(block, dict):
        return Table()
    speeds = []
    for listed, samples in (block.get('samples') or {}).items():
        try:
            speed = float(listed)
        except (TypeError, ValueError):
            continue
        rows = tuple(tuple(float(v) for v in sample) for sample in samples or []
                     if len(sample) >= 6)
        if speed > 0 and len(rows) >= 2:
            speeds.append((speed, tuple(sorted(rows, key=lambda r: r[DISTANCE]))))
    coef = block.get('initSpeedCoef')
    return Table(init_speed_coef=None if coef is None else float(coef),
                 source=str(block.get('source') or ''),
                 speeds=tuple(sorted(speeds)))
