"""Wind: the vector work, and what it does to a solution.

The decomposition is geometry and is checked exactly. The corrections are
checked against a made-up wind table held here in the test - made-up on
purpose, so that the arithmetic and the signs are proven without anyone being
tempted to ship invented figures as if they were measured.
"""
import json
from pathlib import Path
import math
import tempfile
import unittest

from bot.mortar.calibration import Axis, Calibration, Tiles
from bot.mortar.solution import apply_wind, bearing, profile, profiles, solution
from bot.mortar.wind import (CROSS_MRAD, PARALLEL_M, BadWind, Wind, build_table,
                             by_distance, components, read, sight_mils)
from bot.web.server import calculate

MAP = Calibration(name='Everon', width=12800, height=12800, max_zoom=6,
                  lng=Axis(50.0, 0.0), lat=Axis(-50.0, -256.0), earth_correction=True,
                  tiles=Tiles(directory='/nowhere'))

# ---------------------------------------------------------------------------
# FIXTURE DATA - INVENTED FOR THESE TESTS ONLY.
#
# These are NOT Reforger figures and must never reach assets/mortar. They are
# round numbers chosen so the arithmetic can be checked by eye: at 5 m/s, a
# crosswind of 2 mrad and a tailwind carrying the round 20 m, flat across the
# band. Sample order is GetDataByDistance's:
#   [angle rad, distance m, peak altitude m, crosswind mrad, parallel m, impact rad]
# ---------------------------------------------------------------------------
ROWS = [[100, 1500, 10.0, 20], [2000, 500, 30.0, 40]]
FIXTURE_SAMPLES = {'5': [[0.95, 100, 150.0, 2.0, 20.0, 1.05],
                         [0.80, 2000, 900.0, 2.0, 20.0, 1.20]]}


def tube(mils=6400, samples=None, rings=('1',), coef=1.0):
    """A made-up tube carrying the fixture wind table above."""
    wind = {'source': 'FIXTURE - not Reforger data', 'initSpeedCoef': coef,
            'samples': FIXTURE_SAMPLES if samples is None else samples}
    entry = {'name': 'Test tube', 'faction': 'US', 'mils': mils, 'shells': {'he': {
        'name': 'HE TEST', 'rings': {r: {'dispersion': 10, 'rows': ROWS, 'wind': wind}
                                     for r in rings}}}}
    path = Path(tempfile.mkdtemp()) / 'tables.json'
    path.write_text(json.dumps({'test': entry}))
    return profile('test:he', str(path))


