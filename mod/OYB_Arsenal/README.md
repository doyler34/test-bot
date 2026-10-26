# OYB Arsenal

The game's own arsenal screen, improved. OYB doesn't add a second menu: when you
open an arsenal you get the normal Reforger inventory, with the same item cards,
real icons, supply costs, dragging, equipping and controller prompts, plus OYB's
additions built into the arsenal panel.

It's for testing in Workbench on your own PC. It doesn't go on the live servers.

## What's in so far

- **A category column to the left of the arsenal grid:** All, Rifles, Machine guns,
  Sniper rifles, Pistols, Launchers, Ammo, Attachments, Clothing, Vests &
  bags, Medical, Grenades & explosives, Tools, Other, each with its item count.
  Only categories the arsenal has items in get a button. Built from the game's
  own `WLib_ButtonTextUnderlined` buttons.
- **A bar above the grid:** a **Category** button that cycles the same
  categories (the game's `MenuFilter` action, X on keyboard), the current
  category and count, and the game's own search box, which matches each word
  against the name shown in-game, in any order. Q / E stay the
  inventory's page buttons.
- **Every infantry arsenal also stocks its faction's medical gear, vests and
  helmets**, even a weapons-only box (`OYB_ArsenalAlwaysStocked.c`). Arsenals
  with no infantry gear at all, such as vehicle pylons, are left alone.
- The item list comes from the arsenal's own filtered list
  (`SCR_ArsenalComponent.GetAvailablePrefabs`), the same one vanilla shows, so
  faction, item type and mode rules are the game's own.

## Set up (once)

1. On GitHub press **Code → Download ZIP**, unzip it, and copy
   `mod/OYB_Arsenal` to `Documents\OYB_Arsenal`.
2. Launch Arma Reforger Tools, press **Add Existing**, pick
   `OYB_Arsenal.gproj`, and open it.

No layouts, configs or prefabs need making by hand.

### If you tried the earlier version

The earlier version had its own menu and an action on the arsenal box. With
Workbench closed, delete these from `Documents\OYB_Arsenal`, or Workbench
will complain about a class that no longer exists:

- the `Prefabs` folder
- `Configs\System\chimeraMenus.conf` and its `.meta`
- `Ui\OYB_ArsenalMain.layout` and its `.meta`

## Run it

1. In the World Editor, **File → Load World** → `worlds/GameMaster/GM_Eden.ent`
   (not `worlds/Eden/Eden.ent`, which has no game mode), and press **F5**.
2. As Game Master, place the US **Arsenal** and a US soldier, and take control
   of the soldier.
3. Open the arsenal the normal way. The filter bar sits above the item grid.
   Switch categories and search (the search applies when you press Enter or
   leave the box).
4. In the **Log Console** filter `OYB_ARSENAL` and send every line, with a
   screenshot. The first time, it logs the arsenal panel's widget structure
   (`OYB_ARSENAL ui ...`), which shows exactly where the bar went.

## How it works

`OYB_ArsenalStorageFilters.c` modifies `SCR_InventoryStorageBaseUI`, the base of
every inventory panel. An arsenal is usually shown by the **Vicinity** panel
(`SCR_InventoryStorageLootUI`) after you step into the box, and sometimes as an
opened storage (`SCR_InventoryOpenedStorageArsenalUI`); both get their items
through the base class, so that's where OYB filters them and adds the bar. The
bar only shows while an arsenal is on screen. The game creates one card per
offered prefab, in the arsenal's order, so each card's prefab is found by
position; if the counts ever differ it logs `OYB_ARSENAL filter skipped` and
shows everything.

`OYB_ArsenalCatalogService.c` turns the arsenal's offer into categories,
display names and search text, and caches it until the arsenal's offer changes.

## Found in testing (Workbench, 1.8.0.13)

- The scripts compile and the catalogues load without errors.
- Modding `SCR_ArsenalItem` (a class used in config files) made the game reject
  every arsenal entry (`Unknown class 'SCR_ArsenalItem'`). Item names are now
  looked up through `SCR_EntityCatalogManagerComponent` instead.
- A `ButtonWidget` holds one child widget only.
- Taking a widget out of its parent deletes it unless something holds a strong
  reference; widget members are weak, so moves go through a local variable.
- The Game Master arsenals aren't built from `ArsenalBox_US`; they share
  `ArsenalBox_Base`.
- Q / E (`MenuTabLeft` / `MenuTabRight`) turn the inventory grid's pages, so
  they can't switch categories.
- Opening an arsenal from Vicinity doesn't use
  `SCR_InventoryOpenedStorageArsenalUI` at all, so modding only that class did
  nothing; the filter now lives in the shared base class.

## Not tested yet

- Which key `MenuFilter` is on, and whether it reaches the inventory; search;
  the controller's on-screen keyboard for search.
