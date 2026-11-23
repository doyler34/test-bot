# Decoding the game's autosave

How the vanilla Arma Reforger 1.9 persistence save (`.save/.../WorldState/<id>.blob`) is laid out, and how it was
worked out. Decoded from the OGEveron01 server (Conflict Everon, game 1.9.0.37, no mods), savepoints 182–191.
Vanilla only: a modded server adds its own configs and serializers, which this doesn't cover.

The panel reads the save with three modules, each using part of what's below:

| Module | Uses |
|---|---|
| `panel/save_world.py` | Full bit reader: players, characters (position, hit zones), vehicles, mines, MGs for the live map |
| `panel/campaign_save.py` | Byte search of the `MilitaryBase` section for who holds each base, and `Seize` tasks for base names |
| `panel/save_edit.py` | Splits the file into sections and records and writes it back; swaps a player's loadout JSON |

## Coverage (savepoint 191)

| Part | Decoded | Not decoded |
|---|---|---|
| Records (3,706 across 13 sections) | All read, each matched to its persistence config | – |
| Entity blocks (prefab, position, rotation, script data, system states) | 18,349 / 18,349 | 0 |
| Components (every nested inventory item included) | 13,281 | 1,427 native engine components |

Still undecoded: radios, AI perception, weapon/muzzle state, vehicle compartments, lights, and car/heli controllers.
The full decoder kept these as raw bits with the serializers they could be.

## File layout

```
u8 1, u32 sectionCount (13)
per section: u32 nameLen, name, u32 recordCount
  per record: u32 byteLen, then a bitstream:
    u32 bitLen, u32 0
    UUID                 entity id
    block meta           4 bits + config GUID + 1 bit
    block entity         entity/state serializer data      (left out if empty)
    block components     one block per component, in entity order (components at their default are left out)
```

The config GUID is an `EntityPersistenceConfig` object in `Configs/Systems/Persistence`, which says which
serializers the entity's components use.

## Value encodings

The whole record is a bitstream read lowest bit first. Field names aren't stored; values follow the class's
member order.

| Type | Encoding |
|---|---|
| bool | 1 bit |
| unsigned number | flag bit; 0 → 7-bit value; 1 → 3-bit k, then (k+1)·8 bits |
| int | sign bit + unsigned number (0–127 takes 9 bits) |
| float | 32-bit IEEE |
| vector | 3 floats |
| string | unsigned length + bytes (not byte-aligned) |
| UUID | 128 bits |
| ResourceName (script field) | GUID as unsigned number (empty = 8 zero bits) |
| ResourceName (native prefab field) | present bit + GUID number |
| array / map / set | present bit, unsigned count, items (map = key, value pairs) |
| block (object) | `1` + u32 bit length + contents. Empty objects are left out entirely |
| typed block | contents start with the class name as a string, then members in order |
| WriteDefault field | always written, no present bit |

### Matching components

Components carry no type tag. Try each serializer in the record's config (with inheritance, e.g.
`Vehicles.conf → Car.conf → Vehicle.conf`) and keep the one that uses up exactly the block's bits.

### GenericEntitySerializer (most entities)

`version(2), prefab(present + GUID), [bit, position vector], [bit, rotation vector (yaw, pitch, roll)],
[bit, children: array of {id string, bool}]`. Bases with a `TransformMask` skip the rotation bit.

### Inventories (native)

- Storage = `version`, then an array of slots. Each slot is `{slot int, then one of: nested entity block | 1-bit empty | UUID reference}`.
- A nested entity is a full record without the length header (UUID, meta, entity, components), so clothing → items → magazines nest.
- `EquipedWeaponStorage` and `SCR_CharacterQuickBar` wrap the base storage in a block, then add weapon indices or quick slots `{index, entityId}`.
- `SlotManager` = array of `{slotName, nested entity}` (e.g. "Cargo" on trucks).
- Magazines decode as `{version, ammoCount}`.

### Native system states

- Doors: map of string → float (open amount).
- Garbage collector: map of UUID → float (seconds left), then an array of UUIDs.
- Destruction: version, then an array of `{unsigned id, float health}` (-1 = destroyed).

## What's in a save (savepoint 191)

- **Time and weather**: 15 Aug 1989 08:44:12, 21,600 s day length, "Overcast", looping on, lat 47.685 / lon -27.311.
- **Game mode**: callsign offset 15, no commanders, arsenal loadout cost ×0.1, kill feed type 20, deployable spawns on (5 tickets).
- **Military bases**: 53 records; 24 US, 16 USSR, 1 FIA with saved data. `buildingId` points at a Structure record in the same save.
  ```json
  {"serializer":"SCR_CampaignMilitaryBaseComponentSerializer","isHQ":false,
   "buildingId":"01a1e3bc-fdaa-8981-8400-00000040e279","callsign":47,"builtByPlayers":false,"builtFaction":""}
  ```
- **Players**: 211 PlayerController records, 192 with XP `{xp, xpSinceLastSpawn, survivalCycle}`, faction, and the saved
  arsenal loadout (decoded items plus the original JSON string).
- **Characters**: 459 (257 AI, 202 player bodies), with full inventory trees.
- **Vehicles**: 224, with fuel per tank, hit-zone damage (hull, engine, gearbox, fuel tank) and supplies carried.
- **Supply storages**: 101 records, 24,103 supplies in total.
- **AI groups**: 130, with faction, member and waypoint UUIDs, dormant alive/dead counts. Player squads also have radio
  frequency, max members, rally base and player identity UUIDs.
- **Tasks**: 22 `SCR_CampaignMilitaryTaskSave`, e.g. `USSR_SeizeCampaignMilitaryBase_40`, with position, state and linked base UUID.
- **World**: 9,475 destroyed world objects, 1,071 doors, 379 garbage timers, reconnect reservations, editor authors.

## How it was worked out

The serializer scripts and persistence configs ship inside the game, so the member order can be read from them.

**Game paks** (`addons/data/*.pak`): `FORM` / `PAC1` header, then a `FILE` chunk holding a tree. Each node is
`u8 type, u8 nameLen, name`. A folder is followed by `u32 childCount`; a file by `u32 offset, u32 compressedSize,
u32 size` and 12 more bytes. If the two sizes differ the data is zlib-compressed. Pull out `Configs/Systems/Persistence`
and the `*Serializer*.c` scripts.

**GUID → path** (`addons/data/resourceDatabase.rdb`): repeated `u32 length (with NUL), path, 00 06 00 00 00 00 00,
u64 GUID little-endian`. Use it to turn prefab and config GUIDs into names (`assets/save/prefabs.json` is the
prefab part of this).

**Checking the encoding**: a small Workbench plugin wrote known values with the game's own serializer and dumped the
bits, which settled the number, string and block encodings above. The plugin has since been removed from the mod.
