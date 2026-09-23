"""Wind: the vector work, and vanilla's own correction tables.

The decomposition is geometry and is checked exactly. The corrections are
checked against the shipped vanilla figures, which came out of data007.pak's
WindData_Shell_*.conf already converted into each sight's mils.
"""
import json
from pathlib import Path
import math
import tempfile
import unittest

from bot.mortar.calibration import Axis, Calibration, Tiles
from bot.mortar.solution import apply_wind, profile, profiles, solution
from bot.mortar.wind import (CROSS_MILS, RANGE_M, BadWind, Table, Wind, build_table,
                             components, read)
from bot.web.server import calculate

MAP = Calibration(name='Everon', width=12800, height=12800, max_zoom=6,
                  lng=Axis(50.0, 0.0), lat=Axis(-50.0, -256.0), earth_correction=True,
                  tiles=Tiles(directory='/nowhere'))

# Figures quoted in the extraction, checked against what is shipped.
KNOWN = [('m252:he', '2', 1200, 22, 38), ('m252:smoke', '4', 2000, 39, 109),
         ('m252:illum', '4', 1000, 163, 175), ('2b14:he', '4', 2000, 36, 108),
         ('2b14:smoke', '3', 1000, 44, 59), ('2b14:illum', '4', 1000, 184, 210)]

# FIXTURE ONLY - invented, never shipped. A flat 20 mils and 40 m at 10 m/s.
ROWS = [[100, 1500, 10.0, 20], [2000, 500, 30.0, 40]]
FIXTURE_WIND = {'source': 'FIXTURE - not vanilla data', 'referenceSpeedMps': 10,
                'rows': [[100, 20.0, 40.0], [2000, 20.0, 40.0]]}


def tube(mils=6400, wind=None, rings=('1',)):
    entry = {'name': 'Test tube', 'faction': 'US', 'mils': mils, 'shells': {'he': {
        'name': 'HE TEST',
        'rings': {r: {'dispersion': 10, 'rows': ROWS,
                      'wind': FIXTURE_WIND if wind is None else wind} for r in rings}}}}
    path = Path(tempfile.mkdtemp()) / 'tables.json'
    path.write_text(json.dumps({'test': entry}))
    return profile('test:he', str(path))


class DecompositionTests(unittest.TestCase):
    """FROM/TO can never be allowed to slip. These are the guard."""

    def split(self, shot_bearing, wind_from, speed=10):
        return components(Wind(speed, wind_from), shot_bearing)

    def test_firing_north(self):
        west = self.split(0, 270)          # from the west, travelling east
        self.assertAlmostEqual(west.parallel, 0, places=9)
        self.assertAlmostEqual(west.crosswind, 10, places=9)
        east = self.split(0, 90)           # from the east, travelling west
        self.assertAlmostEqual(east.crosswind, -10, places=9)
        self.assertAlmostEqual(self.split(0, 180).parallel, 10, places=9)   # tail
        self.assertAlmostEqual(self.split(0, 0).parallel, -10, places=9)    # head

    def test_firing_east(self):
        north = self.split(90, 0)          # from the north, travelling south
        self.assertAlmostEqual(north.parallel, 0, places=9)
        self.assertAlmostEqual(north.crosswind, 10, places=9)
        self.assertAlmostEqual(self.split(90, 270).parallel, 10, places=9)  # tail
        self.assertAlmostEqual(self.split(90, 90).parallel, -10, places=9)  # head

    def test_a_wind_on_the_quarter_gives_both(self):
        both = self.split(0, 225)
        self.assertAlmostEqual(both.parallel, 10 * math.sqrt(0.5), places=9)
        self.assertAlmostEqual(both.crosswind, 10 * math.sqrt(0.5), places=9)

    def test_left_and_right_are_equal_and_opposite(self):
        self.assertAlmostEqual(self.split(0, 90).crosswind, -self.split(0, 270).crosswind,
                               places=9)

    def test_head_and_tail_are_equal_and_opposite(self):
        self.assertAlmostEqual(self.split(0, 0).parallel, -self.split(0, 180).parallel,
                               places=9)

    def test_a_compass_bearing_wraps(self):
        for same in (0, 360, 720, -360):
            self.assertAlmostEqual(read({'speed_mps': 5, 'from_degrees': same}).bearing, 0)
        self.assertAlmostEqual(read({'speed_mps': 5, 'from_degrees': -90}).bearing, 270)
        self.assertAlmostEqual(self.split(0, 360).crosswind, self.split(0, 0).crosswind)

    def test_the_air_travels_opposite_to_where_it_comes_from(self):
        self.assertEqual(Wind(5, 245).travelling, 65)
        self.assertEqual(Wind(5, 0).travelling, 180)
        self.assertEqual(Wind(5, 180).travelling, 0)


