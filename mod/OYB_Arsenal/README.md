# OYB Arsenal

The game's own arsenal screen, improved. OYB doesn't add a second menu: when you
open an arsenal you get the normal Reforger inventory, with the same item cards,
real icons, supply costs, dragging, equipping and controller prompts, plus OYB's
additions built into the arsenal panel.

It's for testing in Workbench on your own PC. It doesn't go on the live servers.

## What's in so far

- **A category list to the left of the arsenal grid** (dark rows, name on the
  left, count on the right, the selected row lit with an orange edge): All, Rifles, Machine guns,
  Sniper rifles, Pistols, Launchers, Ammo, Attachments, Clothing, Vests &
  bags, Medical, Explosives, Tools, Other, each with its item count.
  Only categories the arsenal has items in get a button. Built from the game's
  own `WLib_ButtonTextUnderlined` buttons.
- **"Fits <weapon>" category** at the top of the column: the attachments,
  magazines and underbarrel rounds in this arsenal that fit the weapon in your
  hands, using the game's own checks (`AttachmentSlotComponent.CanSetAttachment`
  and matching magazine wells on each muzzle). Drag them onto the weapon the
  vanilla way. Hidden when you hold nothing or nothing fits.
- **Weapon picker**: "< Customising: M16A2 >" in the bar steps through the
  guns you carry (in hands, then each weapon slot; grenades skipped). The
  "Fits" list and attachment fitting work on the chosen weapon, slung or
  holstered, without switching to it (`OYB_ArsenalTargetWeapon.c`).
- **Fit attachments in one go** (`OYB_ArsenalWeaponFitting.c`): double-click
  an attachment in the arsenal, or drag it onto the character, and it goes
  straight onto the matching slot of the weapon in your hands. Whatever was in
  that slot (a muzzle brake when fitting a suppressor) goes back into the
  arsenal with the game's own refund, and the new part is only requested once
  the slot is seen to be empty (the server takes supplies before it spawns, so
  this avoids paying for nothing). If the old part can't be refunded, nothing
  changes. The new part is requested with the game's
  own arsenal request, so the server's checks and supply cost still apply.
  Magazines and other items keep the vanilla behaviour.
- **Put clothing on in one go** (`OYB_ArsenalClothing.c`, hooked in `OYB_ArsenalWeaponFitting.c`): double-click a
  uniform, vest, helmet, backpack or other worn item in the arsenal and it is
  worn, not packed. Whatever was worn there comes off, the new piece is
  requested straight into that slot, the old piece's contents move into the
  new one (or anywhere else they fit) and the old piece goes back into the
  arsenal with the game's own refund. If the new piece doesn't arrive, the old
  one goes back on; if something doesn't fit or the arsenal won't take it
  back, the old piece is left on the ground with its contents.
- **Items join the ones you already carry**: double-click (or drag onto the
  character) an item you already have, like a magazine, and it goes into the
  pouch or pocket holding the most of them, if there's room; otherwise the
  game picks as usual.
- **Saved loadouts** (`OYB_ArsenalLoadouts.c`): under the item grid, a
  "Saved loadouts" heading, "< Loadout 1 >" to pick one of five slots,
  **Save** to store the kit you're wearing and **Load** to put it on at this
  arsenal. Empty slots say so. The server keeps them in
  `$profile:OYB_Arsenal/<player id>.json`, using the game's own arsenal
  loadout format. Loading only charges for items you don't already carry, at
  the arsenal's price (or the item catalogue's for things it doesn't sell,
  such as the uniform you spawned in, as the game does for a saved respawn
  kit), refuses if you lack supplies, and refunds what the kit drops the way the arsenal normally refunds. A kit saved
  for another faction won't load. Military supply allocation isn't counted.
