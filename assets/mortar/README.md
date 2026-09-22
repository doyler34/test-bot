# Mortar range tables

One entry per weapon. The engine in `bot/mortar/solution.py` knows nothing
about any particular tube - it reads a profile from here and works everything
out from that, so a new mortar is a change to this file and nothing else.

```json
"m252": {
  "name": "M252 81mm",
  "faction": "US",
  "shell": "HE M821",
  "mils": 6400,
  "source": "where the numbers came from, so they can be checked",
  "rings": {
    "2": {"dispersion": 24, "rows": [[200, 1490, 20.6, 12], [300, 1468, 20.5, 12]]}
  }
}
```

- `mils` is that sight's own full circle: 6400 on the M252, 6000 on the 2B14.
  Azimuth is worked out with it, so two tubes can never share a table.
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

| Tube | Faction | Shell | Sight | Rings | Reach |
| --- | --- | --- | --- | --- | --- |
| M252 81mm | US | HE M821 | 6400 mils | 0-4 | 50-2900 m |
| 2B14 Podnos 82mm | USSR | HE O-832DU | 6000 mils | 0-4 | 50-2300 m |

Real-world figures for either weapon do not apply - Reforger's ballistics are
its own. Smoke, illumination and practice rounds have their own tables in that
repository if they are ever wanted here.

## Adding another tube

Add an entry with its own `mils`, `shell` and `rings`, and say in `source`
where the rows came from. It appears in the `/mortar` dropdown on the next
restart; no code changes.
