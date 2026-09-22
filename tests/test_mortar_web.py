"""The mortar map's web front end: calibration, the API, and its links.

The API is tested against the engine itself rather than against numbers typed
in here, so a table change can never leave the page and the Discord command
disagreeing.

The Everon figures - 12.8 km square, 100 m tiles centred on the camera, six
LODs - and the town coordinates used to check them come from EnfusionMapMaker
(https://github.com/nickludlam/EnfusionMapMaker, APL-SA), which is what
generates the tile sets this reads.
"""
import json
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import AioHTTPTestCase

from bot.mortar.calibration import (Calibration, NotCalibrated, Picture, Tiles, calibration,
                                    image_path, read, reason, tile_path)
from bot.mortar.solution import bearing, profile, solution
from bot.web.server import MortarWeb, calculate, catalogue, loadout, point
from bot.web.sessions import Sessions

# Everon as EnfusionMapMaker captures it: 12.8 km square, LOD0 tiles one 100 m
# screenshot each, drawn 256 px across, six zoom levels.
EVERON = Tiles(directory='assets/mortar/everon', pattern='{z}/{x}/{y}/tile.jpg',
               tile_size=256, max_zoom=5, metres_per_tile=100)
MAP = Calibration(name='Everon', size=12800, offset=50, digits=3, tiles=EVERON)

# Named places from EnfusionMapMaker's own everon-locations.js, as world X/Z.
TOWNS = {'Saint Phillipe': (4500.872, 10776.053),
         'Montignac': (4775.641, 7086.945),
         'Entre-Deux': (5760.571, 7061.821),
         'Meaux': (4517.52, 9467.668),
         'Saint Pierre': (9689.432, 1558.166)}


def written(entry, root=None):
    folder = Path(tempfile.mkdtemp())
    path = folder / 'map.json'
    path.write_text(json.dumps(entry))
    return str(path)


def with_tiles():
    """A config pointing at a tile directory that exists, with one tile in it."""
    root = Path(tempfile.mkdtemp())
    tile = root / 'tiles' / '5' / '45' / '108'
    tile.mkdir(parents=True)
    (tile / 'tile.jpg').write_bytes(b'\xff\xd8\xff')
    # Absolute, because a config's relative paths are read from the project
    # root and this pyramid is off in a temporary directory.
    entry = {'name': 'Everon', 'digits': 3, 'world': {'size': 12800, 'offset': 50},
             'tiles': {'directory': str(root / 'tiles'), 'pattern': '{z}/{x}/{y}/tile.jpg',
                       'tileSize': 256, 'maxZoom': 5, 'metresPerTile': 100}}
    (root / 'map.json').write_text(json.dumps(entry))
    return str(root / 'map.json'), str(root), entry


class ScaleTests(unittest.TestCase):
    """The scale is arithmetic off the tile geometry, not an eyeballed number."""

    def test_the_reforger_tile_set_scales_to_exactly_twelve_and_a_half(self):
        # One LOD0 tile is 100 m of world drawn 256 px across, and there are
        # five doublings above it: 100 * 32 / 256.
        self.assertEqual(EVERON.scale, 12.5)
        self.assertEqual(MAP.scale, 12.5)

    def test_the_scale_follows_the_tiles_rather_than_being_fixed(self):
        # Halve the tile size and the scale halves with it; nothing is baked in.
        self.assertEqual(EVERON.__class__(**{**EVERON.__dict__, 'tile_size': 128}).scale, 25.0)
        self.assertEqual(EVERON.__class__(**{**EVERON.__dict__, 'max_zoom': 4}).scale, 6.25)
        self.assertEqual(EVERON.__class__(**{**EVERON.__dict__, 'metres_per_tile': 50}).scale,
                         6.25)

    def test_an_untiled_map_is_drawn_a_metre_to_the_unit(self):
        plain = Calibration(name='Flat', size=1000, offset=0)
        self.assertEqual(plain.scale, 1.0)


