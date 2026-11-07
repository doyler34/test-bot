import json
import struct
import tempfile
import unittest
from pathlib import Path

from panel import campaign_save
from panel.db import PanelDB


def shifted(data, bits):
    """The bytes as the save would hold them starting `bits` into a byte."""
    return (int.from_bytes(data, "little") << bits).to_bytes(len(data) + 1, "little")


def record(base, side, bits=1):
    body = b"\x00" * 17 + bytes.fromhex(base) + b"\x00" * 5
    if side:
        body += shifted(bytes([len(side) * 2]) + side.encode() + b"\x00" * 4, bits)
    return struct.pack("<I", len(body)) + body


def save(records, head=b""):
    section = b"\x0c\x00\x00\x00MilitaryBase" + struct.pack("<I", len(records)) + b"".join(records)
    return b"\x01" + head + b"\x06\x00\x00\x00Player" + b"\x00" * 8 + section


TILLERS = "000be18458832e8b"
VERNON = "000d58381e83ed8b"
RELAY = "000a9500df90548b"


class CampaignSaveTests(unittest.TestCase):
    def test_reads_each_bases_side(self):
        data = save([record(TILLERS, "USSR"), record(VERNON, "FIA", bits=5), record(RELAY, None)])
        self.assertEqual(campaign_save.bases(data), [(TILLERS, "USSR"), (VERNON, "FIA"), (RELAY, None)])

    def test_us_is_not_read_as_ussr(self):
        self.assertEqual(campaign_save.bases(save([record(TILLERS, "US")])), [(TILLERS, "US")])

    def test_refuses_a_section_that_does_not_add_up(self):
        data = save([record(TILLERS, "USSR")]) + b"\x00"
        self.assertIsNone(campaign_save.bases(data))
        self.assertIsNone(campaign_save.bases(b"no bases here"))

    def test_names_come_from_seize_tasks(self):
        task = b"8SCR_CampaignMilitaryTaskSave>US_SeizeCampaignMilitaryBase_56" + b"\x00" * 9 \
            + b"#AR-Campaign_MapLocation_TillersFind-UC" + b"\x00" * 30 + bytes.fromhex(TILLERS)[1:7]
        stray = bytes.fromhex(VERNON)[1:7]
        data = save([record(TILLERS, "USSR"), record(VERNON, "FIA")], head=stray + shifted(task, 3))
        self.assertEqual(campaign_save.names(data, [TILLERS, VERNON]), {TILLERS: "TillersFind"})

    def test_label(self):
        self.assertEqual(campaign_save.label("TillersFind"), "Tillers Find")
        self.assertEqual(campaign_save.label("Coastal_Chotain"), "Coastal Chotain")

    def test_newest_save_and_cached_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "AReforgerMaster" / "logs"
            logs.mkdir(parents=True)
            game = root / "AReforgerMaster" / "profile" / ".save" / "game" / "ECC-23-Campaign" / "playthrough000"
            for number, saved in (("savepoint001", 100), ("savepoint002", 200)):
                folder = game / number
                (folder / "WorldState").mkdir(parents=True)
                (folder / "meta-info.json").write_text(json.dumps({"m_iSavedAtUnix": saved}))
                (folder / "WorldState" / "x.blob").write_bytes(save([record(TILLERS, "US" if saved == 200 else "USSR")]))
            path, saved = campaign_save.newest(str(logs))
            self.assertEqual(saved, 200)
            self.assertEqual(campaign_save.read(path)["bases"], [(TILLERS, "US")])
            self.assertIsNone(campaign_save.newest(str(root / "nowhere" / "logs")))


class BaseNamesTests(unittest.TestCase):
    def test_names_are_kept_and_updated(self):
        db = PanelDB(":memory:")
        self.assertEqual(db.base_names("s1", {TILLERS: "TillersFind"}), {TILLERS: "TillersFind"})
        self.assertEqual(db.base_names("s1"), {TILLERS: "TillersFind"})
        self.assertEqual(db.base_names("s2"), {})
        self.assertEqual(db.base_names("s1", {TILLERS: "Tillers"}), {TILLERS: "Tillers"})


if __name__ == "__main__":
    unittest.main()
