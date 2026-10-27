# OYB Arsenal

The game's own arsenal screen, improved. OYB doesn't add a second menu: when you
open an arsenal you get the normal Reforger inventory, with the same item cards,
real icons, supply costs, dragging, equipping and controller prompts, plus OYB's
additions built into the arsenal panel.

Being prepared for OYB's servers. Not released yet: see the test checklist
below. Ownership: `NOTICE.md`.

## What's in so far

- **OYB branding** (`OYB_ArsenalPrompt.c`): every arsenal's open prompt reads
  "OYB Arsenal", and so does the arsenal's title bar inside the inventory.
- **A category list to the left of the arsenal grid** (dark rows, name on the
  left, count on the right, the selected row lit with an orange edge): All, Rifles, Machine guns,
  Sniper rifles, Pistols, Launchers, Ammo, Attachments, Clothing, Vests &
  bags, Medical, Explosives (grenades, explosives, and single rounds such as
  40mm grenades, rockets and mortar shells), Tools, Other, each with its item
  count.
  Only categories the arsenal has items in get a button. Built from the game's
  own `WLib_ButtonTextUnderlined` buttons.
- **"Fits <weapon>" category** at the top of the column: the attachments,
  magazines and underbarrel rounds in this arsenal that fit the weapon in your
  hands, using the game's own checks (`AttachmentSlotComponent.CanSetAttachment`
  and matching magazine wells on each muzzle). Drag them onto the weapon the
  vanilla way. Hidden when you hold nothing or nothing fits.
- **Weapon picker**: the **Weapon** key and "< M16A2 >" in the bar step through the
  guns you carry (in hands, then each weapon slot; grenades skipped). The
  "Fits" list and attachment fitting work on the chosen weapon, slung or
  holstered, without switching to it (`OYB_ArsenalTargetWeapon.c`). On a
  controller the Weapon button is the same as Requisition, so the key is
  hidden there; the < > arrows pick the weapon instead.
