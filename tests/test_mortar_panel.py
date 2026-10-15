import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from bot.mortar.calibration import read
from panel import auth, maptiles
from panel.config import PanelConfig
from panel.db import PanelDB
from panel.servers import ServerManager
from panel.web import CONFIG, create_app

EVERON = json.loads((Path(__file__).parents[1] / 'assets/mortar/map.json').read_text())


class Pyramid:
    """Points the downloader at a scratch folder instead of data/map-tiles."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        self.patch = mock.patch.multiple(maptiles, OUT=self.root, FINAL=self.root / 'everon_sat',
                                         STATUS=self.root / 'everon.status.json')

    def __enter__(self):
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class DownloadTests(unittest.TestCase):
    def test_cuts_the_same_pyramid_as_genefrag(self):
        with Pyramid() as box:
            picture = box.root / 'map.png'
            Image.new('RGB', (1000, 1000), (40, 60, 30)).save(picture)
            self.assertEqual(maptiles.main(['--source', str(picture), '--zoom', '2']), 0)
            # 1000px pads to 1024: 4x4 tiles at zoom 2, 2x2 at 1, 1 at 0.
            self.assertEqual(sorted(p.name for p in (box.root / 'everon_sat').iterdir()), ['0', '1', '2'])
            self.assertEqual(len(list((box.root / 'everon_sat').rglob('*.webp'))), 21)
            with Image.open(box.root / 'everon_sat' / '2' / '3' / '3.webp') as tile:
                self.assertEqual(tile.size, (256, 256))
            self.assertEqual(maptiles.read_status()['state'], 'done')
            self.assertTrue(picture.exists())
            entry = {**EVERON, 'tiles': {**EVERON['tiles'], 'directory': str(box.root / 'everon_sat')}}
            self.assertEqual(read(entry).max_zoom, 2)

    def test_a_job_that_died_reads_as_failed(self):
        with Pyramid() as box:
            box.root.joinpath('everon.status.json').write_text(json.dumps({'state': 'cutting', 'pid': 2 ** 22 + 7}))
            self.assertEqual(maptiles.read_status()['state'], 'failed')
            self.assertEqual(maptiles.main(['--source', str(box.root / 'missing.png')]), 1)
            self.assertEqual(maptiles.read_status()['state'], 'failed')
            self.assertFalse((box.root / 'everon_sat').exists())


class FetchTests(unittest.TestCase):
    def test_asks_like_a_browser_and_says_who_refused(self):
        import urllib.error
        seen = []

        def refuse(request, timeout):
            seen.append(request.get_header('User-agent'))
            raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, None)

        with mock.patch('urllib.request.urlopen', side_effect=refuse):
            with self.assertRaisesRegex(RuntimeError, r'pub-x\.r2\.dev refused the download \(HTTP 403\)'):
                maptiles.fetch('https://pub-x.r2.dev/everon_sat_z6_full.png', 5)
        self.assertNotIn('Python-urllib', seen[0])


class PanelMapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = PanelConfig(database=':memory:', cookie_secure=False, servers=[])
        self.db = PanelDB(':memory:')
        self.db.add_user('boss', auth.hash_password('boss-password'), 'owner')
        self.db.add_user('adm', auth.hash_password('adm-password'), 'admin')
        self.client = TestClient(TestServer(create_app(config, self.db, ServerManager(config, self.db),
                                                       start_manager=False)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.box = Pyramid().__enter__()
        self.addCleanup(self.box.__exit__)
        tiles = self.box.root / 'everon_sat' / '3' / '2'
        tiles.mkdir(parents=True)
        Image.new('RGB', (256, 256)).save(tiles / '1.webp', 'WEBP')
        self.mapped = read({**EVERON, 'tiles': {**EVERON['tiles'], 'directory': str(self.box.root / 'everon_sat')}})

    async def login(self, name, password):
        await self.client.post('/login', data={'username': name, 'password': password})
        html = await (await self.client.get('/')).text()
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    async def test_owners_get_the_map_and_nobody_else_does(self):
        token = await self.login('adm', 'adm-password')
        for path in ('/mortar/map', '/mortar/panel/loadouts', '/mortar/panel/tiles/3/2/1', '/mortar/panel/static/mortar.js'):
            self.assertEqual((await self.client.get(path, allow_redirects=False)).status, 403, path)
        reply = await self.client.post('/api/mortar/calculate', json={}, headers={'X-CSRF': token})
        self.assertEqual(reply.status, 403)
        self.assertEqual((await self.client.post('/mortar/download', data={'csrf': token})).status, 403)

    async def test_the_map_page_tiles_and_a_solution(self):
        token = await self.login('boss', 'boss-password')
        with mock.patch('panel.web.mortar_map', return_value=self.mapped):
            page = await self.client.get('/mortar/map')
            html = await page.text()
            self.assertIn(f'data-csrf="{token}"', html)
            self.assertRegex(html, r'src="/mortar/panel/static/leaflet\.js\?v=\d+"')
            self.assertRegex(html, r'href="/mortar/panel/static/mortar\.css\?v=\d+"')
            self.assertNotIn('"/static/', html)
            self.assertNotIn('unpkg.com', html)
            self.assertIn("'unsafe-inline'", page.headers['Content-Security-Policy'])
            self.assertEqual((await self.client.get('/mortar/panel/tiles/3/2/1')).status, 200)
            self.assertEqual((await self.client.get('/mortar/panel/tiles/3/9/9')).status, 404)
            self.assertEqual((await self.client.get('/mortar/panel/static/leaflet.js')).status, 200)
            self.assertEqual((await self.client.get('/mortar/panel/static/../web.py')).status, 404)
            loadouts = await (await self.client.get('/mortar/panel/loadouts')).json()
            self.assertEqual(loadouts['map']['maxZoom'], 3)
            body = {'tube': 'm252', 'round': 'he', 'gun': {'east': 4500, 'north': 10776},
                    'target': {'east': 5200, 'north': 10100}}
            self.assertEqual((await self.client.post('/api/mortar/calculate', json=body)).status, 403)
            answer = await (await self.client.post('/api/mortar/calculate', json=body, headers={'X-CSRF': token})).json()
            self.assertTrue(answer['valid'])
            self.assertEqual(answer['range_m'], 973)
        self.assertNotIn("'unsafe-inline'", (await self.client.get('/mortar')).headers['Content-Security-Policy'])

    async def test_anyone_gets_the_map_on_the_public_site(self):
        self.client.server.app[CONFIG].site_hosts = ['oybgaming.com']
        site = {'Host': 'oybgaming.com'}
        with mock.patch('panel.web.mortar_map', return_value=self.mapped):
            page = await self.client.get('/mortar', headers=site)
            html = await page.text()
            self.assertEqual(page.status, 200)
            self.assertIn('data-base="/mortar" data-api="/mortar/calculate"', html)
            self.assertNotIn('data-csrf', html)
            self.assertRegex(html, r'src="/mortar/static/mortar\.js\?v=\d+"')
            self.assertEqual((await self.client.get('/mortar/static/mortar.css', headers=site)).status, 200)
            self.assertEqual((await self.client.get('/mortar/static/web.py', headers=site)).status, 404)
            self.assertEqual((await self.client.get('/mortar/tiles/3/2/1', headers=site)).status, 200)
            self.assertEqual((await self.client.get('/mortar/tiles/3/9/9', headers=site)).status, 404)
            loadouts = await (await self.client.get('/mortar/loadouts', headers=site)).json()
            self.assertEqual(loadouts['map']['maxZoom'], 3)
            body = {'tube': 'm252', 'round': 'he', 'gun': {'east': 4500, 'north': 10776},
                    'target': {'east': 5200, 'north': 10100}}
            answer = await (await self.client.post('/mortar/calculate', json=body, headers=site)).json()
            self.assertEqual(answer['range_m'], 973)
            # The owner pages stay on the panel's own address.
            self.assertEqual((await self.client.get('/mortar/download', headers=site)).status, 404)
            self.assertEqual((await self.client.get('/mortar/map', headers=site)).status, 404)
        with mock.patch('panel.web.mortar_map', return_value=None):
            self.assertEqual((await self.client.get('/mortar', headers=site)).status, 503)

    async def test_the_download_button(self):
        token = await self.login('boss', 'boss-password')
        with mock.patch('panel.web.mortar_map', return_value=None):
            self.assertIn('Download the map', await (await self.client.get('/mortar')).text())
            self.assertEqual((await self.client.get('/mortar/map', allow_redirects=False)).status, 302)
            with mock.patch('asyncio.create_subprocess_exec', new=mock.AsyncMock()) as start:
                await self.client.post('/mortar/download', data={'csrf': token})
            self.assertEqual(start.call_args.args[1:3], ('-m', 'panel.maptiles'))
            maptiles.write_status(state='cutting', done=50, total=200)
            page = await (await self.client.get('/mortar')).text()
            self.assertIn('Cutting it into tiles: 25%', page)
            self.assertIn('http-equiv="refresh"', page)
            with mock.patch('asyncio.create_subprocess_exec', new=mock.AsyncMock()) as start:
                await self.client.post('/mortar/download', data={'csrf': token})
            start.assert_not_called()
        self.assertEqual(self.db.audit()[0]['action'], 'download mortar map')


    async def test_the_sharper_map_waits_for_enough_memory(self):
        token = await self.login('boss', 'boss-password')
        with mock.patch('panel.web.mortar_map', return_value=self.mapped):
            with mock.patch('panel.maptiles.free_memory', return_value=2 * 1024 ** 3):
                page = await (await self.client.get('/mortar')).text()
                self.assertNotIn('Get the sharper map', page)
                self.assertIn('this box has 2.0 GB free', page)
                with mock.patch('asyncio.create_subprocess_exec', new=mock.AsyncMock()) as start:
                    await self.client.post('/mortar/download', data={'csrf': token, 'zoom': '7'})
                start.assert_not_called()
            with mock.patch('panel.maptiles.free_memory', return_value=8 * 1024 ** 3):
                self.assertIn('Get the sharper map', await (await self.client.get('/mortar')).text())
                with mock.patch('asyncio.create_subprocess_exec', new=mock.AsyncMock()) as start:
                    await self.client.post('/mortar/download', data={'csrf': token, 'zoom': '7'})
                self.assertEqual(start.call_args.args[1:5], ('-m', 'panel.maptiles', '--zoom', '7'))

if __name__ == '__main__':
    unittest.main()
