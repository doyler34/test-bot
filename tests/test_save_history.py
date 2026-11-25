"""The save history: reading players, bases, supplies, builds and mines from an autosave, and keeping each save."""
import json
import struct
import tempfile
import unittest
import uuid
from pathlib import Path

from panel import save_history, save_world
from panel.alerts import Alerts
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB
from panel.servers import ServerManager

PLAYER = "8dd7f736-af82-49e6-9659-9c1762bdc650"
OTHER = "4c5d0fa5-9647-4b5a-83fd-2565d54fc49f"
BODY = "01a1e46c-0000-0000-8400-000000000001"
TRUCK = "01a1e46c-0000-0000-8400-000000000002"
HQ = "01a1e3bc-fdaa-8981-8400-00000040e279"
BASE = "00fca8b6-08ee-89f1-8800-08370654b87b"
BUNKER = "01a1e445-fd84-8034-8400-000297112193"
WORLD_HOUSE = "00bf8d05-939a-8d1d-8800-0d6130d39d1a"
CRATE = "01a1e463-669c-82fd-8400-00033cc6bdf2"
DEPOT = "00ba7d18-8f33-89a1-8800-00a387f31038"
MINE = "01a1e463-669c-82fd-8400-0000000000aa"
PMN = "20EFA9ACB024108F"


class Bits:
    def __init__(self):
        self.v = self.n = 0

    def put(self, value, n):
        self.v |= (value & ((1 << n) - 1)) << self.n
        self.n += n
        return self

    def count(self, x):
        if x < 128:
            return self.put(0, 1).put(x, 7)
        k = (x.bit_length() + 7) // 8 - 1
        return self.put(1, 1).put(k, 3).put(x, (k + 1) * 8)

    def int(self, x):
        return self.put(int(x < 0), 1).count(abs(x))

    def float(self, f):
        return self.put(struct.unpack("<I", struct.pack("<f", f))[0], 32)

    def text(self, t):
        b = t.encode()
        return self.count(len(b)).put(int.from_bytes(b, "little"), len(b) * 8)

    def uuid(self, u):
        return self.put(int.from_bytes(uuid.UUID(u).bytes, "little"), 128)

    def block(self, inner):
        return self.put(1, 1).put(inner.n, 32).put(inner.v, inner.n)


def entity(pos, prefab=None):
    b = Bits().int(2)
    b.put(1, 1).count(int(prefab, 16)) if prefab else b.put(0, 1)
    b.put(1, 1)
    for f in pos:
        b.float(f)
    return b.put(0, 1).put(0, 1)


