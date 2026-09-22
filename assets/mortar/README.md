# Mortar range tables

`tables.json` holds one entry per tube. Azimuth and range never use this file;
only the elevation does, so a tube with no rows still answers with a bearing
and a distance.

```json
"m252": {
  "name": "M252 81mm (US)",
  "source": "where the numbers came from, so they can be checked",
  "charges": {
    "0": [[100, 1500], [150, 1460], [200, 1415]],
    "1": [[300, 1490], [350, 1455]]
  }
}
```

Each row is `[range in metres, elevation in mils]`, lowest range first. Two
rows are the minimum for a charge to be usable; between rows the bot
interpolates, and outside a charge's first and last row it reports out of
range rather than guessing.

Fill these in from the in-game range table, or by firing at measured ranges on
the server and recording where the rounds land. Put where they came from in
`source` - a table nobody can check is a table nobody should trust.
