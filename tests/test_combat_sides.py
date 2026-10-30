"""Filling in the side of players who took the field before sides were kept."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate, record_presence
from bot.tracking.combat_ingestor import backfill_sides, fill_sides

ANN = '11111111-1111-1111-1111-111111111111'
BOB = '22222222-2222-2222-2222-222222222222'
DEE = '44444444-4444-4444-4444-444444444444'


def pick(clock, number, name, identity, faction):
    return (f'{clock}   SCRIPT       : INFO: Faction: player {name} (playerID = {number} | UUID = {identity}) '
            f'has joined faction #AR-Faction_{faction} ({faction})\n')


class SideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.links = AccountLinks(self.root / 'links.db')
        self.addCleanup(self.links.close)
        migrate(self.links.db)
        self.db = self.links.db

    def log(self, folder, *lines):
        path = self.root / 'logs' / folder / 'console.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(lines))
        return path

    def presence(self, server, occurred, identity, faction=None):
        with self.db:
            record_presence(self.db, server, occurred, identity, faction)

    def sides(self):
        return dict(((r[0], r[1], r[2]), r[3]) for r in self.db.execute(
            'SELECT server, occurred, identity, faction FROM combat_presence'))

    def test_fills_rows_that_have_no_side(self):
        path = self.log('logs_2026-09-14_20-00-00',
                        pick('20:00:05.100', 1, 'Ann', ANN, 'US'), pick('20:00:07.300', 4, 'Dee', DEE, 'FIA'))
        self.presence('s', '2026-09-14T20:00:05.100', ANN)
        self.presence('s', '2026-09-14T20:00:07.300', DEE)
        self.assertEqual(fill_sides(self.db, 's', path), 2)
        self.assertEqual(self.sides(), {('s', '2026-09-14T20:00:05.100', ANN): 'US',
                                        ('s', '2026-09-14T20:00:07.300', DEE): 'FIA'})

    def test_never_overwrites_a_side_or_touches_another_server_or_makes_rows(self):
        path = self.log('logs_2026-09-14_20-00-00',
                        pick('20:00:06.200', 2, 'Bob', BOB, 'US'), pick('20:00:07.300', 4, 'Dee', DEE, 'US'))
        self.presence('s', '2026-09-14T20:00:06.200', BOB, 'FIA')
        self.presence('other', '2026-09-14T20:00:07.300', DEE)
        self.assertEqual(fill_sides(self.db, 's', path), 0)
        self.assertEqual(self.sides(), {('s', '2026-09-14T20:00:06.200', BOB): 'FIA',
                                        ('other', '2026-09-14T20:00:07.300', DEE): None})

    def test_a_pick_after_midnight_lands_on_the_next_day(self):
        path = self.log('logs_2026-09-14_20-00-00',
                        pick('23:59:58.000', 1, 'Ann', ANN, 'US'), pick('00:00:02.000', 2, 'Bob', BOB, 'FIA'))
        self.presence('s', '2026-09-14T23:59:58.000', ANN)
        self.presence('s', '2026-09-15T00:00:02.000', BOB)
        self.assertEqual(fill_sides(self.db, 's', path), 2)
        self.assertEqual(set(self.sides().values()), {'US', 'FIA'})

    def test_a_line_still_being_written_is_left_for_later(self):
        path = self.log('logs_2026-09-14_20-00-00', pick('20:00:05.100', 1, 'Ann', ANN, 'US').rstrip('\n'))
        self.presence('s', '2026-09-14T20:00:05.100', ANN)
        self.assertEqual(fill_sides(self.db, 's', path), 0)

    def test_the_saved_read_position_is_left_alone(self):
        path = self.log('logs_2026-09-14_20-00-00', pick('20:00:05.100', 1, 'Ann', ANN, 'US'))
        self.presence('s', '2026-09-14T20:00:05.100', ANN)
        before = self.db.execute('SELECT COUNT(*) FROM combat_sources').fetchone()[0]
        fill_sides(self.db, 's', path)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM combat_sources').fetchone()[0], before)

    def test_only_the_newest_log_is_read_and_only_when_something_is_missing(self):
        self.log('logs_2026-09-10_08-00-00', pick('08:00:05.100', 1, 'Ann', ANN, 'US'))
        self.log('logs_2026-09-14_20-00-00', pick('20:00:06.200', 2, 'Bob', BOB, 'FIA'))
        server = SimpleNamespace(id='s', enabled=True, log_dir=str(self.root / 'logs'))
        database = self.db.execute('PRAGMA database_list').fetchone()[2]
        self.assertEqual(backfill_sides(database, [server]), 0)  # nothing is missing yet
        self.presence('s', '2026-09-10T08:00:05.100', ANN)
        self.presence('s', '2026-09-14T20:00:06.200', BOB)
        self.assertEqual(backfill_sides(database, [server]), 1)
        self.assertEqual(self.sides(), {('s', '2026-09-10T08:00:05.100', ANN): None,
                                        ('s', '2026-09-14T20:00:06.200', BOB): 'FIA'})
        self.assertEqual(backfill_sides(database, [SimpleNamespace(id='s', enabled=False, log_dir=server.log_dir)]), 0)


if __name__ == '__main__':
    unittest.main()