def record(rid, *parts):
    body = Bits().uuid(rid).block(Bits().put(0, 4).count(5).put(0, 1))
    for part in parts:
        body.block(part)
    end = 64 + body.n
    return (end | body.v << 64).to_bytes((end + 7) // 8, "little")


def comps(*blocks):
    out = Bits()
    for b in blocks:
        out.block(b)
    return out


def save_file(sections):
    out = b"\x01" + struct.pack("<I", len(sections))
    for name, recs in sections.items():
        out += struct.pack("<I", len(name)) + name.encode() + struct.pack("<I", len(recs))
        for r in recs:
            out += struct.pack("<I", len(r)) + r
    return out


def supplies(value):
    return Bits().int(1).put(1, 1).count(1).block(Bits().int(0).float(value)).put(0, 1)


def author(who):
    return Bits().int(1).uuid("00000000-0000-0000-0000-000000000000").put(0, 1).uuid(who).int(1794831154)


def a_save(xp=7627, seat=True, crate=True):
    character = [entity((3796.3, 120.5, 8669.6)), comps(Bits().int(1).text("US"))]
    if seat:
        character[1].block(Bits().int(1).uuid(TRUCK).text("Passenger_r01"))
    return save_file({
        "System": [b"\x00" * 12],
        "Player": [record(PLAYER, Bits().int(1).uuid(BODY), comps(Bits().int(1).int(xp).int(0).int(3), Bits().int(1).text("US")))],
        "Character": [record(BODY, *character)],
        "Vehicle": [record(TRUCK, entity((5106.4, 175.0, 6954.4)), comps(author(OTHER)))],
        "Item": [record(MINE, entity((4000.0, 50.0, 6000.0), PMN))],
        "Structure": [record(HQ, entity((4980.05, 28.3, 11947.7))),
                      record(BUNKER, entity((4499.4, 64.3, 4954.1)), comps(author(PLAYER))),
                      record(WORLD_HOUSE, comps(Bits().int(1).float(1.0)))],
        "Storage": [record(DEPOT, comps(supplies(590)))]
                   + ([record(CRATE, entity((4990.0, 30.0, 11960.0)), comps(supplies(150)))] if crate else []),
        "MilitaryBase": [record(BASE, comps(Bits().int(1).text("US"),
                                            Bits().int(1).put(0, 1).uuid(HQ).int(23).put(0, 1).text("")))],
    })


class Reading(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.blob"
            path.write_bytes(data)
            return save_history.read(path)

    def test_reads_everything_the_history_keeps(self):
        got = self.read(a_save())
        self.assertEqual(got["players"], [{"identity": PLAYER, "faction": "US", "xp": 7627, "x": 3796.3, "y": 120.5,
                                           "z": 8669.6, "dead": False, "kind": "", "vehicle": TRUCK, "seat": "Passenger_r01"}])
        self.assertEqual(got["bases"], [{"id": BASE, "faction": "US", "x": 4980.0, "z": 11947.7}])
        self.assertEqual([(s["id"], s["x"], s["supplies"]) for s in got["storages"]], [(DEPOT, None, 590), (CRATE, 4990.0, 150)])
        self.assertEqual([(b["id"], b["kind"], b["author"]) for b in got["builds"]],
                         [(TRUCK, "vehicle", OTHER), (BUNKER, "structure", PLAYER)])
        self.assertEqual([(m["name"], m["x"]) for m in got["mines"]], [("PMN4_base", 4000.0)])

    def test_the_live_map_reader_still_leaves_out_height_and_seat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.blob"
            path.write_bytes(a_save())
            self.assertEqual(save_world.read(path), [{"identity": PLAYER, "faction": "US", "xp": 7627, "x": 3796.3,
                                                      "z": 8669.6, "dead": False, "kind": ""}])

    def test_a_broken_save_reads_as_nothing(self):
        self.assertIsNone(self.read(a_save()[:-40]))

    def test_supplies_go_to_the_nearest_base_and_the_rest_are_not_placed(self):
        got = self.read(a_save())
        totals, loose = save_history.base_supplies(got["bases"], got["storages"])
        self.assertEqual((totals, loose), ({BASE: 150}, 590))
        far = [{"id": "far", "x": 4980.0 + save_history.BASE_REACH + 1, "z": 11947.7, "supplies": 40}]
        self.assertEqual(save_history.base_supplies(got["bases"], far), ({BASE: 0}, 40))

    def test_bases_take_their_map_name(self):
        got = self.read(a_save())
        self.assertEqual(save_history.base_names(got["bases"]), {BASE: "Airbase Saint-Philippe"})


class Keeping(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.tmp = Path(tempfile.mkdtemp())

    def snapshot(self, **kw):
        path = self.tmp / "x.blob"
        path.write_bytes(a_save(**kw))
        return save_history.snapshot(save_history.read(path))

    def test_only_changes_are_written_and_each_save_can_be_looked_back_at(self):
        first = self.db.add_save("one", 100, 1000, *self.snapshot())
        self.assertEqual(self.db.add_save("one", 100, 1200, *self.snapshot()), 0)
        self.assertEqual(self.db.add_save("one", 100, 1400, *self.snapshot(xp=8000, crate=False)), 3)
        self.assertGreater(first, 3)
        now = self.db.save_state("one", 100)
        self.assertEqual(now["player"][PLAYER]["xp"], 8000)
        self.assertNotIn(CRATE, now["storage"])
        self.assertEqual(now["base"][BASE]["supplies"], 0)
        then = self.db.save_state("one", 100, 1200)
        self.assertEqual((then["player"][PLAYER]["xp"], then["base"][BASE]["supplies"]), (7627, 150))
        self.assertEqual(then["base"][BASE]["name"], "Airbase Saint-Philippe")
        self.assertEqual(self.db.last_save_point("one")["loose_supplies"], 590)

    def test_each_game_is_kept_apart(self):
        self.db.add_save("one", 100, 1000, *self.snapshot())
        self.db.add_save("one", 200, 2000, *self.snapshot(xp=5))
        self.assertEqual(self.db.save_state("one", 100)["player"][PLAYER]["xp"], 7627)
        self.assertEqual(self.db.save_state("one", 200)["player"][PLAYER]["xp"], 5)

    def test_old_games_are_pruned(self):
        self.db.add_save("one", 100, 1000, *self.snapshot())
        self.db.prune_save_history()
        self.assertEqual((self.db.save_state("one", 100), self.db.last_save_point("one")), ({}, None))

    async def test_the_panel_keeps_each_new_autosave_once(self):
        logs = self.tmp / "logs"
        point = self.tmp / "profile" / ".save" / "game" / "X-23-Campaign" / "playthrough000" / "savepoint1"
        (point / "WorldState").mkdir(parents=True)
        (point / "WorldState" / "a.blob").write_bytes(a_save())
        (point / "meta-info.json").write_text(json.dumps({"m_iSavedAtUnix": 5000, "m_iStartedUnix": 4000}))
        manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "Server 1", log_dir=str(logs))]), self.db,
                                alerts=Alerts(""))
        state = manager.states["one"]
        await manager.record_save(state)
        self.assertEqual(dict(self.db.last_save_point("one")), {"server": "one", "game": 4000, "saved": 5000,
                                                               "loose_supplies": 590})
        rows = self.db.one("SELECT COUNT(*) n FROM save_rows")["n"]
        await manager.record_save(state)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM save_rows")["n"], rows)