class DecompositionTests(unittest.TestCase):
    """FROM/TO can never be allowed to slip. These are the guard."""

    def split(self, shot_bearing, wind_from, speed=10):
        return components(Wind(speed, wind_from), shot_bearing)

    def test_firing_north(self):
        # Wind FROM the west travels east: straight across, to the right.
        west = self.split(0, 270)
        self.assertAlmostEqual(west.parallel, 0, places=9)
        self.assertAlmostEqual(west.crosswind, 10, places=9)
        # FROM the south travels north, the way the shell is going: tailwind.
        self.assertAlmostEqual(self.split(0, 180).parallel, 10, places=9)
        # FROM the north travels south, into the shell: headwind.
        self.assertAlmostEqual(self.split(0, 0).parallel, -10, places=9)
        # FROM the east travels west: across, to the left.
        self.assertAlmostEqual(self.split(0, 90).crosswind, -10, places=9)

    def test_firing_east(self):
        # Wind FROM the north travels south; facing east, south is the right.
        north = self.split(90, 0)
        self.assertAlmostEqual(north.parallel, 0, places=9)
        self.assertAlmostEqual(north.crosswind, 10, places=9)
        self.assertAlmostEqual(self.split(90, 270).parallel, 10, places=9)   # tail
        self.assertAlmostEqual(self.split(90, 90).parallel, -10, places=9)   # head

    def test_a_wind_on_the_quarter_gives_both(self):
        both = self.split(0, 225)       # from the south-west, towards the north-east
        self.assertAlmostEqual(both.parallel, 10 * math.sqrt(0.5), places=9)
        self.assertAlmostEqual(both.crosswind, 10 * math.sqrt(0.5), places=9)
        self.assertGreater(both.parallel, 0)    # tail
        self.assertGreater(both.crosswind, 0)   # and to the right

    def test_left_and_right_are_equal_and_opposite(self):
        left, right = self.split(0, 90), self.split(0, 270)
        self.assertAlmostEqual(left.crosswind, -right.crosswind, places=9)
        self.assertAlmostEqual(abs(left.crosswind), abs(right.crosswind), places=9)

    def test_head_and_tail_are_equal_and_opposite(self):
        self.assertAlmostEqual(self.split(0, 0).parallel, -self.split(0, 180).parallel,
                               places=9)

    def test_a_compass_bearing_wraps(self):
        for same in (0, 360, 720, -360):
            self.assertAlmostEqual(read({'speed_mps': 5, 'from_degrees': same}).bearing, 0)
        self.assertAlmostEqual(read({'speed_mps': 5, 'from_degrees': -90}).bearing, 270)
        self.assertAlmostEqual(read({'speed_mps': 5, 'from_degrees': 405}).bearing, 45)
        # And the split is the same whichever way it was written.
        self.assertAlmostEqual(self.split(0, 360).crosswind, self.split(0, 0).crosswind)

    def test_calm_is_calm_from_any_direction(self):
        for direction in (0, 90, 187, 359):
            self.assertEqual(components(Wind(0, direction), 123), components(Wind(0, 0), 0))

    def test_the_air_travels_opposite_to_where_it_comes_from(self):
        self.assertEqual(Wind(5, 245).travelling, 65)
        self.assertEqual(Wind(5, 0).travelling, 180)
        self.assertEqual(Wind(5, 180).travelling, 0)


class WindInputTests(unittest.TestCase):
    def test_nothing_given_is_calm(self):
        self.assertTrue(read(None).calm)
        self.assertTrue(read({}).calm)

    def test_a_bad_speed_is_refused(self):
        for speed in (-1, -0.5, 1000, float('nan'), float('inf'), 'breezy', None, True):
            with self.assertRaises(BadWind):
                read({'speed_mps': speed, 'from_degrees': 90})

    def test_a_bad_direction_is_refused_but_a_wrapped_one_is_not(self):
        for direction in ('north', None, float('nan'), True):
            with self.assertRaises(BadWind):
                read({'speed_mps': 5, 'from_degrees': direction})
        self.assertAlmostEqual(read({'speed_mps': 5, 'from_degrees': 359.9}).bearing, 359.9)

    def test_the_whole_wind_block_must_be_a_block(self):
        with self.assertRaises(BadWind):
            read('windy')


