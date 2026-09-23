"""The mortar map's web front end: calibration, the API, and its links.

The API is tested against the engine itself rather than against numbers typed
in here, so a table change can never leave the page and the Discord command
disagreeing.

The Everon map layer - its size, its coordinate_transform and the earth
correction - is GeNeFRAG's, from ArmaReforger/maps_core/all_arma_maps.json
(MIT, (c) 2025 Gerhard Froehlich). The town coordinates used to check it come
from EnfusionMapMaker's everon-locations.js (APL-SA).
"""
import json
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import AioHTTPTestCase

from bot.mortar.calibration import (Axis, Calibration, NotCalibrated, Picture, Tiles,
                                    calibration, image_path, read, reason, tile_path)
from bot.mortar.solution import bearing, profile, solution
from bot.web.server import MortarWeb, calculate, catalogue, loadout, point
from bot.web.sessions import Sessions

# GeNeFRAG's Everon entry, copied as it stands in all_arma_maps.json.
EVERON = {'name': 'Everon', 'namespace': 'everon', 'size': [12800, 12800], 'max_zoom': 7,
          'coordinate_transform': {'lng': {'cof': 50.0, 'offset': 0.0},
                                   'lat': {'cof': -50.0, 'offset': -256.0}},
          'earth_correction': True, 'digits': 3}

MAP = Calibration(name='Everon', width=12800, height=12800, max_zoom=7,
                  lng=Axis(50.0, 0.0), lat=Axis(-50.0, -256.0), earth_correction=True,
                  tiles=Tiles(directory='/nowhere'))

# Named places from EnfusionMapMaker's everon-locations.js, as world X/Z.
TOWNS = {'Saint Phillipe': (4500.872, 10776.053),
         'Montignac': (4775.641, 7086.945),
         'Entre-Deux': (5760.571, 7061.821),
         'Meaux': (4517.52, 9467.668),
         'Saint Pierre': (9689.432, 1558.166)}


def written(entry):
    path = Path(tempfile.mkdtemp()) / 'map.json'
    path.write_text(json.dumps(entry))
    return str(path)


def with_tiles(extra=None):
    """A config pointing at a pyramid that exists, with one tile in it."""
    root = Path(tempfile.mkdtemp())
    tile = root / 'everon_sat' / '7' / '64'
    tile.mkdir(parents=True)
    (tile / '64.webp').write_bytes(b'RIFF....WEBP')
    entry = {**EVERON, 'tiles': {'directory': str(root / 'everon_sat'),
                                 'pattern': '{z}/{x}/{y}.webp', 'tileSize': 256}}
    entry.update(extra or {})
    path = root / 'map.json'
    path.write_text(json.dumps(entry))
    return str(path), str(root), entry


