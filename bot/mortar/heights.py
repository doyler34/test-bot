"""Everon's ground height anywhere on the map, so a shot uphill or downhill gets
its correction without anyone typing altitudes.

The heights are GeNeFRAG's (ArmaReforger, MIT): a JSON grid with a value every
10 m, published beside the map picture he hosts. It is read the way his own
mapEngine.js reads it,

    map[round(x / cell)][round((size - z) / cell)]

so the outer list runs east and the inner one runs south from the top. Here
that grid is fetched once, kept as a compact binary file beside the map tiles,
and read between the four nearest points rather than snapped to one.
"""
import json
import os
import struct
import time
import urllib.error
import urllib.request
from array import array
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "data" / "map-tiles" / "everon_height.bin"
MAPS = "https://raw.githubusercontent.com/GeNeFRAG/ArmaReforger/main/maps_core/all_arma_maps.json"
SIZE = 12800
MAGIC = b"OYBH"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OYB-Control map download)"}


class Heights:
    def __init__(self, columns, rows, values, size=SIZE):
        self.columns, self.rows, self.values, self.size = columns, rows, values, size
        self.cell = (size / columns, size / rows)

    def _value(self, i, j):
        i = min(max(i, 0), self.columns - 1)
        j = min(max(j, 0), self.rows - 1)
        return self.values[i * self.rows + j]

    def at(self, east, north):
        """Ground height in metres at a world X/Z point."""
        x = east / self.cell[0]
        y = (self.size - north) / self.cell[1]
        i, j = int(x // 1), int(y // 1)
        fx, fy = x - i, y - j
        top = self._value(i, j) * (1 - fx) + self._value(i + 1, j) * fx
        bottom = self._value(i, j + 1) * (1 - fx) + self._value(i + 1, j + 1) * fx
        return top * (1 - fy) + bottom * fy


def save(grid, path=None):
    path = Path(path or FILE)
    """GeNeFRAG's grid, written as a header and float32 values."""
    columns, rows = len(grid), len(grid[0])
    values = array("f", (float(v or 0) for column in grid for v in column))
    if len(values) != columns * rows:
        raise ValueError("The height grid isn't square.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    with open(temp, "wb") as out:
        out.write(MAGIC + struct.pack("<II", columns, rows))
        values.tofile(out)
    os.replace(temp, path)


@lru_cache(maxsize=1)
def _load(path, stamp):
    with open(path, "rb") as fh:
        if fh.read(4) != MAGIC:
            return None
        columns, rows = struct.unpack("<II", fh.read(8))
        values = array("f")
        values.fromfile(fh, columns * rows)
    return Heights(columns, rows, values)


def load(path=None):
    """The heights, or None until they've been fetched."""
    path = Path(path or FILE)
    try:
        return _load(str(path), path.stat().st_mtime)
    except (OSError, EOFError, struct.error):
        return None


def at(east, north, path=None):
    heights = load(path)
    return None if heights is None else round(heights.at(east, north), 1)


def climb(gun, target, path=None):
    """How much higher the target stands than the gun, or None without heights."""
    heights = load(path)
    if heights is None:
        return None
    return heights.at(*target) - heights.at(*gun)


def _get(url, timeout):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.load(reply)


def fetch(path=None, namespace="everon"):
    """Download the height grid once. Raises on anything that goes wrong."""
    entry = next((m for m in _get(MAPS, 60) if m.get("namespace") == namespace), None)
    url = ((entry or {}).get("resources") or {}).get("height_data")
    if not url:
        raise RuntimeError("GeNeFRAG's map list has no height data for this map.")
    save(_get(url, 300), path)


_tried = [0.0]


def ensure(path=None, every=3600):
    """Fetch the heights if they aren't here, at most once an hour. Meant for a
    background thread; never raises."""
    if load(path) is not None or time.monotonic() - _tried[0] < every and _tried[0]:
        return
    _tried[0] = time.monotonic()
    try:
        fetch(path)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        import logging
        logging.getLogger(__name__).warning("Couldn't fetch the map heights: %s", exc)
