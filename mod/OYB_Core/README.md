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

The image is `UI/Textures/LoadingScreen/OYB_LoadingScreen.edds`, imported in
Workbench from a 16:9 PNG; its resource name (GUID from the `.meta`) is
`OYB_LoadingScreen.IMAGE`. To change the picture, re-import over the same file
so the GUID stays.

## Vehicles: hiding, prices and limits

`Scripts/Game/OYBCore/Vehicles/`, driven from the OYB panel's **Vehicles** tab.
In Conflict (game 1.8.0.13) vehicles come from the **building menu**; the old
catalogue **depots** are still handled for maps and mods that use them.

- **Hide**: a hidden vehicle is left out of the building menu and refused by the
  server, and left out of every depot's list. Hiding a single variant only
  works at depots; the building menu picks its random variant after the
  request is checked.
- **Price**: depots only. A set price is what the depot shows, checks and takes.
  The building menu's cost is listed on the panel but not changed.
- **Limit groups**: named groups of exact vehicle prefabs, each with a limit per
  side on live vehicles (a vehicle's variants count as the vehicle).

Settings: `$profile:OYB_Core/vehicles.json`, written by the panel and read by
the server every minute:
`{"hidden": [...], "priceItems": [...], "priceCosts": [...], "limitsEnabled": true,
"groupNames": [...], "groupLimits": [...], "groupItems": [...], "groupOf": [...]}`.
`limitsEnabled: false` turns every limit off. A group's limit of -1 is
unlimited, 0 allows none. A prefab in no group is unlimited.

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

Either way the player gets "<group> limit reached: N/N", a second request for
the last slot already sees the first vehicle, and a failed spawn counts
nothing.

- **Ownership**: a building-menu vehicle counts for the placing player's side, a
  depot vehicle for the depot's side, whoever drives it later.
- **Counted until** it's wrecked (`SCR_VehicleDamageManagerComponent`'s damage
  state event, and a check on every count) or deleted. Empty or abandoned
  vehicles still count. Previews are never counted.
- **After a start or a restored save**, every live vehicle already in the world
  is counted once for its own side (its default faction), as the game doesn't
  record which side bought what. That includes HQ starting vehicles and
  map-placed vehicles of a limited group.
- **Not limited or counted**: Game Master placements, the ambient vehicle
  spawns, and other mods' own spawners (not inspected).
- A lowered limit never removes vehicles.

The server writes `$profile:OYB_Core/vehicle_list.json` for the panel: every
faction catalogue vehicle that a depot sells (depot data) or the building menu
places (in the building manager's prefab list, or editor data for building
mode), with its variants, factions, depot price and building cost. The log
shows, per faction, how many catalogue vehicles were found and how many are in
each menu, plus the data on a few entries. An empty result never replaces a
list already written; it's tried again each minute.

Files: `OYB_VehicleDepot.c` (depot, catalogue data, game mode and player
controller additions; named to compile first, as the others call it),
`OYB_VehicleLimits.c` (counting and limits), `OYB_VehicleMenu.c` (the building
menu) and `OYB_VehicleSettings.c` (settings and the panel's list).