class WindInputTests(unittest.TestCase):
    def test_nothing_given_is_calm(self):
        self.assertTrue(read(None).calm)
        self.assertTrue(read({}).calm)

    def test_a_bad_speed_is_refused(self):
        for speed in (-1, 1000, float('nan'), float('inf'), 'breezy', None, True):
            with self.assertRaises(BadWind):
                read({'speed_mps': speed, 'from_degrees': 90})

    def test_a_bad_direction_is_refused_but_a_wrapped_one_is_not(self):
        for direction in ('north', None, float('nan'), True):
            with self.assertRaises(BadWind):
                read({'speed_mps': 5, 'from_degrees': direction})
        self.assertAlmostEqual(read({'speed_mps': 5, 'from_degrees': 359.9}).bearing, 359.9)


class VanillaDataTests(unittest.TestCase):
    """The shipped figures, as quoted in the extraction."""

    def test_the_quoted_values_are_what_is_shipped(self):
        for key, ring, distance, cross, carry in KNOWN:
            table = profile(key).wind_table(ring)
            self.assertAlmostEqual(table.at(distance, CROSS_MILS), cross, places=6,
                                   msg=f'{key} ring {ring} @{distance}m crosswind')
            self.assertAlmostEqual(table.at(distance, RANGE_M), carry, places=6,
                                   msg=f'{key} ring {ring} @{distance}m range')

    def test_every_round_and_ring_carries_wind_data(self):
        expected = {'m252:he': ['0', '1', '2', '3', '4'],
                    'm252:smoke': ['1', '2', '3', '4'],
                    'm252:illum': ['1', '2', '3', '4'],
                    '2b14:he': ['0', '1', '2', '3', '4'],
                    '2b14:smoke': ['0', '1', '2', '3'],
                    '2b14:illum': ['1', '2', '3', '4']}
        self.assertEqual(set(profiles()), set(expected))
        for key, rings in expected.items():
            weapon = profile(key)
            self.assertTrue(weapon.windy, key)
            for ring in rings:
                self.assertTrue(weapon.wind_table(ring).loaded, f'{key} ring {ring}')

    def test_every_ring_with_a_range_table_has_a_wind_table(self):
        for key, weapon in profiles().items():
            for ring, _, _ in weapon.rings:
                self.assertTrue(weapon.wind_table(ring).loaded, f'{key} ring {ring}')

    def test_the_tables_are_quoted_for_ten_metres_a_second(self):
        for key, weapon in profiles().items():
            for ring, _, _ in weapon.rings:
                self.assertEqual(weapon.wind_table(ring).reference, 10.0, f'{key} {ring}')

    def test_the_source_is_recorded_on_every_ring(self):
        blob = json.loads(Path('assets/mortar/tables.json').read_text())
        for tube_key, entry in blob.items():
            for round_key, shell in entry['shells'].items():
                for ring, block in shell['rings'].items():
                    self.assertIn('data007.pak', block['wind']['source'],
                                  f'{tube_key}:{round_key} ring {ring}')

    def test_a_range_halfway_between_rows_is_split_between_them(self):
        # M821 ring 2: 1200m is 22 mils / 38 m, 1300m is 20 / 39.
        table = profile('m252:he').wind_table('2')
        self.assertAlmostEqual(table.at(1250, CROSS_MILS), 21.0, places=9)
        self.assertAlmostEqual(table.at(1250, RANGE_M), 38.5, places=9)
        # 2B14 illum ring 4: 1000m is 184/210, 1100m is 167/212.
        soviet = profile('2b14:illum').wind_table('4')
        self.assertAlmostEqual(soviet.at(1050, CROSS_MILS), 175.5, places=9)
        self.assertAlmostEqual(soviet.at(1050, RANGE_M), 211.0, places=9)

    def test_nothing_is_read_outside_a_rings_wind_rows(self):
        table = profile('m252:he').wind_table('2')
        self.assertEqual(table.span, (200, 1600))
        self.assertIsNone(table.at(199, CROSS_MILS))
        self.assertIsNone(table.at(1601, CROSS_MILS))

    def test_rings_are_never_mixed(self):
        # Ring 2 at 400m is 66 mils; ring 3 at 400m is 109. Neither may leak.
        weapon = profile('m252:he')
        self.assertAlmostEqual(weapon.wind_table('2').at(400, CROSS_MILS), 66, places=9)
        self.assertAlmostEqual(weapon.wind_table('3').at(400, CROSS_MILS), 109, places=9)

    def test_the_two_tubes_have_their_own_figures(self):
        # Not a conversion of one another: the ratio is nothing like 6400/6000.
        nato = profile('m252:he').wind_table('4').at(2000, CROSS_MILS)
        soviet = profile('2b14:he').wind_table('4').at(2000, CROSS_MILS)
        self.assertAlmostEqual(nato, 32, places=9)
        self.assertAlmostEqual(soviet, 36, places=9)