- **Fit attachments in one go** (`OYB_ArsenalWeaponFitting.c`): double-click
  an attachment in the arsenal, or drag it onto the character, and it goes
  straight onto the matching slot of the weapon in your hands. Whatever was in
  that slot (a muzzle brake when fitting a suppressor) goes back into the
  arsenal with the game's own refund, and the new part is only requested once
  the slot is seen to be empty (the server takes supplies before it spawns, so
  this avoids paying for nothing). If the old part can't be refunded, nothing
  changes. The new part is requested with the game's
  own arsenal request, so the server's checks and supply cost still apply.
  Slots on fitted parts count too (a rail adapter's own scope slot), for both
  fitting and the Fits list.
  Magazines and other items keep the vanilla behaviour.
- **Put clothing on in one go** (`OYB_ArsenalClothing.c`, hooked in `OYB_ArsenalWeaponFitting.c`): double-click a
  uniform, vest, helmet, backpack or other worn item in the arsenal and it is
  worn, not packed. Whatever was worn there comes off, the new piece is
  requested straight into that slot, the old piece's contents move into the
  new one (or anywhere else they fit) and the old piece goes back into the
  arsenal with the game's own refund. If the new piece doesn't arrive, the old
  one goes back on; if something doesn't fit or the arsenal won't take it
  back, the old piece is left on the ground with its contents.
- **Body slots**: anything else with its own slot on the character (NVGs in
  a night vision slot, a watch, binoculars) goes there on a double-click,
  swapped like clothing if the slot is taken.
- **Accessories onto worn gear** (also `OYB_ArsenalWeaponFitting.c`):
  double-click a helmet light, NVGs or a vest flashlight and it goes into a
  free spot for it on what you wear (a helmet's mount, or a mount already on
  the helmet), not into the bag. Pockets and backpacks don't count. If the
  spot is taken, the old accessory is swapped out and refunded, like clothing.
- **Swap weapons in one go** (also `OYB_ArsenalClothing.c`): double-click a
  weapon and it goes into a free weapon slot that takes it; with none free,
  the weapon picked in the bar (if the new one can take its place, else the
  one in hand or the first that fits) comes off and goes back into the arsenal whole
  (the game's refund covers its fitted parts and loaded magazine) once the
  new one is on. If the new one doesn't arrive, the old one goes back on.
  Grenades still go to pouches.
- **Items join the ones you already carry**: double-click (or drag onto the
  character) an item you already have, like a magazine, and it goes into the
  pouch or pocket holding the most of them, if there's room; otherwise the
  game picks as usual.
- **Panel settings** (`OYB_ArsenalSettings.c`): the OYB panel's **Arsenal**
  tab on each server lists every item the arsenals can offer (modded ones
  included) and hides items or changes their price. The mod writes the list
  to `$profile:OYB_Arsenal/items.json` when the server starts and reads
  `$profile:OYB_Arsenal/settings.json` every minute, so changes apply
  without a restart. Hidden items drop out of every arsenal and the server
  refuses them, even in a saved kit; a changed price is what the arsenal
  charges, refunds, shows on the item card and charges for a kit.
- **Reorganising from the panel**: any item can be moved to another
  category from the Arsenal tab, one at a time or everything a filter shows
  at once; moving it back to its own category clears the change. Players see
  the new categories within a minute.
- **Sides from the panel**: tick which factions get an item, whatever faction
  its mod tagged it with, so a US gun can be in the USSR arsenal too, or only
  there, one item at a time or everything a filter shows. A side whose catalogue doesn't list the item gets it at its usual
  price; a side it was taken from can't have it, even in a saved kit.
- **Renaming from the panel** (`OYB_ArsenalItemNames.c`): give an item a
  shorter name and every player sees it within a minute, in the arsenal, its
  search, the hover box and the inventory. The game keeps a second copy of a
  weapon's name for the on-screen weapon display (and possibly the kill
  feed), which keeps the original. Items that shared a name share the new one.
- **Mod scan**: on start the server also searches every loaded (non-base)
  mod's `Prefabs` folder for items no faction catalogue lists (a mod that
  wasn't set up properly), a few at a time so it doesn't hold the server up,
  and guesses each one's category from its parts and file path. They show in
  the panel as **Unlisted**; **Add to arsenal** with a price, category and
  faction (or every faction) puts one into the arsenals that carry that kind
  of item. The game's own arsenal won't hand out an item no catalogue lists,
  so OYB hands these out itself with the same checks (reach, ownership,
  supplies first) and refunds them at the arsenal's usual share.
- **Network use**: a player asks for the panel settings and their loadout
  names once per session, the first time they open an arsenal; after that
  the server only sends settings when the panel actually changed something
  (once per player, at most once a minute) and names after a save. The
  server ignores a player asking again within five seconds. Checking the
  settings file is a local read once a minute.
- **Saved loadouts** (`OYB_ArsenalLoadouts.c`): under the item grid, a
  "Saved loadouts" heading, "< Loadout 1 >" to pick one of five slots,
  **Save** (`MenuFavourite`) to store the kit you're wearing and **Load**
  (`MenuDelete`, V) to put it on at this
  arsenal. Empty slots say so. The server keeps them in
  `$profile:OYB_Arsenal/<player id>.json`, using the game's own arsenal
  loadout format. Loading only charges for items you don't already carry, at
  the arsenal's price (or the item catalogue's for things it doesn't sell,
  such as the uniform you spawned in, as the game does for a saved respawn
  kit), refuses if you lack supplies, and refunds what the kit drops the way the arsenal normally refunds. A kit saved
  for another faction won't load. The kit you last saved or loaded also
  becomes your respawn kit, through the game's own saved arsenal loadout
  (what the arsenal's "Save loadout" action does): the respawn screen offers
  it wherever the game mode supports that, with the game's respawn cost and
  the arsenal's own save rules. An arsenal with saving turned off doesn't set
  it. Military supply allocation isn't counted.
- **A bar above the grid:** a **Category** button that cycles the same
  categories (the game's `MenuFilter` action, X on keyboard), the weapon
  picker, and the game's own search box, which matches each word
  against the name shown in-game, in any order. Q / E stay the
  inventory's page buttons.
- **Every infantry arsenal is a full arsenal**: a clothing, equipment or
  weapons box offers all of its faction's infantry gear
  (`OYB_ArsenalAlwaysStocked.c`). Arsenals with no infantry gear at all, such
  as vehicle pylons, are left alone.
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

**1. Core functionality — Verified complete** (Workbench, local host)
- Arsenal opens and closes; the item list loads from the arsenal's own
  offer; categories, counts and search; attachment fitting and part swaps;
  clothing and weapon swaps (including with both weapon slots full); items
  joining the same items already carried; saving and loading kits; an empty
  category falling back to All; clothing for an empty slot only requested
  when it fits.
- Left open: the two `OverlayWidget Frame0 has invalid slot GridWidgetSlot`
  errors may be the game's own (check with the mod off).

**2. Keyboard and controller — Keyboard verified; controller partly tested**
- OYB uses the game's own menu actions, so players change the keys in the
  game's own key settings. Verified in Workbench, one action each, no
  clashes: Category X (`MenuFilter`), Weapon R (`MenuRefresh`), Save F
  (`MenuFavourite`), Load V (`MenuDelete`); Q / E page the grid, also right
  after switching category, and each category remembers its page.
- Tried and dropped: `MenuSave` (X, same as Category), `MenuRename` (R, same
  as Weapon), `MenuDeploy` (Space, only works on the respawn screen).
- Not done: OYB-only named actions under their own heading in the key
  settings (needs the game's input and key-settings configs from Workbench
  as a template); inspecting an arsenal item; a controller button for
  Weapon (X is Requisition there, so it's hidden on a controller).
- Controller (live server): the prompts show Category on the menu button,
  Save on Y and Load on the right stick (not yet confirmed working); Weapon
  on X clashed with Requisition and is now keyboard only.

**3. Loadouts — Verified in Workbench; dedicated server awaiting testing**
- Five slots per player, saved on the server under `$profile:` by player
  identity; saving overwrites (no rename or delete, by choice). Loading
  checks faction, reach and supplies, charges only for what's missing,
  counts parts that come fitted, refunds what's dropped, and sets the
  respawn kit ("Saved loadout" on the respawn screen). Verified with
  modded weapons and clothing the arsenal doesn't sell.
- Awaiting testing: a kit with items from a removed mod (refused, items
  named); a restart and reconnect on a dedicated server.

**4. Modded equipment — Verified in Workbench**
- Tested with six mods (Bacon Desert Eagle, M110, suppressors, RIS optics,
  GRS framework and patches): 519 items listed, modded weapons and parts in
  the right categories, patches in Clothing, 9 uncatalogued items found by
  the scan. The panel's Arsenal tab hides, reprices, recategorises and adds
  items.
- Awaiting testing: the scan on a dedicated server; adding a scanned item
  and buying it.

**5. Performance — Verified in Workbench**
- Cached: each arsenal's item list until its offer or the panel settings
  change; weapon fits until the weapon or its parts change. The mod scan
  runs 20 prefabs at a time. Settings and loadout names are asked once per
  session and pushed only on change.
- Measured so far: 257-item arsenal, no visible stall. With 17 mods loaded
  (GRS, WZ Weapons, Zelik's, Beryl, Scorpion and more) a 418-item arsenal
  builds its list in 12 ms, and the scan lists 745 items (47 found by scan)
  in 280 ms spread over many frames. The log gives these times:
  `built item list … in N ms` and `the scan took N ms`.
- Awaiting testing: the same on a dedicated server.

**6. Security — Implemented — awaiting testing**
- Every item, fit, swap and refund goes through the game's own arsenal
  requests (ownership, distance, faction catalogue, supplies); hidden items
  are refused on the server; added items get the same checks from OYB.
  Loadouts are built and priced on the server from its own copy and refuse
  dead players, other factions, removed items and repeat requests within a
  second. Players can't make the server repeat settings or loadout names
  within five seconds.
- Military supply allocation isn't used on OYB servers, so kit loading
  only charges the arsenal's supplies.
- Ownership notice: `NOTICE.md`. No licence keys or online checks.
- Awaiting testing: two players at one arsenal; a dedicated server.

**7. Release — In progress**
- Done: install, server release and configuration guides, known
  limitations, and a test for every feature (see below).
- Left: the ⏳ tests (dedicated server, two players, big modpack,
  controller), Workshop publishing, and the mirror sync so the panel's
  Arsenal tab reaches the live box.

## Install for testing (Workbench)

1. Get the mod folder: on GitHub press **Code → Download ZIP**, unzip it and
   copy `mod/OYB_Arsenal` to `Documents\OYB_Arsenal` (or clone the repo and
   use `mod/OYB_Arsenal` from the clone, then `git pull` for each update).
2. Launch Arma Reforger Tools, press **Add Existing**, pick
   `OYB_Arsenal.gproj`, and open it.

No layouts, configs or prefabs need making by hand. All scripts live in
`Scripts/Game/OYBArsenal`; keep exactly one copy of each `OYB_Arsenal*.c`
file there and nowhere else under `Scripts`.

If an earlier version left `Prefabs`, `Configs\System\chimeraMenus.conf` or
`Ui\OYB_ArsenalMain.layout` (with their `.meta` files) in the folder, delete
them with Workbench closed.

## Run it

1. In the World Editor, **File → Load World** → `worlds/GameMaster/GM_Eden.ent`
   (not `worlds/Eden/Eden.ent`, which has no game mode), and press **F5**.
2. As Game Master, place the US **Arsenal** and a US soldier, and take control
   of the soldier.
3. Open the arsenal the normal way.
4. In the **Log Console** filter `OYB_ARSENAL`: the item list, searches, key
   presses, fits, swaps and loadouts are all logged there.

## Releasing to a server

1. Publish the project to the Workshop from Workbench's publish dialog (name,
   visibility, preview image). Unlisted or private is fine for OYB-only use.
   Note the mod ID it is given.
2. Add it to the server's config under `game.mods`, with the dependency the
   project already lists (the base game):
   `{ "modId": "<mod ID>", "name": "OYB Arsenal" }`
3. Restart the server. Clients download it on joining.
4. Saved loadouts are kept by the server in its profile folder, in
   `OYB_Arsenal/<player identity>.json`, one file per player. They survive
   restarts and reconnects; back the folder up with the rest of the profile.
   Deleting a player's file clears their five slots.

## Configuration

Nothing needs setting up. What there is:

- **The panel's Arsenal tab** for each server: hide items and change prices
  (see above). The same file can be edited by hand if needed:
  `{"version": 1, "hidden": [prefab, ...], "priceItems": [prefab, ...],
  "priceCosts": [cost, ...], "extraItems": [prefab, ...], "extraCosts":
  [cost, ...], "extraFactions": ["" or "US,FIA", ...], "extraCategories":
  [category name, ...], "categoryItems": [prefab, ...], "categoryNames":
  [category name, ...], "sideItems": [prefab, ...], "sideFactions":
  ["US,USSR", ...], "renameItems": [prefab, ...], "renameNames": [new name,
  ...]}`, prefabs written as `{GUID}Prefabs/....et` the
  way `items.json` lists them.

- **The game's own arsenal settings** on each arsenal (faction, item types,
  supply use, refunds, whether kits may be saved) apply as usual. An arsenal
  with saving turned off still loads OYB slots but doesn't set the respawn
  kit.
- **Keys** are the game's own menu actions, so players change them in the
  game's control settings: Category `MenuFilter` (X), Weapon `MenuRefresh` (R),
  Save `MenuFavourite` (F), Load `MenuDelete` (V), pages `MenuTabLeft` /
  `MenuTabRight` (Q / E).
- **In the scripts**, if ever needed:
  - `OYB_LOADOUT_SLOTS` (5) and `OYB_REQUEST_COOLDOWN_MS` (1000) in
    `OYB_ArsenalLoadouts.c`;
  - what every infantry arsenal always stocks, `OYB_ALWAYS_TYPES` and
    `OYB_ALWAYS_MODES` in `OYB_ArsenalAlwaysStocked.c` (all infantry gear).

## Known limitations

- Controller use is untested; every OYB control is a game key button or a
  game widget-library button, but nobody has tried them with a pad.
- OYB uses the game's own menu actions (X, R, F, V); players change them
  through the game's menu key settings, not under an OYB heading. OYB-only
  named actions are still to do.
- Military supply allocation is not counted when loading a kit (OYB servers
  don't use it). Single items still go through the game's own checks.
- Loading a kit is charged before it is applied; if the game can't place part
  of it, the supplies aren't returned.
- "Comes fitted" parts (an M203 on an M16A2 M203) are read from each prefab's
  settings; an odd modded prefab could make a part look fitted when it isn't,
  making it free when a kit is loaded.
- Flares go into a weapon slot when double-clicked, as the game treats them
  as handheld weapons.
- A big modded arsenal (500-1,000 items) may stall the first time it's opened,
  while item names are read; not measured.
- Two `OverlayWidget Frame0 has invalid slot GridWidgetSlot` errors appear on
  each inventory refresh; not yet checked whether they also appear without
  OYB.
- Several players at one arsenal, and a dedicated server, are untested.
- Panel prices are settled after the game's own charge, so the arsenal must
  hold enough for both the game's price and the new one for a moment; the
  refund of an item with a changed price is worked out as the same share of
  the new price as the game gives of the old one.
- The mod scan uses the game's resource search, which has only been seen
  in Workbench tools; if a dedicated server reports `scan: 0 prefabs` for a
  mod, the scan doesn't work there and only catalogued items are listed.
- A scanned item's category is a guess from its parts and file path; pick
  the right one when adding it. Items added in the panel don't count
  military supply allocation and aren't offered by the game's own respawn
  loadout pricing.
- After adding a mod, the server has to start once before the panel lists
  its items.

## Test checklist

Status: ✅ verified in Workbench, ⏳ awaiting testing.

| Feature | Test | Expected | Status |
|---|---|---|---|
| Item list | Open the US arsenal | `built item list: 173 entries … 0 without a display name` | ✅ |
| Categories | Click each category, press X | Grid shows only that category; counts match | ✅ |
| Empty category | On Fits, put the weapon away, reopen | Falls back to All | ✅ |
| Explosives | Open Explosives | 40mm rounds, rockets, shells listed; log `moved … single-round items` | ✅ |
| Search | Type `m16 ca`, Enter | Only M16A2 Carbine variants | ✅ |
| Pages | Page 2 of a category, switch away and back | Back on page 2; Q / E page straight after switching | ✅ |
| Weapon picker | Press R / click arrows with two guns | Name changes, Fits list follows | ✅ |
| Slung weapon | Empty hands, open Fits | Uses the first carried gun | ⏳ |
| Fitting | Double-click a scope, then a different scope | Fitted; first one back in arsenal | ✅ |
| Clothing swap | Double-click a jacket with full pockets | Worn, pockets moved, old one returned | ✅ |
| Clothing into empty slot | Take helmet off, double-click a helmet | Goes on the head | ✅ |
| Weapon swap | Both weapon slots full, double-click a rifle | Gun in hands replaced, old one returned | ✅ |
| Same items together | Double-click a magazine you carry | Goes into the pouch holding the most | ✅ |
| Save / load | Save a slot, change kit, Load | Kit restored; one log line per press | ✅ |
| Load key | Press V over the arsenal | Loads the selected slot once; R only switches weapon | ✅ |
| Respawn kit | Load a slot, die, respawn | "Saved loadout" offered | ✅ |
| Removed item | Save a kit with a mod item, remove the mod, Load | Refused, item named, no supplies taken | ⏳ |
| Supplies | Load at an arsenal that uses supplies | Charged for missing items only; refund for dropped | ⏳ |
| Spam | Click Load repeatedly | At most one load a second | ⏳ |
| Item list for the panel | Start the server with the mod | `wrote N items to $profile:OYB_Arsenal/items.json`; panel's Arsenal tab lists them | ✅ Workbench (519 items) |
| Hide from panel | Hide an item, wait a minute, open an arsenal | Item gone; `settings loaded: 1 hidden` | ⏳ |
| Price from panel | Set a price, wait, open an arsenal that uses supplies | Card shows the new price; that's what's taken and refunded | ⏳ |
| Hidden in a kit | Save a kit, hide one of its items, load at an arsenal without it on you | Refused, item named | ⏳ |
| Mod scan | Load a weapon mod, start | `scan: N prefabs in <mod>`; its uncatalogued items show as Unlisted in the panel | ✅ Workbench (6 mods, 9 unlisted found); dedicated server ⏳ |
| Added item | Add an unlisted gun for US at 20, open a US arsenal | Gun listed at 20; taking it charges 20; putting it back refunds | ⏳ |
| Network | Open and close an arsenal many times | Settings asked once per session (no repeat in the server log) | ⏳ |
| Move category | Move an item to another category in the panel, wait a minute | It shows under the new category; `settings loaded: … 1 moved` | ⏳ |
| Branding | Walk up to any arsenal, open it | Prompt and the title bar inside say "OYB Arsenal" | ✅ |
| Full arsenal | Open a clothing-only box | Weapons, ammo, equipment and clothing all listed | ✅ |
| Sides | In the panel, tick USSR on a US rifle, wait a minute, open a USSR arsenal | Rifle listed at its usual price; untick US and the US arsenal loses it | ✅ |
| Rename | Rename an item in the panel, wait a minute, open an arsenal | New name on the card, hover box and in the inventory; `settings loaded: … 1 renamed` | ⏳ |
| Nested slots | Fit a rail adapter, then double-click a scope that only fits the rail | Scope in Fits and fitted onto the rail | ⏳ |
| Accessories | Wear a helmet with a free mount, double-click a helmet light or NVGs | Goes onto the helmet; log `accessory … putting … on worn gear` | ⏳ |
| Body slots | Double-click NVGs (with a night vision slot, e.g. NV-System), a watch or binoculars | Goes into its slot on the body; a taken slot is swapped | ⏳ |
| Credits | Look under Save / Load | Grey "Zero fucking thanks to Tay and Saber" | ✅ |
| Modded gear | Load a weapon mod | Right categories, attachments fit | ✅ categories (Bacon Desert Eagle, M110, suppressors, RIS optics: 257 items); patches listed as equipment now go to Clothing ✅ |
| Dedicated server | Save, restart server, rejoin, Load | Slots still there | ⏳ |
| Two players | Both use one arsenal at once | No errors, no duplicate items | ⏳ |
| Controller | Navigate bar, list, buttons with a pad | All reachable | ⏳ |

## How it works

| File | What it does |
|---|---|
| `OYB_ArsenalStorageFilters.c` | Mods the inventory panel base class: the bar, category list, weapon picker, search, paging memory and loadout row, and filters the arsenal's items |
| `OYB_ArsenalCatalogService.c` | Turns the arsenal's offer into categories, display names and search text; cached per arsenal |
| `OYB_ArsenalWeaponFitting.c` | Mods the inventory menu: double-click / drag / equip for attachments, clothing, weapons and same-item stacking; paging context |
| `OYB_ArsenalClothing.c` | The take-off, put-on, move-contents and refund steps for clothing and weapon swaps |
| `OYB_ArsenalTargetWeapon.c` | Which carried weapon the Fits list and fitting use |
| `OYB_ArsenalLoadouts.c` | Server-side loadout slots, pricing, refunds, respawn kit |
| `OYB_ArsenalSettings.c` | The panel's hidden items and prices: writes `items.json`, reads `settings.json`, hides items in every arsenal, makes the server refuse them and charge, refund and show the new prices |
| `OYB_ArsenalAlwaysStocked.c` | Every infantry arsenal offers all infantry gear |
| `OYB_ArsenalItemNames.c` | Gives renamed items their new name on each player's screen |
| `OYB_ArsenalPrompt.c` | Every arsenal's open prompt reads "OYB Arsenal" |

An arsenal is usually shown by the **Vicinity** panel
(`SCR_InventoryStorageLootUI`) and sometimes as an opened storage; both get
their items through the base class, so that's where OYB filters them. The
game creates one card per offered prefab, in the arsenal's order, so each
card's prefab is found by position; if the counts ever differ it logs
`OYB_ARSENAL filter skipped` and shows everything.

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
