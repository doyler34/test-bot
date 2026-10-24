"""The Workshop mod browser: what the API answers become, what gets cached, and the public pages."""
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from panel import auth, workshop
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB
from panel.servers import ServerManager
from panel.web import FILES_ROOT, WORKSHOP, create_app

RADIO = "5965550F24A0C152"
DEP = "597706D1E4C7BAAD"

LIST = {"status": "success", "meta": {"totalPages": 3, "currentPage": 2, "totalMods": 41},
        "data": [{"id": RADIO, "name": "Radio Pack", "author": "Someone", "summary": "Radios",
                  "sizeFormatted": "194 MiB", "rating": 0.88, "ratingCount": 9, "subscriberCount": 31,
                  "imageUrl": "https://example.com/a.png", "workshopUrl": "https://elsewhere.example/x"},
                 {"id": "not-an-id", "name": "Broken row"},
                 {"id": "0123456789ABCDEF", "name": "Plain", "imageUrl": "http://insecure.example/b.png"}]}
DETAIL = {"status": "success", "mod": {"id": RADIO, "name": "Radio Pack", "author": "Someone",
                                       "description": "Line one\nLine two", "license": "Custom license",
                                       "version": "1.2.3", "rating": 0.5, "dependencies": [
                                           {"id": DEP, "name": "Core Framework"}, {"id": "bad", "name": "Junk"}]}}


class WorkshopLogicTests(unittest.IsolatedAsyncioTestCase):
    def fake(self, answers):
        calls = []

        async def fetch(path, query):
            calls.append((path, query))
            answer = answers[path] if path in answers else answers["mods"]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return fetch, calls

    def test_ids_and_links(self):
        self.assertTrue(workshop.valid_id(RADIO))
        self.assertFalse(workshop.valid_id("5965550F24A0C15"))
        self.assertFalse(workshop.valid_id("../../etc"))
        self.assertEqual(workshop.workshop_url(RADIO), f"https://reforger.armaplatform.com/workshop/{RADIO}")

    def test_search_query_is_cleaned(self):
        self.assertEqual(workshop.search_query("  radio   pack ", "newest", 2),
                         {"page": 2, "sort": "newest", "search": "radio pack"})
        self.assertEqual(workshop.search_query("", "bogus", 0), {"page": 1, "sort": "popularity"})
        self.assertEqual(len(workshop.search_query("x" * 200)["search"]), 80)

    def test_preview_drops_bad_rows_and_insecure_images(self):
        rows = [workshop.preview(r) for r in LIST["data"]]
        self.assertIsNotNone(rows[0])
        self.assertIsNone(rows[1])
        self.assertIsNone(rows[2]["imageUrl"])
        self.assertEqual(rows[0]["percent"], 88)
        self.assertEqual(rows[0]["url"], f"/workshop/{RADIO}")
        self.assertEqual(rows[0]["workshopUrl"], workshop.workshop_url(RADIO))
        self.assertIsNone(workshop.preview({"id": RADIO, "rating": None})["percent"])

    async def test_search_returns_only_usable_rows(self):
        fetch, calls = self.fake({"mods": LIST})
        found = await workshop.Workshop(fetch=fetch).search("radio", "popularity", 2)
        self.assertEqual([m["id"] for m in found["mods"]], [RADIO, "0123456789ABCDEF"])
        self.assertEqual((found["page"], found["pages"], found["total"]), (2, 3, 41))
        self.assertEqual(calls[0], ("mods", {"page": 2, "sort": "popularity", "search": "radio"}))

    async def test_answers_are_kept_then_refetched(self):
        now = [0.0]
        fetch, calls = self.fake({"mods": LIST})
        client = workshop.Workshop(fetch=fetch, clock=lambda: now[0])
        await client.search("radio")
        await client.search("radio")
        self.assertEqual(len(calls), 1)
        now[0] += workshop.SEARCH_TTL + 1
        await client.search("radio")
        self.assertEqual(len(calls), 2)

    async def test_errors_are_not_kept(self):
        fetch, calls = self.fake({"mods": workshop.WorkshopError("busy")})
        client = workshop.Workshop(fetch=fetch)
        for _ in range(2):
            with self.assertRaises(workshop.WorkshopError):
                await client.search("radio")
        self.assertEqual(len(calls), 2)

    async def test_mod_detail_and_bad_ids(self):
        fetch, calls = self.fake({f"mods/{RADIO}": DETAIL})
        client = workshop.Workshop(fetch=fetch)
        mod = await client.mod(RADIO)
        self.assertEqual(mod["description"], "Line one\nLine two")
        self.assertEqual(mod["dependencies"], [{"id": DEP, "name": "Core Framework", "url": f"/workshop/{DEP}"}])
        with self.assertRaises(workshop.NotFound):
            await client.mod("nope")
        self.assertEqual(len(calls), 1)

    async def test_missing_mod_is_not_found(self):
        fetch, _ = self.fake({f"mods/{RADIO}": {"status": "error"}})
        with self.assertRaises(workshop.NotFound):
            await workshop.Workshop(fetch=fetch).mod(RADIO)


class WorkshopPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = PanelDB(":memory:")
        self.config = PanelConfig(database=":memory:", cookie_secure=False, servers=[ServerConfig("one", "One")],
                                  site_hosts=["oybgaming.com"])
        self.app = create_app(self.config, self.db, ServerManager(self.config, self.db), start_manager=False)
        self.answers = {"mods": LIST, f"mods/{RADIO}": DETAIL}
        self.app[WORKSHOP] = workshop.Workshop(fetch=self.fetch)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addCleanup(self.tmp.cleanup)

    async def fetch(self, path, query):
        answer = self.answers.get(path, self.answers["mods"])
        if isinstance(answer, Exception):
            raise answer
        if path == "mods" and query.get("search") == "boom":
            raise workshop.WorkshopError("The mod list didn't answer. Try again in a few minutes.")
        return answer

    async def site(self, path, **kwargs):
        return await self.client.get(path, headers={"Host": "www.oybgaming.com"}, **kwargs)

    async def test_search_page_lists_mods(self):
        response = await self.site("/workshop", params={"search": "radio"})
        html = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn("Radio Pack", html)
        self.assertIn(f'href="/workshop/{RADIO}"', html)
        self.assertNotIn("Broken row", html)
        self.assertIn('href="/workshop?search=radio&amp;sort=popularity&amp;page=2"', html)
        self.assertNotIn("Previous", html)

    async def test_search_error_is_shown_not_a_crash(self):
        response = await self.site("/workshop", params={"search": "boom"})
        self.assertEqual(response.status, 200)
        self.assertIn("didn&#39;t answer", await response.text())

    async def test_mod_page_and_official_link(self):
        html = await (await self.site(f"/workshop/{RADIO}")).text()
        self.assertIn("Open in Official Workshop", html)
        self.assertIn(f'href="/workshop/{DEP}"', html)
        self.assertIn("Line one", html)

    async def test_bad_or_unknown_mod_is_404(self):
        self.assertEqual((await self.site("/workshop/not-a-mod")).status, 404)
        self.answers[f"mods/{DEP}"] = {"status": "error"}
        self.assertEqual((await self.site(f"/workshop/{DEP}")).status, 404)

    async def test_panel_host_does_not_serve_the_public_pages(self):
        response = await self.client.get("/workshop", allow_redirects=False)
        self.assertEqual(response.status, 302)

    async def test_the_vanilla_site_does_not_advertise_mods_but_the_page_still_works(self):
        self.assertNotIn('href="/workshop"', await (await self.site("/")).text())
        page = await self.site("/workshop")
        self.assertEqual(page.status, 200)
        self.assertIn('class="site-bar"', await page.text())


class ServerModsSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "instances"
        (root / "Plus01").mkdir(parents=True)
        (root / "Plus01" / "GenericModule.kvp").write_text('App.AppSettings={"mods":""}')
        self.db = PanelDB(":memory:")
        self.db.add_user("boss", auth.hash_password("boss-password"), "owner")
        self.config = PanelConfig(database=":memory:", cookie_secure=False, servers=[ServerConfig("one", "One")])
        self.app = create_app(self.config, self.db, ServerManager(self.config, self.db), start_manager=False)
        self.app[FILES_ROOT] = root
        self.app[WORKSHOP] = workshop.Workshop(fetch=self.fetch)
        self.queries = []
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addCleanup(self.tmp.cleanup)
        await self.client.post("/login", data={"username": "boss", "password": "boss-password"})

    async def fetch(self, path, query):
        self.queries.append(query)
        if query and query.get("search") == "boom":
            raise workshop.WorkshopError("The mod list didn't answer. Try again in a few minutes.")
        return LIST

    async def test_search_on_the_server_mods_tab(self):
        response = await self.client.get("/mods/Plus01", params={"q": "radio"})
        html = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn("Radio Pack", html)
        self.assertIn(f'name="mod_id" value="{RADIO}"', html)
        self.assertIn('href="https://reforger.armaplatform.com/workshop/' + RADIO + '"', html)
        self.assertIn("img-src 'self' https:", response.headers["Content-Security-Policy"])

    async def test_mods_tab_browses_popular_mods_without_a_search(self):
        html = await (await self.client.get("/mods/Plus01")).text()
        self.assertIn("Find a mod", html)
        self.assertIn("Radio Pack", html)
        self.assertEqual(self.queries[-1], {"page": 1, "sort": "popularity"})
        self.assertIn('href="/mods/Plus01?sort=subscribers"', html)

    async def test_category_browses_only_that_tag(self):
        html = await (await self.client.get("/mods/Plus01", params={"tag": "VEHICLES"})).text()
        self.assertEqual(self.queries[-1], {"page": 1, "sort": "popularity", "tags": "VEHICLES"})
        self.assertIn('href="/mods/Plus01?sort=popularity&amp;tag=VEHICLES"', html)
        self.assertIn('class="on" aria-current="true">Vehicles', html)

    async def test_unknown_category_is_ignored(self):
        await self.client.get("/mods/Plus01", params={"tag": "NOPE"})
        self.assertNotIn("tags", self.queries[-1])

    async def test_sort_chip_changes_the_browse_order(self):
        html = await (await self.client.get("/mods/Plus01", params={"sort": "subscribers"})).text()
        self.assertEqual(self.queries[-1], {"page": 1, "sort": "subscribers"})
        self.assertIn('class="on" aria-current="true"', html)

    async def test_search_error_shows_on_the_tab(self):
        html = await (await self.client.get("/mods/Plus01", params={"q": "boom"})).text()
        self.assertIn("didn&#39;t answer", html)


if __name__ == "__main__":
    unittest.main()
