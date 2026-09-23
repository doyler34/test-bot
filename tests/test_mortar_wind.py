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
from bot.mortar.wind import BadWind, Wind, components, drift_mils, read
from bot.web.server import calculate

MAP = Calibration(name='Everon', width=12800, height=12800, max_zoom=6,
                  lng=Axis(50.0, 0.0), lat=Axis(-50.0, -256.0), earth_correction=True,
                  tiles=Tiles(directory='/nowhere'))

# A stand-in wind table: 1 m of drift per m/s of crosswind, and 2 m of range
# per m/s of tailwind, flat across the band. Round numbers so the sums can be
# checked by eye. These are NOT Reforger figures.
ROWS = [[100, 1500, 10.0, 20], [2000, 500, 30.0, 40]]
GUSTS = [[100, 1.0, 2.0], [2000, 1.0, 2.0]]


def tube(mils=6400, gusts=GUSTS, rings=('1',)):
    entry = {'name': 'Test tube', 'faction': 'US', 'mils': mils, 'shells': {'he': {
        'name': 'HE TEST', 'rings': {r: {'dispersion': 10, 'rows': ROWS,
                                         'wind': {'rows': gusts}} for r in rings}}}}
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
    def test_the_same_drift_is_a_different_angle_on_each_sight(self):
        # 10m of drift at 1000m: 10.19 mils on a 6400 sight, 9.55 on a 6000.
        self.assertAlmostEqual(drift_mils(10, 1000, 6400), 10.1859, places=3)
        self.assertAlmostEqual(drift_mils(10, 1000, 6000), 9.5493, places=3)
        self.assertGreater(drift_mils(10, 1000, 6400), drift_mils(10, 1000, 6000))

    def test_each_tube_corrects_on_its_own_circle(self):
        nato, soviet = tube(mils=6400), tube(mils=6000)
        wind = Wind(10, 270)                       # straight across, to the right
        one = apply_wind(nato, 1000, 0, wind)
        two = apply_wind(soviet, 1000, 0, wind)
        self.assertLess(one.azimuth_mils, 0)       # right drift, traverse left
        self.assertLess(two.azimuth_mils, 0)
        # Same physical drift, different sight: the 6400 number must be larger.
        self.assertAlmostEqual(one.azimuth_mils / two.azimuth_mils, 6400 / 6000, places=6)

    def test_the_shipped_tubes_keep_their_circles(self):
        self.assertEqual(profile('m252:he').mils, 6400)
        self.assertEqual(profile('2b14:he').mils, 6000)


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.tube = tube()

    def test_calm_leaves_the_solution_exactly_as_it_was(self):
        plain, _ = solution(self.tube, 1000)
        for wind in (None, Wind(), Wind(0, 245)):
            shot = apply_wind(self.tube, 1000, 45, wind)
            self.assertEqual(shot.final, plain)
            self.assertEqual(shot.azimuth_mils, 0)
            self.assertEqual(shot.range_m, 0)
            self.assertEqual(shot.effective_range, 1000)

    def test_a_crosswind_from_the_left_traverses_right(self):
        # Wind FROM the east, firing north: air moves west, to the shooter's
        # left, so the round lands left and the gun goes right.
        shot = apply_wind(self.tube, 1000, 0, Wind(10, 90))
        self.assertAlmostEqual(shot.crosswind, -10, places=9)
        self.assertGreater(shot.azimuth_mils, 0)

    def test_a_crosswind_from_the_right_traverses_left(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(10, 270))
        self.assertAlmostEqual(shot.crosswind, 10, places=9)
        self.assertLess(shot.azimuth_mils, 0)

    def test_left_and_right_corrections_match_in_size(self):
        left = apply_wind(self.tube, 1000, 0, Wind(10, 90))
        right = apply_wind(self.tube, 1000, 0, Wind(10, 270))
        self.assertAlmostEqual(left.azimuth_mils, -right.azimuth_mils, places=9)

    def test_a_tailwind_makes_the_gun_shoot_shorter(self):
        # 2m of carry per m/s: 10m/s of tail carries it 20m long, so the table
        # is read 20m short of the target.
        shot = apply_wind(self.tube, 1000, 0, Wind(10, 180))
        self.assertAlmostEqual(shot.parallel, 10, places=9)
        self.assertAlmostEqual(shot.range_m, 20, places=9)
        self.assertAlmostEqual(shot.effective_range, 980, places=9)

    def test_a_headwind_makes_the_gun_shoot_longer(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(10, 0))
        self.assertAlmostEqual(shot.parallel, -10, places=9)
        self.assertAlmostEqual(shot.range_m, -20, places=9)
        self.assertAlmostEqual(shot.effective_range, 1020, places=9)

    def test_head_and_tail_move_the_elevation_opposite_ways(self):
        head = apply_wind(self.tube, 1000, 0, Wind(10, 0))
        tail = apply_wind(self.tube, 1000, 0, Wind(10, 180))
        plain, _ = solution(self.tube, 1000)
        # A tailwind carries the round long, so the gun is laid for a shorter
        # range - and on these tables a shorter range is a STEEPER tube.
        self.assertGreater(tail.final.elevation, plain.elevation)
        self.assertLess(head.final.elevation, plain.elevation)
        # Whichever way, the two move opposite ways about the still-air figure.
        self.assertLess(head.final.elevation, tail.final.elevation)

    def test_the_elevation_comes_from_the_existing_table(self):
        # Whatever the wind does, the number fired is a table lookup at the
        # corrected range - not a formula of ours.
        shot = apply_wind(self.tube, 1000, 0, Wind(10, 180))
        straight, _ = solution(self.tube, shot.effective_range)
        self.assertEqual(shot.final.elevation, straight.elevation)
        self.assertEqual(shot.final.flight, straight.flight)

    def test_a_quartering_wind_moves_both(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(10, 225))
        self.assertGreater(shot.parallel, 0)
        self.assertGreater(shot.crosswind, 0)
        self.assertNotEqual(shot.range_m, 0)
        self.assertNotEqual(shot.azimuth_mils, 0)

    def test_the_base_solution_is_kept_beside_the_corrected_one(self):
        shot = apply_wind(self.tube, 1000, 0, Wind(10, 180))
        plain, _ = solution(self.tube, 1000)
        self.assertEqual(shot.base, plain)
        self.assertNotEqual(shot.final, shot.base)

    def test_wind_can_carry_a_shot_out_of_range(self):
        # 1990m is inside the band; a headwind asks the gun for 2030m, and the
        # table stops at 2000m.
        self.assertIsNotNone(solution(self.tube, 1990)[0])
        shot = apply_wind(self.tube, 1990, 0, Wind(20, 0))
        self.assertGreater(shot.effective_range, 2000)
        self.assertIsNone(shot.final)
        self.assertIsNotNone(shot.base)

    def test_every_ring_with_data_is_corrected(self):
        several = tube(rings=('0', '1', '2'))
        for ring in ('0', '1', '2'):
            self.assertEqual(len(several.wind_rows(ring)), 2, ring)
        shot = apply_wind(several, 1000, 0, Wind(10, 180))
        self.assertTrue(shot.has_data)
        self.assertAlmostEqual(shot.range_m, 20, places=9)

    def test_a_ring_without_data_is_not_guessed_at(self):
        half = tube(gusts=[])
        shot = apply_wind(half, 1000, 0, Wind(10, 180))
        self.assertFalse(shot.has_data)
        self.assertEqual(shot.range_m, 0)
        self.assertEqual(shot.azimuth_mils, 0)
        self.assertEqual(shot.final, shot.base)
        # The split is still reported, so the gunner can see the wind.
        self.assertAlmostEqual(shot.parallel, 10, places=9)


class ShippedDataTests(unittest.TestCase):
    """Every real round is wired for wind and has no figures yet."""

    def test_no_shipped_round_claims_wind_data_it_does_not_have(self):
        for key, weapon in profiles().items():
            self.assertFalse(weapon.windy, f'{key} claims wind data - where did it come from?')

    def test_every_shipped_round_is_wired_for_it(self):
        blob = json.loads(Path('assets/mortar/tables.json').read_text())
        for tube_key, entry in blob.items():
            for round_key, shell in entry['shells'].items():
                for ring, block in shell['rings'].items():
                    self.assertIn('wind', block, f'{tube_key}:{round_key} ring {ring}')
                    self.assertEqual(block['wind']['rows'], [])

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
        for key in ('crosswind_mps', 'parallel_mps', 'azimuth_correction_mils',
                    'range_correction_m', 'has_data', 'applied'):
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