class TransformTests(unittest.TestCase):
    """GeNeFRAG's coordinate_transform, both ways."""

    def test_the_centre_of_the_map_is_the_centre_of_the_island(self):
        # The earth correction is zero in the middle, so this is the one point
        # that pins the transform with nothing else in the way.
        self.assertEqual(MAP.world(128, 128), (6400.0, 6400.0))
        self.assertEqual(MAP.leaflet(6400, 6400), (128.0, 128.0))

    def test_the_imagery_stops_where_the_correction_pulls_it(self):
        # 100 m comes off the span, 50 m at each end.
        self.assertEqual(MAP.world(256, 0), (50.0, 50.0))
        self.assertEqual(MAP.world(0, 256), (12750.0, 12750.0))
        self.assertEqual(MAP.corners, ((50.0, 50.0), (12750.0, 12750.0)))

    def test_north_is_up_and_east_is_right(self):
        south, north = MAP.leaflet(6400, 1000), MAP.leaflet(6400, 12000)
        west, east = MAP.leaflet(1000, 6400), MAP.leaflet(12000, 6400)
        self.assertLess(north[0], south[0])   # lat grows downwards on this map
        self.assertLess(west[1], east[1])

    def test_known_towns_survive_the_round_trip(self):
        for name, (east, north) in TOWNS.items():
            lat, lng = MAP.leaflet(east, north)
            back = MAP.world(lat, lng)
            self.assertAlmostEqual(back[0], east, places=9, msg=name)
            self.assertAlmostEqual(back[1], north, places=9, msg=name)

    def test_a_kilometre_on_the_map_is_a_kilometre_to_the_engine(self):
        # Two points exactly 1000 m apart must reach the engine as 1000 m.
        for gun, target in [((6000, 6000), (7000, 6000)),
                            ((6000, 6000), (6000, 7000)),
                            ((6000, 6000), (6600, 6800))]:
            for weapon in ('m252:he', '2b14:he'):
                _, distance = bearing(gun, target, profile(weapon).mils)
                self.assertAlmostEqual(distance, 1000, places=6)
            answer = calculate({'tube': 'm252', 'round': 'he',
                                'gun': {'east': gun[0], 'north': gun[1]},
                                'target': {'east': target[0], 'north': target[1]}}, MAP)
            self.assertEqual(answer['range_m'], 1000)

    def test_a_kilometre_of_map_is_a_kilometre_of_world(self):
        # The same distance measured through the map rather than around it.
        a, b = MAP.leaflet(6000, 6000), MAP.leaflet(7000, 6000)
        first, second = MAP.world(*a), MAP.world(*b)
        self.assertAlmostEqual(second[0] - first[0], 1000, places=6)

    def test_a_world_point_falls_in_the_tile_that_holds_it(self):
        # 128 tiles across at the deepest zoom; the centre sits on the seam.
        self.assertEqual(MAP.tile(6400, 6400), (64, 64))
        self.assertEqual(MAP.tile(60, 60), (0, 127))
        self.assertEqual(MAP.tile(12740, 12740), (127, 0))
        # One zoom out, a tile covers twice the ground.
        self.assertEqual(MAP.tile(6400, 6400, zoom=6), (32, 32))
        self.assertEqual(MAP.tile(6400, 6400, zoom=0), (0, 0))

    def test_a_map_without_the_correction_spans_the_whole_island(self):
        plain = Calibration(name='Plain', width=12800, height=12800, max_zoom=7,
                            lng=Axis(50.0, 0.0), lat=Axis(-50.0, -256.0),
                            tiles=Tiles(directory='/nowhere'))
        self.assertEqual(plain.world(128, 128), (6400.0, 6400.0))
        self.assertEqual(plain.corners, ((0.0, 0.0), (12800.0, 12800.0)))

    def test_points_off_the_imagery_are_not_on_the_map(self):
        self.assertTrue(MAP.inside(6400, 6400))
        self.assertTrue(MAP.inside(50, 50))
        self.assertFalse(MAP.inside(49, 6400))
        self.assertFalse(MAP.inside(6400, 12751))
        self.assertFalse(MAP.inside(-100, 0))

    def test_a_town_reads_as_its_grid(self):
        self.assertEqual(MAP.grid(*TOWNS['Saint Phillipe']), '045 107')
        self.assertEqual(MAP.grid(*TOWNS['Saint Pierre']), '096 015')


