# OYB Arsenal

The game's own arsenal screen, improved. OYB doesn't add a second menu: when you
open an arsenal you get the normal Reforger inventory, with the same item cards,
real icons, supply costs, dragging, equipping and controller prompts, plus OYB's
additions built into the arsenal panel.

Being prepared for OYB's servers. Not released yet: see the test checklist
below. Ownership: `NOTICE.md`.

## What's in so far

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
- **Swap weapons in one go** (also `OYB_ArsenalClothing.c`): double-click a
  weapon and it goes into a free weapon slot that takes it; with none free,
  the weapon in its slot comes off and goes back into the arsenal whole
  (the game's refund covers its fitted parts and loaded magazine) once the
  new one is on. If the new one doesn't arrive, the old one goes back on.
  Grenades still go to pouches.
- **Items join the ones you already carry**: double-click (or drag onto the
  character) an item you already have, like a magazine, and it goes into the
  pouch or pocket holding the most of them, if there's room; otherwise the
  game picks as usual.
- **Saved loadouts** (`OYB_ArsenalLoadouts.c`): under the item grid, a
  "Saved loadouts" heading, "< Loadout 1 >" to pick one of five slots,
  **Save** (`MenuFavourite`) to store the kit you're wearing and **Load**
  (`MenuRename`) to put it on at this
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

**1. Core functionality — Verified complete** (Workbench, local host)
- Arsenal opens and closes; the item list loads from the arsenal's own
  offer; categories, counts and search; attachment fitting and part swaps;
  clothing and weapon swaps (including with both weapon slots full); items joining the same items already carried;
  saving and loading kits; an empty category falling back to All; clothing
  for an empty slot only requested when it fits.
- Left open: the two `OverlayWidget Frame0 has invalid slot GridWidgetSlot`
  errors may be the game's own (check with the mod off); the loadout
  one-second limit hasn't been seen in action.

**2. Keyboard and controller — In progress (awaiting testing)**
- OYB uses the game's own menu actions through its Input Manager, so the
  game's key settings and controller layouts apply and no extra input file
  is needed. Each key button shows its prompt and can also be clicked:
  Category `MenuFilter` (X), Weapon `MenuRefresh` (R), Save `MenuFavourite` (F), Load
  `MenuRename` (`MenuDeploy`, Space, never fired: it's a hold on the
  respawn screen). `MenuSave` was tried for Save but is X, the same key as Category;
  a crossed circle on a prompt means the action has no key there, and every
  press is logged as `OYB_ARSENAL key`.
- Q / E (LB / RB) page the grid, also right after switching category, and
  each category remembers its page until the search or weapon changes.
- Not done: OYB-only rebindable actions (needs an input config the mod
  would ship; the format couldn't be checked from here); inspecting an
  arsenal item; controller testing.

**3. Loadouts — In progress**
- Done: five slots per player saved on the server under `$profile:`, keyed
  by the player's identity, so they survive reconnects and restarts; save
  overwrites; load checks faction, reach and supplies, charges only for
  what's missing and refunds what's dropped; clothing, weapons,
  attachments, magazines and pocket contents use the game's own loadout
  format.
- Not wanted: deleting and renaming slots (saving over a slot replaces it).
- Verified: the last saved or loaded kit becomes the respawn kit; the
  respawn screen offers it as "Saved loadout" (Game Master, Workbench).
- Implemented — awaiting testing: a kit holding items that no longer exist
  (a removed mod) isn't loaded; the player is told which items, before any
  supplies are taken. The character itself is no longer counted as an item
  when a kit is priced.
- Not done: a reconnect / restart test on a dedicated server.

**4. Modded equipment — Implemented — awaiting testing**
- Items come from the arsenal's own filtered catalogue, categories from
  each item's arsenal type and mode, names from its inventory info, fits
  from the game's attachment and magazine-well checks. No item is listed
  by hand. Items without a name are logged and shown by file name.
- Not tested with any mod's items.

**5. Performance — In progress (awaiting testing)**
- Cached: the item list (names, categories, costs) per arsenal until its
  offer changes; which items fit the chosen weapon, until the weapon or its
  fitted parts change; the arsenal's prefab list is read once per refresh.
- Search no longer logs every matching name on each refresh.
- Widgets are built once per panel and only shown or hidden afterwards.
- Not measured: large modded arsenals (500-1,000 items). The first opening
  of an arsenal reads each item's name from its prefab once; that is the
  part most likely to stall on a big list.

**6. Security — Implemented — awaiting testing**
- Every item, fit, swap and refund goes through the game's own arsenal
  requests, so the server checks ownership, distance, the arsenal's faction
  catalogue and supplies. Loadouts are saved, checked and priced on the
  server from its own copy, refuse dead players, other factions, items that
  no longer exist and requests less than a second apart, and charge before
  anything spawns.
- Military supply allocation is not used on OYB servers, so loading a kit
  only charges the arsenal's supplies (single items still go through the
  game's own checks either way).
- Ownership notice: `NOTICE.md`. No licence keys, online authentication or
  allowlists.
- Not tested: several players at one arsenal; a dedicated server.

**7. Release — In progress**
- Done: install, server release and configuration guides, known
  limitations, and a test for every feature (see below).
- Left: the ⏳ tests in the checklist, Workshop publishing, then the mirror
  sync for the live server.

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

- **The game's own arsenal settings** on each arsenal (faction, item types,
  supply use, refunds, whether kits may be saved) apply as usual. An arsenal
  with saving turned off still loads OYB slots but doesn't set the respawn
  kit.
- **Keys** are the game's own menu actions, so players change them in the
  game's control settings: Category `MenuFilter` (X), Weapon `MenuRefresh` (R),
  Save `MenuFavourite` (F), Load `MenuRename`, pages `MenuTabLeft` /
  `MenuTabRight` (Q / E).
- **In the scripts**, if ever needed:
  - `OYB_LOADOUT_SLOTS` (5) and `OYB_REQUEST_COOLDOWN_MS` (1000) in
    `OYB_ArsenalLoadouts.c`;
  - what every infantry arsenal always stocks, `OYB_ALWAYS_TYPES` in
    `OYB_ArsenalAlwaysStocked.c` (medical, vests, helmets).

## Known limitations

- Controller use is untested; every OYB control is a game key button or a
  game widget-library button, but nobody has tried them with a pad.
- The Load key's binding inside the inventory hasn't been confirmed
  (`MenuDeploy`, Space, was dropped because it seemed not to fire).
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

## Test checklist

Status: ✅ verified in Workbench, ⏳ awaiting testing.

| Feature | Test | Expected | Status |
|---|---|---|---|
| Item list | Open the US arsenal | `built item list: 173 entries … 0 without a display name` | ✅ |
| Categories | Click each category, press X | Grid shows only that category; counts match | ✅ |
| Empty category | On Fits, put the weapon away, reopen | Falls back to All | ✅ |
| Explosives | Open Explosives | 40mm rounds, rockets, shells listed; log `moved … single-round items` | ⏳ |
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
| Load key | Press the key shown on Load | Loads; one `key MenuRename` line | ⏳ |
| Respawn kit | Load a slot, die, respawn | "Saved loadout" offered | ✅ |
| Removed item | Save a kit with a mod item, remove the mod, Load | Refused, item named, no supplies taken | ⏳ |
| Supplies | Load at an arsenal that uses supplies | Charged for missing items only; refund for dropped | ⏳ |
| Spam | Click Load repeatedly | At most one load a second | ⏳ |
| Credits | Look under Save / Load | Grey "Zero fucking thanks to Tay and Saber" | ✅ |
| Modded gear | Load a weapon mod | Right categories, attachments fit | ⏳ |
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
| `OYB_ArsenalAlwaysStocked.c` | Every infantry arsenal also offers medical gear, vests and helmets |

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
