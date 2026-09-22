# /mortar

Two grids in, a firing solution out. Everything is worked out from the game's
own range tables in `assets/mortar/tables.json`; see that folder's README for
the tables themselves and where they came from.

## In Discord

`/mortar` opens a box for the gun grid, the target grid and, optionally, both
altitudes. The reply is private and carries the azimuth, the range, the ring to
use with its flight time and dispersion, the higher rings underneath, and a
plot of the two points.

With the map page turned on, `/mortar` instead offers **🗺️ Open Mortar Map**
and an **Enter grids instead** button, so both ways in stay available.

## The map page

A page served by the bot itself: tap your mortar, tap the target, read the
solution. Markers drag, the target moves on the next tap while the gun stays
put, and the dashed ring is the selected round's own maximum range.

The page does no ballistics. It sends two world positions and a loadout, and
the bot answers from the same engine the Discord command uses. There is one
ballistic implementation and one set of tables.

It works in Reforger's own world X/Z metres throughout - no latitude, no
longitude, no geographic projection. The map layer, including the Everon
coordinate transform, is GeNeFRAG's from
[ArmaReforger](https://github.com/GeNeFRAG/ArmaReforger);
`bot/mortar/calibration.py` implements it in both directions and the tests
check it against known Everon positions. Tiles are served from our own disk.

### Routes

| Route | What it is |
| --- | --- |
| `GET /mortar/{token}` | the page, for a link that has not expired |
| `GET /mortar/{token}/map` | the map picture, when an image is configured |
| `GET /mortar/{token}/tiles/{z}/{x}/{y}` | one map tile, when tiles are configured |
| `GET /mortar/{token}/loadouts` | tubes, rounds, each round's reach, map calibration |
| `POST /api/mortar/calculate` | a firing solution |
| `GET /static/{name}` | the page's own stylesheet and script |

`POST /api/mortar/calculate` takes:

```json
{"token": "...", "tube": "m252", "round": "smoke",
 "gun": {"east": 4500, "north": 10776}, "target": {"east": 5200, "north": 10100},
 "climb": 0}
```

`east` and `north` are the game's own world X and Z in metres - the same
numbers the map shows - and they must be on the island. `climb` is how much
higher the target is than the gun, in metres.

It answers:

```json
{"valid": true, "tube": "m252", "round": "smoke", "range_m": 1209,
 "azimuth_mils": 2418, "ring": "2", "elevation_mils": 1059,
 "tof_seconds": 21, "dispersion_m": 20,
 "mortar_grid": "047 063", "target_grid": "052 071"}
```

and when nothing reaches:

```json
{"valid": false, "reason": "out_of_range", "min_range_m": 50, "max_range_m": 1600}
```

The limits are the loaded round's own, not the tube's.

### Links

Each `/mortar` hands out a fresh link with a random token that expires after
`MORTAR_SESSION_TTL` (an hour by default). The link carries nothing else - no
bot token, no account details - and the server forgets it when it runs out. An
expired or unknown link gets a plain "this link has expired" page.

## Turning it on

1. Generate the Everon tiles on the box that will serve them:
   `bash dev/fetch_map_tiles.sh everon`. Everon's coordinate system is already
   in `assets/mortar/map.json` - GeNeFRAG's own map entry - so there is nothing
   to calibrate. See `assets/mortar/README.md`, including what the imagery's
   licensing does and does not cover. Without tiles the page stays off.
2. Set `MORTAR_WEB_ENABLED=true` and `MORTAR_WEB_BASE_URL` in `.env`.
3. Point a reverse proxy at `MORTAR_WEB_HOST:MORTAR_WEB_PORT` (127.0.0.1:8085
   by default) and give it HTTPS. The bot listens on loopback only, so the
   proxy is what players actually reach.
4. Restart the bot.

With no base URL, or no usable map config, `/mortar` carries on with its grid
boxes and the page is never served. The reason is written to the log at start.
