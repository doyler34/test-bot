"""Where a click on the map is, in the coordinates the engine fires in.

Enfusion maps are not geographic, and this does not pretend otherwise: the page
works in the game's own world X/Z metres, and Leaflet is bent to match.

The map layer is GeNeFRAG's, from the ArmaReforger project (MIT,
https://github.com/GeNeFRAG/ArmaReforger). Its `all_arma_maps.json` entry for a
map is copied into ours as-is, because that entry IS the calibration:

    "coordinate_transform": {"lng": {"cof": 50.0, "offset":    0.0},
                             "lat": {"cof": -50.0, "offset": -256.0}}

and `mapEngine.js` reads it as

    gameX = (lng + lng.offset) * lng.cof
    gameZ = (lat + lat.offset) * lat.cof

with, where `earth_correction` is set, a further pull towards the middle that
takes 100 m off the span and is zero at the centre of the map:

    game += (size / 2 - game) / size * 100

For Everon that puts the map's centre at exactly 6400, 6400 and its edges at
50 and 12750. Both directions are implemented here, and only here: the page
mirrors this file and the tests check the pair round-trips.

Tiles are a plain pyramid from `generate_tiles.py` - `{z}/{x}/{y}.webp`, 256 px,
rows counted from the top like any web map - served from our own disk. Nothing
is fetched from anyone else's site at runtime.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'assets/mortar/map.json'
# What the earth correction takes off the span, in metres, per GeNeFRAG.
CORRECTION = 100.0


class NotCalibrated(Exception):
    """The map config is missing, incomplete or self-contradictory."""


@dataclass(frozen=True)
class Axis:
    """One half of GeNeFRAG's coordinate_transform."""
    cof: float
    offset: float

    def game(self, value):
        return (value + self.offset) * self.cof

    def map(self, metres):
        return metres / self.cof - self.offset


@dataclass(frozen=True)
class Tiles:
    directory: str
    pattern: str = '{z}/{x}/{y}.webp'
    tile_size: int = 256
    min_zoom: int = 0


@dataclass(frozen=True)
class Picture:
    path: str


