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

`Scripts/Game/OYBCore/Vehicles/`, driven from the OYB panel's **Vehicles** tab:

- **Hide** a depot vehicle and it's left out of every depot's list; hide a
  variant and the depot stops picking that version.
- **Price**: a set price is what the depot shows, checks and takes.
- **Limit groups**: named groups of exact vehicle prefabs, each with a limit per
  side on live vehicles (a vehicle's variants count as the vehicle).

Settings: `$profile:OYB_Core/vehicles.json`, written by the panel and read by
the server every minute:
`{"hidden": [...], "priceItems": [...], "priceCosts": [...], "limitsEnabled": true,
"groupNames": [...], "groupLimits": [...], "groupItems": [...], "groupOf": [...]}`.
`limitsEnabled: false` turns every limit off. A group's limit of -1 is
unlimited, 0 allows none. A prefab in no group is unlimited.

### How a limit is enforced

The depot request is handled in one go on the server (game 1.8.0.13):
`SCR_SpawnerRequestComponent.RPC_DoRequestCatalogSpawn` →
`SCR_CatalogEntitySpawnerComponent.InitiateSpawn` (the game's supply, side,
rank and cooldown checks via `GetRequestState`) → `PerformSpawn` (slot, spawn,
and only then the supply cost). OYB checks the limit in `InitiateSpawn` before
any of that, so a refused request spawns and costs nothing, and the player
gets "<group> limit reached: N/N". A vehicle is counted as soon as
`PerformSpawn` returns it, inside the same call, so a second request for the
last slot already sees it; a failed spawn returns nothing and counts nothing.

- **Ownership**: a depot vehicle counts for the depot's side (the game only
  sells to that side), whoever drives it later.
- **Counted until** it's wrecked (`SCR_VehicleDamageManagerComponent`'s damage
  state event, and a check on every count) or deleted. Empty or abandoned
  vehicles still count. Depot previews are never counted.
- **After a start or a restored save**, every live vehicle already in the world
  is counted once for its own side (its default faction), as the game doesn't
  record which side bought what. That includes HQ starting vehicles and
  map-placed vehicles of a limited group.
- **Not limited or counted**: Game Master and building-mode placements, the
  ambient vehicle spawns, and other mods' own spawners (not inspected).
- A lowered limit never removes vehicles.

The server writes `$profile:OYB_Core/vehicle_list.json` (every depot vehicle
and its variants, mods included) for the panel, retrying each minute until
the factions' catalogues are ready.

Files: `OYB_VehicleDepot.c` (depot, catalogue data, game mode and player
controller additions; named to compile first, as the others call it),
`OYB_VehicleLimits.c` (counting and limits) and `OYB_VehicleSettings.c`
(settings and the panel's list).
