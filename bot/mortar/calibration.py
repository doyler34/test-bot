"""Where a click on the map is, in the coordinates the engine fires in.

Enfusion maps are not geographic, and this does not pretend otherwise: the page
works in the game's own world X/Z metres throughout, and Leaflet is bent to
match rather than the other way round.

The scheme is the one EnfusionMapMaker uses for its Reforger tile sets
(https://github.com/nickludlam/EnfusionMapMaker, APL-SA), because that is how
tiles from its tooling are laid out:

  * a tile at the deepest zoom is one downward screenshot covering
    `metres_per_tile` metres of world, and Leaflet draws it `tile_size` pixels
    across;
  * the camera sits at the CENTRE of that tile, so a tile's index times its
    width names the world point in its middle - which is why world coordinates
    are carried with half a tile added, `offset` below;
  * tile rows count upwards with Z, where Leaflet counts them down.

From that, the scale is arithmetic rather than opinion:

    metres per Leaflet unit = metres_per_tile * 2 ** max_zoom / tile_size

For a Reforger set - 100 m tiles, 256 px, six LODs - that is exactly 12.5.
(EnfusionMapMaker eyeballed 12.501, which is a metre out across Everon.)

Nothing here is Everon-specific. Another island is another entry in map.json.
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
class Tiles:
    """A pyramid of tiles generated from the game."""
    directory: str
    pattern: str            # how a tile is named under that directory
    tile_size: int
    max_zoom: int
    metres_per_tile: float  # world metres one tile covers at max zoom
    invert_y: bool = True   # tile rows count up with Z, Leaflet counts down

    @property
    def scale(self):
        """World metres per Leaflet unit at zoom 0."""
        return self.metres_per_tile * 2 ** self.max_zoom / self.tile_size


@dataclass(frozen=True)
class Picture:
    """A single image stretched over a patch of the world."""
    path: str
    south_west: tuple
    north_east: tuple


@dataclass(frozen=True)
class Calibration:
    name: str
    size: float             # the world square, metres on a side
    offset: float           # half a tile, because tiles are centred on the camera
    digits: int = 3
    tiles: Tiles = None
    picture: Picture = None

    @property
    def scale(self):
        """World metres per Leaflet unit at zoom 0. An untiled map is drawn at
        one unit per metre, which needs no scaling at all."""
        return self.tiles.scale if self.tiles else 1.0

    # --- world <-> the map, the one definition the page mirrors -----------
    def leaflet(self, east, north):
        """World X/Z to the map's own coordinates.

        Leaflet wants (lat, lng) and gets (Z, X): the axes are swapped, and
        half a tile is added because a tile is named for the camera at its
        centre, not for its corner.
        """
        return north + self.offset, east + self.offset

    def world(self, lat, lng):
        """Back the other way: map coordinates to world X/Z."""
        return lng - self.offset, lat - self.offset

    def projected(self, east, north, zoom=0):
        """Where a world point lands in pixels at a zoom level. North is up, so
        the vertical axis is negated the way Leaflet expects."""
        lat, lng = self.leaflet(east, north)
        factor = 2 ** zoom / self.scale
        return lng * factor, -lat * factor

    def unprojected(self, x, y, zoom=0):
        """And back from pixels to world X/Z."""
        factor = self.scale / 2 ** zoom
        return self.world(-y * factor, x * factor)

    def tile(self, east, north, zoom=None):
        """Which tile file holds this world point.

        Leaflet's row index counts downwards, the generated tiles count upwards
        with Z, so the row is flipped here exactly as the tile layer flips it.
        """
        if self.tiles is None:
            return None
        zoom = self.tiles.max_zoom if zoom is None else zoom
        x, y = self.projected(east, north, zoom)
        size = self.tiles.tile_size
        row = int(y // size)
        return int(x // size), -(row + 1) if self.tiles.invert_y else row

    def inside(self, east, north):
        """Is this world point on the island? Half a tile of slack at the edge,
        which is where the outermost tile centres sit."""
        edge = self.offset
        return (-edge <= east <= self.size + edge) and (-edge <= north <= self.size + edge)

    def grid(self, east, north):
        """A world point as the map's own grid readout, e.g. "047 063"."""
        step = 10 ** (5 - self.digits)
        return (f'{int(east // step) % 10 ** self.digits:0{self.digits}d} '
                f'{int(north // step) % 10 ** self.digits:0{self.digits}d}')

    @property
    def bounds(self):
        """World corners: south-west then north-east."""
        return (0.0, 0.0), (float(self.size), float(self.size))

    def describe(self):
        """What the browser needs to draw the map. No paths: the tile route and
        the image route are served by name, not by anything from disk."""
        shown = {'name': self.name, 'size': self.size, 'offset': self.offset,
                 'digits': self.digits, 'scale': self.scale}
        if self.tiles:
            shown['tiles'] = {'tileSize': self.tiles.tile_size,
                              'maxZoom': self.tiles.max_zoom,
                              'metresPerTile': self.tiles.metres_per_tile,
                              'invertY': self.tiles.invert_y}
        if self.picture:
            shown['image'] = {'southWest': list(self.picture.south_west),
                              'northEast': list(self.picture.north_east)}
        return shown


