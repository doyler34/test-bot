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

The image goes in `UI/Textures/LoadingScreen/OYB_LoadingScreen.png`, imported
in Workbench; its resource name goes into `OYB_LoadingScreen.IMAGE`.
