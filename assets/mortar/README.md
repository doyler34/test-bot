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

`/mortar` works from typed grids with no map at all. Add map imagery and it
also offers a page you tap instead - same engine, same tables, just a different
way in.

The page works in the game's own world X/Z metres from the click onwards.
There is no eyeballing and no two-point calibration to do: the coordinate
system is fixed by how the tiles are generated, and it is already written down
below for Everon.

```json
{
  "name": "Everon",
  "digits": 3,
  "world": { "size": 12800, "offset": 50 },
  "tiles": {
    "directory": "assets/mortar/everon",
    "pattern": "{z}/{x}/{y}/tile.jpg",
    "tileSize": 256,
    "maxZoom": 5,
    "metresPerTile": 100,
    "invertY": true
  }
}
```

- `world.size` is the island, metres on a side. Bohemia document Everon as
  12.8 km square.
- `world.offset` is half a tile. A generated tile is named for the camera at
  its **centre**, not its corner, so world coordinates carry half a tile on the
  way into the map and lose it on the way out.
- `tiles.metresPerTile` is the ground one tile covers at the deepest zoom - one
  downward screenshot, 100 m for a Reforger set.
- `tiles.maxZoom` is how many LOD levels there are above LOD 0.
- `tiles.invertY` because tile rows count upwards with Z while Leaflet counts
  them down.
- `digits` is how many digits a grid readout uses per axis: 3 gives `047 063`,
  100 m squares, the same rule the typed grids follow.

The map scale is **not** a constant anybody chose. It falls out of the above:

    metres per map unit at zoom 0 = metresPerTile * 2 ** maxZoom / tileSize

which for a Reforger tile set is `100 * 32 / 256` = exactly **12.5**. Change
the tile size or the number of LODs and the scale follows on its own.

### Getting the tiles

The tiles are generated from the game itself with
[EnfusionMapMaker](https://github.com/nickludlam/EnfusionMapMaker): its
Enfusion Workbench tool drives the camera over the island taking one downward
screenshot per 100 m, then `Scripts/crop_screenshots.py` cuts them to tiles and
`Scripts/create_zoom_levels.py` builds the LODs above. Point `directory` at the
result - the folder holding `0/`, `1/` … - and the page works. It can be an
absolute path; a full Everon set is a lot of files.

No tiles yet means no map page: `/mortar` keeps to its grid boxes and the log
says what is missing, rather than drawing on nothing.

### An image instead of tiles

A single picture works too, and still needs no reference points - the world
square is the calibration and the image is stretched over it:

```json
{ "name": "Everon", "world": { "size": 12800, "offset": 0 },
  "image": { "path": "assets/mortar/everon.jpg" } }
```

Add `"southWest"` and `"northEast"` in world metres if the picture covers only
part of the island. Tiles are better on a phone - a full-island image has to be
downloaded whole before anything is drawn - but an image needs no game tools.

### Credit and licensing

The coordinate scheme, and the tooling that generates the tiles, come from
[EnfusionMapMaker](https://github.com/nickludlam/EnfusionMapMaker) by Nick
Ludlam, published under the
[Arma Public License Share Alike](https://www.bohemia.net/community/licenses/arma-public-license-share-alike).
Tiles generated with it are made from Bohemia's game content and carry the same
licence: non-commercial, Arma-only, share-alike, with attribution. Keep that
attribution wherever the map is served, and do not redistribute a tile set
under anything else.

Do not point `directory` at somebody else's live tile server. Generate a set,
host it yourself.
