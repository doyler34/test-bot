# OYB Arsenal (phase 1: look, don't equip)

The first runnable step of the OYB arsenal. Walk up to an arsenal, pick **Open
OYB Arsenal**, and a window lists exactly what that arsenal offers: by
category, with search, paging, and a details panel (supply cost, required rank,
arsenal type and mode). It works with a mouse and a controller.

It only reads. Nothing is equipped, spawned, charged or saved from this menu
yet, and the vanilla arsenal actions are left as they are. It's for testing in
Workbench on your own PC. It doesn't go on the live servers.

The item list comes from the arsenal's own filtered list
(`SCR_ArsenalComponent.GetFilteredArsenalItems`): its overwrite config if it has
one, otherwise its faction catalogue limited to its item types and modes. An
item that's installed but not offered by that arsenal never shows up.

## Set up (once)

Three of these steps are done in Workbench because they create files with IDs
that only Workbench can make. Each one takes a minute.

1. **Get this folder.** On GitHub press **Code → Download ZIP**, unzip it, and
   copy `mod/OYB_Arsenal` to `Documents\OYB_Arsenal`.
2. **Open it.** Launch Arma Reforger Tools, press **Add Existing**, pick
   `OYB_Arsenal.gproj`, and double-click it. If a red error box appears,
   screenshot it and send it over: it names the exact line.
3. **Make the empty layout.** In the **Resource Browser**, inside
   `OYB_Arsenal`, make the folders `UI/layouts/OYBArsenal`. Right-click in that
   folder, create a new **Layout**, and call it `OYB_ArsenalMain`. Open it: the
   root widget should be a **FrameWidget** that fills the screen (anchors
   stretched on both sides, offsets 0). Leave it empty and save. The menu
   builds everything inside it.
4. **Register the menu.** In the Resource Browser search `chimeraMenus`,
   right-click `chimeraMenus.conf` → **Override in OYB_Arsenal**, and open the
   copy. Add one entry to the menu list:
   - **Name:** `OYB_ArsenalMenu` (exactly this; it must match the script)
   - **Class:** `OYB_ArsenalMenu`
   - **Layout:** pick `OYB_ArsenalMain.layout` from step 3

   Save with Ctrl+S.
5. **Put the action on an arsenal.** Search the Resource Browser for `Arsenal`
   and pick the arsenal box you test with (the US or USSR one from Conflict is
   fine). Right-click it → **Override in OYB_Arsenal**, open the copy, and
   select its **ActionsManagerComponent**. Under **Additional Actions** press
   **+** and choose `OYB_OpenArsenalUserAction`. Set its **Parent Context
   List** to the same context the box's other actions use, and its **UI Info →
   Name** to `Open OYB Arsenal`. Save with Ctrl+S.

If any of these menus look different from the description, send a screenshot
of what you see.

## Run it

1. In the **Resource Browser** search `GM_Everon`, double-click that world,
   and press **F5**.
2. As Game Master, place the arsenal box from step 5 and a soldier, and take
   control of the soldier.
3. Walk up to the box and pick **Open OYB Arsenal**.
4. Try it with the mouse, then with a controller:
   - Categories down the left; LB / RB also switch category.
   - Hover or move onto an item to see its details on the right.
   - Search filters as you type (on a controller, select the box to type).
   - **Previous page** / **Next page** under the list.
   - Back (Esc / B) closes it.
5. In the **Log Console** type `OYB_ARSENAL_UI` in the filter, copy every
   line, and send them over, with a screenshot of the window.

## What the log tells us

- `built item list`: how many entries the arsenal gave, how many were
  different items, and how many had no display name in the game.
- `opened`: how many items, how long the window took to open in ms, and
  whether the list came from the cache (it should on the second open).
- `arsenal offer changed`: the arsenal changed what it offers, so the list is
  rebuilt next time.
- `arsenal was removed or disabled while open`: the window closed itself.

## Phase 0: the game's API, checked against 1.8.0.13

