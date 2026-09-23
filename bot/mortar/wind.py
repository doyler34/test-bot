"""Wind: which way it blows, and what Reforger says that does.

Two halves, kept apart on purpose.

The first half is geometry and is exact: turning the wind a spotter reports
into components along and across the line of fire. It owes the game nothing.

The second half is the game's own data, pulled out of vanilla's
WindData_Shell_*.conf. Each row of a ring's table is

    [range m, crosswind correction at 10 m/s, range correction at 10 m/s]

where the crosswind figure is **already in that sight's mils** - 6400-mil
figures for the M252, 6000-mil for the 2B14 - and the range figure is metres.
Nothing here converts milliradians: the extraction did that, and doing it
again would be a silent factor of about six. There is deliberately no
milliradian helper in this module for anything to reach for.

Both columns are quoted for a full 10 m/s of that component, so a component
of any other strength scales linearly:

    correction = value_at_10 * component / 10

The table supplies MAGNITUDE. The geometry below supplies DIRECTION.

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

The stored table holds magnitudes only; the signs above are what give them a
direction.
"""
from dataclasses import dataclass
import math

# A gale in Reforger is a few metres a second; a bound, not a forecast.
MAX_SPEED = 60.0
# Columns of a wind row, named once.
RANGE, CROSS_MILS, RANGE_M = range(3)
# What the extracted figures are quoted for.
REFERENCE_SPEED = 10.0


class BadWind(Exception):
    """The wind as given cannot be used."""


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
    """One ring's wind data, as vanilla quotes it at 10 m/s."""
    source: str = ''
    reference: float = REFERENCE_SPEED
    rows: tuple = ()        # (range m, crosswind mils at reference, metres at reference)

    @property
    def loaded(self):
        return len(self.rows) >= 2

    @property
    def span(self):
        return (self.rows[0][RANGE], self.rows[-1][RANGE]) if self.rows else None

    def at(self, distance, column):
        """One column read at a range, between the rows either side.

        Never past the ends: a solution outside this ring's wind data gets no
        correction rather than an invented one.
        """
        if not self.loaded or not self.rows[0][RANGE] <= distance <= self.rows[-1][RANGE]:
            return None
        for low, high in zip(self.rows, self.rows[1:]):
            if low[RANGE] <= distance <= high[RANGE]:
                span = high[RANGE] - low[RANGE]
                if span == 0:
                    return low[column]
                share = (distance - low[RANGE]) / span
                return low[column] + (high[column] - low[column]) * share
        return None

    def scaled(self, distance, component, column):
        """The correction for a wind component of any strength.

        The table is quoted for a full 10 m/s, so this is linear in the
        component - and the component's sign comes through it, which is what
        turns a magnitude into a direction.
        """
        value = self.at(distance, column)
        if value is None:
            return None
        return value * component / self.reference

    def crosswind(self, distance, component):
        """Sideways correction, in this sight's own mils. Already converted."""
        return self.scaled(distance, component, CROSS_MILS)

    def carry(self, distance, component):
        """How far a parallel wind carries the round, in metres."""
        return self.scaled(distance, component, RANGE_M)


def build_table(block):
    """A ring's wind block from the table file, or an empty table."""
    if not isinstance(block, dict):
        return Table()
    rows = []
    for row in block.get('rows') or []:
        if len(row) >= 3:
            rows.append(tuple(float(v) for v in row[:3]))
    reference = block.get('referenceSpeedMps', REFERENCE_SPEED)
    try:
        reference = float(reference)
    except (TypeError, ValueError):
        return Table()
    if reference <= 0:
        return Table()
    return Table(source=str(block.get('source') or ''), reference=reference,
                 rows=tuple(sorted(rows)))
