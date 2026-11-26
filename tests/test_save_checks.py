"""The exploit checks that compare one autosave with the next, and ban evasion by Steam ID or BattlEye GUID."""
import json
import unittest

from bot.discord import welcome_doc
from panel import save_checks
from panel.alerts import Alerts
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB, now
from panel.servers import ServerManager

A, B, C = "aaaaaaaa-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002", "cccccccc-0000-4000-8000-000000000003"
SETTINGS = welcome_doc.check_staffalerts(welcome_doc.default_staffalerts())[0]


def player(side="US", xp=0, x=1000.0, z=1000.0, y=50.0, dead=False, body="b1", seat=None):
    return {"faction": side, "xp": xp, "x": x, "y": y, "z": z, "dead": dead, "body": body, "seat": seat, "vehicle": None}


def name(identity):
    return {A: "Alpha", B: "Bravo", C: "Charlie"}.get(identity, identity)


def titles(found):
    return [f[0] for f in found]


class Checks(unittest.TestCase):
    def run_checks(self, before, after, gap=300, kills=None, game_vehicles=None, roadkills=None, wrecks=None, **changes):
        settings = {**SETTINGS, **changes}
        return save_checks.run(before, after, gap, settings, name, kills or {}, game_vehicles or {}, roadkills or {},
                               [] if wrecks is None else wrecks)

    def test_xp_without_kills_is_farming(self):
        before, after = {"player": {A: player(xp=100)}}, {"player": {A: player(xp=1200)}}
        found = self.run_checks(before, after)
        self.assertEqual(titles(found), ["XP farming"])
        self.assertIn("**Alpha** gained 1100 game XP in 5 minutes", found[0][2])

    def test_xp_with_a_kill_or_below_the_limit_is_fine(self):
        before, after = {"player": {A: player(xp=100)}}, {"player": {A: player(xp=1200)}}
        self.assertEqual(self.run_checks(before, after, kills={A: 1}), [])
        self.assertEqual(self.run_checks(before, after, xp_gain=2000), [])

    def test_supplies_from_nowhere_are_a_jump(self):
        before = {"storage": {"s1": {"x": None, "z": None, "supplies": 100}, "s2": {"x": None, "z": None, "supplies": 500}}}
        after = {"storage": {"s1": {"x": None, "z": None, "supplies": 2100}, "s2": {"x": None, "z": None, "supplies": 500}}}
        found = self.run_checks(before, after)
        self.assertEqual(titles(found), ["Supply jump"])
        self.assertIn("A map depot (the save doesn't say where) gained 2000 supplies", found[0][2])

    def test_supplies_moved_without_a_truck_are_a_transfer(self):
        before = {"storage": {"s1": {"x": None, "z": None, "supplies": 100}, "s2": {"x": None, "z": None, "supplies": 2500}},
                  "cargo": {"t1": {"supplies": 300}}}
        after = {"storage": {"s1": {"x": None, "z": None, "supplies": 2100}, "s2": {"x": None, "z": None, "supplies": 450}},
                 "cargo": {"t1": {"supplies": 300}}}
        self.assertEqual(titles(self.run_checks(before, after)), ["Supply transfer"])
        after["cargo"] = {"t1": {"supplies": 0}}
        self.assertEqual(self.run_checks(before, after), [])

    def test_a_storage_back_in_the_save_has_not_jumped(self):
        before = {"storage": {}}
        after = {"storage": {"s1": {"x": None, "z": None, "supplies": 3000}}}
        self.assertEqual(self.run_checks(before, after), [])

    def walls(self, progress=1.0, at=1000.0):
        return {f"w{n}": {"kind": "structure", "name": "E_SandbagWall_S_US_01", "author": A, "x": at + 10 * n,
                          "z": 1000.0, "progress": progress} for n in range(3)}

    def test_finished_friendly_structures_gone_near_teammates_is_one_alert(self):
        people = {A: player(x=5000), B: player(x=1010), C: player(x=1020)}
        found = self.run_checks({"build": self.walls(), "player": people}, {"build": {}, "player": people})
        self.assertEqual(titles(found), ["Base griefing"])
        self.assertIn("3 finished structures built by their own side went between two autosaves: "
                      "SandbagWall by Alpha", found[0][2])
        self.assertIn("Their own side nearby: **Bravo**, **Charlie**.", found[0][2])

    def test_unfinished_or_replaced_structures_are_not_griefing(self):
        people = {A: player(x=5000), B: player(x=1010)}
        # Placed but never built: the game clears those away.
        self.assertEqual(self.run_checks({"build": self.walls(progress=0.4), "player": people},
                                         {"build": {}, "player": people}), [])
        self.assertEqual(self.run_checks({"build": self.walls(progress=None), "player": people},
                                         {"build": {}, "player": people}), [])
        # Swapped for a new one in the same spot once built.
        swapped = {f"n{k}": {**w, "progress": 1.0} for k, w in self.walls().items()}
        self.assertEqual(self.run_checks({"build": self.walls(), "player": people},
                                         {"build": swapped, "player": people}), [])

    def test_enemies_and_the_builder_do_not_count_as_griefing(self):
        walls = self.walls()
        before = {"build": walls, "player": {A: player(x=1000), C: player(side="USSR", x=1000)}}
        after = {"build": {}, "player": {A: player(x=1000), C: player(side="USSR", x=1000)}}
        self.assertEqual(self.run_checks(before, after), [])

    def test_vehicle_griefing(self):
        base = {"faction": "US", "hq": True, "x": 2000.0, "z": 2000.0, "name": "Main"}
        truck = {"kind": "vehicle", "name": "M923A1", "author": A, "faction": "US", "x": 2050.0, "z": 2000.0}
        before = {"base": {"b": base}, "build": {"t": {**truck, "wreck": False}}}
        after = {"base": {"b": base}, "build": {"t": {**truck, "wreck": True}}}
        wrecks = []
        found = self.run_checks(before, after, roadkills={B: 3}, game_vehicles={C: set(range(10))}, wrecks=wrecks,
                                vehicle_wrecks=1)
        self.assertEqual(sorted(f[1] for f in found), [("roadkill", B), ("spawns", C), ("wrecks", "")])
        self.assertEqual(wrecks, [("M923A1", A)])

    def test_mines_of_a_side_inside_its_own_base(self):
        base = {"faction": "USSR", "x": 3000.0, "z": 3000.0, "name": "Levie"}
        mines = {f"m{n}": {"name": "PMN4_base", "x": 3010.0, "z": 3000.0} for n in range(3)}
        found = self.run_checks({}, {"base": {"b": base}, "mine": mines})
        self.assertEqual(titles(found), ["Mines in own base"])
        mines = {f"m{n}": {"name": "M14_base", "x": 3010.0, "z": 3000.0} for n in range(3)}
        self.assertEqual(self.run_checks({}, {"base": {"b": base}, "mine": mines}), [])

    def test_teleport_and_under_the_map(self):
        before = {"player": {A: player(x=1000), B: player(x=1000)}}
        after = {"player": {A: player(x=6000), B: player(x=1000, y=-20)}}
        self.assertEqual(sorted(titles(self.run_checks(before, after))), ["Teleport", "Under the map"])

    def test_a_new_life_or_a_vehicle_is_not_a_teleport(self):
        before = {"player": {A: player(x=1000), B: player(x=1000)}}
        after = {"player": {A: player(x=6000, body="b2"), B: player(x=6000, seat="Driver")}}
        self.assertEqual(self.run_checks(before, after), [])

    def test_switched_off_checks_are_quiet(self):
        before, after = {"player": {A: player(xp=0)}}, {"player": {A: player(xp=5000, x=9000, y=-50)}}
        self.assertEqual(self.run_checks(before, after, xp_on=False, teleport_on=False), [])


