"""Wind: which way it blows, and what that does to a firing solution.

Two halves, kept apart on purpose.

The first half is geometry and is exact: turning the wind a spotter reports
into components along and across the line of fire. That is trigonometry, it
owes nothing to the game, and it is fully tested.

The second half needs the game's own numbers - how far a given shell on a
given ring is actually pushed by a metre per second of wind. Those are read
from the range tables beside the elevations, never calculated here. A shell
with no wind rows reports that it has none rather than guessing: a made-up
drift figure is worse than no correction at all, because it looks like an
answer.

CONVENTIONS, fixed here and nowhere else
----------------------------------------
Direction is reported the way every spotter reports it: the bearing the wind
is coming FROM, degrees clockwise from north. The air therefore travels
towards `from + 180`.

Decomposed against the gun-target line:

    parallel  + = tailwind, air moving from gun towards target
              - = headwind, air moving from target towards gun

    crosswind + = air moving towards the shooter's RIGHT
              - = air moving towards the shooter's LEFT

The shell is pushed the way the air moves, so a crosswind to the right puts
the round right of the target and the gun must be traversed LEFT to meet it:
the azimuth correction carries the opposite sign to the crosswind. A tailwind
carries the round long, so the gun must be laid for a SHORTER range.
"""
from dataclasses import dataclass
import math

# A gale in Reforger is a few metres a second; this is a sanity bound, not a
# statement about the weather.
MAX_SPEED = 60.0


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
    """A wind block from a request, or calm when there is none.

    Speed has to be a real number inside a sane range. Direction is a compass
    bearing, so it wraps: 360 and -90 are 0 and 270, not errors.
    """
    if entry is None:
        return Wind()
    if not isinstance(entry, dict):
        raise BadWind('Wind must be given as a speed and a direction.')
    speed = entry.get('speed_mps', 0)
    bearing = entry.get('from_degrees', 0)
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
    """Split the wind against a line of fire.

    `bearing_degrees` is the gun-to-target bearing, degrees clockwise from
    north. See the module docstring for the signs.
    """
    if wind.calm:
        return Components(0.0, 0.0)
    # The angle between where the air is going and where the shell is going.
    offset = math.radians(wind.travelling - bearing_degrees)
    return Components(parallel=wind.speed * math.cos(offset),
                      crosswind=wind.speed * math.sin(offset))


def drift_mils(drift_metres, distance, circle):
    """A sideways miss, as an angle at the gun, in the sight's own mils.

    The tube's own circle is used - 6400 on the M252, 6000 on the 2B14 - so a
    correction can never be carried across from one sight to the other.
    """
    if distance <= 0:
        return 0.0
    return math.atan2(drift_metres, distance) * circle / (2 * math.pi)
