"""The mortar map's web front end: calibration, the API, and its links.

The API is tested against the engine itself rather than against numbers typed
in here, so a table change can never leave the page and the Discord command
disagreeing.
"""
import json
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import AioHTTPTestCase

from bot.mortar.calibration import Calibration, NotCalibrated, calibration, image_path, read, reason
from bot.mortar.solution import bearing, profile, solution
from bot.web.server import MortarWeb, calculate, catalogue, loadout, point
from bot.web.sessions import Sessions

# A square map with two reference points at opposite corners: 4096 px covering
# 11 km, north up. Made up on purpose - the maths is what is being checked.
MAP = {'name': 'Test Island', 'image': 'assets/mortar/tables.json', 'width': 4096,
       'height': 4096, 'digits': 3,
       'reference': [{'image': [100, 3900], 'world': [1000, 1000]},
                     {'image': [3900, 100], 'world': [12000, 12000]}]}


def written(entry):
    path = Path(tempfile.mkdtemp()) / 'map.json'
    path.write_text(json.dumps(entry))
    return str(path)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.mapped = read(MAP)

    def test_the_reference_points_come_back_exactly(self):
        # Whatever else is true, the two points it was built from must land.
        self.assertEqual(self.mapped.world(100, 3900), (1000, 1000))
        self.assertEqual(self.mapped.world(3900, 100), (12000, 12000))
        self.assertEqual(self.mapped.pixel(1000, 1000), (100, 3900))
        self.assertEqual(self.mapped.pixel(12000, 12000), (3900, 100))

    def test_the_scale_is_read_off_the_reference_points(self):
        # 11000 m across 3800 px, and north runs up the image, so the north
        # axis comes out negative without anyone saying so.
        self.assertAlmostEqual(self.mapped.east_per_pixel, 11000 / 3800)
        self.assertAlmostEqual(self.mapped.north_per_pixel, -11000 / 3800)
        self.assertAlmostEqual(self.mapped.metres_per_pixel, 11000 / 3800)

    def test_a_pixel_converts_and_converts_back(self):
        for x, y in [(0, 0), (2048, 2048), (4096, 4096), (37, 1234)]:
            east, north = self.mapped.world(x, y)
            back_x, back_y = self.mapped.pixel(east, north)
            self.assertAlmostEqual(back_x, x, places=6)
            self.assertAlmostEqual(back_y, y, places=6)

    def test_a_known_point_reads_as_the_grid_on_the_map(self):
        # Halfway between the two references is 6500, 6500 - grid "065 065".
        middle = self.mapped.world(2000, 2000)
        self.assertEqual([round(v) for v in middle], [6500, 6500])
        self.assertEqual(self.mapped.grid(*middle), '065 065')
        self.assertEqual(self.mapped.grid(4700, 6300), '047 063')

    def test_the_corners_give_the_world_bounds(self):
        (west, south), (east, north) = self.mapped.bounds
        self.assertAlmostEqual(west, self.mapped.world(0, 0)[0])
        self.assertAlmostEqual(north, self.mapped.world(0, 0)[1])
        self.assertLess(west, east)
        self.assertLess(south, north)

    def test_a_config_that_cannot_be_trusted_is_refused(self):
        for broken, complaint in [
                ({}, 'needs'),
                ({**MAP, 'image': ''}, 'image'),
                ({**MAP, 'width': 0}, 'width'),
                ({**MAP, 'reference': [MAP['reference'][0]]}, 'two reference points'),
                # Two points on the same row fix no vertical scale at all.
                ({**MAP, 'reference': [{'image': [100, 100], 'world': [1000, 1000]},
                                       {'image': [3900, 100], 'world': [12000, 12000]}]},
                 'differ in both'),
                ({**MAP, 'reference': [{'image': [100, 3900], 'world': [1000, 1000]},
                                       {'image': [3900, 100], 'world': [1000, 12000]}]},
                 'different eastings')]:
            with self.assertRaises(NotCalibrated) as caught:
                read(broken)
            self.assertIn(complaint, str(caught.exception))

    def test_an_uncalibrated_map_turns_the_page_off_rather_than_guessing(self):
        self.assertIsNone(calibration(written({'name': 'Nothing yet'})))
        self.assertIn('needs', reason(written({'name': 'Nothing yet'})))
        self.assertIsNone(calibration('/nonexistent/map.json'))
        self.assertIn('No map config', reason('/nonexistent/map.json'))

    def test_the_shipped_config_is_not_pretending_to_be_calibrated(self):
        # Until a real Everon image and its reference points are put in, the
        # map must stay off instead of drawing on invented bounds.
        self.assertIsNone(calibration())

    def test_the_image_must_live_inside_the_project(self):
        self.assertIsNotNone(image_path(self.mapped))
        outside = Calibration(name='x', image='../../../etc/passwd', width=10, height=10,
                              east_per_pixel=1, north_per_pixel=-1, origin=(0, 0),
                              origin_world=(0, 0))
        self.assertIsNone(image_path(outside))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.sessions = Sessions(ttl=1800, clock=lambda: self.now[0])

    def test_a_link_works_until_it_runs_out(self):
        token = self.sessions.open(holder=42)
        self.assertTrue(self.sessions.valid(token))
        self.now[0] += 1799
        self.assertTrue(self.sessions.valid(token))
        self.now[0] += 2
        self.assertFalse(self.sessions.valid(token))

    def test_an_expired_link_is_forgotten_not_just_refused(self):
        token = self.sessions.open(holder=42)
        self.now[0] += 3600
        self.sessions.valid(token)
        self.assertEqual(len(self.sessions), 0)
        self.assertIsNone(self.sessions.holder(token))

    def test_rubbish_is_never_a_link(self):
        for junk in ('', None, 0, 'not-a-token', ['x'], 'a' * 43):
            self.assertFalse(self.sessions.valid(junk))

    def test_two_links_are_never_the_same(self):
        tokens = {self.sessions.open() for _ in range(50)}
        self.assertEqual(len(tokens), 50)
        self.assertTrue(all(len(t) > 30 for t in tokens))

    def test_a_link_is_not_issued_before_the_map_is_ready(self):
        off = MortarWeb(base_url='', config=written(MAP))
        self.assertIsNone(off.link(7))
        uncalibrated = MortarWeb(base_url='https://map.test', config='/nonexistent/map.json')
        self.assertIsNone(uncalibrated.link(7))
        ready = MortarWeb(base_url='https://map.test/', config=written(MAP))
        self.assertTrue(ready.link(7).startswith('https://map.test/mortar/'))


