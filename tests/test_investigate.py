import unittest

from panel import investigate

ME, OTHER = "me", "other"


def row(at, killer, victim, distance=50, zone="Chest", damage="KINETIC", kind="kill", kname="John Wick", vname="Them"):
    return {"at": at, "killer": killer, "victim": victim, "distance": distance, "zone": zone, "damage": damage,
            "kind": kind, "killer_name": kname if killer == ME else "Someone", "victim_name": vname if victim != ME else kname}


class InvestigateTests(unittest.TestCase):
    def test_far_apart_bullets_and_bursts(self):
        rows = [row(0, ME, "a", 20), row(1, ME, "b", 1069, "Head"), row(100, ME, "c", 30), row(103, ME, "d", 40),
                row(500, ME, "e", 600, "Head", damage="FRAGMENTATION"), row(501, ME, "f", 10)]
        rows += [row(1000 + i, ME, f"x{i}", 5) for i in range(5)]
        stats = investigate.kill_stats(ME, rows)
        self.assertEqual(len(stats["switches"]), 1)
        self.assertEqual(stats["switches"][0][1]["distance"], 1069)
        self.assertEqual(stats["burst"], 5)
        self.assertEqual((stats["kills"], stats["long"], stats["longest"]), (11, 1, 1069))

    def test_names_and_renames(self):
        rows = [row(0, ME, "a", kname="Lt. Rogers"), row(10, ME, "b"), row(20, "z", ME, kname="John Wick")]
        self.assertEqual(investigate.kill_stats(ME, rows)["names"], ["John Wick", "Lt. Rogers"])

    def test_together_or_swapping(self):
        mine = {ME: [row(0, ME, "a"), row(5000, ME, "b")], OTHER: [row(100, OTHER, "c")]}
        self.assertEqual(investigate.together(mine[ME], mine[OTHER], ME, OTHER), [0])
        self.assertIn("same time", investigate.relationship(ME, OTHER, mine, ["1.2.3.4"])["reading"])
        apart = {ME: [row(0, ME, "a")], OTHER: [row(90000, OTHER, "c")]}
        self.assertIn("swapping", investigate.relationship(ME, OTHER, apart, ["1.2.3.4"])["reading"])

    def test_verdicts(self):
        accounts = [{"identity": ME, "label": "John Wick", "banned": False,
                     "stats": investigate.kill_stats(ME, [row(0, ME, "a", 20), row(1, ME, "b", 900),
                                                          row(50, ME, "c", 15), row(51, ME, "d", 700)])}]
        found = investigate.findings(ME, accounts, {}, [], [])
        self.assertEqual(investigate.verdict(found)[0], "strong")
        quiet = [{"identity": ME, "label": "Fair", "banned": False, "stats": investigate.kill_stats(ME, [row(0, ME, "a")])}]
        self.assertEqual(investigate.verdict(investigate.findings(ME, quiet, {}, [], []))[1], "Nothing stands out")


if __name__ == "__main__":
    unittest.main()


class InvestigatePageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import re
        from aiohttp.test_utils import TestClient, TestServer
        from panel import auth
        from panel.config import PanelConfig
        from panel.db import PanelDB
        from panel.servers import ServerManager
        from panel.web import create_app
        self.re = re
        config = PanelConfig(database=':memory:', cookie_secure=False, servers=[])
        self.db = PanelDB(':memory:')
        self.db.add_user('boss', auth.hash_password('boss-password'), 'owner')
        self.db.add_user('adm', auth.hash_password('adm-password'), 'admin')
        self.who = '5dd87237-58a0-42a7-9379-fa2acfbee349'
        self.db.saw_player(self.who, 'John Wick', 'server-1')
        self.client = TestClient(TestServer(create_app(config, self.db, ServerManager(config, self.db), start_manager=False)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_owners_only(self):
        await self.client.post('/login', data={'username': 'adm', 'password': 'adm-password'})
        self.assertEqual((await self.client.get(f'/player/{self.who}/investigate')).status, 403)
        self.assertNotIn('/investigate', await (await self.client.get(f'/player/{self.who}')).text())
        self.client.session.cookie_jar.clear()
        await self.client.post('/login', data={'username': 'boss', 'password': 'boss-password'})
        page = await (await self.client.get(f'/player/{self.who}/investigate?days=all')).text()
        self.assertIn('Nothing stands out', page)
        self.assertEqual(self.db.audit()[0]['action'], 'investigate')

    async def test_steam_shows_and_links_accounts(self):
        alt = 'aaaa7237-58a0-42a7-9379-fa2acfbee349'
        self.db.saw_player(alt, 'Steak Eggs', 'server-1')
        self.db.add_platforms([{'identity': self.who, 'platform': 'steam', 'steam': '76561199889955833', 'at': 100},
                               {'identity': alt, 'platform': 'steam', 'steam': '76561199889955833', 'at': 50}])
        await self.client.post('/login', data={'username': 'adm', 'password': 'adm-password'})
        page = await (await self.client.get(f'/player/{self.who}')).text()
        self.assertIn('PC · Steam', page)
        self.assertNotIn('76561199889955833', page)
        self.client.session.cookie_jar.clear()
        await self.client.post('/login', data={'username': 'boss', 'password': 'boss-password'})
        page = await (await self.client.get(f'/player/{self.who}')).text()
        self.assertIn('steamcommunity.com/profiles/76561199889955833', page)
        self.assertIn('Same Steam account as', page)
        report = await (await self.client.get(f'/player/{self.who}/investigate?days=all')).text()
        self.assertIn('Steak Eggs', report)
        self.assertIn('same Steam account', report)
        found = await (await self.client.get('/players?q=76561199889955833')).text()
        self.assertIn('Steak Eggs', found)

    async def test_report_with_sus_alerts_and_incidents(self):
        self.db.add_feed('server-1', 'sus', 'John Wick got 6 bullet kills in 30 seconds')
        await self.client.post('/login', data={'username': 'boss', 'password': 'boss-password'})
        reply = await self.client.get(f'/player/{self.who}/investigate?days=all')
        self.assertEqual(reply.status, 200)
        self.assertIn('John Wick got 6 bullet kills in 30 seconds', await reply.text())