class MilSystemTests(unittest.TestCase):
    """Milliradians are not either sight's mils. One conversion, both ways."""

    def test_a_full_circle_is_two_thousand_pi_milliradians(self):
        self.assertAlmostEqual(sight_mils(2000 * math.pi, 6400), 6400, places=6)
        self.assertAlmostEqual(sight_mils(2000 * math.pi, 6000), 6000, places=6)

    def test_one_radian_reads_differently_on_each_sight(self):
        self.assertAlmostEqual(sight_mils(1000, 6400), 1018.5916, places=3)
        self.assertAlmostEqual(sight_mils(1000, 6000), 954.9297, places=3)

    def test_the_m252_conversion(self):
        # 2 mrad on a 6400 sight.
        self.assertAlmostEqual(sight_mils(2, 6400), 2 / 1000 * 6400 / (2 * math.pi), places=9)
        self.assertAlmostEqual(sight_mils(2, 6400), 2.0372, places=3)

    def test_the_2b14_conversion(self):
        self.assertAlmostEqual(sight_mils(2, 6000), 2 / 1000 * 6000 / (2 * math.pi), places=9)
        self.assertAlmostEqual(sight_mils(2, 6000), 1.9099, places=3)

    def test_nothing_converts_to_nothing(self):
        for circle in (6400, 6000):
            self.assertEqual(sight_mils(0, circle), 0)

    def test_the_two_sights_differ_by_their_circles(self):
        self.assertAlmostEqual(sight_mils(7, 6400) / sight_mils(7, 6000), 6400 / 6000,
                               places=9)

    def test_each_tube_corrects_on_its_own_circle(self):
        nato, soviet = tube(mils=6400), tube(mils=6000)
        wind = Wind(5, 270)                        # straight across, to the right
        one = apply_wind(nato, 1000, 0, wind)
        two = apply_wind(soviet, 1000, 0, wind)
        self.assertLess(one.azimuth_mils, 0)       # right drift, traverse left
        self.assertAlmostEqual(one.crosswind_mrad, two.crosswind_mrad, places=9)
        self.assertAlmostEqual(one.azimuth_mils / two.azimuth_mils, 6400 / 6000, places=9)
        # And the native figure is the fixture's, untouched by either sight.
        self.assertAlmostEqual(one.crosswind_mrad, 2.0, places=9)

    def test_the_shipped_tubes_keep_their_circles(self):
        self.assertEqual(profile('m252:he').mils, 6400)
        self.assertEqual(profile('2b14:he').mils, 6000)


class TableLookupTests(unittest.TestCase):
    """Reading Reforger's samples: by range, by wind speed, never past them."""

    def setUp(self):
        self.table = build_table({'initSpeedCoef': 1.0, 'samples': {
            '5': [[0.9, 500, 200, 2.0, 20.0, 1.1], [0.8, 1500, 300, 6.0, 60.0, 1.2]]}})

    def test_the_table_knows_its_charge(self):
        self.assertEqual(self.table.init_speed_coef, 1.0)
        self.assertTrue(self.table.loaded)
        self.assertEqual(self.table.fastest, 5.0)

    def test_a_range_between_samples_is_split_between_them(self):
        self.assertEqual(by_distance(self.table.speeds[0][1], 500, CROSS_MRAD), 2.0)
        self.assertEqual(by_distance(self.table.speeds[0][1], 1000, CROSS_MRAD), 4.0)
        self.assertEqual(by_distance(self.table.speeds[0][1], 1000, PARALLEL_M), 40.0)

    def test_outside_the_samples_there_is_nothing_to_read(self):
        self.assertIsNone(by_distance(self.table.speeds[0][1], 499, CROSS_MRAD))
        self.assertIsNone(by_distance(self.table.speeds[0][1], 1501, CROSS_MRAD))
        self.assertIsNone(self.table.at(2000, 5, CROSS_MRAD))

    def test_wind_speed_is_split_down_to_nothing(self):
        # No wind is no correction: that end is certain, so it anchors.
        self.assertEqual(self.table.at(1000, 0, CROSS_MRAD), 0.0)
        self.assertEqual(self.table.at(1000, 2.5, CROSS_MRAD), 2.0)
        self.assertEqual(self.table.at(1000, 5, CROSS_MRAD), 4.0)

    def test_above_the_fastest_sample_nothing_is_guessed(self):
        self.assertIsNone(self.table.at(1000, 5.1, CROSS_MRAD))
        self.assertIsNone(self.table.at(1000, 20, CROSS_MRAD))

    def test_two_wind_speeds_are_split_between_each_other(self):
        pair = build_table({'samples': {
            '5': [[0.9, 500, 200, 2.0, 20.0, 1.1], [0.8, 1500, 300, 2.0, 20.0, 1.2]],
            '10': [[0.9, 500, 200, 6.0, 60.0, 1.1], [0.8, 1500, 300, 6.0, 60.0, 1.2]]}})
        self.assertEqual(pair.at(1000, 5, CROSS_MRAD), 2.0)
        self.assertEqual(pair.at(1000, 10, CROSS_MRAD), 6.0)
        self.assertEqual(pair.at(1000, 7.5, CROSS_MRAD), 4.0)
        self.assertIsNone(pair.at(1000, 11, CROSS_MRAD))

    def test_a_block_with_nothing_in_it_is_an_empty_table(self):
        for block in (None, {}, {'samples': {}}, 'windy',
                      {'samples': {'5': [[0.9, 500, 200, 2.0, 20.0, 1.1]]}}):
            self.assertFalse(build_table(block).loaded)


