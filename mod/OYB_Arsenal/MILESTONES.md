# OYB Arsenal milestones

Statuses: Not started · In progress · Implemented — awaiting testing ·
Verified complete · Blocked. Nothing here has been compiled or run unless it
says so; "verified" means tested in game and confirmed by the owner.

| # | Milestone | Status |
| --- | --- | --- |
| 1 | Project audit and core functionality | Implemented — awaiting testing |
| 2 | Keyboard and controller support | Not started |
| 3 | Loadout saving and persistence | Not started |
| 4 | Modded equipment compatibility | Not started |
| 5 | Performance optimisation | Not started |
| 6 | Security, validation and ownership protection | Not started |
| 7 | Final integration and testing | Not started |

## Milestone 1 — project audit and core functionality

Already working and confirmed in game by the owner before this milestone:
the arsenal opens and closes, items show in their categories, items and
kits are taken and fitted, saved loadouts save and load, the leftover title
bar after closing is gone, medical tents and ambulances show only medical
items, and kits with OYB-offered items can be saved.

Audit findings (from `OYB_Arsenal_Full_Audit.md`, 29 October 2026) and what
this milestone did with them:

| Finding | Status |
| --- | --- |
| 1. A bad or unreadable `settings.json` cleared hidden items, sides, prices and added items | Fixed — awaiting testing. The last good settings stay, the file is refused and logged; the panel writes it whole |
| 3. Added items still offered by an arsenal the game switched off | Fixed — awaiting testing. `GetExtrasFor` offers nothing for an arsenal that's off, which also stops the server handing them out there |
| 11. Title strip left on screen | Verified complete by the owner (`0fb9d1b`) |
| 2. Loadout charge and refund on a failed or partial load | Milestone 3 |
| 7. Loadout rank and blacklist checks on loading | Milestone 3 |
| 4. Lowered price still needs the original price in stock | Milestone 6 |
| 5. Refund cap and price adjustment can disagree | Milestone 6 |
| 6. Fast successive clothing swaps | Milestone 6 |
| 8. "Comes fitted" parts priced from references | Milestone 4 |
| 9. Weapon-fit cache not refreshed when one part replaces another | Milestone 5 |
| 10. Renames shared by items with the same original name | Documented design; left as is |

Unexplained: two `OverlayWidget Frame0 has invalid slot GridWidgetSlot`
errors each time the arsenal opens. OYB's own layout calls are all on
widgets in horizontal or vertical layouts; the warning names an overlay in a
grid, and it also appears in a player's own game log. Its source isn't
established.

### Tests still to run (Workbench or a test server)

1. Settings kept on a bad file: hide an item on the panel, wait a minute,
   then replace `settings.json` with `{` and wait a minute. The log shows
   `OYB_ARSENAL … refused (not valid JSON), keeping the settings in force`
   and the item stays hidden. Put a valid file back: the log shows
   `settings loaded` and it applies.
2. Settings kept on mismatched lists: edit `settings.json` so `priceItems`
   has one more entry than `priceCosts`. Same result as test 1, with
   "priceItems and priceCosts differ in length".
3. Switched-off arsenal: add an item on the panel, place an arsenal, switch
   it off as Game Master (or by the game mode's arsenal types). The added
   item is gone from it; switch it on again and it's back.
4. Regression: open, browse and close the arsenal; take items, fit an
   attachment, save and load a loadout.
