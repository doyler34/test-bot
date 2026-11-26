"""Rotations: game setups kept as autosaves and loaded one after another at each game over."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from panel import rotation, servers
from panel.alerts import Alerts
from panel.config import PanelConfig, ServerConfig
from panel.connections import read_lines
from panel.db import PanelDB
from panel.servers import ServerManager
from tests.test_save_history import BASE, HQ, Bits, comps, entity, record, save_file

MISSION = "ECC61978EDCC2B5A-23-Campaign"
GAME_OVER = "12:59:39.676 SCRIPT       : SCR_BaseGameMode::OnGameStateChanged = POSTGAME"


def a_setup(moved=True):
    base = [comps(Bits().int(1).text("US"), Bits().int(1).put(1, 1).uuid(HQ).int(23).put(0, 1).text(""))]
    if moved:
        base.insert(0, entity((4895.0, 168.3, 7116.8)))
    return save_file({"Structure": [record(HQ, entity((4980.05, 28.3, 11947.7)))],
                      "MilitaryBase": [record(BASE, *base)]})


def put_save(log_dir, number, saved, blob):
    point = Path(log_dir).parent / "profile" / ".save" / "game" / MISSION / "playthrough000" / f"savepoint{number}"
    (point / "WorldState").mkdir(parents=True)
    (point / "WorldState" / "w.blob").write_bytes(blob)
    (point / "meta-info.json").write_text(json.dumps({"m_iSavedAtUnix": saved, "m_iStartedUnix": 1}))
    return point


class Saves(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.logs = self.tmp / "profile" / "logs"

    def test_a_save_says_where_the_mobs_are_and_which_points_moved(self):
        put_save(self.logs, 1, 100, a_setup())
        about = rotation.describe(next(self.tmp.rglob("w.blob")))
        self.assertEqual(len(about["mobs"]), 1)
        self.assertTrue(about["mobs"][0].startswith("US at "))
        self.assertEqual(len(about["moved"]), 1)
        put_save(self.logs, 2, 200, a_setup(moved=False))
        self.assertEqual(rotation.describe(next((self.tmp / "profile").rglob("savepoint2/WorldState/w.blob")))["moved"], [])

    def test_the_newest_autosave_is_kept_and_loads_as_the_only_save(self):
        put_save(self.logs, 1, 100, b"old")
        put_save(self.logs, 2, 200, a_setup())
        keep = self.tmp / "kept"
        mission, about = rotation.capture(self.logs, keep)
        self.assertEqual(mission, MISSION)
        self.assertEqual(len(about["moved"]), 1)
        self.assertTrue((keep / "savepoint2" / "WorldState" / "w.blob").exists())
        # The game wipes its saves at game over and carries on writing new ones; the kept copy is untouched.
        shutil.rmtree(rotation.game_folder(self.logs))
        put_save(self.logs, 7, 900, b"newer game")
        rotation.install(self.logs, keep, mission)
        points = sorted(p.name for p in (rotation.game_folder(self.logs) / MISSION).rglob("savepoint*"))
        self.assertEqual(points, ["savepoint2"])
        self.assertEqual((rotation.game_folder(self.logs) / MISSION / "playthrough000" / "savepoint2" / "WorldState"
                          / "w.blob").read_bytes(), a_setup())

    def test_no_autosave_yet(self):
        with self.assertRaises(FileNotFoundError):
            rotation.capture(self.logs, self.tmp / "kept")


class Order(unittest.TestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.ids = [self.db.add_rotation("one", name, "/x", MISSION, {}, "boss") for name in "ABC"]

    def names(self):
        return [r["name"] for r in self.db.rotations("one")]

    def test_they_load_in_order_and_go_round(self):
        seen = []
        for _ in range(4):
            nxt = self.db.next_rotation("one")
            seen.append(nxt["name"])
            self.db.rotated("one", nxt["id"])
        self.assertEqual(seen, ["A", "B", "C", "A"])

    def test_a_switched_off_one_is_skipped(self):
        self.db.set_rotation_active(self.ids[1], False)
        self.db.rotated("one", self.ids[0])
        self.assertEqual(self.db.next_rotation("one")["name"], "C")

    def test_after_the_last_loaded_one_is_deleted_the_next_one_still_follows(self):
        self.db.rotated("one", self.ids[1])
        self.db.delete_rotation(self.ids[1])
        self.assertEqual(self.db.next_rotation("one")["name"], "C")

    def test_moving_one_up_and_down(self):
        self.db.move_rotation(self.ids[2], -1)
        self.assertEqual(self.names(), ["A", "C", "B"])
        self.db.move_rotation(self.ids[0], -1)
        self.assertEqual(self.names(), ["A", "C", "B"])

    def test_a_failed_load_keeps_the_place(self):
        self.db.rotated("one", self.ids[0])
        self.db.rotated("one", None, "AMP said no")
        state = self.db.rotation_state("one")
        self.assertEqual((state["last_id"], state["last_error"]), (self.ids[0], "AMP said no"))
        self.assertEqual(self.db.next_rotation("one")["name"], "B")


class GameOver(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.logs = self.tmp / "profile" / "logs"
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "Server 1", log_dir=str(self.logs))]),
                                     self.db, alerts=Alerts(""))
        self.state = self.manager.states["one"]
        self.calls = []

        async def power(state, action):
            self.calls.append(action)
        self.manager.amp_power = power

        async def no_program():
            self.state.found_pid = 0
        self.manager.find_programs = no_program
        put_save(self.logs, 1, 100, a_setup())
        self.kept = self.tmp / "kept"
        mission, about = rotation.capture(self.logs, self.kept)
        self.rid = self.db.add_rotation("one", "West", str(self.kept), mission, about, "boss")
        put_save(self.logs, 2, 200, b"the game that just ended")

    def test_the_log_line(self):
        self.assertEqual(read_lines([GAME_OVER], 0)[0]["kind"], "gameover")

    async def run_game_over(self):
        with mock.patch.object(servers, "ROTATE_AFTER", 0), mock.patch.object(servers, "STOP_WAIT", 3):
            self.manager.game_over(self.state)
            for task in self.manager._tasks:
                await task

    async def test_nothing_happens_while_rotations_are_off(self):
        await self.run_game_over()
        self.assertEqual(self.calls, [])

    async def test_game_over_loads_the_next_rotation(self):
        self.db.set_rotation_enabled("one", True)
        await self.run_game_over()
        self.assertEqual(self.calls, ["stop", "start"])
        points = [p.name for p in (rotation.game_folder(self.logs) / MISSION).rglob("savepoint*")]
        self.assertEqual(points, ["savepoint1"])
        self.assertEqual(self.db.rotation_state("one")["last_id"], self.rid)
        self.assertEqual(self.db.staff_alerts()[0]["title"], "Rotation on Server 1")
        self.assertFalse(self.state.rotating)

    async def test_a_failed_swap_starts_a_normal_game_and_tells_staff(self):
        self.db.set_rotation_enabled("one", True)
        shutil.rmtree(self.kept)
        await self.run_game_over()
        self.assertEqual(self.calls, ["stop", "start"])
        self.assertEqual(self.db.staff_alerts()[0]["title"], "Rotation failed on Server 1")
        self.assertIsNone(self.db.rotation_state("one")["last_id"])

    async def test_loading_one_from_the_panel(self):
        with mock.patch.object(servers, "STOP_WAIT", 3):
            self.assertEqual(await self.manager.load_rotation("one", self.rid, "boss"), "")
            self.assertEqual(await self.manager.load_rotation("one", 999, "boss"), "That rotation isn't on this server.")
        self.assertEqual(self.calls, ["stop", "start"])

    def test_new_game_starts_on_the_next_rotation(self):
        self.assertIsNone(self.manager.new_game_rotation("one", "boss"))
        shutil.rmtree(rotation.game_folder(self.logs))
        self.db.set_rotation_enabled("one", True)
        self.assertEqual(self.manager.new_game_rotation("one", "boss")["id"], self.rid)
        points = [p.name for p in (rotation.game_folder(self.logs) / MISSION).rglob("savepoint*")]
        self.assertEqual(points, ["savepoint1"])
        self.assertEqual(self.db.rotation_state("one")["last_id"], self.rid)


if __name__ == "__main__":
    unittest.main()


class ResetScript(unittest.IsolatedAsyncioTestCase):
    """The live box's postgame reset script restarts the server itself; the panel only readies rotation-next."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.logs = self.tmp / "profile" / "logs"
        self.script = self.tmp / "reset.sh"
        patcher = mock.patch.object(rotation, "RESET_SCRIPT", self.script)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "Server 1", log_dir=str(self.logs))]),
                                     self.db, alerts=Alerts(""))
        self.state = self.manager.states["one"]
        self.calls = []

        async def power(state, action):
            self.calls.append(action)
        self.manager.amp_power = power
        put_save(self.logs, 1, 100, a_setup())
        self.ids = []
        for name in ("West", "East"):
            kept = self.tmp / name
            mission, about = rotation.capture(self.logs, kept)
            self.ids.append(self.db.add_rotation("one", name, str(kept), mission, about, "boss"))
        self.db.set_rotation_enabled("one", True)

    def write_script(self, with_line):
        self.script.write_text('rm -rf -- "$save_dir"\n' + ('cp -a "$profile_dir/rotation-next" "$save_dir"\n'
                                                            if with_line else ""))

    def reset_like_the_script(self):
        """What the script does at game over: delete the save folder and copy rotation-next in."""
        save = rotation.game_folder(self.logs).parent
        shutil.rmtree(save)
        shutil.copytree(rotation.stage_folder(self.logs), save)

    def test_which_way_a_server_rotates(self):
        self.assertEqual(self.manager.rotation_mode(self.state), "panel")
        self.write_script(False)
        self.assertEqual(self.manager.rotation_mode(self.state), "old")
        self.write_script(True)
        self.assertEqual(self.manager.rotation_mode(self.state), "script")

    def test_the_next_rotation_waits_in_rotation_next(self):
        self.write_script(True)
        self.manager.restage("one")
        self.assertEqual(rotation.staged(self.logs), self.ids[0])
        self.db.set_rotation_enabled("one", False)
        self.manager.restage("one")
        self.assertFalse(rotation.stage_folder(self.logs).exists())

    async def test_after_the_reset_the_rotation_is_on_and_the_next_one_waits(self):
        self.write_script(True)
        self.manager.restage("one")
        self.reset_like_the_script()
        with mock.patch.object(servers, "RESET_SETTLE", 0):
            self.manager.game_over(self.state)
            for task in self.manager._tasks:
                await task
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.rotation_state("one")["last_id"], self.ids[0])
        self.assertEqual(self.db.staff_alerts()[0]["title"], "Rotation on Server 1")
        self.assertEqual(rotation.staged(self.logs), self.ids[1])

    async def test_a_reset_that_didnt_copy_it_in_tells_staff(self):
        self.write_script(True)
        self.manager.restage("one")
        shutil.rmtree(rotation.game_folder(self.logs).parent)
        with mock.patch.object(servers, "RESET_SETTLE", 0):
            self.manager.game_over(self.state)
            for task in self.manager._tasks:
                await task
        self.assertEqual(self.db.staff_alerts()[0]["title"], "Rotation failed on Server 1")
        self.assertEqual(rotation.staged(self.logs), self.ids[0])

    async def test_nothing_runs_at_game_over_while_the_script_lacks_the_line(self):
        self.write_script(False)
        self.manager.game_over(self.state)
        self.assertEqual((self.manager._tasks, self.calls), ([], []))