class CorrectionTests(unittest.TestCase):
    """The fixture gives 2 mrad and 20 m at 5 m/s, so the sums are checkable."""

    def setUp(self):
        self.tube = tube()

    def test_calm_leaves_the_solution_exactly_as_it_was(self):
        plain, _ = solution(self.tube, 1000)
        for wind in (None, Wind(), Wind(0, 245)):
            shot = apply_wind(self.tube, 1000, 45, wind)
            self.assertEqual(shot.final, plain)
            self.assertEqual(shot.azimuth_mils, 0)
            self.assertEqual(shot.crosswind_mrad, 0)
            self.assertEqual(shot.range_m, 0)
            self.assertEqual(shot.effective_range, 1000)
            self.assertFalse(shot.applied)

    def test_a_crosswind_from_the_right_traverses_left(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(5, 270))
        self.assertAlmostEqual(shot.crosswind, 5, places=9)
        self.assertAlmostEqual(shot.crosswind_mrad, 2.0, places=9)
        self.assertLess(shot.azimuth_mils, 0)
        self.assertAlmostEqual(shot.azimuth_mils, -sight_mils(2.0, 6400), places=9)

    def test_a_crosswind_from_the_left_traverses_right(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(5, 90))
        self.assertAlmostEqual(shot.crosswind, -5, places=9)
        self.assertAlmostEqual(shot.crosswind_mrad, -2.0, places=9)
        self.assertGreater(shot.azimuth_mils, 0)

    def test_left_and_right_corrections_match_in_size(self):
        left = apply_wind(self.tube, 1000, 0, Wind(5, 90))
        right = apply_wind(self.tube, 1000, 0, Wind(5, 270))
        self.assertAlmostEqual(left.azimuth_mils, -right.azimuth_mils, places=9)
        self.assertAlmostEqual(left.crosswind_mrad, -right.crosswind_mrad, places=9)

    def test_a_tailwind_makes_the_gun_shoot_shorter(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(5, 180))
        self.assertAlmostEqual(shot.parallel, 5, places=9)
        self.assertAlmostEqual(shot.range_m, 20, places=9)
        self.assertAlmostEqual(shot.effective_range, 980, places=9)
        self.assertEqual(shot.crosswind_mrad, 0)

    def test_a_headwind_makes_the_gun_shoot_longer(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(5, 0))
        self.assertAlmostEqual(shot.parallel, -5, places=9)
        self.assertAlmostEqual(shot.range_m, -20, places=9)
        self.assertAlmostEqual(shot.effective_range, 1020, places=9)

    def test_head_and_tail_move_the_elevation_opposite_ways(self):
        head = apply_wind(self.tube, 1000, 0, Wind(5, 0))
        tail = apply_wind(self.tube, 1000, 0, Wind(5, 180))
        plain, _ = solution(self.tube, 1000)
        # A tailwind carries it long, so the gun is laid for a shorter range -
        # and on these tables a shorter range is a STEEPER tube.
        self.assertGreater(tail.final.elevation, plain.elevation)
        self.assertLess(head.final.elevation, plain.elevation)

    def test_the_elevation_comes_from_the_existing_table(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(5, 180))
        straight, _ = solution(self.tube, shot.effective_range)
        self.assertEqual(shot.final.elevation, straight.elevation)
        self.assertEqual(shot.final.flight, straight.flight)

    def test_a_quartering_wind_moves_both(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(5, 225))
        self.assertGreater(shot.parallel, 0)
        self.assertGreater(shot.crosswind, 0)
        self.assertNotEqual(shot.range_m, 0)
        self.assertNotEqual(shot.azimuth_mils, 0)
        # Each component is looked up at its own strength, not the full speed.
        self.assertLess(abs(shot.crosswind_mrad), 2.0)

    def test_the_base_solution_is_kept_beside_the_corrected_one(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(5, 180))
        plain, _ = solution(self.tube, 1000)
        self.assertEqual(shot.base, plain)
        self.assertNotEqual(shot.final, shot.base)

    def test_wind_can_carry_a_shot_out_of_range(self):
        self.assertIsNotNone(solution(self.tube, 1990)[0])
        shot = apply_wind(self.tube, 1990, 0, Wind(5, 0))
        self.assertGreater(shot.effective_range, 2000)
        self.assertIsNone(shot.final)
        self.assertIsNotNone(shot.base)

    def test_every_ring_with_samples_is_corrected(self):
        several = tube(rings=('0', '1', '2'))
        for ring in ('0', '1', '2'):
            self.assertTrue(several.wind_table(ring).loaded, ring)
            self.assertEqual(several.wind_table(ring).init_speed_coef, 1.0)
        self.assertAlmostEqual(apply_wind(several, 1000, 0, Wind(5, 180)).range_m, 20,
                               places=9)

    def test_a_ring_without_samples_is_not_guessed_at(self):
        half = tube(samples={})
        shot = apply_wind(half, 1000, 0, Wind(5, 180))
        self.assertFalse(shot.has_data)
        self.assertEqual(shot.range_m, 0)
        self.assertEqual(shot.azimuth_mils, 0)
        self.assertEqual(shot.final, shot.base)
        self.assertAlmostEqual(shot.parallel, 5, places=9)   # still reported

    def test_a_wind_beyond_the_samples_is_not_reached_past(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(9, 180))   # fixture stops at 5
        self.assertTrue(shot.has_data)
        self.assertFalse(shot.measured)
        self.assertFalse(shot.applied)
        self.assertEqual(shot.final, shot.base)


