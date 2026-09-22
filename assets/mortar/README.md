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
