# Mortar range tables

`tables.json` holds one entry per tube. Azimuth and range never use this file -
they come straight from the grids - but everything about elevation does.

```json
"m252": {
  "name": "M252 81mm (US)",
  "shell": "HE M821",
  "mils": 6400,
  "source": "where the numbers came from, so they can be checked",
  "rings": {
    "2": {"dispersion": 24, "rows": [[200, 1490, 20.6, 12], [300, 1468, 20.5, 12]]}
  }
}
```

- `mils` is the tube's own circle: 6400 on the M252 sight, 6000 on the 2B14.
  Azimuth is worked out with it, so the two must never share a table.
- Each row is `[range m, elevation mils, time of flight s, mils per 100 m of
  height difference]`, lowest range first.
- Between two rows every column is interpolated. Outside a ring's first and
  last row that ring does not reach, and when no ring reaches, the answer is
  OUT OF RANGE. Nothing is ever extrapolated past the ends of a ring.
- Height: a target above the gun is met earlier in the shell's fall, so the
  tube has to throw further and the elevation comes down. The per-100 m column
  is what that correction is worked out from.

## Where the M252 numbers came from

Copied out of the game by vova_4104 and published at
github.com/147888sf/ArmA-Reforger-mortar-calculator (commit 89f8c21), whose
readme grants reuse. HE M821, rings 0-4, 50-2900 m. Real-world M252 data does
not apply - Reforger's ballistics are its own.

The 2B14 entry is deliberately empty until its own table is put in. It fires
O-832DU on a 6000 mil circle and must not borrow anything from the M252.