def positive(entry, key, default=None):
    value = entry.get(key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise NotCalibrated(f'"{key}" must be a number.')
    if value <= 0:
        raise NotCalibrated(f'"{key}" must be greater than zero.')
    return value


def corner(entry, key, fallback):
    value = entry.get(key)
    if value is None:
        return fallback
    try:
        east, north = (float(v) for v in value)
    except (TypeError, ValueError):
        raise NotCalibrated(f'"{key}" must be [east, north].')
    return east, north


def settled(where, root=None):
    """A configured location, as an absolute path.

    Relative to the project by default, so the usual config needs no absolute
    paths; absolute is allowed because a tile pyramid is large and may well
    live on another disk. This is config, written by whoever runs the bot - it
    is the request-driven part, the tile indices, that is pinned down.
    """
    return (Path(root or ROOT) / where).resolve()


def read_tiles(entry, root=None):
    directory = str(entry.get('directory') or '').strip()
    if not directory:
        raise NotCalibrated('Tiles need a "directory" holding the generated pyramid.')
    settled_directory = settled(directory, root)
    if not settled_directory.is_dir():
        raise NotCalibrated(f'No tiles found at {directory}; generate them first.')
    tiles = Tiles(directory=str(settled_directory),
                  pattern=str(entry.get('pattern') or '{z}/{x}/{y}/tile.jpg'),
                  tile_size=int(positive(entry, 'tileSize', 256)),
                  max_zoom=int(entry.get('maxZoom', 5)),
                  metres_per_tile=positive(entry, 'metresPerTile', 100),
                  invert_y=bool(entry.get('invertY', True)))
    if tiles.max_zoom < 0 or tiles.max_zoom > 22:
        raise NotCalibrated('"maxZoom" must be a sensible number of zoom levels.')
    if '{z}' not in tiles.pattern or '{x}' not in tiles.pattern or '{y}' not in tiles.pattern:
        raise NotCalibrated('The tile "pattern" needs {z}, {x} and {y} in it.')
    return tiles


def read_picture(entry, size, root=None):
    path = str(entry.get('path') or '').strip()
    if not path:
        raise NotCalibrated('An image map needs a "path".')
    if not settled(path, root).is_file():
        raise NotCalibrated(f'No map image found at {path}.')
    path = str(settled(path, root))
    south_west = corner(entry, 'southWest', (0.0, 0.0))
    north_east = corner(entry, 'northEast', (float(size), float(size)))
    if north_east[0] <= south_west[0] or north_east[1] <= south_west[1]:
        raise NotCalibrated('"northEast" must be north and east of "southWest".')
    return Picture(path=path, south_west=south_west, north_east=north_east)


def read(entry, root=None):
    """Build a calibration from a plain dict, or say why it cannot be built.

    The coordinate system stands on its own; what is missing in practice is the
    imagery, and that is said plainly rather than drawn on empty space.
    """
    if not isinstance(entry, dict):
        raise NotCalibrated('The map config must be an object.')
    world = entry.get('world')
    if not isinstance(world, dict):
        raise NotCalibrated('The map config needs a "world" block with its size in metres.')
    size = positive(world, 'size')
    offset = world.get('offset', 0)
    try:
        offset = float(offset)
    except (TypeError, ValueError):
        raise NotCalibrated('"offset" must be a number of metres.')
    tiles = read_tiles(entry['tiles'], root) if isinstance(entry.get('tiles'), dict) else None
    picture = (read_picture(entry['image'], size, root)
               if isinstance(entry.get('image'), dict) else None)
    if tiles is None and picture is None:
        raise NotCalibrated('The map has no imagery yet: add "tiles" (generated with '
                            'EnfusionMapMaker) or an "image".')
    return Calibration(name=str(entry.get('name', 'Map')), size=size, offset=offset,
                       digits=int(entry.get('digits', 3)), tiles=tiles, picture=picture)


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
    if not 0 <= z <= calibrated.tiles.max_zoom or x < 0 or y < 0:
        return None
    base = Path(calibrated.tiles.directory).resolve()
    path = (base / calibrated.tiles.pattern.format(z=z, x=x, y=y)).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path