class ScalingTests(unittest.TestCase):
    """Ten metres a second is the reference; everything else is a fraction."""

    def setUp(self):
        self.table = profile('m252:he').wind_table('2')

    def test_a_full_ten_is_the_quoted_figure(self):
        self.assertAlmostEqual(self.table.crosswind(1200, 10), 22, places=9)
        self.assertAlmostEqual(self.table.carry(1200, 10), 38, places=9)

    def test_half_the_wind_is_half_the_correction(self):
        self.assertAlmostEqual(self.table.crosswind(1200, 5), 11, places=9)
        self.assertAlmostEqual(self.table.carry(1200, 5), 19, places=9)

    def test_the_component_carries_its_own_sign(self):
        self.assertAlmostEqual(self.table.crosswind(1200, -5), -11, places=9)
        self.assertAlmostEqual(self.table.carry(1200, -10), -38, places=9)

    def test_no_wind_is_no_correction(self):
        self.assertEqual(self.table.crosswind(1200, 0), 0)
        self.assertEqual(self.table.carry(1200, 0), 0)

    def test_a_diagonal_scales_each_component_on_its_own(self):
        # 4.2 across and 3.7 against, as the brief puts it.
        self.assertAlmostEqual(self.table.crosswind(1200, 4.2), 22 * 0.42, places=9)
        self.assertAlmostEqual(self.table.carry(1200, -3.7), -38 * 0.37, places=9)

    def test_a_table_read_by_the_engine_is_not_converted_again(self):
        # 22 mils must arrive as 22, not run through any milliradian maths.
        shot = apply_wind(profile('m252:he'), 1200, 0, Wind(10, 270))
        self.assertAlmostEqual(shot.crosswind_mils, 22.0, places=9)
        self.assertAlmostEqual(abs(shot.azimuth_mils), 22.0, places=9)
        # What a second conversion would have produced, for contrast.
        self.assertNotAlmostEqual(abs(shot.azimuth_mils),
                                  22.0 / 1000 * 6400 / (2 * math.pi), places=3)


