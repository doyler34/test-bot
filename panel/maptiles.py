"""Downloads the Everon map picture once and cuts it into the tiles the mortar
map draws. The panel's "Download the map" button runs this as its own process,
so the memory it needs can never take the panel down with it.

The tiles are laid out exactly as GeNeFRAG's generate_tiles.py lays them out
({z}/{x}/{y}.webp, 256px, row 0 at the top), because the map's coordinate
transform in assets/mortar/map.json is his and expects his pyramid. Zoom 6 is a
16384px picture: about 0.8 GB in memory and 0.78 m a pixel. The map page
enlarges past that, so a marker still goes down to the metre.

    python -m panel.maptiles [--source URL-or-file] [--zoom N]
"""
import argparse
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "map-tiles"
FINAL = OUT / "everon_sat"
STATUS = OUT / "everon.status.json"
MAPS = "https://raw.githubusercontent.com/GeNeFRAG/ArmaReforger/main/maps_core/all_arma_maps.json"
TILE = 256
ZOOM = 6
RUNNING = ("starting", "downloading", "cutting")
# The picture sits behind Cloudflare, which turns away Python's default User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OYB-Control map download)"}


def write_status(**fields):
    OUT.mkdir(parents=True, exist_ok=True)
    temp = STATUS.with_suffix(".tmp")
    temp.write_text(json.dumps({**fields, "pid": os.getpid(), "at": time.time()}))
    os.replace(temp, STATUS)


def read_status():
    """Where the last download got to. A job that says it's running but whose
    process has gone (the box restarted, it ran out of memory) reads as failed."""
    try:
        status = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {"state": "none"}
    if status.get("state") in RUNNING:
        try:
            os.kill(int(status.get("pid", 0)), 0)
        except (OSError, ValueError):
            return {**status, "state": "failed",
                    "error": "It stopped part way, probably a restart or running out of memory."}
    return status


def fetch(url, timeout):
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{urllib.parse.urlsplit(url).netloc} refused the download (HTTP {exc.code}).") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Couldn't reach {urllib.parse.urlsplit(url).netloc}: {exc.reason}.") from None


def source_url(zoom):
    with fetch(MAPS, 60) as reply:
        maps = json.load(reply)
    entry = next((m for m in maps if m.get("namespace") == "everon"), None)
    image = ((entry or {}).get("resources") or {}).get("map_image", "")
    if not image:
        raise RuntimeError("GeNeFRAG's map list has no Everon picture any more.")
    return re.sub(r"_z\d+_full", f"_z{zoom}_full", image)


def download(url, dest):
    with fetch(url, 120) as reply, open(dest, "wb") as out:
        total = int(reply.headers.get("Content-Length") or 0)
        done, shown = 0, 0.0
        while chunk := reply.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if time.monotonic() - shown > 2:
                shown = time.monotonic()
                write_status(state="downloading", done=done, total=total)


def cut(picture, zoom, final):
    Image.MAX_IMAGE_PIXELS = None
    image = Image.open(picture).convert("RGB")
    width, height = image.size
    size = (1 << (width - 1).bit_length(), 1 << (height - 1).bit_length())
    if size != image.size:
        padded = Image.new("RGB", size, (0, 0, 0))
        padded.paste(image, (0, 0))
        image.close()
        image = padded
    work = final.with_name(final.name + ".partial")
    shutil.rmtree(work, ignore_errors=True)
    total = sum(math.ceil(size[0] / 2 ** (zoom - z) / TILE) * math.ceil(size[1] / 2 ** (zoom - z) / TILE)
                for z in range(zoom + 1))
    made = 0
    level = image
    for z in range(zoom, -1, -1):
        cols, rows = math.ceil(level.width / TILE), math.ceil(level.height / TILE)
        for x in range(cols):
            column = work / str(z) / str(x)
            column.mkdir(parents=True, exist_ok=True)
            for y in range(rows):
                tile = level.crop((x * TILE, y * TILE, min((x + 1) * TILE, level.width),
                                   min((y + 1) * TILE, level.height)))
                if tile.size != (TILE, TILE):
                    padded = Image.new("RGB", (TILE, TILE), (0, 0, 0))
                    padded.paste(tile, (0, 0))
                    tile = padded
                tile.save(column / f"{y}.webp", "WEBP", quality=90, method=4)
                made += 1
            write_status(state="cutting", done=made, total=total)
        if z:
            smaller = level.reduce(2)
            level.close()
            level = smaller
    level.close()
    shutil.rmtree(final, ignore_errors=True)
    os.replace(work, final)
    return made


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="the map picture: a URL or a file (default: GeNeFRAG's)")
    parser.add_argument("--zoom", type=int, default=ZOOM)
    args = parser.parse_args(argv)
    picture = OUT / "everon.download.png"
    try:
        write_status(state="starting")
        source = args.source or source_url(args.zoom)
        if Path(source).is_file():
            picture = Path(source)
        else:
            download(source, picture)
        made = cut(picture, args.zoom, FINAL)
        write_status(state="done", tiles=made, zoom=args.zoom)
    except Exception as exc:
        write_status(state="failed", error=str(exc) or exc.__class__.__name__)
        return 1
    finally:
        if picture.name == "everon.download.png":
            picture.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
