# OYB Core

OYB's server-side extras, starting with the loading screen. Copy this folder's
contents into the Workbench project folder of the same name (the one with
`addon.gproj`).

## Loading screen

`Scripts/Game/OYBCore/LoadingScreen/OYB_LoadingScreen.c` puts OYB's image in
the picture slot of the game's scenario loading screen. Everything else on
that screen (progress bar, spinner, hints, title, description) is the game's
own. It works with any scenario, Conflict included; no mission header change.

Which loading stage: the scenario loading screen, once the scenario's details
have arrived. The game's start-up splash and the moment before the scenario's
details arrive (the first, plain screen) stay as they are: OYB Core's scripts
can only change what is shown after the game has loaded its mods, and the
picture slot is only filled once the scenario is known.

The image is `UI/Textures/LoadingScreen/OYB_LoadingScreen.edds`, imported in
Workbench from a 16:9 PNG; its resource name (GUID from the `.meta`) is
`OYB_LoadingScreen.IMAGE`. To change the picture, re-import over the same file
so the GUID stays.

## Vehicle limiter

`Scripts/Game/OYBCore/Vehicles/` works on the game's vehicle depots (the
catalog spawner), driven from the OYB panel's **Vehicles** tab:

- **Hide** a vehicle and it's left out of every depot's list; hide a variant
  and the depot stops picking that version.
- **Price**: a set price is what the depot shows, checks and takes.
- **Limit per side**: how many of a vehicle each side may have alive at once
  from the depots (destroyed or deleted ones stop counting). No limit set means
  unlimited. A request over the limit is refused on the server and the player
  gets a pop-up.

The server writes `$profile:OYB_Core/vehicle_list.json` (every depot vehicle
and its variants, mods included) when the game starts, and reads
`$profile:OYB_Core/vehicles.json` (from the panel) every minute. Hidden
vehicles and prices are sent to players when they join and when they change.

Files: `OYB_VehicleDepot.c` (the depot, catalogue data, game mode and player
controller additions) and `OYB_VehicleSettings.c` (settings, the list for the
panel, and counting live vehicles). The depot file is named to compile first,
because the settings file calls the methods it adds.
