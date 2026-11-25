"""Stacked saved loadouts (the supplies refund glitch): counting, the staff alert, and the automatic kick and ban."""
import json
import unittest
import unittest.mock

from bot.discord import welcome_doc
from panel import loadouts
from panel.alerts import Alerts
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB, now
from panel.servers import STACK_RETRY, ServerManager

BUFORD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
GAME = 1700000000
SCOPE = "Prefabs/Weapons/Attachments/Optics/Optic_4x20/Optic_4x20.et"
MORPHINE = "Prefabs/Items/Medicine/MorphineInjection_01/MorphineInjection_01.et"


class FakeAlerts(Alerts):
    def __init__(self):
        super().__init__("")
        self.sent = []

    def send(self, title, description="", colour=0, fields=()):
        self.sent.append(title)


class Counting(unittest.TestCase):
    def test_scopes_and_morphine_are_counted_wherever_they_sit(self):
        rifle = {"name": "Rifle", "path": "Prefabs/Weapons/Rifles/M16/Rifle_M16A2.et",
                 "items": [{"name": "Scope", "path": SCOPE, "items": []}]}
        pack = {"name": "Pack", "path": "Prefabs/Items/Backpack.et",
                "items": [{"name": "Scope", "path": SCOPE, "items": []}] * 3
                + [{"name": "Morphine", "path": MORPHINE, "items": []}] * 25}
        self.assertEqual(loadouts.stacked([rifle, pack]), (4, 25))

    def test_old_settings_get_the_new_part_with_kick_and_ban_off(self):
        doc, problems = welcome_doc.check_staffalerts({"on": True, "count": 3, "seconds": 60})
        self.assertEqual(problems, [])
        self.assertEqual((doc["loadout_on"], doc["loadout_scopes"], doc["loadout_morphine"],
                          doc["loadout_kick"], doc["loadout_ban"]), (True, 5, 20, False, False))


class SpawnKit(unittest.TestCase):
    LOADOUT = {"faction": "US", "arsenalLoadout": {"prefab": "AAAAAAAAAAAAAAAA", "storages": [
        {"id": "SCR_CharacterInventoryStorageComponent:1", "slots": {
            str(n): {"prefab": "5C095D3E8F1B9A00"} for n in range(25)}}]}}
    NAMES = {"5C095D3E8F1B9A00": MORPHINE}

    def test_counts_when_the_character_spawned_from_it(self):
        self.assertEqual(ServerManager.spawn_kit({"loadout": self.LOADOUT}, "AAAAAAAAAAAAAAAA", self.NAMES), (0, 25))

    def test_ignored_when_they_are_playing_something_else(self):
        # e.g. a loadout saved earlier as US while now playing RU
        self.assertEqual(ServerManager.spawn_kit({"loadout": self.LOADOUT}, "BBBBBBBBBBBBBBBB", self.NAMES), (0, 0))
        self.assertEqual(ServerManager.spawn_kit(None, "AAAAAAAAAAAAAAAA", self.NAMES), (0, 0))

    def test_carried_items_are_counted_by_prefab(self):
        guids = {"5C095D3E8F1B9A00": 21, "1111111111111111": 4}
        names = {**self.NAMES, "1111111111111111": SCOPE}
        self.assertEqual(loadouts.stacked_carried(guids, names), (4, 21))


