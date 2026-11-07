# OYB Core

OYB's server-side extras, starting with the loading screen. Copy this folder's
contents into the Workbench project folder of the same name (the one with
`addon.gproj`).

## Loading screen

`Scripts/Game/OYBCore/LoadingScreen/OYB_LoadingScreen.c` puts OYB's image
across the whole scenario loading screen, in place of the background: scaled to
cover the screen at its own shape and centred, so other screen shapes crop the
edges instead of stretching it. The scenario's photo, title, description and
the Reforger logo are hidden once the scenario is known (the image carries
OYB's branding); the progress bar, spinner, hints and warnings stay the game's
own. It works with any scenario, Conflict included; no mission header change.

Which loading stage: the scenario loading screen, from when it opens once the
game has loaded its mods. The game's start-up splash, and the connecting and
mod download before that, stay as they are: OYB Core's scripts only run once
the mods are loaded.

The image is `UI/Textures/LoadingScreen/OYB_LoadingScreen2.edds`, imported in
Workbench from a 16:9 PNG; its resource name (GUID from the `.meta`) is
`OYB_LoadingScreen.IMAGE`. To change the picture, import a new PNG and put its
resource name there.

## Announcements

`Scripts/Game/OYBCore/Announcements/OYB_Announcements.c`, set on the OYB
panel's **Announcements** tab. Each player sees them as a hint box on their own
screen, never in chat: the title as its heading and a message under it. The
welcome shows once they're in the world, then the messages every so many
minutes, in turn. The panel puts all its lines into one message, written as
both the welcome and the only message, so the same popup shows each time. The panel writes
`$profile:OYB_Core/announcements.json`; the server reads it when a game starts
and sends it to each player as they join, so a change applies from the next
game. The log says `OYB_CORE announcements: welcome 1, N messages every M minutes`.

## Rules, AFK kick and teamkill popup

`Scripts/Game/OYBCore/Players/OYB_Players.c`, set on the panel's
**Announcements** tab, which writes `$profile:OYB_Core/players.json`, read when
a game starts.

- **AFK kick**: each player's game tells the server when its camera or
  character moved. A player with neither for the set minutes gets a warning
  popup, and a minute later is kicked. Admins and Game Masters are never
  kicked; it can be limited to when enough players are on. Off (0) until set.
- **Rules**: each joining player gets them in the game's own one-button dialog
  (`SCR_CommonDialogs.CreateTimeoutOkDialog`, retitled) and has to press OK;
  closing it any other way brings it back. Their game keeps the rules they
  accepted in its own `$profile:OYB_Core/rules_accepted.json`, so they only
  see them again once the rules change.
- **Teamkill popup**: the killer sees the panel's message straight away, for a
  kill the game counts as a teamkill.

The log says `OYB_CORE players: AFK kick after N minutes ...`, and names
anyone kicked for being AFK.

## Vehicles: hiding and limits

`Scripts/Game/OYBCore/Vehicles/`, driven from the OYB panel's **Vehicles** tab.
In Conflict (game 1.8.0.13) vehicles come from the **building menu**; the old
catalogue **depots** are still handled for maps and mods that use them. The
startup log names the build (`OYB_CORE vehicles: build vehicles-...`), and so
does `vehicle_list.json` and the panel's Vehicles page.

- **Hide**: a hidden vehicle is left out of the building menu and refused by the
  server, and left out of every depot's list and refused there too. Hiding a
  single variant only works at depots; the building menu picks its random
  variant after the request is checked.
- **Costs**: a vehicle's building menu cost can be set on the panel. The panel
  writes `$profile:OYB_Core/vehicle_costs.json`; when a game starts the server
  puts each cost into the vehicle's own data, and sends the list to each
  player as they join, so the card, the supply check and the charge all read
  it as if the vehicle came with it. A change takes effect at the next game,
  and a vehicle taken off the list gets its own cost back then. The log says
  `OYB_CORE costs: N vehicle costs set for this game`, and names any vehicle
  whose cost didn't take. Depot prices stay the game's.
- **Base building limit**: a vehicle placed at a base doesn't count against
  the base's building limit, as the game's own vehicles don't. Some modded
  helicopters carry a props cost big enough that two of them filled a base's
  whole limit; the change is taken off again and logged as `OYB_CORE vehicles:
  … would have changed the base's building count`.
- **Broken mod entries**: a building or Game Master menu entry whose prefab
  can't be loaded (a mod listing a file that isn't there) used to stop the
  game loading the menu's cards, so the cards after it showed no price. It's
  left out instead and logged as `OYB_CORE building menu: … can't be loaded`.
