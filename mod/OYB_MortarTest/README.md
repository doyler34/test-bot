# OYB Mortar Test

A tiny Reforger mod for checking the OYB mortar calculator against the real
game. Fire a round and it tells you, on screen and in the log, exactly where it
was fired from and where it landed. Paste the log lines into the **Test shots**
box under the solution on the OYB mortar map, and it marks each landing spot
and says how far long or short, left or right of your target it was.

It's for testing in Workbench on your own PC. It doesn't need publishing, and
it shouldn't go on the live servers.

## Set up (once)

1. **Get the tools.** Steam → Library → change *Games* to *Tools* → install
   **Arma Reforger Tools** and launch it.
2. **Get this folder onto your PC.** Download the repo as a ZIP from GitHub and
   unzip it; you want the `mod/OYB_MortarTest` folder. Put it somewhere simple,
   like `Documents\OYB_MortarTest`.
3. **Open it.** In the Workbench launcher press **Add Existing**, pick
   `OYB_MortarTest.gproj` in that folder, then open it.
4. **Put the tracker on the shells.** In the **Resource Browser** search for
   `M821` (the US 81mm HE round). Right-click its prefab → **Override in
   OYB_MortarTest**. Open the copy that makes, press **Add Component**, pick
   **OYB_ShotTrackerComponent**, and save (Ctrl+S). Do the same for the Russian
   HE round: search `O832`.
5. **Check it builds.** Open the **Script Editor** and press **F7** (Compile and
   Reload). If it shows an error, screenshot it and send it over.

## Fire a test shot

1. In the Resource Browser search `GM_Everon` and open that world, then press
   **Play** (F5).
2. As Game Master, place a mortar, an ammo box with mortar rounds, and a
   soldier; take control of the soldier.
3. Work out a shot on the OYB mortar map (put your markers where the mortar and
   target really are), set the sight, and fire.
4. When it lands a box pops up: where it landed, how far it went, and the angle
   it left the tube at.
5. In Workbench's **Log Console** (bottom of the window) type `OYB_MORTAR` in
   the filter, copy the lines, and paste them into **Test shots** on the map.

Three or four shots per ring at different ranges, flat ground and no wind,
tells us everything. Then try a couple uphill, downhill and with wind.

## If nothing pops up after firing

The round in flight may be a different prefab from the one in your kit. Tell
us, with a screenshot of the Resource Browser search for `M821`, and we'll
point the tracker at the right one.
