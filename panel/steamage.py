import asyncio
import re
import time
from datetime import datetime, timezone

import aiohttp

PROFILE = "https://steamcommunity.com/profiles/{}?xml=1"
STEAM_ID = re.compile(r"\d{17}")
STATE = re.compile(r"<privacyState>(\w+)</privacyState>")
SINCE = re.compile(r"<memberSince>([^<]+)</memberSince>")
# A private profile can go public, so it's asked again after this long. A date never changes.
RECHECK = 7 * 86400
PER_REFRESH = 4
GAP = 0.5
# When Steam says slow down, nothing is asked for this long.
BACKOFF = 300
blocked_until = 0.0


def parse(xml: str):
    """(created, state) from a public Steam profile page. created is when the account was made, or None
    when the profile keeps it private."""
    since, state = SINCE.search(xml), STATE.search(xml)
    created = None
    if since:
        try:
            created = int(datetime.strptime(since[1].strip(), "%B %d, %Y").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return created, state[1] if state else "unknown"


def stale(row, at) -> bool:
    """Whether this player's Steam account still needs asking about."""
    if not row["steam_id"] or not STEAM_ID.fullmatch(row["steam_id"]) or row["created"]:
        return False
    return row["checked"] is None or row["checked"] < at - RECHECK


async def refresh(db, rows, at) -> bool:
    """Looks up a few accounts that haven't been asked about yet, a few at a time so a busy server doesn't
    hammer Steam. True when something was saved."""
    global blocked_until
    ids = list(dict.fromkeys(r["steam_id"] for r in rows if stale(r, at)))[:PER_REFRESH]
    if not ids or time.monotonic() < blocked_until:
        return False
    saved = False
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6), headers={"User-Agent": "OYB-panel"}) as http:
        for number, steam_id in enumerate(ids):
            if number:
                await asyncio.sleep(GAP)
            try:
                async with http.get(PROFILE.format(steam_id)) as response:
                    if response.status == 429:
                        blocked_until = time.monotonic() + BACKOFF
                    if response.status != 200:
                        break
                    created, state = parse(await response.text())
            except (aiohttp.ClientError, asyncio.TimeoutError):
                break
            db.save_steam_account(steam_id, created, state)
            saved = True
    return saved