- **A bar above the grid:** a **Category** button that cycles the same
  categories (the game's `MenuFilter` action, X on keyboard), the weapon
  picker, and the game's own search box, which matches each word
  against the name shown in-game, in any order. Q / E stay the
  inventory's page buttons.
- **Every infantry arsenal also stocks its faction's medical gear, vests and
  helmets**, even a weapons-only box (`OYB_ArsenalAlwaysStocked.c`). Arsenals
  with no infantry gear at all, such as vehicle pylons, are left alone.
- **Paging inside a category**: the game only turns on Q / E and the page
  arrows while the mouse is over a panel, and only counts the pages when the
  mouse enters it. OYB counts them again every frame, so switching to a
  category with several pages lets you page straight away.
- The item list comes from the arsenal's own filtered list
  (`SCR_ArsenalComponent.GetAvailablePrefabs`), the same one vanilla shows, so
  faction, item type and mode rules are the game's own.

## Milestones

Status: Not started / In progress / Implemented — awaiting testing /
Verified complete / Blocked. "Verified" means seen working in Workbench
(1.8.0.13, Game Master, local host); nothing has been run on a dedicated
server or with a controller yet.

**1. Core functionality — In progress (audit done, fixes awaiting testing)**
- Verified: arsenal opens and closes; the item list loads from the arsenal's
  own offer (173 items in the US box, no unnamed items); categories, counts
  and search; fitting attachments and swapping parts; clothing swap;
  saving and loading kits.
- Fixed in the audit, awaiting testing: a category the arsenal has nothing
  in (or Fits after the weapon is put away) now falls back to All instead
  of an empty grid; clothing into an empty slot is only requested if it
  fits, since the server takes supplies before it spawns; loadout requests
  from dead players or less than a second apart are ignored.
- Not checked: whether the two `OverlayWidget Frame0 has invalid slot
  GridWidgetSlot` errors also appear without OYB (they look like the game's
  own item cards); double-clicking a weapon while one is already in that
  slot still does whatever the game does.

**2. Keyboard and controller — In progress**
- Done: Category on the game's `MenuFilter` action with its prompt; Q / E
  (LB / RB) paging, now also right after switching category; every OYB
  button is a game widget-library button, so it can be focused and pressed
  with a controller.
- Not done: dedicated, rebindable OYB input actions (needs an input config
  in the mod); inspect from the arsenal; keeping the page when switching
  category; any controller testing.

**3. Loadouts — In progress**
- Done: five slots per player saved on the server under `$profile:`, keyed
  by the player's identity, so they survive reconnects and restarts; save
  overwrites; load checks faction, reach and supplies, charges only for
  what's missing and refunds what's dropped; clothing, weapons,
  attachments, magazines and pocket contents use the game's own loadout
  format.
- Not done: deleting and renaming slots; a clear message naming items that
  no longer exist (a removed mod); a reconnect / restart test on a
  dedicated server.

**4. Modded equipment — Implemented — awaiting testing**
- Items come from the arsenal's own filtered catalogue, categories from
  each item's arsenal type and mode, names from its inventory info, fits
  from the game's attachment and magazine-well checks. No item is listed
  by hand. Items without a name are logged and shown by file name.
- Not tested with any mod's items.

**5. Performance — Not started** (the item list is already cached per
arsenal until its offer changes; nothing has been measured).

**6. Security — In progress**
- Item requests and refunds go through the game's own server checks
  (ownership, distance, faction catalogue, supplies). Loadouts are built
  and checked on the server from its own copy, with a one-second limit.
- Not done: an ownership / licence notice; a review of the military supply
  allocation, which loadout loading doesn't count.

**7. Release — Not started.**

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
4. In the **Log Console** filter `OYB_ARSENAL`: searches, filter problems and
   every attachment fit are logged there.

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
- Weapon variants share the weapon component's name (an M16A2 carbine is
  "M16A2" in the catalogue); the name the tooltip shows comes from the item's
  inventory attributes, so that's what OYB uses.
- Working in Workbench: the category column on the left with counts, filtering
  by category, the search box (each word, display names only), the filter bar
  and column hiding outside an arsenal, and fitting attachments by double-click
  (the game's quick-move path), including swapping scopes and suppressors with
  the old part going back into the arsenal.
- Double-clicking clothing swaps it with what's worn and returns the old piece.
- Saving and loading a loadout works in Workbench, including a kit with an
  M203 fitted and clothing the arsenal doesn't sell. The restyled bar,
  category list and loadout row show as intended.
- Opening an arsenal from Vicinity doesn't use
  `SCR_InventoryOpenedStorageArsenalUI` at all, so modding only that class did
  nothing; the filter now lives in the shared base class.

## Not tested yet

- Saved loadouts: the supply charge and refund at an arsenal that uses
  supplies (the Game Master arsenal charged 0), and the file on a dedicated
  server.
- Whether X (`MenuFilter`) also triggers the inventory's own X actions inside
  an arsenal; meds and armour in a weapons-only box; dragging to equip after
  filtering; a controller and its on-screen keyboard; a dedicated server.
