import asyncio
import unittest
import unittest.mock
from datetime import datetime, timezone

from panel import steamage
from panel.db import PanelDB
from panel.joiners import describe, window_text
from panel.steamage import parse, stale

DAY = 86400
AT = 1_800_000_000


def epoch(year, month, day):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


class ParseTests(unittest.TestCase):
    def test_public_profile_gives_the_creation_date(self):
        xml = "<profile><privacyState>public</privacyState><memberSince>December 14, 2009</memberSince></profile>"
        self.assertEqual(parse(xml), (epoch(2009, 12, 14), "public"))

    def test_private_profile_gives_no_date(self):
        self.assertEqual(parse("<profile><privacyState>friendsonly</privacyState></profile>"), (None, "friendsonly"))

    def test_nonsense_is_unknown(self):
        self.assertEqual(parse("<error>The specified profile could not be found.</error>"), (None, "unknown"))
        self.assertEqual(parse("<memberSince>sometime</memberSince>"), (None, "unknown"))

    def test_what_still_needs_asking(self):
        row = {"steam_id": "76561198017975643", "created": None, "checked": None}
        self.assertTrue(stale(row, AT))
        self.assertFalse(stale({**row, "created": 5}, AT))
        self.assertFalse(stale({**row, "checked": AT - DAY}, AT))
        self.assertTrue(stale({**row, "checked": AT - 8 * DAY}, AT))
        self.assertFalse(stale({**row, "steam_id": ""}, AT))
        self.assertFalse(stale({**row, "steam_id": "../etc/passwd"}, AT))


