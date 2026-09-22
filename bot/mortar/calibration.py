"""Turning a map image into the coordinates the ballistic engine works in.

The map is a picture, not a geographic projection: pixels across, pixels down.
Two reference points - a pixel position and the world grid it sits on - fix the
whole thing. Their difference gives metres per pixel on each axis, and the sign
of that gives the axis direction, so nothing has to be assumed about which way
north runs down the image.

    east  = e1 + (x - x1) * metres_per_pixel_x
    north = n1 + (y - y1) * metres_per_pixel_y

On a north-up image the second of those is negative, because image y grows
downwards while northing grows up. That falls out of the reference points
rather than being written in here.

Nothing in this module is specific to one map. Point it at another config and
another image and it works the same.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'assets/mortar/map.json'


class NotCalibrated(Exception):
    """The map config is missing, incomplete or self-contradictory."""


@dataclass(frozen=True)
class Calibration:
    name: str
    image: str
    width: int
    height: int
    east_per_pixel: float
    north_per_pixel: float
    origin: tuple          # the first reference point, image (x, y)
    origin_world: tuple    # and the world (east, north) it sits on
    digits: int = 3

    def world(self, x, y):
        """Image pixel to world metres."""
        return (self.origin_world[0] + (x - self.origin[0]) * self.east_per_pixel,
                self.origin_world[1] + (y - self.origin[1]) * self.north_per_pixel)

    def pixel(self, east, north):
        """World metres back to image pixel. (The field `image` is the picture
        itself, so the reverse conversion is named for what it returns.)"""
        return (self.origin[0] + (east - self.origin_world[0]) / self.east_per_pixel,
                self.origin[1] + (north - self.origin_world[1]) / self.north_per_pixel)

    def grid(self, east, north):
        """The world point as the map's own grid readout, e.g. "047 063"."""
        step = 10 ** (5 - self.digits)
        return (f'{int(east // step) % 10 ** self.digits:0{self.digits}d} '
                f'{int(north // step) % 10 ** self.digits:0{self.digits}d}')

    @property
    def bounds(self):
        """World corners of the image: south-west then north-east."""
        corners = [self.world(0, 0), self.world(self.width, self.height)]
        easts = sorted(c[0] for c in corners)
        norths = sorted(c[1] for c in corners)
        return (easts[0], norths[0]), (easts[1], norths[1])

    @property
    def metres_per_pixel(self):
        """Scale for drawing a range ring, taken off the east axis."""
        return abs(self.east_per_pixel)

    def describe(self):
        """What the browser needs to draw the map and label a cursor: the same
        linear transform, for display only. Solutions are still worked out from
        the pixels the page sends, server side, off this one calibration."""
        return {'name': self.name, 'width': self.width, 'height': self.height,
                'metresPerPixel': self.metres_per_pixel, 'digits': self.digits,
                'eastPerPixel': self.east_per_pixel, 'northPerPixel': self.north_per_pixel,
                'originPixel': list(self.origin), 'originWorld': list(self.origin_world)}


def read(entry):
    """Build a calibration from a plain dict, or say why it cannot be built."""
    if not isinstance(entry, dict):
        raise NotCalibrated('The map config must be an object.')
    missing = [key for key in ('image', 'width', 'height', 'reference') if not entry.get(key)]
    if missing:
        raise NotCalibrated(f'The map config still needs: {", ".join(missing)}.')
    try:
        width, height = int(entry['width']), int(entry['height'])
    except (TypeError, ValueError):
        raise NotCalibrated('Image width and height must be whole numbers of pixels.')
    if width < 2 or height < 2:
        raise NotCalibrated('Image width and height must be real pixel sizes.')
    points = entry['reference']
    if not isinstance(points, list) or len(points) != 2:
        raise NotCalibrated('Calibration needs exactly two reference points.')
    try:
        (x1, y1), (e1, n1) = tuple(points[0]['image']), tuple(points[0]['world'])
        (x2, y2), (e2, n2) = tuple(points[1]['image']), tuple(points[1]['world'])
    except (KeyError, TypeError, ValueError):
        raise NotCalibrated('Each reference point needs "image": [x, y] and "world": [east, north].')
    if x1 == x2 or y1 == y2:
        raise NotCalibrated('The two reference points must differ in both x and y; '
                            'pick points well apart, such as opposite corners.')
    if e1 == e2 or n1 == n2:
        raise NotCalibrated('The two reference points must sit on different eastings '
                            'and different northings.')
    return Calibration(name=entry.get('name', 'Map'), image=str(entry['image']),
                       width=width, height=height,
                       east_per_pixel=(e2 - e1) / (x2 - x1),
                       north_per_pixel=(n2 - n1) / (y2 - y1),
                       origin=(x1, y1), origin_world=(e1, n1),
                       digits=int(entry.get('digits', 3)))


@lru_cache(maxsize=4)
def calibration(path=None):
    """The configured map, or None when it has not been calibrated yet.

    A map that cannot be trusted is no map at all: rather than fall back to
    guessed bounds, the web front end stays off and says what is missing.
    """
    try:
        entry = json.loads(Path(path or CONFIG).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    try:
        return read(entry)
    except NotCalibrated:
        return None


def reason(path=None):
    """Why the map is not usable, for the log and the error page."""
    try:
        entry = json.loads(Path(path or CONFIG).read_text(encoding='utf-8'))
    except OSError:
        return 'No map config found at assets/mortar/map.json.'
    except ValueError:
        return 'The map config is not valid JSON.'
    try:
        read(entry)
    except NotCalibrated as exc:
        return str(exc)
    return ''


def image_path(calibrated, root=None):
    """Where the map picture lives on disk, kept inside the project."""
    base = Path(root or ROOT).resolve()
    path = (base / calibrated.image).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path