- **Limits by category**: each category (Helicopters, Planes, Armour, Trucks,
  Cars, Turrets, Other) can have a limit per side on its live vehicles, all of
  the category together (a vehicle's variants count as the vehicle).
- **Custom groups** (optional): named groups of exact vehicle prefabs with a
  limit of their own; a vehicle in one counts there instead of in its
  category.

Settings: `$profile:OYB_Core/vehicles.json`, written by the panel (whole, by
replacing the file) and read by the server every minute:
`{"hidden": [...], "limitsEnabled": true,
"groupNames": [...], "groupLimits": [...], "groupItems": [...], "groupOf": [...],
"categoryNames": [...], "categoryLimits": [...]}`.
`limitsEnabled: false` turns every limit off. A group's limit of -1 is
unlimited, 0 allows none. A prefab in no group is unlimited. A file that isn't
valid (bad JSON, lists of different lengths, a vehicle in a group that doesn't
exist) is refused and logged, and the settings in force stay.

### How a limit is enforced

**Building menu** (`SCR_CampaignBuildingPlacingEditorComponent`), one server
RPC: `SCR_PlacingEditorComponent.CreateEntityServer` → `CanPlaceEntityServer`
(the game's side, cooldown and budget checks) → `IsThereEnoughBudgetToSpawn`
(supplies) → `SpawnEntityResource` → `OnEntityCreatedServer`; the supplies are
taken afterwards, when the new entity's budget is registered
(`SCR_CampaignBuildingManagerComponent.OnEntityCoreBudgetUpdated`). OYB checks
the limit at the end of `CanPlaceEntityServer`, so a refused vehicle spawns and
costs nothing, and counts the new vehicle in `OnEntityCreatedServer`, inside the
same call.

**Depot** (`SCR_CatalogEntitySpawnerComponent`), one server RPC:
`SCR_SpawnerRequestComponent.RPC_DoRequestCatalogSpawn` → `InitiateSpawn` (the
game's supply, side, rank and cooldown checks via `GetRequestState`) →
`PerformSpawn` (slot, spawn, and only then the supply cost). OYB checks the
limit in `InitiateSpawn` and counts the vehicle when `PerformSpawn` returns it.

Either way the player gets "<group> limit reached: N/N", and because the check,
the spawn and the count happen in one call on the server, a second request for
the last slot already sees the first vehicle; a failed spawn counts nothing,
so nothing has to be given back.

- **Ownership**: a building-menu vehicle counts for the placing player's side, a
  depot vehicle for the depot's side, whoever drives it later. A limited
  vehicle whose side can't be worked out is refused.
- **Counted until** it's wrecked (`SCR_VehicleDamageManagerComponent`'s damage
  state event, and a check on every count) or deleted. Empty or abandoned
  vehicles still count. Previews are never counted.
- **After a start or a restored save**, once the editor core is up and the save
  has finished loading, every live vehicle already in the world is counted
  once for its own side (its default faction): the game doesn't record which
  side bought what, so a captured vehicle counts for its original side after a
  restart. That includes HQ starting vehicles and map-placed vehicles of a
  limited group.
- **Not limited or counted**: Game Master placements, the ambient vehicle
  spawns, and other mods' own spawners (not inspected).
- A lowered limit never removes vehicles.
- A new game (scenario restart) clears the counts, timers and event hooks of
  the last one before starting again.

### The vehicle list

The server writes `$profile:OYB_Core/vehicle_list.json` (version 3) for the
panel, with each row's sources:

- **catalogue**: faction catalogue vehicles that a depot sells (depot data) or
  the building menu places (editor data for building mode, or in the building
  manager's list);
- **building**: vehicles in the building manager's list, the list the building
  menu is filled from (its structures and compositions are left out);
- **scan**: every placeable vehicle prefab (a `Vehicle` with an editable entity
  of the vehicle type that the editor can place) in the base game's
  `Prefabs/Vehicles` and each mod's `Prefabs`, checked a few at a time after
  startup. These may not be in any menu; the panel says so.

Rows carry variants, factions, depot price and building cost. The log prints a
summary line whenever it changes (catalogue entries, building list size, scan
progress, row count) with per-faction details. An empty result or a failed
write never replaces a list already written; it's tried again.

Files: `OYB_VehicleBudget.c` (building-menu costs for the list),
`OYB_VehicleDepot.c` (depot, catalogue data, game mode and player controller
additions), `OYB_VehicleLimits.c` (counting and limits), `OYB_VehicleMenu.c`
(the building menu) and `OYB_VehicleSettings.c` (settings and the panel's list).