class JoinerTests(unittest.TestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.since = AT - 1800

    def tearDown(self):
        self.db.db.close()

    def join(self, identity, name, first, last, platform="steam", steam="", server="one"):
        self.db.saw_player(identity, name, server, at=first)
        self.db.saw_player(identity, name, server, at=last)
        if platform:
            self.db.add_platforms([{"identity": identity, "platform": platform, "steam": steam, "at": last}])

    def rows(self, steam=False, alts=None, online=()):
        found = self.db.joiners("one", self.since)
        return {r["name"]: r for r in describe(found, AT, self.since, set(online), alts or {}, steam)}

    def test_only_recent_joiners_on_this_server(self):
        self.join("a", "Fresh", AT - 600, AT - 600)
        self.join("b", "Old", AT - 5 * DAY, AT - 4000)
        self.join("c", "Elsewhere", AT - 600, AT - 600, server="two")
        self.assertEqual(set(self.rows()), {"Fresh"})

    def test_first_time_and_platform(self):
        self.join("a", "Fresh", AT - 600, AT - 600, steam="76561198017975643")
        self.join("b", "Returning", AT - 9 * DAY, AT - 300, platform="console")
        rows = self.rows(online=["b"])
        self.assertTrue(rows["Fresh"]["first"])
        self.assertEqual(rows["Fresh"]["platform"], "PC")
        self.assertFalse(rows["Returning"]["first"])
        self.assertEqual(rows["Returning"]["platform"], "Console")
        self.assertTrue(rows["Returning"]["online"])
        self.assertFalse(rows["Fresh"]["online"])

    def test_new_steam_account_on_a_first_visit_is_flagged(self):
        self.join("a", "Newbie", AT - 600, AT - 600, steam="76561198000000001")
        self.join("b", "Veteran", AT - 600, AT - 300, steam="76561198000000002")
        self.join("c", "Hidden", AT - 600, AT - 200, steam="76561198000000003")
        self.db.save_steam_account("76561198000000001", AT - 10 * DAY, "public")
        self.db.save_steam_account("76561198000000002", AT - 4000 * DAY, "public")
        self.db.save_steam_account("76561198000000003", None, "friendsonly")
        rows = self.rows(steam=True)
        self.assertTrue(rows["Newbie"]["watch"])
        self.assertTrue(rows["Newbie"]["account"]["new"])
        self.assertFalse(rows["Veteran"]["watch"])
        self.assertTrue(rows["Veteran"]["account"]["text"].startswith("Steam since"))
        self.assertEqual(rows["Hidden"]["account"]["text"], "Steam profile private")
        self.assertFalse(rows["Hidden"]["watch"])

    def test_account_age_is_left_out_unless_asked_for(self):
        self.join("a", "Newbie", AT - 600, AT - 600, steam="76561198000000001")
        self.db.save_steam_account("76561198000000001", AT - DAY, "public")
        row = self.rows(steam=False)["Newbie"]
        self.assertIsNone(row["account"])
        self.assertFalse(row["watch"])

    def test_a_returning_player_on_a_new_account_is_not_flagged(self):
        self.join("a", "Back", AT - 9 * DAY, AT - 600, steam="76561198000000001")
        self.db.save_steam_account("76561198000000001", AT - DAY, "public")
        self.assertFalse(self.rows(steam=True)["Back"]["watch"])

    def test_never_on_oyb_comes_first_and_oyb_play_time_rules_it_out(self):
        self.join("a", "Has_Played", AT - 600, AT - 100, platform="")
        self.join("b", "Truly_New", AT - 900, AT - 900, platform="")
        self.join("c", "Old_Hand", AT - 9 * DAY, AT - 50, platform="")
        found = self.db.joiners("one", self.since)
        order = describe(found, AT, self.since, set(), {}, False, known={"a"})
        self.assertEqual([r["name"] for r in order], ["Truly_New", "Old_Hand", "Has_Played"])
        self.assertTrue(order[0]["first"])
        self.assertFalse(order[2]["first"])

    def test_without_play_time_records_the_panels_own_sight_decides(self):
        self.join("a", "Truly_New", AT - 900, AT - 900, platform="")
        for known in (None, set()):
            order = describe(self.db.joiners("one", self.since), AT, self.since, set(), {}, False, known=known)
            self.assertTrue(order[0]["first"])

    def test_the_time_looked_back_over_reads_plainly(self):
        self.assertEqual([window_text(m) for m in (1, 30, 60, 90, 120, 1440, 4320)],
                         ["1 minute", "30 minutes", "1 hour", "90 minutes", "2 hours", "1 day", "3 days"])

    def test_a_banned_alt_is_flagged_and_listed_first(self):
        self.join("a", "Quiet", AT - 100, AT - 100, platform="")
        self.join("b", "Evader", AT - 900, AT - 900, platform="")
        order = describe(self.db.joiners("one", self.since), AT, self.since, set(),
                         {"b": {"count": 1, "banned": ["Banned guy"]}}, False)
        self.assertEqual([r["name"] for r in order], ["Evader", "Quiet"])
        self.assertTrue(order[0]["watch"])


PUBLIC = "<profile><privacyState>public</privacyState><memberSince>May 3, 2024</memberSince></profile>"


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self.body


class FakeSteam:
    """Stands in for aiohttp's session so no test ever reaches Steam."""
    status = 200
    asked = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        FakeSteam.asked.append(url)
        return FakeResponse(FakeSteam.status, PUBLIC)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        for number in (1, 2):
            self.db.saw_player(f"id{number}", f"P{number}", "one", at=AT - 60)
            self.db.add_platforms([{"identity": f"id{number}", "platform": "steam", "steam": f"7656119800000000{number}", "at": AT}])
        FakeSteam.status, FakeSteam.asked = 200, []
        steamage.blocked_until = 0.0
        patches = (unittest.mock.patch("panel.steamage.aiohttp.ClientSession", FakeSteam),
                   unittest.mock.patch("panel.steamage.GAP", 0))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.db.db.close)
        self.addCleanup(setattr, steamage, "blocked_until", 0.0)

    def run_refresh(self):
        return asyncio.run(steamage.refresh(self.db, self.db.joiners("one", 0), AT))

    def test_saves_what_steam_says_and_does_not_ask_twice(self):
        self.assertTrue(self.run_refresh())
        self.assertEqual(len(FakeSteam.asked), 2)
        self.assertEqual({r["created"] for r in self.db.joiners("one", 0)}, {epoch(2024, 5, 3)})
        self.assertFalse(self.run_refresh())
        self.assertEqual(len(FakeSteam.asked), 2)

    def test_a_lower_limit_asks_about_fewer_accounts(self):
        asyncio.run(steamage.refresh(self.db, self.db.joiners("one", 0), AT, limit=1))
        self.assertEqual(len(FakeSteam.asked), 1)

    def test_says_how_long_until_steam_is_asked_again(self):
        self.assertEqual(steamage.waiting(), 0)
        FakeSteam.status = 429
        self.run_refresh()
        self.assertTrue(0 < steamage.waiting() <= steamage.BACKOFF)

    def test_backs_off_when_steam_says_slow_down(self):
        FakeSteam.status = 429
        self.assertFalse(self.run_refresh())
        self.assertEqual(len(FakeSteam.asked), 1)
        self.assertFalse(self.run_refresh())
        self.assertEqual(len(FakeSteam.asked), 1)
        self.assertEqual({r["created"] for r in self.db.joiners("one", 0)}, {None})


if __name__ == "__main__":
    unittest.main()
