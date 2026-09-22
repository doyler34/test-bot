# Mortar range tables

One entry per weapon. The engine in `bot/mortar/solution.py` knows nothing
about any particular tube - it reads a profile from here and works everything
out from that, so a new mortar is a change to this file and nothing else.

```json
"m252": {
  "name": "M252 81mm",
  "faction": "US",
  "mils": 6400,
  "source": "where the numbers came from, so they can be checked",
  "shells": {
    "he": {
      "name": "HE M821",
      "rings": {
        "2": {"dispersion": 24, "rows": [[200, 1490, 20.6, 12], [300, 1468, 20.5, 12]]}
      }
    }
  }
}
```

- `mils` is that sight's own full circle: 6400 on the M252, 6000 on the 2B14.
  Azimuth is worked out with it, so two tubes can never share a table.
- Each round under `shells` carries its own rings and its own reach - smoke
  does not fly like HE. Use the same round keys across tubes (`he`, `smoke`,
  `illum`) and changing tube keeps the round in hand.
- Each row is `[range m, elevation mils, time of flight s, mils per 100 m of
  height difference]`, lowest range first. A ring needs two rows to be usable.
- Between two rows every column is interpolated. Outside a ring's first and
  last row that ring does not reach, and when no ring reaches, the answer is
  OUT OF RANGE. Nothing is ever extrapolated past the ends of a ring.
- The lowest ring that reaches is the one offered - tightest group, shortest
  flight - with the higher rings listed under it for a steeper arc.
- Height: a target above the gun is met earlier in the shell's fall, so the
  tube has to throw further and the elevation comes down. The per-100 m column
  is what that correction is worked out from.

## Where the numbers came from

Both tables were copied out of the game by vova_4104 and published at
github.com/147888sf/ArmA-Reforger-mortar-calculator (commit 89f8c21), whose
readme grants reuse:

| Tube | Faction | Sight | Round | Rings | Reach |
| --- | --- | --- | --- | --- | --- |
| M252 81mm | US | 6400 mils | HE M821 | 0-4 | 50-2900 m |
| M252 81mm | US | 6400 mils | Smoke M819 | 1-4 | 200-2400 m |
| M252 81mm | US | 6400 mils | Illumination M853A1 | 1-4 | 200-2400 m |
| 2B14 Podnos 82mm | USSR | 6000 mils | HE O-832DU | 0-4 | 50-2300 m |
| 2B14 Podnos 82mm | USSR | 6000 mils | Smoke D-832DU | 0-3 | 50-1600 m |
| 2B14 Podnos 82mm | USSR | 6000 mils | Illumination S-832C | 1-4 | 100-2200 m |

Real-world figures for either weapon do not apply - Reforger's ballistics are
its own. Practice rounds (M879) have their own table in that repository if
they are ever wanted here.

## Adding another tube

Add an entry with its own `mils` and `shells`, and say in `source` where the
rows came from. It appears in the `/mortar` dropdowns on the next restart; no
code changes. A round whose rings hold fewer than two rows is left out rather
than offered with nothing behind it.

## The map page (`map.json`)

`/mortar` works from typed grids with no map at all. Add map tiles and it also
offers a page you tap instead - same engine, same tables, just a different way
in.

The map layer is **GeNeFRAG's**, from
[ArmaReforger](https://github.com/GeNeFRAG/ArmaReforger) (MIT, © 2025 Gerhard
Fröhlich). `map.json` is his `maps_core/all_arma_maps.json` entry copied as it
stands, plus where our own tiles live - so there is nothing to recalibrate and
nothing to measure:

```json
{
  "name": "Everon",
  "namespace": "everon",
  "size": [12800, 12800],
  "max_zoom": 7,
  "coordinate_transform": {
    "lng": { "cof": 50.0, "offset": 0.0 },
    "lat": { "cof": -50.0, "offset": -256.0 }
  },
  "earth_correction": true,
  "digits": 3,
  "tiles": { "directory": "data/map-tiles/everon_sat",
             "pattern": "{z}/{x}/{y}.webp", "tileSize": 256 }
}
```

`mapEngine.js` reads that transform as

    gameX = (lng + lng.offset) * lng.cof
    gameZ = (lat + lat.offset) * lat.cof

and `earth_correction` adds a pull towards the middle worth 100 m across the
span and nothing at the centre. `bot/mortar/calibration.py` implements both
directions and the tests check them: Everon's centre lands on exactly
**6400, 6400**, and the imagery runs from **50 m to 12750 m** on each axis -
the outermost 50 m of the island is not clickable, which is the correction
doing its job rather than a bug.

- `size` is the island in metres.
- `max_zoom` is the deepest zoom the pyramid was built to. Everon at z7 is
  128×128 tiles at the bottom level.
- `digits` is how many digits a grid readout uses per axis: 3 gives `047 063`.
- `tiles.directory` may be absolute; a full pyramid is a lot of files.

### Getting the tiles

```
bash dev/fetch_map_tiles.sh everon
```

That clones GeNeFRAG's project and runs **his** `maps_core/generate_tiles.py`,
which downloads the map image and cuts the pyramid, then moves the result to
`data/map-tiles/everon_sat`. Run it on the box that will serve the map, in
tmux - it is a large download and a long job. Tiles are gitignored: they are
generated where they are served, never committed.

Once they exist, restart the bot. Until then the page stays off and `/mortar`
keeps to its grid boxes, with the reason in the log.

Nothing is fetched from another site while the bot runs - the tiles are served
from our own disk.

### An image instead of tiles

A single picture works too, with the same transform:

```json
{ ...same map entry..., "image": { "path": "assets/mortar/everon.jpg" } }
```

Worse zoom quality and a slower first load, but it needs no generation step.

### Credit and licensing

- **Coordinate system and tile tooling**: GeNeFRAG's
  [ArmaReforger](https://github.com/GeNeFRAG/ArmaReforger), MIT, © 2025 Gerhard
  Fröhlich. Keep that credit.
- **The map imagery itself is a different matter.** It is a render of Bohemia
  Interactive's Everon terrain, downloaded from the project's CDN. An MIT
  licence covers that project's code, not Bohemia's content, and neither the
  repository nor the CDN states terms for the imagery. Bohemia's own
  [APL-SA](https://www.bohemia.net/community/licenses/arma-public-license-share-alike)
  is what normally covers game-derived assets: non-commercial, Arma-only,
  share-alike, with attribution - which a community Discord bot fits, but we
  are asserting that rather than being granted it.

  Treat the tiles accordingly: keep them non-commercial and Arma-only, credit
  both Bohemia and GeNeFRAG wherever the map is shown, and do not redistribute
  the set. If in doubt, ask Gerhard Fröhlich - he is contactable through the
  repository and through armamortars.org.
