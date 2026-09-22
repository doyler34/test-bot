"""The mortar map's web front end.

It owns no ballistics. Every solution on the page came from the same engine
and the same tables the Discord command uses - the browser sends two points
and a loadout, and this hands back what bot.mortar worked out.

aiohttp is already in the tree under discord.py, so the page runs on the bot's
own event loop with nothing new installed. Every request is a table lookup and
a little arithmetic, so nothing here blocks it.
"""
import logging
from pathlib import Path

from aiohttp import web

from bot.mortar.calibration import calibration, image_path, reason, tile_path
from bot.mortar.solution import bearing, profile, profiles, solution, swap, weapons
from bot.web.sessions import Sessions

LOG = logging.getLogger('reforger.mortarweb')
STATIC = Path(__file__).resolve().parent / 'static'
# The page posts two points and a loadout; anything larger is not from us.
BODY_LIMIT = 8 * 1024
TYPES = {'.css': 'text/css', '.js': 'application/javascript', '.png': 'image/png',
         '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}


def number(value):
    """A coordinate from the browser, or None. Rejects text, NaN and infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float('inf'), float('-inf')):
        return None
    return number


def point(entry, mapped):
    """One end of the shot, in the game's own world X/Z metres.

    The page works in world coordinates from the click onwards - that is what
    the map's own coordinate system gives it - so there is nothing to convert
    here beyond checking the point is on the island.
    """
    if not isinstance(entry, dict):
        return None
    east, north = number(entry.get('east')), number(entry.get('north'))
    if east is None or north is None:
        return None
    if mapped is not None and not mapped.inside(east, north):
        return None
    return east, north


def loadout(body):
    """The weapon and round asked for, falling back to the first tube rather
    than to a guess about which one was meant."""
    tube = body.get('tube') if isinstance(body.get('tube'), str) else None
    shell = body.get('round') if isinstance(body.get('round'), str) else None
    chosen = profile(f'{tube}:{shell}')
    if chosen is not None:
        return chosen
    return swap(profile(f'{tube}:he') or None, weapon=tube, round_key=shell)


def catalogue():
    """Every tube and round the tables hold, with each round's own reach."""
    listing = []
    for key, rounds in weapons().items():
        first = rounds[0]
        listing.append({
            'key': key, 'name': first.name, 'faction': first.faction,
            'label': first.label, 'mils': first.mils,
            'rounds': [{'key': shell.round_key, 'name': shell.shell,
                        'minRange': shell.span[0], 'maxRange': shell.span[1]}
                       for shell in rounds]})
    return listing


def calculate(body, mapped):
    """One firing solution, straight off the engine."""
    weapon = loadout(body)
    if weapon is None:
        return {'valid': False, 'reason': 'no_tables'}
    gun, target = point(body.get('gun'), mapped), point(body.get('target'), mapped)
    if gun is None or target is None:
        return {'valid': False, 'reason': 'bad_request'}
    climb = number(body.get('climb')) or 0.0
    mils, distance = bearing(gun, target, weapon.mils)
    best, every = solution(weapon, distance, climb)
    low, high = weapon.span
    answer = {
        'tube': weapon.weapon, 'round': weapon.round_key,
        'tubeName': weapon.label, 'roundName': weapon.shell, 'mils': weapon.mils,
        'range_m': round(distance), 'azimuth_mils': round(mils),
        'min_range_m': low, 'max_range_m': high,
        'gun': {'east': round(gun[0]), 'north': round(gun[1])},
        'target': {'east': round(target[0]), 'north': round(target[1])},
    }
    if mapped is not None:
        answer['mortar_grid'] = mapped.grid(*gun)
        answer['target_grid'] = mapped.grid(*target)
    if best is None:
        answer.update(valid=False, reason='out_of_range')
        return answer
    answer.update(valid=True, ring=best.ring, elevation_mils=round(best.elevation),
                  tof_seconds=round(best.flight), dispersion_m=best.dispersion,
                  rings=[{'ring': r.ring, 'elevation_mils': round(r.elevation),
                          'tof_seconds': round(r.flight), 'dispersion_m': r.dispersion}
                         for r in every])
    if climb:
        answer['height_correction_mils'] = round(best.correction)
    return answer


class MortarWeb:
    """The service itself: one aiohttp app beside the bot."""

    def __init__(self, base_url='', host='127.0.0.1', port=8085, ttl=3600, config=None):
        self.base_url = (base_url or '').rstrip('/')
        self.host, self.port = host, int(port)
        self.sessions = Sessions(ttl)
        self.config = config
        self.runner = None

    # --- links -----------------------------------------------------------
    @property
    def ready(self):
        return bool(self.base_url) and calibration(self.config) is not None

    def link(self, holder=None):
        """A fresh map link, or None when the map is not set up."""
        if not self.ready:
            return None
        return f'{self.base_url}/mortar/{self.sessions.open(holder)}'

    # --- routes ----------------------------------------------------------
    def app(self):
        app = web.Application(client_max_size=BODY_LIMIT)
        app.add_routes([
            web.get('/mortar/{token}', self.page),
            web.get('/mortar/{token}/map', self.picture),
            web.get('/mortar/{token}/tiles/{z}/{x}/{y}', self.tile),
            web.get('/mortar/{token}/loadouts', self.loadouts),
            web.get('/static/{name}', self.asset),
            web.post('/api/mortar/calculate', self.solve),
        ])
        return app

    def known(self, request):
        return self.sessions.valid(request.match_info.get('token', ''))

    async def page(self, request):
        if not self.known(request):
            return web.Response(text=read_static('expired.html'), content_type='text/html',
                                status=404)
        mapped = calibration(self.config)
        if mapped is None:
            return web.Response(text=read_static('expired.html'), content_type='text/html',
                                status=503)
        body = read_static('mortar.html').replace('__TOKEN__', request.match_info['token'])
        return web.Response(text=body, content_type='text/html')

    async def picture(self, request):
        mapped = calibration(self.config)
        if not self.known(request) or mapped is None:
            raise web.HTTPNotFound()
        path = image_path(mapped)
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control': 'private, max-age=86400'})

    async def tile(self, request):
        mapped = calibration(self.config)
        if not self.known(request) or mapped is None:
            raise web.HTTPNotFound()
        path = tile_path(mapped, request.match_info['z'], request.match_info['x'],
                         request.match_info['y'])
        if path is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Cache-Control': 'private, max-age=604800'})

    async def loadouts(self, request):
        mapped = calibration(self.config)
        if not self.known(request) or mapped is None:
            raise web.HTTPNotFound()
        return web.json_response({'map': mapped.describe(), 'tubes': catalogue()})

    async def asset(self, request):
        name = request.match_info['name']
        path = (STATIC / name).resolve()
        if not path.is_relative_to(STATIC) or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={'Content-Type': TYPES.get(path.suffix,
                                                                         'text/plain')})

    async def solve(self, request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({'valid': False, 'reason': 'bad_request'}, status=400)
        if not isinstance(body, dict) or not self.sessions.valid(body.get('token')):
            return web.json_response({'valid': False, 'reason': 'expired'}, status=403)
        try:
            answer = calculate(body, calibration(self.config))
        except Exception:
            # Never let an internal message or a path reach the browser.
            LOG.exception('Mortar solution failed')
            return web.json_response({'valid': False, 'reason': 'failed'}, status=500)
        return web.json_response(answer, status=200 if answer.get('valid') else 200)

    # --- lifecycle -------------------------------------------------------
    async def run(self):
        """Serve until cancelled, alongside everything else the bot runs."""
        if not self.base_url:
            LOG.info('Mortar map is off; set MORTAR_WEB_BASE_URL to turn it on')
            return
        if calibration(self.config) is None:
            LOG.warning('Mortar map is off: %s See assets/mortar/README.md', reason(self.config))
            return
        self.runner = web.AppRunner(self.app(), access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        LOG.info('Mortar map listening on %s:%s for %s', self.host, self.port, self.base_url)
        try:
            while True:
                await _forever()
        finally:
            await self.close()

    async def close(self):
        if self.runner is not None:
            runner, self.runner = self.runner, None
            await runner.cleanup()


async def _forever():
    import asyncio
    await asyncio.sleep(3600)


def read_static(name):
    return (STATIC / name).read_text(encoding='utf-8')
