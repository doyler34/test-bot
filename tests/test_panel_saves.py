"""Game XP and bases from OYB Control's kept autosaves on the match boards."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from bot.discord.leaderboard_command import bases_line, match_rows, table
from bot.discord.live_board import board_embeds
from bot.storage import panel_saves
from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import combat_xp, migrate, record_match, stamp
from panel.db import PanelDB

MEDIC = '11111111-0000-4000-8000-000000000001'
GUNNER = '22222222-0000-4000-8000-000000000002'
STRANGER = '33333333-0000-4000-8000-000000000003'
START = datetime(2026, 9, 7, 15, 0)
END = START + timedelta(hours=1)


def at(minutes):
    return int((START + timedelta(minutes=minutes)).timestamp())


class PanelSaves(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'panel.sqlite3'
        panel = PanelDB(str(self.path))
        game = at(0)
        bases = {"b1": {"faction": "US"}, "b2": {"faction": "US"}, "b3": {"faction": "USSR"}}
        panel.add_save("server-7", game, at(5), 0, {"player": {MEDIC: {"xp": 100}, GUNNER: {"xp": 50}}, "base": bases})
        panel.add_save("server-7", game, at(30), 0, {"player": {MEDIC: {"xp": 600}, GUNNER: {"xp": 80}}, "base": bases})
        # A side switch that sets game XP back to nothing doesn't take any off.
        panel.add_save("server-7", game, at(40), 0, {"player": {MEDIC: {"xp": 0}, GUNNER: {"xp": 90}},
                                                     "base": {**bases, "b3": {"faction": "US"}}})
        panel.add_save("server-7", game, at(55), 0, {"player": {MEDIC: {"xp": 200}, GUNNER: {"xp": 90}},
                                                     "base": {**bases, "b3": {"faction": "US"}}})
        # The same players on another server's earlier game aren't this match.
        panel.add_save("server-2", at(-600), at(-500), 0, {"player": {MEDIC: {"xp": 9000}}})
        panel.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_game_xp_gained_and_bases_held_at_the_end(self):
        gains, bases = panel_saves.match_summary({MEDIC, GUNNER}, START, END, self.path)
        # The game began with the match, so the first save's XP counts too.
        self.assertEqual(dict(gains), {MEDIC: 800, GUNNER: 90})
        self.assertEqual(dict(bases), {"US": 3})
        self.assertEqual(bases_line(bases), '🏳️ Bases at the end: US 3')

    def test_no_panel_database_or_no_save_gives_nothing(self):
        self.assertEqual(panel_saves.match_summary({MEDIC}, START, END, Path(self.tmp.name) / 'missing'), ({}, {}))
        self.assertEqual(panel_saves.match_summary({STRANGER}, START, END, self.path), ({}, {}))


class Boards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / 'links.db')
        migrate(self.links.db)
        self.links.verified_link(1, 10, MEDIC, 'admin:1')
        self.links.verified_link(1, 20, GUNNER, 'admin:1')
        with self.links.db:
            self.links.db.execute('INSERT INTO combat_presence VALUES (?,?,?,?)', ('one', stamp(START), MEDIC, 'US'))
            self.links.db.execute("INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation)"
                                  " VALUES ('one','k1',?,?,?,'ENEMY')",
                                  (stamp(START + timedelta(minutes=5)), STRANGER, GUNNER))
        self.guild = SimpleNamespace(id=1, get_member=lambda member_id: None)

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def test_the_match_board_has_no_score_column(self):
        rows = match_rows(self.links, self.guild, 'one', START, END)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(r) == 4 for r in rows))
        self.assertNotIn('Score', table(rows))

    def test_the_finished_board_says_who_held_the_bases(self):
        embeds = board_embeds('Server 1', [('Rook', 3, 1, 'US')], START, END, '#0001A', {'US': 24, 'USSR': 16})
        self.assertTrue(embeds[0].description.startswith('🏳️ Bases at the end: US 24 · RU 16\n'))
        live = board_embeds('Server 1', [('Rook', 3, 1, 'US')], START, None, '#0001A', {'US': 24})
        self.assertNotIn('Bases at the end', live[0].description)

    def test_no_kill_and_no_death_means_no_match_xp(self):
        record_match(self.links.db, 'one', 'Server 1', START, END)
        self.assertEqual(combat_xp(self.links.db, MEDIC), 0)


if __name__ == '__main__':
    unittest.main()


class HeliSeats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'panel.sqlite3'
        panel = PanelDB(str(self.path))
        panel.add_save("server-7", at(0), at(10), 0, {
            "heli": {"h1": {"name": "UH1H"}},
            "player": {GUNNER: {"xp": 0, "vehicle": "h1", "seat": "Gunner"}, MEDIC: {"xp": 0, "vehicle": None}}})
        panel.close()

    def tearDown(self):
        self.tmp.cleanup()

    def spot(self, killer, minutes):
        return ('one', stamp(START + timedelta(minutes=minutes)), killer, 'EXPLOSIVE', 100.0, 80.0, 100.0)

    def test_a_kill_from_up_high_counts_only_from_a_heli_seat(self):
        from bot.tracking.heli import heli_kills
        ground = SimpleNamespace(at=lambda x, z: 0)
        spots = [self.spot(GUNNER, 11), self.spot(MEDIC, 11), self.spot(STRANGER, 11)]
        confirm = panel_saves.heli_check(spots, self.path)
        # The gunner was in the heli, the medic was on a tower, and nothing is known about the third.
        self.assertEqual(dict(heli_kills(spots, ground, confirm)), {GUNNER: 1, STRANGER: 1})
        self.assertEqual(dict(heli_kills(spots, ground)), {GUNNER: 1, MEDIC: 1, STRANGER: 1})

    def test_far_from_any_save_the_height_guess_decides(self):
        confirm = panel_saves.heli_check([self.spot(MEDIC, 50)], self.path)
        self.assertIsNone(confirm(MEDIC, (START + timedelta(minutes=50)).timestamp()))