class ShippedDataTests(unittest.TestCase):
    """Every real round is wired for wind and has no figures yet."""

    def test_no_shipped_round_claims_wind_data_it_does_not_have(self):
        for key, weapon in profiles().items():
            self.assertFalse(weapon.windy, f'{key} claims wind data - where did it come from?')

    def test_every_shipped_round_is_wired_for_it(self):
        # Reforger's own shape, empty, on every ring of every round.
        blob = json.loads(Path('assets/mortar/tables.json').read_text())
        for tube_key, entry in blob.items():
            self.assertEqual(entry['tables']['status'], 'provisional', tube_key)
            for round_key, shell in entry['shells'].items():
                for ring, block in shell['rings'].items():
                    where = f'{tube_key}:{round_key} ring {ring}'
                    self.assertIn('wind', block, where)
                    self.assertEqual(block['wind']['samples'], {}, where)
                    self.assertIsNone(block['wind']['initSpeedCoef'], where)
                    self.assertEqual(block['wind']['source'], '', where)

    def test_the_shipped_rounds_answer_calmly_with_no_data(self):
        for key in profiles():
            weapon = profile(key)
            shot = apply_wind(weapon, 900, 45, Wind(8, 245))
            self.assertFalse(shot.has_data, key)
            self.assertEqual(shot.final, shot.base, key)


