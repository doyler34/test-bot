"""What the leaderboards count: net kills, the repeat cap, crash and long-range kills, own accounts,
kills between matches, and the side each player played."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from bot.discord.leaderboard_command import side_of, table
from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import (MAX_DISTANCE, REPEAT_CAP, migrate, record_match, stamp, window_sides,
                                      window_standings)

ALPHA = '11111111-0000-4000-8000-000000000001'
ALPHA_XBOX = '11111111-0000-4000-8000-000000000011'
BRAVO = '22222222-0000-4000-8000-000000000002'
START = datetime(2026, 9, 7, 15, 0)


def foe(n):
    return f'99999999-0000-4000-8000-{n:012d}'


class BoardRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / 'links.db')
        self.db = self.links.db
        migrate(self.db)
        self.links.verified_link(1, 10, ALPHA, 'admin:1')
        self.links.verified_link(1, 10, ALPHA_XBOX, 'admin:1')
        self.links.verified_link(1, 20, BRAVO, 'admin:1')
        self.seq = 0

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def kill(self, minute, killer, victim, relation='ENEMY', damage='KINETIC', distance=50.0, server='one'):
        self.seq += 1
        with self.db:
            self.db.execute('INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation,distance,'
                            'damage_type) VALUES (?,?,?,?,?,?,?,?)',
                            (server, f'k{self.seq}', stamp(START + timedelta(minutes=minute)), victim, killer,
                             relation, distance, damage))

    def board(self, **kw):
        return {r[0]: (r[1], r[2]) for r in window_standings(self.db, 1, START - timedelta(hours=1),
                                                              START + timedelta(hours=5), ground=False, **kw)}

    def test_net_kills_take_teamkills_off(self):
        for n in range(4):
            self.kill(n, ALPHA, foe(n))
        self.kill(5, ALPHA, BRAVO, relation='TK')
        self.assertEqual(self.board()[10], (3, 0))
        self.assertEqual(self.board()[20], (0, 1))

    def test_crash_and_long_range_kills_are_left_off(self):
        self.kill(1, ALPHA, foe(1), damage='COLLISION')
        self.kill(2, ALPHA, foe(2), distance=MAX_DISTANCE + 1)
        self.kill(3, ALPHA, foe(3), distance=MAX_DISTANCE)
        self.assertEqual(self.board()[10], (1, 0))

    def test_the_same_victim_counts_a_few_times_a_match(self):
        for n in range(REPEAT_CAP + 3):
            self.kill(n, ALPHA, BRAVO)
        self.assertEqual(self.board(server='one')[10], (REPEAT_CAP, 0))
        self.assertEqual(self.board(server='one')[20], (0, REPEAT_CAP))

    def test_the_cap_starts_again_each_match(self):
        record_match(self.db, 'one', 'Server 1', START, START + timedelta(minutes=30))
        record_match(self.db, 'one', 'Server 1', START + timedelta(minutes=40), START + timedelta(minutes=90))
        for n in range(REPEAT_CAP + 1):
            self.kill(n, ALPHA, BRAVO)
            self.kill(45 + n, ALPHA, BRAVO)
        self.assertEqual(self.board()[10], (2 * REPEAT_CAP, 0))

    def test_killing_your_own_other_account_does_not_count(self):
        self.kill(1, ALPHA, ALPHA_XBOX)
        self.assertNotIn(10, self.board())

    def test_weekly_board_leaves_out_kills_between_matches(self):
        record_match(self.db, 'one', 'Server 1', START, START + timedelta(minutes=30))
        record_match(self.db, 'one', 'Server 1', START + timedelta(minutes=60), START + timedelta(minutes=90))
        self.kill(10, ALPHA, foe(1))
        self.kill(45, ALPHA, foe(2))     # between the two matches
        self.kill(120, ALPHA, foe(3))    # after the last one: the match being played now
        self.assertEqual(self.board()[10], (2, 0))

    def test_a_server_with_no_matches_recorded_keeps_every_kill(self):
        self.kill(10, ALPHA, foe(1), server='two')
        self.kill(45, ALPHA, foe(2), server='two')
        self.assertEqual(self.board()[10], (2, 0))

    def test_side_played_and_switched(self):
        with self.db:
            for minute, identity, faction in ((1, ALPHA, 'US'), (20, ALPHA_XBOX, 'USSR'), (2, BRAVO, 'USSR')):
                self.db.execute('INSERT INTO combat_presence VALUES (?,?,?,?)',
                                ('one', stamp(START + timedelta(minutes=minute)), identity, faction))
        sides = window_sides(self.db, 1, START, START + timedelta(hours=1))
        self.assertEqual((side_of(sides, 10), side_of(sides, 20), side_of(sides, 30)), ('RU*', 'RU', '-'))


class Table(unittest.TestCase):
    def test_a_row_with_score_still_fits_a_phone(self):
        rows = [('A very long player name', 120, 45, 'FIA*', 12345), ('Short', 3, 1, 'US', None)]
        lines = table(rows).splitlines()[1:-1]
        self.assertEqual(lines[0].split(), ['#', 'Name', 'K', 'D', 'Score', 'Side'])
        self.assertTrue(all(len(line) <= 37 for line in lines), lines)
        self.assertIn('12345', lines[1])
        self.assertIn(' - ', lines[2])

    def test_the_weekly_table_without_score(self):
        lines = table([('Rook', 9, 2, 'US')]).splitlines()[1:-1]
        self.assertEqual(lines[0].split(), ['#', 'Name', 'K', 'D', 'Side'])
        self.assertTrue(lines[1].endswith('US'))


if __name__ == '__main__':
    unittest.main()


class RankSide(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / 'links.db')
        migrate(self.links.db)
        self.links.verified_link(1, 10, ALPHA, 'admin:1')
        self.bot = SimpleNamespace(account_links=self.links)
        self.guild = SimpleNamespace(id=1, get_member=lambda member_id: None)

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def played(self, *factions):
        with self.links.db:
            for n, faction in enumerate(factions):
                self.links.db.execute('INSERT INTO combat_presence VALUES (?,?,?,?)',
                                      ('one', stamp(START + timedelta(minutes=n)), ALPHA, faction))

    def test_no_role_ranks_by_the_side_played_most(self):
        from bot.discord.factions import ranked_faction
        self.assertEqual(ranked_faction(self.bot, self.guild, 10), (None, False))
        self.played('US', 'USSR', 'USSR')
        self.assertEqual(ranked_faction(self.bot, self.guild, 10), ('USSR', True))

    def test_a_faction_pick_wins_and_choosing_renegade_stays_renegade(self):
        from bot.discord.factions import STAYED_RENEGADE, ranked_faction
        self.played('USSR')
        self.links.set_faction(1, 10, 'US')
        self.assertEqual(ranked_faction(self.bot, self.guild, 10), ('US', False))
        self.links.set_faction(1, 10, STAYED_RENEGADE)
        self.assertEqual(ranked_faction(self.bot, self.guild, 10), (None, False))