class SignTests(unittest.TestCase):
    """The table gives magnitude. Geometry gives direction."""

    def setUp(self):
        self.tube = profile('m252:he')

    def test_a_crosswind_from_the_west_firing_north_traverses_left(self):
        shot = apply_wind(self.tube, 1200, 0, Wind(10, 270))
        self.assertAlmostEqual(shot.crosswind, 10, places=9)     # air moves east, to the right
        self.assertGreater(shot.crosswind_mils, 0)               # pushed right
        self.assertLess(shot.azimuth_mils, 0)                    # so aim left

    def test_a_crosswind_from_the_east_traverses_right(self):
        shot = apply_wind(self.tube, 1200, 0, Wind(10, 90))
        self.assertAlmostEqual(shot.crosswind, -10, places=9)
        self.assertLess(shot.crosswind_mils, 0)
        self.assertGreater(shot.azimuth_mils, 0)

    def test_the_two_are_equal_and_opposite(self):
        west = apply_wind(self.tube, 1200, 0, Wind(10, 270))
        east = apply_wind(self.tube, 1200, 0, Wind(10, 90))
        self.assertAlmostEqual(west.azimuth_mils, -east.azimuth_mils, places=9)
        self.assertAlmostEqual(abs(west.azimuth_mils), 22, places=9)

    def test_a_head_or_tail_wind_moves_the_azimuth_not_at_all(self):
        for direction in (0, 180):
            shot = apply_wind(self.tube, 1200, 0, Wind(10, direction))
            self.assertAlmostEqual(shot.crosswind, 0, places=9)
            self.assertAlmostEqual(shot.azimuth_mils, 0, places=9)

    def test_a_tailwind_shortens_the_range_the_gun_is_laid_for(self):
        shot = apply_wind(self.tube, 1200, 0, Wind(10, 180))
        self.assertAlmostEqual(shot.parallel, 10, places=9)
        self.assertAlmostEqual(shot.range_m, 38, places=9)
        self.assertAlmostEqual(shot.effective_range, 1162, places=9)

    def test_a_headwind_lengthens_it(self):
        shot = apply_wind(self.tube, 1200, 0, Wind(10, 0))
        self.assertAlmostEqual(shot.parallel, -10, places=9)
        self.assertAlmostEqual(shot.range_m, -38, places=9)
        self.assertAlmostEqual(shot.effective_range, 1238, places=9)

    def test_the_elevation_comes_from_the_existing_range_table(self):
        shot = apply_wind(self.tube, 1200, 0, Wind(10, 180))
        straight, _ = solution(self.tube, shot.effective_range)
        self.assertEqual(shot.final.elevation, straight.elevation)
        self.assertEqual(shot.final.flight, straight.flight)


class ZeroWindTests(unittest.TestCase):
    """Nothing about wind may touch a still-air solution."""

    def test_every_round_is_untouched_by_calm(self):
        for key, weapon in profiles().items():
            low, high = weapon.span
            distance = (low + high) / 2
            plain, every = solution(weapon, distance)
            for wind in (None, Wind(), Wind(0, 245)):
                shot = apply_wind(weapon, distance, 137, wind)
                self.assertEqual(shot.final, plain, key)
                self.assertEqual(shot.final.elevation, plain.elevation, key)
                self.assertEqual(shot.final.ring, plain.ring, key)
                self.assertEqual(shot.azimuth_mils, 0, key)
                self.assertEqual(shot.range_m, 0, key)
                self.assertEqual(shot.effective_range, distance, key)
                self.assertFalse(shot.applied, key)

    def test_the_api_with_calm_matches_the_api_with_no_wind_block(self):
        body = {'tube': 'm252', 'round': 'he', 'gun': {'east': 6000, 'north': 6000},
                'target': {'east': 6800, 'north': 6600}}
        without = calculate(dict(body), MAP)
        calm = calculate({**body, 'wind': {'speed_mps': 0, 'from_degrees': 0}}, MAP)
        for key in ('azimuth_mils', 'elevation_mils', 'ring', 'tof_seconds', 'range_m',
                    'dispersion_m'):
            self.assertEqual(without[key], calm[key], key)