class ApiTests(unittest.TestCase):
    def solve(self, wind=None, tube='m252', shell='he', target=(6800, 6600)):
        body = {'tube': tube, 'round': shell, 'gun': {'east': 6000, 'north': 6000},
                'target': {'east': target[0], 'north': target[1]}}
        if wind is not None:
            body['wind'] = wind
        return calculate(body, MAP)

    def test_no_wind_block_is_the_old_answer_exactly(self):
        without = self.solve()
        calm = self.solve({'speed_mps': 0, 'from_degrees': 0})
        for key in ('azimuth_mils', 'elevation_mils', 'ring', 'tof_seconds', 'range_m'):
            self.assertEqual(without[key], calm[key], key)
        self.assertEqual(without['base_solution'], without['final_solution']
                         | {'range_m': without['range_m']})

    def test_the_reply_carries_the_split_and_both_solutions(self):
        answer = self.solve({'speed_mps': 6, 'from_degrees': 245})
        self.assertEqual(answer['wind']['speed_mps'], 6)
        self.assertEqual(answer['wind']['from_degrees'], 245)
        for key in ('crosswind_mps', 'parallel_mps', 'crosswind_correction_mrad',
                    'azimuth_correction_weapon_mils', 'parallel_range_correction_m',
                    'has_data', 'measured', 'applied'):
            self.assertIn(key, answer['wind'])
        self.assertIn('base_solution', answer)
        self.assertIn('final_solution', answer)

    def test_the_split_is_reported_even_with_no_table(self):
        # Firing north-east-ish with a wind from the south-west: a tailwind.
        answer = self.solve({'speed_mps': 10, 'from_degrees': 225}, target=(6800, 6600))
        self.assertNotEqual(answer['wind']['parallel_mps'], 0)
        self.assertFalse(answer['wind']['has_data'])
        self.assertFalse(answer['wind']['applied'])

    def test_a_bad_wind_is_refused_cleanly(self):
        for bad in ({'speed_mps': -4}, {'speed_mps': 'breezy'}, {'speed_mps': 999},
                    {'speed_mps': 5, 'from_degrees': 'north'}):
            answer = self.solve(bad)
            self.assertFalse(answer['valid'])
            self.assertEqual(answer['reason'], 'bad_wind')

    def test_a_wrapped_direction_is_taken_as_a_bearing(self):
        answer = self.solve({'speed_mps': 5, 'from_degrees': 450})
        self.assertTrue(answer['valid'])
        self.assertEqual(answer['wind']['from_degrees'], 90)

    def test_changing_only_the_wind_changes_nothing_else(self):
        still = self.solve({'speed_mps': 0, 'from_degrees': 0})
        blowing = self.solve({'speed_mps': 9, 'from_degrees': 300})
        for key in ('gun', 'target', 'range_m', 'mortar_grid', 'target_grid'):
            self.assertEqual(still[key], blowing[key], key)
        self.assertNotEqual(still['wind']['crosswind_mps'], blowing['wind']['crosswind_mps'])

    def test_both_tubes_report_their_own_circle_with_wind_on(self):
        nato = self.solve({'speed_mps': 6, 'from_degrees': 245}, tube='m252')
        soviet = self.solve({'speed_mps': 6, 'from_degrees': 245}, tube='2b14')
        self.assertEqual(nato['mils'], 6400)
        self.assertEqual(soviet['mils'], 6000)
        self.assertNotEqual(nato['azimuth_mils'], soviet['azimuth_mils'])

    def test_every_round_answers_with_wind_asked_for(self):
        for tube_key in ('m252', '2b14'):
            for shell in ('he', 'smoke', 'illum'):
                answer = self.solve({'speed_mps': 6, 'from_degrees': 245},
                                    tube=tube_key, shell=shell, target=(6600, 6400))
                self.assertIn('wind', answer, f'{tube_key}:{shell}')
                self.assertIn('has_data', answer['wind'])

    def test_out_of_range_still_reads_as_out_of_range(self):
        answer = self.solve({'speed_mps': 6, 'from_degrees': 245}, tube='2b14',
                            shell='smoke', target=(6000, 8500))
        self.assertFalse(answer['valid'])
        self.assertEqual(answer['reason'], 'out_of_range')


if __name__ == '__main__':
    unittest.main()
