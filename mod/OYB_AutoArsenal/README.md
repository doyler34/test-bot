# OYB Auto Arsenal (step 1: look, don't touch)

The first step towards getting equipment mods to show up in Bacon Loadout Editor
without registering each item by hand. This step only **looks**: ten seconds after
a mission starts it counts everything the game has registered in its arsenal
catalogues and writes the numbers to the log. It changes nothing: no item,
price, rank or Bacon file.

It's for testing in Workbench on your own PC. It doesn't go on the live servers,
so there's nothing to deploy or roll back yet.

## Set up (once)

1. **Get this folder.** On GitHub press **Code → Download ZIP**, unzip it, and
   copy `mod/OYB_AutoArsenal` to `Documents\OYB_AutoArsenal`.
2. **Open it.** Launch Arma Reforger Tools, press **Add Existing**, pick
   `OYB_AutoArsenal.gproj`, and double-click it. If a red error box appears,
   screenshot it and send it over: it names the exact line.

## Run it

1. In the **Resource Browser** search `GM_Everon`, double-click that world,
   and press **F5**.
2. Wait about 15 seconds after the mission loads.
3. In the **Log Console** type `OYB_ARSENAL` in the filter, copy every line, and
   send them over.

To see how Bacon fits in, run it a second time with Bacon Loadout Editor added
to the project's dependencies (Workshop ID `606B100247F5C709`), and an equipment
mod you want to test added the same way. If you can't find where dependencies
are added in Workbench, say so: it's one line in the `.gproj` and I'll put it in.

## What the log tells us

- How many arsenal entries the game has, how many are different items, and how
  many are repeats.
- How they split by type and by mode, as numbers.
- Whether any item has `bacon` or `loadout` in its name.
- A sample of 25 item names.

Run it with and without the extra mod and compare: items that appear in the
second run are the ones the game already registers properly. Those are the ones
this first approach can pass to Bacon.

## What's known and what isn't

- Working: the same project layout, `modded class`, `OnPostInit`, the delayed
  call and `PrintFormat` the mortar mod already uses in the game, and
  `map` with `Contains`, `Insert`, `Get` and `Set`.
- **Not tried in the game yet:** `SCR_EntityCatalogManagerComponent`,
  `GetAllArsenalItems`, `SCR_ArsenalItem`, `GetItemResourceName`, `GetItemType`
  and `GetItemMode`. They come from Bohemia's script documentation as quoted in
  the handoff. If Workbench reports one of them as unknown, the error message
  says which and I'll change it.

## What this does not do

- It doesn't find items that aren't registered in a catalogue. That needs a
  second kind of scan, and a decision on how to register them safely.
- It doesn't read Bacon's own settings or item list. Those aren't published,
  so the next step is looking at Bacon's scripts in Workbench once this
  step's log shows what the game itself registers.
- Bacon has a custom licence that asks for permission before it's copied or
  adapted. Nothing here copies or changes any of Bacon.
