#!/usr/bin/env bash
# Generate the mortar map's tiles on the box that will serve them.
#
# The work is GeNeFRAG's: this clones his ArmaReforger project (MIT) and runs
# its own generate_tiles.py, which downloads the map image and cuts the tile
# pyramid. We host the result ourselves - the bot never fetches anything from
# another site while it is running.
#
# Usage: bash dev/fetch_map_tiles.sh [map] [zoom] [output]
#   map     map namespace, default everon
#   zoom    deepest zoom level, default whatever the map's own entry says.
#           Everon ships as 7, which means a 32768px image: a 632MB download
#           and about 3.2GB of memory while it is cut up. Zoom 6 is a 16384px
#           image, 0.8GB of memory, and still 0.78m per pixel - far finer than
#           anyone needs to drop a marker. Use 6 unless the box is roomy.
#   output  where the pyramid lands, default data/map-tiles
#
# Whatever zoom is generated must match "max_zoom" in assets/mortar/map.json;
# this prints a reminder at the end.
set -euo pipefail
MAP="${1:-everon}"
ZOOM="${2:-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/${3:-data/map-tiles}"
WORK="${TMPDIR:-/tmp}/genefrag-maps"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
command -v git >/dev/null || { echo "git is required"; exit 1; }
python3 -c 'import PIL, requests' 2>/dev/null || {
  echo "Pillow and requests are needed. Install them, or run this with a venv on PATH:"
  echo "  python3 -m venv /root/maptiles && /root/maptiles/bin/pip install Pillow requests"
  echo "  source /root/maptiles/bin/activate"
  exit 1
}

[ -d "$WORK/.git" ] || git clone --depth 1 https://github.com/GeNeFRAG/ArmaReforger "$WORK"
git -C "$WORK" pull --ff-only || true

if [ -n "$ZOOM" ]; then
  echo "Setting $MAP to zoom $ZOOM in the generator's own map list"
  MAP="$MAP" ZOOM="$ZOOM" python3 - "$WORK/maps_core/all_arma_maps.json" <<'PY'
import json, os, re, sys
path, namespace, zoom = sys.argv[1], os.environ['MAP'], int(os.environ['ZOOM'])
maps = json.load(open(path))
entry = next((m for m in maps if m.get('namespace') == namespace), None)
if entry is None:
    raise SystemExit(f'No map called {namespace} in the generator map list')
entry['max_zoom'] = zoom
image = entry.get('resources', {}).get('map_image', '')
if image:
    entry['resources']['map_image'] = re.sub(r'_z\d+_full', f'_z{zoom}_full', image)
    print('Source image:', entry['resources']['map_image'])
json.dump(maps, open(path, 'w'), indent=2)
PY
fi

mkdir -p "$OUT"
echo "Generating $MAP tiles into $OUT - this is a large download and a long job"
( cd "$WORK/maps_core" && python3 generate_tiles.py "$MAP" )

SRC="$WORK/maps_core/tiles/${MAP}_sat"
[ -d "$SRC" ] || { echo "No tiles produced at $SRC"; exit 1; }
rm -rf "${OUT:?}/${MAP}_sat"
mv "$SRC" "$OUT/${MAP}_sat"

DEEPEST="$(ls "$OUT/${MAP}_sat" | sort -n | tail -1)"
echo
echo "Done: $OUT/${MAP}_sat"
du -sh "$OUT/${MAP}_sat"
echo "Deepest zoom generated: $DEEPEST"
echo "Set \"max_zoom\": $DEEPEST and tiles.directory to that path in assets/mortar/map.json, then restart the bot."
