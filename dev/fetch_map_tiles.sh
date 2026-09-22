#!/usr/bin/env bash
# Generate the mortar map's Everon tiles on the box that will serve them.
#
# The work is GeNeFRAG's: this clones his ArmaReforger project (MIT) and runs
# its own generate_tiles.py, which downloads the map image and cuts the tile
# pyramid. We host the result ourselves - the bot never fetches anything from
# another site while it is running.
#
# Usage: bash dev/fetch_map_tiles.sh [map] [output]
#   map     map namespace, default everon
#   output  where the pyramid lands, default data/map-tiles
#
# Everon is a big download and a long job. Expect hundreds of megabytes and
# tens of thousands of files; run it in tmux or screen.
set -euo pipefail
MAP="${1:-everon}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/${2:-data/map-tiles}"
WORK="${TMPDIR:-/tmp}/genefrag-maps"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
python3 -c 'import PIL, requests' 2>/dev/null || python3 -m pip install --quiet Pillow requests

[ -d "$WORK/.git" ] || git clone --depth 1 https://github.com/GeNeFRAG/ArmaReforger "$WORK"
mkdir -p "$OUT"

echo "Generating $MAP tiles into $OUT (this takes a while)"
( cd "$WORK/maps_core" && python3 generate_tiles.py "$MAP" )

# generate_tiles.py writes tiles/<map>_sat/ next to itself; move it to ours.
SRC="$WORK/maps_core/tiles/${MAP}_sat"
[ -d "$SRC" ] || { echo "No tiles produced at $SRC"; exit 1; }
rm -rf "${OUT:?}/${MAP}_sat"
mv "$SRC" "$OUT/${MAP}_sat"

echo "Done: $OUT/${MAP}_sat"
echo "Point assets/mortar/map.json -> tiles.directory at that path, then restart the bot."
du -sh "$OUT/${MAP}_sat"
