# OYB Mortar Test

A tiny Reforger mod for checking the OYB mortar calculator against the real
game. Fire a round and it tells you, on screen and in the log, exactly where it
was fired from and where it landed. Paste the log lines into the **Test shots**
box under the solution on the OYB mortar map, and it marks each landing spot
and says how far long or short, left or right of your target it was.

Every mortar round is tracked automatically, US and Russian. It's for testing
in Workbench on your own PC: it doesn't need publishing, and it shouldn't go on
the live servers.

## Set up (once)

1. **Install the tools.** Steam → Library → switch *Games* to *Tools* →
   install **Arma Reforger Tools**.
2. **Get this folder.** On GitHub press **Code → Download ZIP**, unzip it, and
   copy the `mod/OYB_MortarTest` folder to `Documents\OYB_MortarTest`.
3. **Open it.** Launch Arma Reforger Tools, press **Add Existing**, pick
   `OYB_MortarTest.gproj` in that folder, and double-click it.

If a red error box appears while it opens, screenshot it and send it over.

## Fire a test shot

1. In the **Resource Browser** (bottom left) search `GM_Everon`, double-click
   that world, then press **F5** to play.
2. As Game Master, place a mortar, an ammo box with mortar rounds, and a
   soldier; take control of the soldier.
3. Work out a shot on the OYB mortar map (put your markers where the mortar and
   target really are), set the sight, and fire.
4. When it lands a box pops up: where it landed, how far it went, and the angle
   it left the tube at.
5. In the **Log Console** (bottom of the window) type `OYB_MORTAR` in the
   filter, copy the lines, and paste them into **Test shots** on the map.

Three or four shots per ring at different ranges, flat ground and no wind,
tells us everything. Then try a couple uphill, downhill and with wind.

## If nothing pops up after firing

Send a screenshot of the Log Console straight after firing. As a fallback the
tracker can be put on a round by hand: search `M821` in the Resource Browser,
right-click → **Override in OYB_MortarTest**, open the copy, **Add Component**
→ **OYB_ShotTrackerComponent**, Ctrl+S.

## Sending the game's own mortar data

The first time a mortar round appears in the game (place an ammo box with
rounds, or the mortar), the mod copies every mortar data file it can find in
the game into `Documents\My Games\ArmaReforgerWorkbench\profile\OYB_Manual`.
Zip that folder and send it over: it's what the in-game ballistic manual is
built from, every ring of every round. Every weapon file (guns, rounds,
magazines, suppressors) goes into `OYB_Weapons` next to it, for the bullet drop
guide. The Log Console lists each file with `OYB_MANUAL` in front, and ends
with how many were copied.

If Workbench ever shows a red error about `OYB_ManualDump`, delete
`Scripts/Game/OYB/OYB_ManualDump.c`: the shot tracking works without it.