class StackedLoadouts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "Server 1")]), self.db,
                                     alerts=FakeAlerts())
        self.state = self.manager.states["one"]
        self.state.players = [{"id": "7", "identity": BUFORD, "name": "Buford"}]
        self.state.online = True
        self.kicked = []

        async def kick(server_id, player_id):
            self.kicked.append(player_id)
            return "ok"
        self.manager.kick = kick
        self.manager.kick_everywhere = unittest.mock.AsyncMock(return_value=[])
        self.manager.push_ban = unittest.mock.AsyncMock(return_value={})

    def settings(self, **extra):
        self.db.publish_discord_doc("staffalerts", json.dumps({"on": True, "count": 3, "seconds": 60, **extra}), "gaz")

    async def save(self, scopes=31, morphine=100, saved=None, game=GAME, kit=(0, 0), fia=False):
        online = {p["identity"] for p in self.state.players}
        await self.manager.stacked_loadouts(self.state, saved or now(), game, [(BUFORD, (scopes, morphine), kit, fia)],
                                            online)

    def titles(self):
        return [a["title"] for a in self.db.staff_alerts()]

    async def test_under_the_limits_nothing_happens(self):
        self.settings(loadout_kick=True, loadout_ban=True)
        await self.save(scopes=5, morphine=20)
        self.assertEqual((self.titles(), self.kicked), ([], []))

    async def test_one_alert_per_game_with_what_they_had(self):
        self.settings()
        await self.save()
        await self.save()
        alert = self.db.staff_alerts()[0]
        self.assertEqual(self.titles(), ["Stacked loadout on Server 1"])
        self.assertIn("**Buford** is carrying 31 scopes and 100 morphine on Server 1", alert["text"])
        await self.save(game=GAME + 1)   # a new game starts the count again
        self.assertEqual(len(self.titles()), 2)
        self.assertEqual(self.kicked, [])   # kick and ban are off unless switched on

    async def test_only_what_is_over_the_limit_is_named(self):
        self.settings()
        await self.save(scopes=1, morphine=22)
        self.assertIn("is carrying 22 morphine on Server 1", self.db.staff_alerts()[0]["text"])

    async def test_the_loadout_they_spawned_from_counts_when_it_has_more(self):
        self.settings(loadout_kick=True)
        await self.save(scopes=1, morphine=0, kit=(31, 100))
        texts = " | ".join(a["text"] for a in self.db.staff_alerts())
        self.assertIn("**Buford** spawned with a loadout of 31 scopes and 100 morphine on Server 1", texts)
        self.assertIn("was kicked for spawning with a loadout of 31 scopes and 100 morphine", texts)

    async def test_each_count_is_named_with_where_it_came_from(self):
        self.settings(loadout_kick=True)
        await self.save(scopes=9, morphine=0, kit=(0, 40))
        texts = " | ".join(a["text"] for a in self.db.staff_alerts())
        self.assertIn("**Buford** is carrying 9 scopes and spawned with a loadout of 40 morphine", texts)
        self.assertIn("was kicked for carrying 9 scopes and spawning with a loadout of 40 morphine", texts)

    async def test_nothing_is_done_while_rcon_is_down(self):
        # RCON down empties the player list; that mustn't read as "they left" and ban them.
        self.settings(loadout_kick=True, loadout_ban=True)
        self.state.online, self.state.players = False, []
        await self.save()
        self.assertIsNone(self.db.active_ban(BUFORD))
        self.assertEqual(self.titles(), ["Stacked loadout on Server 1"])

    async def test_kick_only(self):
        self.settings(loadout_kick=True)
        await self.save()
        self.assertEqual(self.kicked, ["7"])
        self.assertIn("Auto-kicked on Server 1", self.titles())
        self.assertIsNone(self.db.active_ban(BUFORD))

    async def test_ban_only(self):
        self.settings(loadout_ban=True, loadout_ban_length=0)
        await self.save()
        ban = self.db.active_ban(BUFORD)
        self.assertEqual((ban["reason"], ban["expires_at"]),
                         ("Stacked loadout, supplies refund glitch (automatic ban)", None))
        self.assertEqual(self.kicked, [])
        self.manager.kick_everywhere.assert_awaited_once_with(BUFORD)

    async def test_both_kick_first_then_ban_if_a_later_save_still_has_it(self):
        self.settings(loadout_kick=True, loadout_ban=True)
        start = now()
        await self.save(saved=start)
        self.assertEqual(self.kicked, ["7"])
        self.assertIsNone(self.db.active_ban(BUFORD))
        await self.save(saved=start + 60)                # too soon to have fixed it: nothing more
        self.assertIsNone(self.db.active_ban(BUFORD))
        await self.save(saved=start + STACK_RETRY + 1)   # back on, still stacked
        self.assertIsNotNone(self.db.active_ban(BUFORD))

    async def test_someone_who_left_is_banned_with_the_ban_on(self):
        self.settings(loadout_kick=True, loadout_ban=True)
        self.state.players = []
        await self.save()
        self.assertIsNotNone(self.db.active_ban(BUFORD))

    async def test_fia_only_get_the_alert(self):
        self.settings(loadout_kick=True, loadout_ban=True)
        await self.save(fia=True)
        await self.save(fia=True, saved=now() + STACK_RETRY + 1)
        self.assertEqual(self.kicked, [])
        self.assertIsNone(self.db.active_ban(BUFORD))
        self.assertEqual(self.titles(), ["Stacked loadout on Server 1"])
        self.assertIn("Playing FIA, so alert only", self.db.staff_alerts()[0]["text"])

    async def test_admins_are_never_kicked_or_banned(self):
        self.settings(loadout_kick=True, loadout_ban=True)
        self.db.set_staff(BUFORD, "gaz", True)
        await self.save()
        self.assertEqual(self.kicked, [])
        self.assertIsNone(self.db.active_ban(BUFORD))
        self.assertEqual(self.titles(), ["Stacked loadout on Server 1"])   # staff still hear about it


if __name__ == "__main__":
    unittest.main()