class Wiring(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "Server 1")]), self.db, alerts=Alerts(""))
        self.state = self.manager.states["one"]

    def test_each_finding_is_sent_once_a_game(self):
        before, after = {"player": {A: player(xp=0)}}, {"player": {A: player(xp=5000)}}
        self.manager.save_checks(self.state, 100, 1000, 1300, before, after)
        self.manager.save_checks(self.state, 100, 1300, 1600, before, after)
        self.assertEqual([a["title"] for a in self.db.staff_alerts()], ["XP farming on Server 1"])
        self.manager.save_checks(self.state, 200, 2000, 2300, before, after)
        self.assertEqual(len(self.db.staff_alerts()), 2)

    def test_live_kills_count_between_the_saves(self):
        self.manager.check(self.state, [{"kind": "kill", "at": 1100, "killer": A, "killer_name": "Alpha",
                                         "victim_name": "V", "relation": "ENEMY"}])
        before, after = {"player": {A: player(xp=0)}}, {"player": {A: player(xp=5000)}}
        self.manager.save_checks(self.state, 100, 1000, 1300, before, after)
        self.assertEqual(self.db.staff_alerts(), [])

    def test_a_new_autosave_runs_the_checks_against_the_last(self):
        self.db.add_save("one", 100, 1000, 0, {"player": {A: player(xp=0)}})
        self.assertEqual(self.db.save_state("one", 100, 1000)["player"][A]["xp"], 0)

    def test_vehicles_taken_out_this_game_come_from_the_kept_saves(self):
        truck = {"kind": "vehicle", "name": "M923A1", "author": A, "faction": "US", "wreck": False, "x": 1.0, "z": 1.0}
        self.db.add_save("one", 100, 1000, 0, {"build": {"t1": truck}})
        self.db.add_save("one", 100, 1300, 0, {"build": {"t2": truck}})
        self.db.add_save("one", 100, 1600, 0, {"build": {"w": {"kind": "structure", "author": A}}})
        self.assertEqual(self.db.game_vehicles("one", 100), {A: {"t1", "t2"}})

    async def test_ban_evasion_is_checked_once_per_join(self):
        from unittest.mock import patch as mock_patch
        self.state.players = [{"identity": B, "name": "Bravo", "id": "2"}]
        with mock_patch.object(self.db, "id_ban_hit", return_value=None) as lookup:
            await self.manager.enforce_ip_bans(self.state)
            await self.manager.enforce_ip_bans(self.state)
            self.assertEqual(lookup.call_count, 1)
            self.state.players = []
            await self.manager.enforce_ip_bans(self.state)
            self.state.players = [{"identity": B, "name": "Bravo", "id": "2"}]
            await self.manager.enforce_ip_bans(self.state)
            self.assertEqual(lookup.call_count, 2)

    def test_ban_evasion_by_steam_id_and_guid(self):
        ban = self.db.add_ban(A, "Alpha", "cheating", "staff")
        self.db.add_platforms([{"identity": A, "platform": "steam", "steam": "76561190000000001", "at": now()},
                               {"identity": B, "platform": "steam", "steam": "76561190000000001", "at": now()}])
        self.manager.ban_evasion(self.state, B, "Bravo")
        self.manager.ban_evasion(self.state, B, "Bravo")
        alerts = self.db.staff_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("**Bravo** joined with the same Steam ID as **Alpha**, who is banned (cheating)", alerts[0]["text"])
        self.db.db.execute("INSERT INTO connections (identity, ip, guid, name, server, first_seen, last_seen)"
                           " VALUES (?, '1.1.1.1', 'g1', 'Alpha', 'one', 0, 0), (?, '2.2.2.2', 'g1', 'Charlie', 'one', 0, 0)",
                           (A, C))
        self.assertEqual(self.db.id_ban_hit(C)["shared"], "BattlEye GUID")
        self.assertIsNone(self.db.id_ban_hit(A))
        self.assertTrue(ban)


class Settings(unittest.TestCase):
    def test_old_pages_get_the_checks_switched_on(self):
        doc, problems = welcome_doc.check_staffalerts({"on": True, "count": 3, "seconds": 60})
        self.assertEqual((problems, doc["xp_on"], doc["xp_gain"], doc["teleport_km"]), ([], True, 1000, 4))

    def test_limits_are_checked(self):
        doc, problems = welcome_doc.check_staffalerts({**SETTINGS, "teleport_km": 99, "xp_on": False})
        self.assertEqual((doc["teleport_km"], doc["xp_on"]), (15, False))
        self.assertIn("Distance has to be between 1 and 15.", problems)

    def test_each_part_pings_its_own_role(self):
        settings = {**SETTINGS, "ping_role": "Mods", "grief_ping_role": "Builders", "teleport_ping_role": "Admins"}
        self.assertEqual(welcome_doc.alert_role(settings, "Base griefing on S1"), "Builders")
        self.assertEqual(welcome_doc.alert_role(settings, "Under the map on S1"), "Admins")
        self.assertEqual(welcome_doc.alert_role(settings, "Mass teamkill on S1"), "Mods")


if __name__ == "__main__":
    unittest.main()
