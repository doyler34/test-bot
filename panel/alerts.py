"""Posts panel events to a Discord channel through a webhook."""

import asyncio
import logging
import time

import aiohttp

log = logging.getLogger("panel.alerts")

GOLD = 0xD9A441
KHAKI = 0xA9BC8C
RED = 0xE74C3C
GREEN = 0x2ECC71


class Alerts:
    def __init__(self, webhook: str = ""):
        self.webhook = webhook
        self._session: aiohttp.ClientSession | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.webhook)

    def send(self, title: str, description: str = "", colour: int = KHAKI, fields=()):
        if not self.enabled:
            return
        embed = {"title": title[:256], "description": description[:2000], "colour": colour,
                 "footer": {"text": "OYB Control"},
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if fields:
            embed["fields"] = [{"name": n[:256], "value": str(v)[:1024] or "—", "inline": True} for n, v in fields]
        task = asyncio.create_task(self._post({"embeds": [embed], "allowed_mentions": {"parse": []}}))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _post(self, payload):
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
            async with self._session.post(self.webhook, json=payload) as response:
                if response.status >= 300:
                    log.warning("Discord webhook answered %s", response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Discord alert failed: %s", exc)

    async def close(self):
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._session:
            await self._session.close()
