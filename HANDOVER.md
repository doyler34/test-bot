# Handover: OYB mods (updated 29 October 2026)

Read CLAUDE.md first. Status words: CONFIRMED = the owner saw it working;
AWAITING TEST = in `main`, not tested; OPEN = not solved.

## Where things run

- `/root/test-bot` on the VPS (`srv1669502`) is the checkout the Gazlagom sync
  reads. It's on `main` (switched on 29 Oct; it was on an old branch name with
  the same contents as `main`).
- The live game server is NOT on the VPS. The VPS has only an old test server
  in `/root/reforger` (no OYB mods, last run 17 Oct). The live AMP instance
  `ArmaReforger01` runs on the machine the panel's command box runs on; its
  logs are in
  `/home/amp/.ampdata/instances/ArmaReforger01/arma-reforger/1874900/AReforgerMaster/logs/logs_*/console.log`
  there. Read them through the panel's command box (the owner pastes output).
- Workbench and the game run on the owner's Windows PC. The owner compiles,
  plays and publishes; nothing in `mod/` can be compiled anywhere else.
- The building menu is drawn on each player's own game, so building-menu
  lines (`OYB_CORE price …`, `OYB_CORE building menu: …`) are in the owner's
  game log: `Documents\My Games\ArmaReforger\logs\<newest>\console.log`.

## The two known bugs

### 1. Arsenal bar left across the screen (OYB Arsenal): fix AWAITING TEST

- `026dd0b` + `0fb9d1b` (OYB stops holding the panel's widgets and removes
  its own on close; layout kept as before): the owner tested in Workbench,
  "the bar didn't stay, layout good". CONFIRMED in Workbench.
- `24863c2`, merged as `8e546cb` (another session): also removes the game's
  own traverse title strips on close and logs `OYB_ARSENAL UI panel=…
  attached / built / visible / detach begin / detach complete`. AWAITING TEST.
- If the bar shows again on that build, the panel lines show which panel
  wasn't cleaned up (one `detach complete` per `attached`).

### 2. Heli price bug (OYB Core): NO FIX YET, diagnostic logging only

- CONFIRMED: with a building-menu vehicle priced on the panel, the building
  menu cards show no price; resetting the prices brings them back. No script
  error from OYB in the server's or the owner's game log.
- The price code (`mod/OYB_Core/Scripts/Game/OYBCore/Vehicles/OYB_VehicleBudget.c`)
  was checked line by line: it never writes to the prefab or its UI info; it
  replaces the campaign cost entry in the list the game builds for each
  question (card, supply check, charge, refund), worked out fresh each time.
  Prices reverting on reset agrees with that. Why the card ends up blank is
  unknown.
- `28e`/`28f` add `OYB_CORE price <prefab> (card|check|charge): price, own
  cost, costs before, after`, once per vehicle. Those lines from the owner's
  game log, with one heli priced and the heli pad opened, are what's needed.
- "Couldn't spawn any more even under the limit": the server log for that
  session (12:38–12:43) has only the game's own `Entity budget exceeded for
  player` (rank, supplies or the base's limit), no OYB refusal. Not OYB.
  The panel can't rename vehicles (only arsenal items).
- Fallback if unresolved: building-menu prices off, depot prices only.

## OYB Core vehicle limiter (`mod/OYB_Core`, see its README)

- Build marker `vehicles-2026-10-28f`; the server log's first OYB line names
  the running build (`OYB_CORE vehicles: build …`). `28d` was running at last
  check; `28f` was handed to the owner to publish.
- CONFIRMED from logs: the list (~347 rows, catalogues + building list + mod
  scan) and limits work; refusals log `OYB_CORE vehicle limit: <side> has N/N
  <group>, refused <prefab> …`, and from `28f` list the counted vehicles
  (`counted: <prefab> at <pos> (in the world at start)`).
- CONFIRMED: a mod lists a missing prefab
  `{2FCF3A82322F7390}IFV/BMP2/BMP2_Olive.et` in its
  `Configs/Editor/PlaceableEntities/Vehicles/Vehicles.conf`; OYB now skips it
  instead of the game's card loading crashing (`39d33ad`, the owner's log shows
  "can't be loaded … left out").
- Owner's config: the "Attack Helis RU" group contains the Mi-8 transports,
  so they share its limit. Vehicles already in the world at server start count
  once for their own side.
- Not limited: Game Master placements, ambient spawns, other mods' spawners.

## OYB Arsenal (`mod/OYB_Arsenal`, see MILESTONES.md and README)

- The owner says it works apart from bug 1.
- MILESTONES.md milestone 1 AWAITING TEST: a bad or unreadable
  `settings.json` is refused and the last good settings stay; switched-off
  arsenals offer no added items. Tests are in MILESTONES.md.
- Medical tent and ambulance are left medical-only (`3a6a30b`), kits with
  OYB-offered items can be saved (`c5ad821`): both in `main`.
- Another session added a log line `OYB_ARSENAL settings sent to N players: …`
  (`3e8c2f1`, `ff8c50a`), AWAITING TEST.

## Panel

Runs from `/root/oyb-panel`: `cd /root/oyb-panel && git pull`, then
`sudo systemctl restart oyb-panel`. Tests: `python -m unittest discover -s tests`
from the repo root (920 pass).