class OutsideTheTableTests(unittest.TestCase):
    def test_a_range_past_the_wind_rows_gets_no_correction(self):
        # A fixture whose wind rows stop short of its range table.
        short = tube(wind={'referenceSpeedMps': 10, 'rows': [[100, 20.0, 40.0],
                                                             [500, 20.0, 40.0]]})
        shot = apply_wind(short, 1000, 0, Wind(10, 270))
        self.assertTrue(shot.has_data)
        self.assertFalse(shot.measured)
        self.assertFalse(shot.applied)
        self.assertEqual(shot.final, shot.base)
        self.assertAlmostEqual(shot.crosswind, 10, places=9)   # still reported

    def test_a_ring_with_no_rows_at_all_is_not_guessed_at(self):
        none = tube(wind={'referenceSpeedMps': 10, 'rows': []})
        shot = apply_wind(none, 1000, 0, Wind(10, 270))
        self.assertFalse(shot.has_data)
        self.assertEqual(shot.azimuth_mils, 0)

    def test_a_broken_block_is_an_empty_table(self):
        for block in (None, {}, 'windy', {'rows': [[100, 1]]},
                      {'rows': [[1, 2, 3], [4, 5, 6]], 'referenceSpeedMps': 0}):
            self.assertFalse(build_table(block).loaded)

    def test_wind_can_carry_a_shot_out_of_range(self):
        weapon = profile('m252:he')
        self.assertIsNotNone(solution(weapon, 2890)[0])
        shot = apply_wind(weapon, 2890, 0, Wind(10, 0))      # headwind, asks for further
        self.assertGreater(shot.effective_range, 2900)
        self.assertIsNone(shot.final)


class ApiTests(unittest.TestCase):
    def solve(self, wind=None, tube='m252', shell='he', target=(6800, 6600)):
        body = {'tube': tube, 'round': shell, 'gun': {'east': 6000, 'north': 6000},
                'target': {'east': target[0], 'north': target[1]}}
        if wind is not None:
            body['wind'] = wind
        return calculate(body, MAP)

    def test_the_reply_carries_the_split_and_both_solutions(self):
        answer = self.solve({'speed_mps': 6, 'from_degrees': 245})
        for key in ('speed_mps', 'from_degrees', 'crosswind_mps', 'parallel_mps',
                    'crosswind_at_10mps_weapon_mils', 'azimuth_correction_weapon_mils',
                    'parallel_range_correction_m', 'has_data', 'measured', 'applied'):
            self.assertIn(key, answer['wind'])
        self.assertTrue(answer['wind']['applied'])
        self.assertIn('base_solution', answer)
        self.assertIn('final_solution', answer)

    def test_the_final_azimuth_is_the_base_plus_the_correction(self):
        answer = self.solve({'speed_mps': 10, 'from_degrees': 245})
        base = answer['base_solution']['azimuth_mils']
        correction = answer['wind']['azimuth_correction_weapon_mils']
        self.assertEqual(answer['final_solution']['azimuth_mils'],
                         round((base + correction) % 6400))
        self.assertEqual(answer['azimuth_mils'], answer['final_solution']['azimuth_mils'])

    def test_a_bad_wind_is_refused_cleanly(self):
        for bad in ({'speed_mps': -4}, {'speed_mps': 'breezy'}, {'speed_mps': 999}):
            answer = self.solve(bad)
            self.assertFalse(answer['valid'])
            self.assertEqual(answer['reason'], 'bad_wind')

    def test_every_round_corrects_for_wind(self):
        for tube_key, shell, target in (('m252', 'he', (6600, 6400)),
                                        ('m252', 'smoke', (6600, 6400)),
                                        ('m252', 'illum', (6600, 6400)),
                                        ('2b14', 'he', (6600, 6400)),
                                        ('2b14', 'smoke', (6600, 6400)),
                                        ('2b14', 'illum', (6600, 6400))):
            answer = self.solve({'speed_mps': 8, 'from_degrees': 245},
                                tube=tube_key, shell=shell, target=target)
            where = f'{tube_key}:{shell}'
            self.assertTrue(answer['valid'], where)
            self.assertTrue(answer['wind']['has_data'], where)
            self.assertTrue(answer['wind']['applied'], where)
            self.assertNotEqual(answer['wind']['azimuth_correction_weapon_mils'], 0, where)

    def test_both_sights_keep_their_own_circles(self):
        nato = self.solve({'speed_mps': 6, 'from_degrees': 245}, tube='m252')
        soviet = self.solve({'speed_mps': 6, 'from_degrees': 245}, tube='2b14')
        self.assertEqual(nato['mils'], 6400)
        self.assertEqual(soviet['mils'], 6000)


if __name__ == '__main__':
    unittest.main()
