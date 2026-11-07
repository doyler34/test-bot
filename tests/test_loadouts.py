import json
import struct
import unittest
import uuid

from panel import loadouts
from panel.db import PanelDB

PLAYER = "5d6b2829-aad3-48da-af92-5a0b8579df25"
RIFLE, LAUNCHER, MAG, SALINE = "AAAAAAAAAAAAAAA1", "AAAAAAAAAAAAAAA2", "AAAAAAAAAAAAAAA3", "AAAAAAAAAAAAAAA4"
NAMES = {RIFLE: "Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et", LAUNCHER: "Prefabs/Weapons/Launchers/M72/Launcher_M72A3.et",
         MAG: "Prefabs/Weapons/Magazines/Magazine_556x45_STANAG_30rnd_M855_Ball.et",
         SALINE: "Prefabs/Items/Medicine/SalineBag_01/SalineBag_US_01.et"}


def kit(*prefabs):
    slots = {str(i): {"prefab": p} for i, p in enumerate(prefabs)}
    return {"faction": "US", "arsenalLoadout": {"prefab": "X", "storages": [
        {"id": "SCR_CharacterInventoryStorageComponent:1", "slots": {"0": {"prefab": RIFLE, "storages": [
            {"id": "SCR_UniversalInventoryStorageComponent:2", "slots": slots}]}}}]}}


def shifted(data, bits):
    return (int.from_bytes(data, "little") << bits).to_bytes(len(data) + 1, "little")


def save(*records):
    body = b""
    for identity, side, loadout, bits in records:
        record = b"\x00" * 8 + uuid.UUID(identity).bytes + b"\x00" * 6 + shifted(bytes([len(side) * 2]) + side.encode(), 1)
        if loadout:
            record += shifted(json.dumps(loadout, separators=(",", ":")).encode(), bits)
        body += struct.pack("<I", len(record)) + record
    return b"\x01junk" + b"\x06\x00\x00\x00Player" + struct.pack("<I", len(records)) + body + b"\x09\x00\x00\x00Character"


class LoadoutTests(unittest.TestCase):
    def test_reads_each_player(self):
        other = str(uuid.uuid4())
        found = loadouts.players(save((PLAYER, "US", kit(MAG), 3), (other, "USSR", None, 0)))
        self.assertEqual(found[PLAYER]["side"], "US")
        self.assertEqual(found[PLAYER]["loadout"]["arsenalLoadout"]["prefab"], "X")
        self.assertEqual(found[other], {"side": "USSR", "loadout": None})

    def test_tree_names_and_nests_items(self):
        items = loadouts.tree(kit(MAG, "FFFFFFFFFFFFFFFF"), NAMES)
        self.assertEqual(items[0]["name"], "Rifle M16A2")
        self.assertEqual([i["name"] for i in items[0]["items"]],
                         ["Magazine 556x45 STANAG 30rnd M855 Ball", "FFFFFFFFFFFFFFFF"])
        self.assertEqual(loadouts.weapons(items), ["Rifle M16A2"])

    def test_title_and_fingerprint(self):
        rifle = loadouts.tree(kit(MAG), NAMES)
        self.assertEqual(loadouts.title(loadouts.tree(kit(LAUNCHER, MAG), NAMES)), "Rifle M16A2 · Launcher M72A3")
        self.assertEqual(loadouts.title([]), "No weapon")
        self.assertEqual(loadouts.fingerprint(rifle), loadouts.fingerprint(loadouts.tree(kit(MAG), NAMES)))
        self.assertNotEqual(loadouts.fingerprint(rifle), loadouts.fingerprint(loadouts.tree(kit(MAG, MAG), NAMES)))

    def test_label(self):
        self.assertEqual(loadouts.label("Prefabs/Items/Medicine/MorphineInjection_01/MorphineInjection_01.et"),
                         "MorphineInjection")


class LoadoutStoreTests(unittest.TestCase):
    def test_counts_each_loadout_only_for_players_on(self):
        db = PanelDB(":memory:")
        medic = {"identity": PLAYER, "side": "US", "kit": "a", "title": "Rifle M16A2", "loadout": "[]"}
        at = dict(medic, kit="b", title="Rifle M16A2 · Launcher M72A3")
        away = dict(medic, identity=str(uuid.uuid4()))
        db.add_loadouts("s1", 100, [medic, away], {PLAYER})
        db.add_loadouts("s1", 200, [at], {PLAYER})
        db.add_loadouts("s1", 300, [at], {PLAYER})
        mine = db.loadouts(PLAYER)
        self.assertEqual([(k["title"], k["times"]) for k in mine], [("Rifle M16A2 · Launcher M72A3", 2), ("Rifle M16A2", 1)])
        self.assertEqual(db.loadouts(away["identity"]), [])
        self.assertEqual(db.loadout_save_read("s1"), 300)


if __name__ == "__main__":
    unittest.main()