class CalibrationTests(unittest.TestCase):
    def test_genefrags_entry_loads_as_it_stands(self):
        path, root, entry = with_tiles()
        mapped = read(entry)
        self.assertEqual(mapped.size, (12800.0, 12800.0))
        self.assertEqual(mapped.max_zoom, 7)
        self.assertEqual((mapped.lng.cof, mapped.lng.offset), (50.0, 0.0))
        self.assertEqual((mapped.lat.cof, mapped.lat.offset), (-50.0, -256.0))
        self.assertTrue(mapped.earth_correction)
        self.assertEqual(mapped.world(128, 128), (6400.0, 6400.0))
        self.assertIsNotNone(tile_path(mapped, 7, 64, 64))

    def test_tiles_that_have_not_been_generated_are_not_pretended_into_existence(self):
        entry = {**EVERON, 'tiles': {'directory': 'not/generated/yet'}}
        with self.assertRaises(NotCalibrated) as caught:
            read(entry)
        self.assertIn('generate_tiles.py', str(caught.exception))

    def test_a_config_that_cannot_be_trusted_is_refused(self):
        for broken, complaint in [
                ({}, 'size'),
                ({**EVERON, 'size': [0, 0]}, 'greater than zero'),
                ({**EVERON, 'size': 'big'}, 'size'),
                ({k: v for k, v in EVERON.items() if k != 'coordinate_transform'},
                 'coordinate_transform'),
                ({**EVERON, 'coordinate_transform': {'lng': {'cof': 50}}}, '"lat"'),
                ({**EVERON, 'coordinate_transform': {'lng': {'cof': 0}, 'lat': {'cof': -50}}},
                 'cannot be zero'),
                (EVERON, 'no imagery'),
                ({**EVERON, 'image': {}}, 'needs a "path"'),
                ({**EVERON, 'image': {'path': 'nope.png'}}, 'No map image')]:
            with self.assertRaises(NotCalibrated) as caught:
                read(broken)
            self.assertIn(complaint, str(caught.exception))

    def test_a_tile_pattern_must_name_all_three_indices(self):
        path, root, entry = with_tiles()
        entry['tiles']['pattern'] = '{z}/{x}.webp'
        with self.assertRaises(NotCalibrated) as caught:
            read(entry)
        self.assertIn('{z}, {x} and {y}', str(caught.exception))

    def test_an_image_works_in_place_of_tiles(self):
        mapped = read({**EVERON, 'image': {'path': 'assets/mortar/tables.json'}})
        self.assertIsNone(mapped.tiles)
        self.assertIsNotNone(image_path(mapped))
        self.assertEqual(mapped.world(128, 128), (6400.0, 6400.0))

    def test_the_shipped_config_is_genefrags_everon_awaiting_tiles(self):
        shipped = json.loads(Path('assets/mortar/map.json').read_text())
        self.assertEqual(shipped['size'], [12800, 12800])
        self.assertEqual(shipped['max_zoom'], 7)
        self.assertEqual(shipped['coordinate_transform'],
                         EVERON['coordinate_transform'])
        self.assertTrue(shipped['earth_correction'])
        self.assertIn('GeNeFRAG', shipped['_source'])
        # No tiles generated yet, so the page stays off rather than drawing nothing.
        self.assertIsNone(calibration())
        self.assertIn('generate_tiles.py', reason())

    def test_an_uncalibrated_map_turns_the_page_off_rather_than_guessing(self):
        self.assertIsNone(calibration(written({'name': 'Nothing yet'})))
        self.assertIsNone(calibration('/nonexistent/map.json'))
        self.assertIn('No map config', reason('/nonexistent/map.json'))

    def test_a_tile_request_cannot_walk_out_of_its_directory(self):
        path, root, entry = with_tiles()
        mapped = read(entry)
        for z, x, y in [('../../etc', 64, 64), (7, '../..', 64), (7, 64, '../../../etc/passwd'),
                        ('nope', 1, 1), (-1, 64, 64), (99, 64, 64), (7, -1, 64)]:
            self.assertIsNone(tile_path(mapped, z, x, y))
        # A tile that simply is not there is a miss, not an error.
        self.assertIsNone(tile_path(mapped, 7, 63, 64))

    def test_a_tile_pyramid_may_sit_outside_the_project(self):
        path, root, entry = with_tiles()
        mapped = read(entry)
        self.assertTrue(Path(mapped.tiles.directory).is_absolute())
        self.assertIsNone(image_path(Calibration(
            name='x', width=1, height=1, max_zoom=1, lng=Axis(1, 0), lat=Axis(1, 0),
            picture=Picture('/nowhere.png'))))


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
        config, _, _ = with_tiles()
        self.assertIsNone(MortarWeb(base_url='', config=config).link(7))
        uncalibrated = MortarWeb(base_url='https://map.test', config='/nonexistent/map.json')
        self.assertIsNone(uncalibrated.link(7))
        ready = MortarWeb(base_url='https://map.test/', config=config)
        self.assertTrue(ready.link(7).startswith('https://map.test/mortar/'))