Checked against Bohemia's published scripts for 1.8.0.13
(`BohemiaInteractive/Arma-Reforger-Script-Diff`, 3 September 2026). That is the
game's own source, but it is not the same as compiling in Workbench: if
Workbench disagrees, Workbench wins.

| Used here | Found in | Matches the brief |
|---|---|---|
| `SCR_ArsenalComponent.FindArsenalComponent(notnull IEntity, bool getFromSlotted = true)` | `Game/Components/Arsenal/SCR_ArsenalComponent.c:242` | yes |
| `GetFilteredArsenalItems(out notnull array<SCR_ArsenalItem>, EArsenalItemDisplayType = -1)` | same file `:351` | yes; it handles the overwrite config itself |
| `GetOnArsenalUpdated()` → `ScriptInvokerArsenalUpdated`, callback `(array<ResourceName>)` | same file `:471`, `:5` | yes |
| `GetAssignedFaction()` → `SCR_Faction` | same file `:323` | yes |
| `GetArsenalSaveType()`, `IsArsenalEnabled()`, `GetSupplyCostType()` | same file `:174`, `:98`, `:154` | yes |
| `SCR_EArsenalSaveType` (0, 10, 20, 21, 30) | same file `:624` | yes |
| `SCR_ArsenalItem.GetItemResourceName / GetItemType / GetItemMode / GetRequiredRank / GetSupplyCost(costType)` | `Game/Components/Arsenal/SCR_ArsenalItem.c` | yes |
| Item display name | `SCR_EntityCatalogManagerComponent.GetEntryWithPrefabFromAnyCatalog(ITEM, prefab, faction)` then `GetEntityName()` | **differs**: `SCR_ArsenalItem` has no public link to its entry, so the name is looked up by prefab. (Modding `SCR_ArsenalItem` to reach it broke config loading in Workbench: `Unknown class 'SCR_ArsenalItem'`.) Items with no UI info are named `MISSING` by the game; those show their file name instead |
| `ScriptedUserAction` with `PerformAction`, `CanBeShownScript`, `CanBePerformedScript`, `HasLocalEffectOnlyScript` | `Game/generated/UserAction/ScriptedUserAction.c` | yes |
| Adding the action without editing a prefab | `BaseActionsManagerComponent` has no add-action call | **no**: it has to go on the prefab (setup step 5) |
| `MenuManager.OpenMenu(preset, userId, unique, hideParent)`, `modded enum ChimeraMenuPreset` | `GameLib/generated/Menu/MenuManager.c:44`, `Game/UI/Menu/ChimeraMenuBase.c:3` | yes; the menu also needs its entry in `chimeraMenus.conf` (setup step 4) |
| Widgets made in script: `WorkspaceWidget.CreateWidget`, `FrameSlot`, `LayoutSlot`, `AlignableSlot`, `EditBoxWidget` | `Core/generated/UI/` | yes; the game itself makes text and image widgets this way |
| `MenuBack`, `MenuTabLeft`, `MenuTabRight` input actions | used by vanilla menus | yes |
| `SCR_PlayerArsenalLoadout.ReadLoadoutString(IEntity, SaveContext)` / `ApplyLoadoutString(IEntity, LoadContext)` | `Game/GameMode/Loadout/SCR_PlayerArsenalLoadout.c:200`, `:383` | yes, both static; not used until the saved-kits phase |

## Tested and not tested

- **Not compiled and not run yet.** Every name above was checked against the
  game's source, but none of this has been through Workbench.
- Not tried: the layout, menu and prefab steps; how the window looks at
  different resolutions; controller focus moving between the three columns;
  the on-screen keyboard for search.

## What this does not do yet

- Equip anything. That's phase 2, done on the server with proper checks and
  error messages.
- Weight, calibre and ammo type. The details panel says "not read yet" rather
  than guessing.
- Saved kits, restock, weapon building, admin diagnostics. Later phases.
- Nothing from Bacon or WCS is used or copied.
