# Handover — where the OYB mods stand (29 October 2026)

Read CLAUDE.md first. This file is for a session running on the VPS
(`srv1669502`), which can read the game server's logs directly. The game
server is the AMP instance `ArmaReforger01`; its logs are in
`/home/amp/.ampdata/instances/ArmaReforger01/arma-reforger/1874900/AReforgerMaster/logs/logs_*/console.log`
(newest folder = current run). Its profile is the `profile` folder next to
`logs`; OYB's files are in `profile/OYB_Core/` and `profile/OYB_Arsenal/`.

Nothing in the mods can be compiled here: Workbench and the game run on the
owner's Windows PC. Compiling, playing and publishing are the owner's.

## OYB Core vehicle limiter (`mod/OYB_Core`, see its README)

Latest build marker: `vehicles-2026-10-28f`. The server log's first OYB line
says which build is running (`OYB_CORE vehicles: build …`). At last check the
server ran `28d`; `28f` was handed over to publish.

Proven by logs:
- The list (every vehicle, ~347 rows) and the limits work; refusals log as
  `OYB_CORE vehicle limit: <side> has N/N <group>, refused <prefab> …`.
  From `28f` the line also lists the counted vehicles (`counted: …`).
- A mod lists a missing prefab `{2FCF3A82322F7390}IFV/BMP2/BMP2_Olive.et`
  in its `Configs/Editor/PlaceableEntities/Vehicles/Vehicles.conf`; it used to
  crash the menu's card loading. Fixed in `OYB_VehicleMenu.c` (logged as
  "can't be loaded … left out").
- `Entity budget exceeded for player` lines are the game's own budget check
  (rank, supplies or the base's limit), not OYB.

Open bug: setting a price on a building-menu vehicle makes the cards show no
price (resetting the prices brings them back). No script error in either log.
`28e`/`28f` log `OYB_CORE price <prefab> (card|check|charge): price, own cost,
costs before, after` once per vehicle; those lines (from the owner's game
log, since the menu is drawn on the client) are needed to find the cause. Code:
`mod/OYB_Core/Scripts/Game/OYBCore/Vehicles/OYB_VehicleBudget.c`. Fallback if
unresolved: building-menu prices off, depot prices only.

Config note: the owner's "Attack Helis RU" group contains the Mi-8
transports, so they share its limit.

## OYB Arsenal (`mod/OYB_Arsenal`, see MILESTONES.md and README)

Owner considers it working. Milestone 1 of the development plan is
implemented and awaiting testing (bad settings file keeps the last good
settings; switched-off arsenals offer no added items). Milestones 2–7 not
started.

## Panel

Runs from `/root/oyb-panel` (`cd /root/oyb-panel && git pull && sudo systemctl restart oyb-panel`).
Tests: `python -m unittest discover -s tests` from the repo root.
