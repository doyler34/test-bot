import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from panel import mobs

# The line the live server wrote when it picked the bases.
REAL = ("12:52:24.663 SCRIPT       : 2026-10-28 12:52:24 MOBsSelected: SCR_MOBSelectedData{, "
        "MOB_names=[#AR-Campaign_MapLocation_MOB-UC, #AR-Campaign_MapLocation_MOB-UC], "
        "MOB_factions=[#AR-Faction_US, #AR-Faction_USSR], MOB_xs=[6437.16, 5384.56], "
        "MOB_ys=[106.295, 42.451], MOB_zs=[5938.73, 11059.6]}\n")


class ParseTests(unittest.TestCase):
    def test_the_real_line(self):
        self.assertEqual(mobs.parse(REAL), [{"side": "US", "label": "US MOB", "x": 6437.16, "z": 5938.73},
                                            {"side": "RU", "label": "RU MOB", "x": 5384.56, "z": 11059.6}])

    def test_other_text_is_not_a_pick(self):
        self.assertIsNone(mobs.parse("12:00:00.000 SCRIPT : something else\n"))
        self.assertIsNone(mobs.parse(REAL.replace("MOB_zs=[5938.73, 11059.6]", "MOB_zs=[5938.73]")))
        self.assertIsNone(mobs.parse(REAL.replace("6437.16", "north")))


class ReadTests(unittest.TestCase):
    def setUp(self):
        mobs._seen.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name, "console.log")

    def test_found_once_and_kept(self):
        self.log.write_text("noise\n" + REAL)
        self.assertEqual([b["label"] for b in mobs.read(self.log, at=0)], ["US MOB", "RU MOB"])
        self.log.write_text("")
        self.assertEqual(len(mobs.read(self.log, at=1)), 2)

    def test_a_game_that_has_not_picked_yet_is_asked_again_later(self):
        self.log.write_text("noise\n")
        self.assertEqual(mobs.read(self.log, at=0), [])
        self.log.write_text("noise\n" + REAL)
        self.assertEqual(mobs.read(self.log, at=mobs.RETRY - 1), [])
        self.assertEqual(len(mobs.read(self.log, at=mobs.RETRY + 1)), 2)

    def test_a_missing_log_is_just_empty(self):
        self.assertEqual(mobs.read(Path(self.tmp.name, "nothing.log"), at=0), [])


class PlacesTests(unittest.TestCase):
    def test_the_named_places(self):
        places = mobs.places()
        names = {p["name"] for p in places}
        self.assertEqual(len(places), len(names))
        self.assertTrue({"Airbase Saint-Philippe", "Military Base Levie", "Calvary Hill", "Tower Régina"} <= names)
        for place in places:
            self.assertTrue(0 < place["x"] < 12800 and 0 < place["z"] < 12800, place)
            self.assertIn(place["kind"], ("base", "tower"))

    def test_the_two_bases_in_the_real_log_are_among_the_possible_starts(self):
        possible = json.loads(mobs.PLACES.read_text(encoding="utf-8"))["mobs"]
        for base in mobs.parse(REAL):
            self.assertTrue(any(abs(x - base["x"]) < 1 and abs(z - base["z"]) < 1 for x, z in possible), base)


if __name__ == "__main__":
    unittest.main()