class EveronCoordinateTests(unittest.TestCase):
    """World X/Z in, world X/Z out, through the map's own coordinate system."""

    def test_known_towns_survive_the_round_trip(self):
        for name, (east, north) in TOWNS.items():
            lat, lng = MAP.leaflet(east, north)
            back = MAP.world(lat, lng)
            self.assertAlmostEqual(back[0], east, places=9, msg=name)
            self.assertAlmostEqual(back[1], north, places=9, msg=name)

    def test_known_towns_survive_the_round_trip_through_pixels(self):
        # Every zoom level, because the projection is where a scale error hides.
        for name, (east, north) in TOWNS.items():
            for zoom in range(0, 6):
                x, y = MAP.projected(east, north, zoom)
                back = MAP.unprojected(x, y, zoom)
                self.assertAlmostEqual(back[0], east, places=6, msg=f'{name} @ z{zoom}')
                self.assertAlmostEqual(back[1], north, places=6, msg=f'{name} @ z{zoom}')

    def test_the_corners_of_the_island_land_where_they_should(self):
        # Half a tile is added because tiles are named for their centre camera.
        self.assertEqual(MAP.leaflet(0, 0), (50, 50))
        self.assertEqual(MAP.leaflet(12800, 12800), (12850, 12850))
        self.assertEqual(MAP.world(50, 50), (0, 0))
        self.assertEqual(MAP.bounds, ((0.0, 0.0), (12800.0, 12800.0)))

    def test_a_world_point_falls_in_the_tile_that_holds_it(self):
        # At the deepest zoom a tile is 100 m, centred on a multiple of 100, so
        # the index is the coordinate in hundreds.
        self.assertEqual(MAP.tile(0, 0), (0, 0))
        self.assertEqual(MAP.tile(100, 0), (1, 0))
        self.assertEqual(MAP.tile(0, 12800), (0, 128))
        self.assertEqual(MAP.tile(4500.872, 10776.053), (45, 108))
        # One zoom out, a tile covers twice the ground.
        self.assertEqual(MAP.tile(4500.872, 10776.053, zoom=4), (22, 54))

    def test_north_is_up_and_east_is_right(self):
        middle = MAP.projected(6400, 6400, 5)
        self.assertGreater(MAP.projected(7400, 6400, 5)[0], middle[0])   # east  -> right
        self.assertLess(MAP.projected(6400, 7400, 5)[1], middle[1])      # north -> up

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

    def test_a_town_reads_as_its_grid(self):
        self.assertEqual(MAP.grid(*TOWNS['Saint Phillipe']), '045 107')
        self.assertEqual(MAP.grid(*TOWNS['Saint Pierre']), '096 015')

    def test_points_off_the_island_are_not_on_the_map(self):
        self.assertTrue(MAP.inside(0, 0))
        self.assertTrue(MAP.inside(12800, 12800))
        self.assertTrue(MAP.inside(-50, -50))        # the outermost tile centres
        self.assertFalse(MAP.inside(-51, 0))
        self.assertFalse(MAP.inside(0, 12851))