@dataclass(frozen=True)
class Calibration:
    name: str
    width: float                # world metres, east
    height: float               # world metres, north
    max_zoom: int
    lng: Axis
    lat: Axis
    earth_correction: bool = False
    digits: int = 3
    tiles: Tiles = None
    picture: Picture = None

    # --- world <-> the map: the one definition the page mirrors -----------
    def corrected(self, metres, size):
        """GeNeFRAG's earth correction, pulling towards the middle."""
        if not self.earth_correction:
            return metres
        return metres + (size / 2 - metres) / size * CORRECTION

    def uncorrected(self, metres, size):
        """The same, undone. It is linear, so it inverts exactly."""
        if not self.earth_correction:
            return metres
        return (metres - CORRECTION / 2) / (1 - CORRECTION / size)

    def world(self, lat, lng):
        """A point on the map, as world X/Z metres."""
        return (self.corrected(self.lng.game(lng), self.width),
                self.corrected(self.lat.game(lat), self.height))

    def leaflet(self, east, north):
        """World X/Z metres, as a point on the map: (lat, lng)."""
        return (self.lat.map(self.uncorrected(north, self.height)),
                self.lng.map(self.uncorrected(east, self.width)))

    def tile(self, east, north, zoom=None):
        """Which tile file holds this world point. Rows count from the top,
        the way generate_tiles.py writes them."""
        if self.tiles is None:
            return None
        zoom = self.max_zoom if zoom is None else zoom
        lat, lng = self.leaflet(east, north)
        step = self.tiles.tile_size / 2 ** zoom
        return int(lng // step), int(lat // step)

    # --- the rest ---------------------------------------------------------
    @property
    def size(self):
        return self.width, self.height

    @property
    def reach(self):
        """The world square the imagery actually covers, corners included."""
        return self.world(*self.leaflet(0, 0)), self.world(*self.leaflet(self.width,
                                                                        self.height))

    def inside(self, east, north):
        """Is this world point on the imagery? The correction pulls the edges
        in, so the last few metres of the island are off the map."""
        (west, south), (far_east, north_edge) = self.corners
        return west <= east <= far_east and south <= north <= north_edge

    @property
    def corners(self):
        """South-west and north-east of what can actually be clicked."""
        low = self.world(self.tile_span[1], 0.0)
        high = self.world(0.0, self.tile_span[0])
        return (min(low[0], high[0]), min(low[1], high[1])), \
               (max(low[0], high[0]), max(low[1], high[1]))

    @property
    def tile_span(self):
        """The map's own extent in Leaflet units at zoom 0: (lng, lat)."""
        return (abs(self.width / self.lng.cof), abs(self.height / self.lat.cof))

    def grid(self, east, north):
        """A world point as the map's own grid readout, e.g. "047 063"."""
        step = 10 ** (5 - self.digits)
        return (f'{int(east // step) % 10 ** self.digits:0{self.digits}d} '
                f'{int(north // step) % 10 ** self.digits:0{self.digits}d}')

    def describe(self):
        """What the browser needs to draw the map, and nothing from disk."""
        shown = {'name': self.name, 'size': [self.width, self.height],
                 'maxZoom': self.max_zoom, 'digits': self.digits,
                 'metresPerUnit': abs(self.lng.cof),
                 'transform': {'lng': {'cof': self.lng.cof, 'offset': self.lng.offset},
                               'lat': {'cof': self.lat.cof, 'offset': self.lat.offset}},
                 'earthCorrection': self.earth_correction,
                 'correction': CORRECTION,
                 'span': list(self.tile_span)}
        if self.tiles:
            shown['tiles'] = {'tileSize': self.tiles.tile_size, 'minZoom': self.tiles.min_zoom}
        if self.picture:
            shown['image'] = True
        return shown


def number(entry, key, default=None):
    value = entry.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise NotCalibrated(f'"{key}" must be a number.')


def axis(entry, key):
    half = (entry or {}).get(key)
    if not isinstance(half, dict):
        raise NotCalibrated(f'"coordinate_transform" needs a "{key}" with cof and offset.')
    cof = number(half, 'cof')
    if cof == 0:
        raise NotCalibrated(f'"{key}.cof" cannot be zero.')
    return Axis(cof=cof, offset=number(half, 'offset', 0))


def settled(where, root=None):
    """A configured location, as an absolute path. Relative to the project
    unless it is already absolute - a tile pyramid is large and may well live
    on another disk."""
    return (Path(root or ROOT) / where).resolve()


def read_tiles(entry, root=None):
    directory = str(entry.get('directory') or '').strip()
    if not directory:
        raise NotCalibrated('Tiles need a "directory" holding the generated pyramid.')
    where = settled(directory, root)
    if not where.is_dir():
        raise NotCalibrated(f'No tiles found at {directory}; '
                            'generate them with generate_tiles.py first.')
    pattern = str(entry.get('pattern') or '{z}/{x}/{y}.webp')
    if not all(part in pattern for part in ('{z}', '{x}', '{y}')):
        raise NotCalibrated('The tile "pattern" needs {z}, {x} and {y} in it.')
    size = int(number(entry, 'tileSize', 256))
    if size < 1:
        raise NotCalibrated('"tileSize" must be a real pixel size.')
    return Tiles(directory=str(where), pattern=pattern, tile_size=size,
                 min_zoom=int(number(entry, 'minZoom', 0)))


def read(entry, root=None):
    """Build a calibration from a map entry, or say why it cannot be built.

    The entry is GeNeFRAG's `all_arma_maps.json` shape, so a map can be copied
    across without translating anything, plus where our own tiles live.
    """
    if not isinstance(entry, dict):
        raise NotCalibrated('The map config must be an object.')
    size = entry.get('size')
    try:
        width, height = (float(v) for v in size)
    except (TypeError, ValueError):
        raise NotCalibrated('The map needs "size": [width, height] in metres.')
    if width <= 0 or height <= 0:
        raise NotCalibrated('The map size must be greater than zero.')
    transform = entry.get('coordinate_transform')
    if not isinstance(transform, dict):
        raise NotCalibrated('The map needs a "coordinate_transform" from all_arma_maps.json.')
    max_zoom = int(number(entry, 'max_zoom', 7))
    if not 0 <= max_zoom <= 22:
        raise NotCalibrated('"max_zoom" must be a sensible number of zoom levels.')
    # The coordinate system is checked before the imagery: a map that cannot
    # say where a click is has a worse problem than a missing picture.
    lng, lat = axis(transform, 'lng'), axis(transform, 'lat')
    tiles = read_tiles(entry['tiles'], root) if isinstance(entry.get('tiles'), dict) else None
    picture = None
    if isinstance(entry.get('image'), dict):
        path = str(entry['image'].get('path') or '').strip()
        if not path:
            raise NotCalibrated('An image map needs a "path".')
        if not settled(path, root).is_file():
            raise NotCalibrated(f'No map image found at {path}.')
        picture = Picture(path=str(settled(path, root)))
    if tiles is None and picture is None:
        raise NotCalibrated('The map has no imagery yet: generate tiles with '
                            'generate_tiles.py, or point "image" at a picture.')
    return Calibration(name=str(entry.get('name', 'Map')), width=width, height=height,
                       max_zoom=max_zoom, lng=lng, lat=lat,
                       earth_correction=bool(entry.get('earth_correction', False)),
                       digits=int(number(entry, 'digits', 3)), tiles=tiles, picture=picture)


@lru_cache(maxsize=4)
def calibration(path=None):
    """The configured map, or None when it is not usable yet."""
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
    if calibrated.picture is None:
        return None
    path = settled(calibrated.picture.path, root)
    return path if path.is_file() else None


def tile_path(calibrated, z, x, y, root=None):
    """One tile, by index.

    The indices come off a request, so they have to be whole numbers inside the
    pyramid and the finished path has to still be under the tile directory -
    two locks, either of which is enough on its own.
    """
    if calibrated.tiles is None:
        return None
    try:
        z, x, y = int(z), int(x), int(y)
    except (TypeError, ValueError):
        return None
    if not calibrated.tiles.min_zoom <= z <= calibrated.max_zoom or x < 0 or y < 0:
        return None
    base = Path(calibrated.tiles.directory).resolve()
    path = (base / calibrated.tiles.pattern.format(z=z, x=x, y=y)).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path
