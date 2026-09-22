"""Firing solutions: grid parsing, bearing, range and the elevation table."""
import json
from pathlib import Path
import tempfile
import unittest

from bot.mortar.plot import render
from bot.mortar.solution import bearing, has_table, interpolate, parse_grid, solution, tube_names


def table(charges):
    folder = tempfile.mkdtemp()
    path = Path(folder) / 'tables.json'
    path.write_text(json.dumps({'m252': {'name': 'M252 81mm (US)', 'charges': charges}}))
    return str(path)


class GridTests(unittest.TestCase):
    def test_a_grid_reads_at_the_precision_it_was_given(self):
        # 6 digits is 100m, 8 is 10m, 10 is 1m - the same as reading the map.
        self.assertEqual(parse_grid('042118'), (4200, 11800))
        self.assertEqual(parse_grid('04281183'), (4280, 11830))
        self.assertEqual(parse_grid('0428311834'), (4283, 11834))
        self.assertEqual(parse_grid('0411'), (4000, 11000))

    def test_spacing_and_punctuation_are_ignored(self):
        for text in ('0428 1183', '0428-1183', ' 0428,1183 '):
            self.assertEqual(parse_grid(text), (4280, 11830))

    def test_a_grid_that_is_not_a_grid_is_refused(self):
        for text in ('', '123', '0428118', 'north of the bridge', '1' * 12):
            with self.assertRaises(ValueError):
                parse_grid(text)


class BearingTests(unittest.TestCase):
    def test_the_cardinals_come_out_where_they_should(self):
        gun = (5000, 5000)
        for target, mils in [((5000, 6000), 0), ((6000, 5000), 1600),
                             ((5000, 4000), 3200), ((4000, 5000), 4800)]:
            self.assertAlmostEqual(bearing(gun, target)[0], mils, places=3)

    def test_range_is_the_straight_line_distance(self):
        self.assertAlmostEqual(bearing((5000, 5000), (5300, 5400))[1], 500, places=6)

    def test_a_target_on_the_gun_has_no_range(self):
        self.assertEqual(bearing((5000, 5000), (5000, 5000))[1], 0)


class ElevationTests(unittest.TestCase):
    def test_a_range_between_two_rows_is_split_between_them(self):
        rows = [[100, 1500], [200, 1400]]
        self.assertEqual(interpolate(rows, 100), 1500)
        self.assertEqual(interpolate(rows, 150), 1450)
        self.assertEqual(interpolate(rows, 200), 1400)

    def test_beyond_the_table_is_out_of_range_not_a_guess(self):
        rows = [[100, 1500], [200, 1400]]
        self.assertIsNone(interpolate(rows, 99))
        self.assertIsNone(interpolate(rows, 260))

    def test_each_charge_reports_its_own_reach(self):
        path = table({'0': [[100, 1500], [200, 1400]], '1': [[300, 1490], [400, 1420]]})
        charges = {c.charge: c for c in solution('m252', 350, path)}
        self.assertAlmostEqual(charges['1'].elevation, 1455)
        self.assertIsNone(charges['0'].elevation)
        self.assertIn('out of range', charges['0'].note)

    def test_a_tube_with_no_numbers_yet_says_so(self):
        path = table({'0': []})
        self.assertFalse(has_table('m252', path))
        self.assertEqual([c.note for c in solution('m252', 350, path)], ['no table'])

    def test_the_shipped_tables_load(self):
        # The file is real and readable even while the numbers are still being
        # gathered; a missing file must not take the command down.
        self.assertIn('m252', tube_names())
        self.assertEqual(solution('m252', 900, '/nonexistent/tables.json'), [])


class PlotTests(unittest.TestCase):
    def test_the_plot_renders_a_png(self):
        gun, target = parse_grid('04281183'), parse_grid('05121096')
        mils, distance = bearing(gun, target)
        data = render(gun, target, mils, distance, 'M252 81mm (US)')
        self.assertTrue(data.startswith(b'\x89PNG'))

    def test_a_short_shot_still_draws(self):
        # Two points a few metres apart must not collapse the scale to zero.
        data = render((5000, 5000), (5005, 5002), 1200, 5.4, 'M252 81mm (US)')
        self.assertTrue(data.startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
