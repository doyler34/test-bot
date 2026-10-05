import json
import unittest
from urllib.parse import unquote

from aiohttp import web
from aiohttp.test_utils import TestServer

from panel.battlemetrics import BanSync, Client, plain_reason, read_bans
from panel.db import PanelDB, now

BUFORD = "a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d"
HAVOC = "5f1c2a90-8b1e-4a57-9a3e-2d4b6c8e0f11"


class FakeBM:
    """The BattleMetrics side: a ban list held in memory."""

    def __init__(self):
        self.bans = {}
        self.calls = []
        self.next = 100

    async def ban_lists(self):
        return [{"id": "list-1", "name": "OYB", "org": "777"}]

    async def active_bans(self, ban_list):
        return [dict(b, bm_id=bm_id) for bm_id, b in self.bans.items()]

    async def create_ban(self, ban, ban_list, org):
        self.next += 1
        self.calls.append(("create", ban["identity"], ban_list, org))
        self.bans[str(self.next)] = {"identities": [ban["identity"]], "reason": ban["reason"],
                                     "expires": ban["expires_at"], "by": "OYB"}
        return str(self.next)

    async def update_reason(self, bm_id, reason):
        self.calls.append(("reason", bm_id, reason))
        self.bans[bm_id]["reason"] = reason

    async def delete_ban(self, bm_id):
        self.calls.append(("delete", bm_id))
        self.bans.pop(bm_id, None)

    async def close(self):
        pass


class BanSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.bm = FakeBM()
        self.sync = BanSync(self.db, "token", client=self.bm)

    async def test_their_bans_come_in_once(self):
        self.bm.bans["9"] = {"identities": [BUFORD], "reason": "Cheating", "expires": None, "by": "Blitz"}
        self.bm.bans["10"] = {"identities": [], "reason": "Old Steam ban", "expires": None, "by": "Blitz"}
        await self.sync.round()
        await self.sync.round()
        ban = self.db.active_ban(BUFORD)
        self.assertEqual((ban["reason"], ban["created_by"]), ("Cheating", "Blitz (BattleMetrics)"))
        self.assertEqual(len(self.db.bans()), 1)
        self.assertEqual(self.bm.calls, [])
        self.assertEqual(self.sync.status["unmatched"], 1)

    async def test_ours_go_out_once_and_unbans_follow(self):
        ban = self.db.add_ban(HAVOC, "Havoc", "Teamkilling", "gaz", now() + 3600)
        await self.sync.round()
        await self.sync.round()
        self.assertEqual(self.bm.calls, [("create", HAVOC, "list-1", "777")])
        self.db.set_ban_reason(ban, "Teamkilling at main")
        await self.sync.round()
        self.assertEqual(self.bm.calls[-1], ("reason", "101", "Teamkilling at main"))
        self.db.remove_ban(ban, "gaz")
        await self.sync.round()
        await self.sync.round()
        self.assertEqual(self.bm.calls[-1], ("delete", "101"))
        self.assertEqual(self.bm.bans, {})

    async def test_unban_on_their_side_lifts_ours(self):
        self.bm.bans["9"] = {"identities": [BUFORD], "reason": "Cheating", "expires": None, "by": "Blitz"}
        await self.sync.round()
        del self.bm.bans["9"]
        await self.sync.round()
        self.assertIsNone(self.db.active_ban(BUFORD))
        self.assertEqual(self.db.bans(include_old=True)[0]["removed_by"], "BattleMetrics")
        self.assertEqual(self.bm.calls, [])

    async def test_a_player_banned_on_both_stays_one_ban(self):
        self.db.add_ban(BUFORD, "Buford", "cheating", "gaz")
        self.bm.bans["9"] = {"identities": [BUFORD], "reason": "Cheating", "expires": None, "by": "Blitz"}
        await self.sync.round()
        self.assertEqual(len(self.db.bans()), 1)
        self.assertEqual(self.bm.calls, [])

    async def test_several_lists_need_one_picked(self):
        async def two():
            return [{"id": "a", "name": "Main", "org": "1"}, {"id": "b", "name": "Other", "org": "1"}]
        self.bm.ban_lists = two
        with self.assertRaisesRegex(Exception, "Main \\(a\\), Other \\(b\\)"):
            await self.sync.round()


class ReadingTests(unittest.TestCase):
    def test_identifiers_typed_or_linked(self):
        page = {"data": [
            {"id": "1", "attributes": {"reason": "Aimbot | {{timeLeft}}", "expires": "2030-01-01T00:00:00.000Z",
                                       "identifiers": [55, {"type": "ip", "identifier": "1.2.3.4"}]},
             "relationships": {"user": {"data": {"type": "user", "id": "8"}}}},
            {"id": "2", "attributes": {"reason": "Toxic", "expires": None,
                                       "identifiers": [{"type": "reforgerUUID", "identifier": HAVOC.upper()}]}}],
            "included": [{"type": "identifier", "id": "55", "attributes": {"type": "reforgerUUID", "identifier": BUFORD}},
                         {"type": "user", "id": "8", "attributes": {"nickname": "Blitz"}}]}
        bans = read_bans([page])
        self.assertEqual(bans[0]["identities"], [BUFORD])
        self.assertEqual((bans[0]["reason"], bans[0]["by"], bans[0]["expires"]), ("Aimbot", "Blitz", 1893456000))
        self.assertEqual((bans[1]["identities"], bans[1]["expires"]), ([HAVOC], None))

    def test_plain_reason(self):
        self.assertEqual(plain_reason("Cheating - expires {{timeLeft}}"), "Cheating - expires")
        self.assertEqual(plain_reason(""), "Banned on BattleMetrics")


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_requests(self):
        seen = []

        async def handle(request):
            body = await request.text()
            seen.append((request.method, request.path_qs, request.headers.get("Authorization"),
                         json.loads(body) if body else None))
            if request.method == "POST":
                return web.json_response({"data": {"id": "42", "type": "ban"}}, status=201)
            if request.method == "DELETE":
                return web.Response(status=204)
            if request.path == "/bans" and "page2" not in request.path_qs:
                return web.json_response({"data": [], "links": {"next": str(request.url.with_query({"page2": "1"}))}})
            return web.json_response({"data": []})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handle)
        server = TestServer(app)
        await server.start_server()
        client = Client("secret", base=str(server.make_url("")).rstrip("/"))
        try:
            ban = {"identity": BUFORD, "reason": "Cheating", "expires_at": None, "created_by": "gaz"}
            self.assertEqual(await client.create_ban(ban, "list-1", "777"), "42")
            await client.delete_ban("42")
            await client.active_bans("list-1")
        finally:
            await client.close()
            await server.close()
        method, path, auth, body = seen[0]
        self.assertEqual((method, path, auth), ("POST", "/bans", "Bearer secret"))
        self.assertEqual(body["data"]["attributes"]["identifiers"],
                         [{"type": "reforgerUUID", "identifier": BUFORD, "manual": True}])
        self.assertEqual(body["data"]["relationships"]["banList"]["data"]["id"], "list-1")
        self.assertEqual(seen[1][:2], ("DELETE", "/bans/42"))
        self.assertIn("filter[banList]=list-1", unquote(seen[2][1]))
        self.assertEqual(len(seen), 4)


if __name__ == "__main__":
    unittest.main()