class CalibrationTests(unittest.TestCase):
    def test_a_config_with_tiles_on_disk_loads(self):
        path, root, entry = with_tiles()
        mapped = read(entry, root)
        self.assertEqual(mapped.scale, 12.5)
        self.assertEqual(mapped.size, 12800)
        self.assertIsNotNone(tile_path(mapped, 5, 45, 108))

    def test_tiles_that_have_not_been_generated_are_not_pretended_into_existence(self):
        entry = {'world': {'size': 12800}, 'tiles': {'directory': 'not/generated/yet'}}
        with self.assertRaises(NotCalibrated) as caught:
            read(entry)
        self.assertIn('generate them first', str(caught.exception))

    def test_a_config_that_cannot_be_trusted_is_refused(self):
        for broken, complaint in [
                ({}, 'world'),
                ({'world': {}}, 'size'),
                ({'world': {'size': 0}}, 'greater than zero'),
                ({'world': {'size': 'big'}}, 'number'),
                ({'world': {'size': 12800}}, 'no imagery'),
                ({'world': {'size': 12800}, 'image': {}}, 'needs a "path"'),
                ({'world': {'size': 12800}, 'image': {'path': 'nope.png'}}, 'No map image')]:
            with self.assertRaises(NotCalibrated) as caught:
                read(broken)
            self.assertIn(complaint, str(caught.exception))

    def test_a_tile_pattern_must_name_all_three_indices(self):
        path, root, entry = with_tiles()
        entry['tiles']['pattern'] = '{z}/{x}/tile.jpg'
        with self.assertRaises(NotCalibrated) as caught:
            read(entry, root)
        self.assertIn('{z}, {x} and {y}', str(caught.exception))

    def test_an_image_map_needs_no_reference_points(self):
        # The world square is the calibration; an image is just stretched over it.
        entry = {'world': {'size': 12800}, 'image': {'path': 'assets/mortar/tables.json'}}
        mapped = read(entry)
        self.assertEqual(mapped.picture.south_west, (0.0, 0.0))
        self.assertEqual(mapped.picture.north_east, (12800.0, 12800.0))
        self.assertIsNotNone(image_path(mapped))

    def test_an_image_can_cover_part_of_the_world(self):
        entry = {'world': {'size': 12800},
                 'image': {'path': 'assets/mortar/tables.json',
                           'southWest': [2000, 3000], 'northEast': [6000, 7000]}}
        self.assertEqual(read(entry).picture.south_west, (2000.0, 3000.0))
        entry['image']['northEast'] = [1000, 7000]
        with self.assertRaises(NotCalibrated):
            read(entry)

    def test_the_shipped_config_carries_everon_but_no_imagery_yet(self):
        shipped = json.loads(Path('assets/mortar/map.json').read_text())
        self.assertEqual(shipped['world'], {'size': 12800, 'offset': 50})
        self.assertEqual(shipped['tiles']['metresPerTile'], 100)
        self.assertEqual(shipped['tiles']['maxZoom'], 5)
        # No tiles generated, so the page stays off rather than drawing nothing.
        self.assertIsNone(calibration())
        self.assertIn('generate them first', reason())

    def test_an_uncalibrated_map_turns_the_page_off_rather_than_guessing(self):
        self.assertIsNone(calibration(written({'name': 'Nothing yet'})))
        self.assertIsNone(calibration('/nonexistent/map.json'))
        self.assertIn('No map config', reason('/nonexistent/map.json'))

    def test_a_tile_request_cannot_walk_out_of_its_directory(self):
        path, root, entry = with_tiles()
        mapped = read(entry, root)
        for z, x, y in [('../../etc', 45, 108), (5, '../..', 108), (5, 45, '../../../etc/passwd'),
                        ('nope', 1, 1), (-1, 45, 108), (99, 45, 108), (5, -1, 108)]:
            self.assertIsNone(tile_path(mapped, z, x, y))
        # A tile that simply is not there is a miss, not an error.
        self.assertIsNone(tile_path(mapped, 5, 44, 108))

    def test_a_configured_location_may_sit_outside_the_project(self):
        # A tile pyramid is large and may well live on another disk.
        path, root, entry = with_tiles()
        mapped = read(entry)
        self.assertTrue(Path(mapped.tiles.directory).is_absolute())
        self.assertIsNotNone(tile_path(mapped, 5, 45, 108))
        self.assertIsNone(image_path(Calibration(name='x', size=1, offset=0,
                                                 picture=Picture('/nowhere.png', (0, 0), (1, 1)))))


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
        self.assertIsNone(point({'east': -500, 'north': 0}, self.mapped))
        self.assertIsNone(point({'east': 0, 'north': 99999}, self.mapped))
        self.assertEqual(point({'east': 0, 'north': 0}, self.mapped), (0, 0))
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
        self.assertEqual(data['map']['size'], 12800)
        self.assertEqual(data['map']['scale'], 12.5)
        self.assertEqual(data['map']['offset'], 50)
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
        for giveaway in ('dispersion:', 'elevation =', 'Math.atan2', '6400', '6000', '12.5'):
            self.assertNotIn(giveaway, page)


if __name__ == '__main__':
    unittest.main()