class EngineAgreementTests(unittest.TestCase):
    """Whatever the page shows has to be what the engine said."""

    def setUp(self):
        self.mapped = read(MAP)

    def solve(self, tube='m252', shell='he', gun=(500, 3000), target=(800, 2700), **extra):
        body = {'tube': tube, 'round': shell,
                'gun': {'x': gun[0], 'y': gun[1]}, 'target': {'x': target[0], 'y': target[1]}}
        body.update(extra)
        return calculate(body, self.mapped)

    def test_the_api_answers_with_the_engines_own_figures(self):
        answer = self.solve()
        weapon = profile('m252:he')
        gun = self.mapped.world(500, 3000)
        target = self.mapped.world(800, 2700)
        mils, distance = bearing(gun, target, weapon.mils)
        best, _ = solution(weapon, distance)
        self.assertTrue(answer['valid'])
        self.assertEqual(answer['range_m'], round(distance))
        self.assertEqual(answer['azimuth_mils'], round(mils))
        self.assertEqual(answer['ring'], best.ring)
        self.assertEqual(answer['elevation_mils'], round(best.elevation))
        self.assertEqual(answer['tof_seconds'], round(best.flight))

    def test_the_two_tubes_keep_their_own_mil_systems(self):
        us, ru = self.solve('m252'), self.solve('2b14')
        self.assertEqual((us['mils'], ru['mils']), (6400, 6000))
        self.assertNotEqual(us['azimuth_mils'], ru['azimuth_mils'])
        self.assertEqual(us['range_m'], ru['range_m'])   # same ground, same distance
        self.assertNotEqual(us['elevation_mils'], ru['elevation_mils'])

    def test_each_round_answers_off_its_own_table(self):
        he, smoke, illum = (self.solve('m252', r) for r in ('he', 'smoke', 'illum'))
        self.assertEqual(he['roundName'], 'HE M821')
        self.assertEqual(smoke['roundName'], 'Smoke M819')
        self.assertEqual(illum['roundName'], 'Illumination M853A1')
        self.assertNotEqual(he['elevation_mils'], smoke['elevation_mils'])

    def test_out_of_range_reports_that_rounds_own_limits(self):
        # 2B14 smoke stops at 1600m where its HE carries on to 2300m.
        far = ((300, 3000), (788, 2512))   # about 2000 m apart
        smoke = self.solve('2b14', 'smoke', *far)
        self.assertFalse(smoke['valid'])
        self.assertEqual(smoke['reason'], 'out_of_range')
        self.assertEqual((smoke['min_range_m'], smoke['max_range_m']), (50, 1600))
        self.assertNotIn('elevation_mils', smoke)
        # The same shot on HE is inside that round's reach.
        self.assertTrue(self.solve('2b14', 'he', *far)['valid'])

    def test_moving_a_marker_gives_a_different_solution(self):
        near = self.solve(target=(700, 2800))
        far = self.solve(target=(1000, 2500))
        self.assertGreater(far['range_m'], near['range_m'])
        self.assertNotEqual(far['elevation_mils'], near['elevation_mils'])

    def test_height_is_carried_through_to_the_engine(self):
        flat, uphill = self.solve(), self.solve(climb=150)
        self.assertLess(uphill['elevation_mils'], flat['elevation_mils'])
        self.assertIn('height_correction_mils', uphill)

    def test_grids_are_reported_for_both_ends(self):
        answer = self.solve()
        self.assertEqual(answer['mortar_grid'], self.mapped.grid(*self.mapped.world(500, 3000)))
        self.assertEqual(answer['target_grid'], self.mapped.grid(*self.mapped.world(800, 2700)))

    def test_switching_tube_keeps_the_round_in_hand(self):
        self.assertEqual(loadout({'tube': '2b14', 'round': 'smoke'}).key, '2b14:smoke')
        self.assertEqual(loadout({'tube': 'm252', 'round': 'illum'}).key, 'm252:illum')
        # An unknown round falls back within the tube asked for, never to another tube.
        self.assertEqual(loadout({'tube': '2b14', 'round': 'nope'}).weapon, '2b14')

    def test_the_catalogue_carries_each_rounds_own_reach(self):
        listing = {tube['key']: tube for tube in catalogue()}
        self.assertEqual(listing['m252']['mils'], 6400)
        self.assertEqual(listing['2b14']['mils'], 6000)
        rounds = {r['key']: r for r in listing['2b14']['rounds']}
        self.assertEqual((rounds['smoke']['minRange'], rounds['smoke']['maxRange']), (50, 1600))
        self.assertEqual((rounds['he']['minRange'], rounds['he']['maxRange']), (50, 2300))

    def test_rubbish_coordinates_are_refused_not_guessed(self):
        for gun in ({}, {'x': 'over there', 'y': 10}, {'x': float('nan'), 'y': 1},
                    {'x': True, 'y': 2}, {'x': -5, 'y': 10}, {'x': 99999, 'y': 10}, None):
            answer = calculate({'tube': 'm252', 'round': 'he', 'gun': gun,
                                'target': {'x': 10, 'y': 10}}, self.mapped)
            self.assertFalse(answer['valid'])
            self.assertEqual(answer['reason'], 'bad_request')

    def test_world_coordinates_are_taken_as_they_come(self):
        direct = calculate({'tube': 'm252', 'round': 'he',
                            'gun': {'east': 5000, 'north': 5000},
                            'target': {'east': 5800, 'north': 5600}}, self.mapped)
        self.assertTrue(direct['valid'])
        self.assertEqual(direct['range_m'], 1000)

    def test_a_point_needs_a_map_before_pixels_mean_anything(self):
        self.assertIsNone(point({'x': 10, 'y': 10}, None))
        self.assertEqual(point({'east': 1, 'north': 2}, None), (1, 2))


class ApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.service = MortarWeb(base_url='https://map.test', config=written(MAP))
        self.token = self.service.sessions.open(holder=11)
        return self.service.app()

    def body(self, token=None, **extra):
        body = {'token': token if token is not None else self.token,
                'tube': 'm252', 'round': 'he',
                'gun': {'x': 500, 'y': 3000}, 'target': {'x': 800, 'y': 2700}}
        body.update(extra)
        return body

    async def test_a_good_link_gets_a_solution(self):
        reply = await self.client.post('/api/mortar/calculate', json=self.body())
        self.assertEqual(reply.status, 200)
        answer = await reply.json()
        self.assertTrue(answer['valid'])
        self.assertEqual(answer['tubeName'], 'US M252 81mm')
        self.assertIn('elevation_mils', answer)

    async def test_an_unknown_link_gets_nothing(self):
        reply = await self.client.post('/api/mortar/calculate', json=self.body(token='made-up'))
        self.assertEqual(reply.status, 403)
        self.assertEqual((await reply.json())['reason'], 'expired')

    async def test_an_expired_link_gets_nothing(self):
        self.service.sessions.drop(self.token)
        reply = await self.client.post('/api/mortar/calculate', json=self.body())
        self.assertEqual(reply.status, 403)

    async def test_a_missing_link_gets_nothing(self):
        reply = await self.client.post('/api/mortar/calculate',
                                       json={'tube': 'm252', 'round': 'he'})
        self.assertEqual(reply.status, 403)

    async def test_malformed_requests_are_turned_away_cleanly(self):
        for body in ('not json at all', '[]', '{"token":'):
            reply = await self.client.post('/api/mortar/calculate', data=body,
                                           headers={'Content-Type': 'application/json'})
            self.assertIn(reply.status, (400, 403))
            self.assertFalse((await reply.json())['valid'])

    async def test_nothing_internal_leaks_to_the_browser(self):
        reply = await self.client.post('/api/mortar/calculate',
                                       json=self.body(gun={'x': 'nope', 'y': 1}))
        text = await reply.text()
        for leak in ('/home/', 'Traceback', 'assets/', '.py'):
            self.assertNotIn(leak, text)

    async def test_the_page_opens_for_a_good_link_only(self):
        good = await self.client.get(f'/mortar/{self.token}')
        self.assertEqual(good.status, 200)
        self.assertIn(self.token, await good.text())
        bad = await self.client.get('/mortar/nope')
        self.assertEqual(bad.status, 404)
        self.assertIn('expired', (await bad.text()).lower())

    async def test_the_loadouts_come_with_the_map_and_need_a_link(self):
        reply = await self.client.get(f'/mortar/{self.token}/loadouts')
        data = await reply.json()
        self.assertEqual(data['map']['width'], 4096)
        self.assertEqual({t['key'] for t in data['tubes']}, {'m252', '2b14'})
        self.assertEqual((await self.client.get('/mortar/nope/loadouts')).status, 404)

    async def test_the_page_scripts_are_served(self):
        for name in ('mortar.js', 'mortar.css'):
            reply = await self.client.get(f'/static/{name}')
            self.assertEqual(reply.status, 200)
        # Nothing outside the static folder, however the path is spelled.
        self.assertEqual((await self.client.get('/static/../server.py')).status, 404)

    async def test_the_javascript_does_no_ballistics_of_its_own(self):
        page = await (await self.client.get('/static/mortar.js')).text()
        for giveaway in ('dispersion:', 'elevation =', 'Math.atan2', '6400', '6000'):
            self.assertNotIn(giveaway, page)


if __name__ == '__main__':
    unittest.main()
