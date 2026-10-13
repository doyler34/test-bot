import unittest

from panel.connections import read_lines
from panel.db import PanelDB

PC = "5dd87237-58a0-42a7-9379-fa2acfbee349"
XBOX = "13f977b2-510c-45fd-a125-94b8f5d8946e"
ALT = "aaaa7237-58a0-42a7-9379-fa2acfbee349"
LOG = f"""07:27:58.364 BACKEND      : Authenticated player: rplIdentity=0x00000025 identityId={PC} name=John Wick
07:27:58.379  DEFAULT      : BattlEye Server: 'Player #37 John Wick (10.0.0.1:55942) connected'
07:27:58.379  DEFAULT      : BattlEye Server: Setting GUID for player identity=0x00000025, GUID=76561199889955833
07:27:58.815 BACKEND      : Authenticated player: rplIdentity=0x00000026 identityId={XBOX} name=XxOJandV0DKAxX
07:27:58.846  DEFAULT      : BattlEye Server: Setting GUID for player identity=0x00000026, GUID=[u8; 64]
07:27:59.000  DEFAULT      : BattlEye Server: Setting GUID for player identity=0x00000099, GUID=76561190000000000
"""


class PlatformTests(unittest.TestCase):
    def test_steam_ids_and_consoles_from_the_log(self):
        events = [e for e in read_lines(LOG.splitlines(), 1791876433) if e["kind"] == "platform"]
        self.assertEqual([(e["identity"], e["platform"], e["steam"]) for e in events],
                         [(PC, "steam", "76561199889955833"), (XBOX, "console", "")])

    def test_same_steam_account_ties_accounts_together(self):
        db = PanelDB(":memory:")
        db.saw_player(PC, "John Wick", "server-1")
        db.saw_player(ALT, "Steak Eggs", "server-1")
        db.add_platforms([{"identity": PC, "platform": "steam", "steam": "76561199889955833", "at": 100},
                          {"identity": ALT, "platform": "steam", "steam": "76561199889955833", "at": 50},
                          {"identity": XBOX, "platform": "console", "steam": "", "at": 60}], "server-1", "logs_x")
        self.assertEqual(db.platform(PC)["steam_id"], "76561199889955833")
        self.assertEqual([r["identity"] for r in db.same_steam("76561199889955833", PC)], [ALT])
        self.assertEqual(db.same_steam("", XBOX), [])
        db.add_platforms([{"identity": PC, "platform": "steam", "steam": "76561199889955833", "at": 10}])
        self.assertEqual(db.platform(PC)["last_seen"], 100)
        self.assertEqual(db.unread_platform_games("server-1"), [])


if __name__ == "__main__":
    unittest.main()
