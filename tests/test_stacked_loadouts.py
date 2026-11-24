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


class StackedLoadouts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "Server 1")]), self.db,
                                     alerts=FakeAlerts())
        self.state = self.manager.states["one"]
        self.state.players = [{"id": "7", "identity": BUFORD, "name": "Buford"}]
        self.kicked = []

        async def kick(server_id, player_id):
            self.kicked.append(player_id)
            return "ok"
        self.manager.kick = kick
        self.manager.kick_everywhere = unittest.mock.AsyncMock(return_value=[])
        self.manager.push_ban = unittest.mock.AsyncMock(return_value={})

    def settings(self, **extra):
        self.db.publish_discord_doc("staffalerts", json.dumps({"on": True, "count": 3, "seconds": 60, **extra}), "gaz")

    async def save(self, scopes=31, morphine=100, saved=None, game=GAME):
        online = {p["identity"] for p in self.state.players}
        await self.manager.stacked_loadouts(self.state, saved or now(), game, [(BUFORD, scopes, morphine)], online)

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
        self.assertIn("**Buford** has a saved loadout with 31 scopes and 100 morphine", alert["text"])
        await self.save(game=GAME + 1)   # a new game starts the count again
        self.assertEqual(len(self.titles()), 2)
        self.assertEqual(self.kicked, [])   # kick and ban are off unless switched on

    async def test_only_what_is_over_the_limit_is_named(self):
        self.settings()
        await self.save(scopes=1, morphine=22)
        self.assertIn("with 22 morphine on Server 1", self.db.staff_alerts()[0]["text"])

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

    async def test_admins_are_never_kicked_or_banned(self):
        self.settings(loadout_kick=True, loadout_ban=True)
        self.db.set_staff(BUFORD, "gaz", True)
        await self.save()
        self.assertEqual(self.kicked, [])
        self.assertIsNone(self.db.active_ban(BUFORD))
        self.assertEqual(self.titles(), ["Stacked loadout on Server 1"])   # staff still hear about it


if __name__ == "__main__":
    unittest.main()
