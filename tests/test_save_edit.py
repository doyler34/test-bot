import json
import struct
import unittest
import uuid

from panel import loadouts, save_edit

A, B = str(uuid.uuid4()), str(uuid.uuid4())


def shifted(data, bits):
    return (int.from_bytes(data, "little") << bits).to_bytes(len(data) + 1, "little")


def player(identity, loadout, bits):
    text = json.dumps(loadout, separators=(",", ":")).encode()
    body = b"\x00" * 4 + uuid.UUID(identity).bytes + b"\x07" * 40
    body += shifted(struct.pack("<H", len(text)) + text + b"\xab\xcd" * 20, bits)
    return struct.pack("<I", 8 * len(body) - 600) + body


def save(*players):
    sections = [("System", [b"\x01\x02\x03"]), ("Player", list(players)), ("MilitaryBase", [b"\x09" * 30])]
    return save_edit.build(1, sections)


def kit(prefab):
    return {"faction": "US", "arsenalLoadout": {"prefab": prefab, "storages": []}}


class SaveEditTests(unittest.TestCase):
    def test_parse_and_build_round_trip(self):
        data = save(player(A, kit("X"), 3), player(B, kit("Y"), 0))
        version, sections = save_edit.parse(data)
        self.assertEqual([name for name, _ in sections], ["System", "Player", "MilitaryBase"])
        self.assertEqual(save_edit.build(version, sections), data)

    def test_refuses_a_save_that_does_not_add_up(self):
        with self.assertRaises(ValueError):
            save_edit.parse(save(player(A, kit("X"), 3)) + b"\x00")

    def test_swapping_a_loadout_for_itself_changes_nothing(self):
        record = player(A, kit("X"), 5)
        start, length = save_edit.find_loadout(record)
        value = int.from_bytes(record, "little")
        text = ((value >> start) & ((1 << (8 * length)) - 1)).to_bytes(length, "little")
        self.assertEqual(save_edit.with_loadout(record, text), record)

    def test_set_loadout_changes_only_that_player(self):
        data = save(player(A, kit("X"), 3), player(B, kit("Y"), 6))
        longer = json.dumps(kit("A much longer prefab name than before"), separators=(",", ":")).encode()
        changed = save_edit.set_loadout(data, A, longer)
        found = loadouts.players(changed)
        self.assertEqual(found[A]["loadout"], kit("A much longer prefab name than before"))
        self.assertEqual(found[B]["loadout"], kit("Y"))
        old, new = dict(save_edit.parse(data)[1]), dict(save_edit.parse(changed)[1])
        self.assertEqual(old["System"], new["System"])
        self.assertEqual(old["MilitaryBase"], new["MilitaryBase"])
        before, after = old["Player"][0], new["Player"][0]
        grown = len(after) - len(before)
        self.assertEqual(grown, len(longer) - len(json.dumps(kit("X"), separators=(",", ":"))))
        self.assertEqual(struct.unpack_from("<I", after, 0)[0] - struct.unpack_from("<I", before, 0)[0], 8 * grown)
        self.assertEqual(after[-40:], before[-40:])

    def test_unknown_player(self):
        with self.assertRaises(ValueError):
            save_edit.set_loadout(save(player(A, kit("X"), 3)), B, b"{}")


if __name__ == "__main__":
    unittest.main()