class EngineAgreementTests(unittest.TestCase):
    """Whatever the page shows has to be what the engine said."""

    def setUp(self):
        self.mapped = MAP

    def solve(self, tube='m252', shell='he', gun=(6000, 6000), target=(6800, 6600), **extra):
        body = {'tube': tube, 'round': shell,
                'gun': {'east': gun[0], 'north': gun[1]},
                'target': {'east': target[0], 'north': target[1]}}
        body.update(extra)
        return calculate(body, self.mapped)

    def test_the_api_answers_with_the_engines_own_figures(self):
        answer = self.solve()
        weapon = profile('m252:he')
        gun, target = (6000, 6000), (6800, 6600)
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
        far = ((6000, 6000), (8000, 6000))   # 2000 m apart
        smoke = self.solve('2b14', 'smoke', *far)
        self.assertFalse(smoke['valid'])
        self.assertEqual(smoke['reason'], 'out_of_range')
        self.assertEqual((smoke['min_range_m'], smoke['max_range_m']), (50, 1600))
        self.assertNotIn('elevation_mils', smoke)
        # The same shot on HE is inside that round's reach.
        self.assertTrue(self.solve('2b14', 'he', *far)['valid'])

    def test_moving_a_marker_gives_a_different_solution(self):
        near = self.solve(target=(6500, 6000))
        far = self.solve(target=(8000, 6000))
        self.assertGreater(far['range_m'], near['range_m'])
        self.assertNotEqual(far['elevation_mils'], near['elevation_mils'])

    def test_height_is_carried_through_to_the_engine(self):
        flat, uphill = self.solve(), self.solve(climb=150)
        self.assertLess(uphill['elevation_mils'], flat['elevation_mils'])
        self.assertIn('height_correction_mils', uphill)

    def test_grids_are_reported_for_both_ends(self):
        answer = self.solve()
        self.assertEqual(answer['mortar_grid'], '060 060')
        self.assertEqual(answer['target_grid'], '068 066')

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
        for gun in ({}, {'east': 'over there', 'north': 10}, {'east': float('nan'), 'north': 1},
                    {'east': True, 'north': 2}, {'east': -500, 'north': 10},
                    {'east': 99999, 'north': 10}, {'x': 10, 'y': 10}, None):
            answer = calculate({'tube': 'm252', 'round': 'he', 'gun': gun,
                                'target': {'east': 6000, 'north': 6000}}, self.mapped)
            self.assertFalse(answer['valid'])
            self.assertEqual(answer['reason'], 'bad_request')

    def test_a_point_off_the_island_is_refused(self):
        self.assertIsNone(point({'east': -500, 'north': 6400}, self.mapped))
        self.assertIsNone(point({'east': 6400, 'north': 99999}, self.mapped))
        # The earth correction pulls the imagery in, so the last 50 m is off it.
        self.assertIsNone(point({'east': 0, 'north': 0}, self.mapped))
        self.assertEqual(point({'east': 6400, 'north': 6400}, self.mapped), (6400, 6400))
        # With no map loaded there is nothing to bound it against.
        self.assertEqual(point({'east': 1, 'north': 2}, None), (1, 2))


class ApiTests(AioHTTPTestCase):
    async def get_application(self):
        config, self.root, _ = with_tiles()
        self.service = MortarWeb(base_url='https://map.test', config=config)
        self.token = self.service.sessions.open(holder=11)
        return self.service.app()

    def body(self, token=None, **extra):
        body = {'token': token if token is not None else self.token,
                'tube': 'm252', 'round': 'he',
                'gun': {'east': 6000, 'north': 6000},
                'target': {'east': 6800, 'north': 6600}}
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
                                       json=self.body(gun={'east': 'nope', 'north': 1}))
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
        self.assertEqual(data['map']['size'], [12800, 12800])
        self.assertEqual(data['map']['maxZoom'], 7)
        self.assertEqual(data['map']['metresPerUnit'], 50)
        self.assertEqual(data['map']['transform']['lat'], {'cof': -50.0, 'offset': -256.0})
        self.assertTrue(data['map']['earthCorrection'])
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
        for giveaway in ('dispersion:', 'elevation =', 'Math.atan2', '6400', '6000', '12.5',
                         'Math.cos', 'Math.sin', 'crosswind =', 'DRIFT', 'COEFFICIENT'):
            self.assertNotIn(giveaway, page)


if __name__ == '__main__':
    unittest.main()
