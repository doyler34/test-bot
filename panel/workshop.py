"""Arma Reforger Workshop mods on the public site, read from the ReforgerMods API.

The API gives metadata only: names, descriptions, sizes and links. Mod files
are never fetched here; the game server downloads what it lists when it starts.
Answers are kept for a while so visitors don't each call the API.
"""
import re
import time

import aiohttp

API = "https://api.reforgermods.net/v2"
AGENT = "OYB-Workshop/1.0"
TIMEOUT = aiohttp.ClientTimeout(total=10)
SEARCH_TTL = 600
DETAIL_TTL = 3600
CACHE_SIZE = 300
SORTS = {"popularity": "Most popular", "newest": "Newest", "recently-updated": "Recently updated",
         "highest-rated": "Highest rated", "subscribers": "Most subscribed", "name": "Name"}
MOD_ID = re.compile(r"^[0-9A-Fa-f]{16}$")
PREVIEW_FIELDS = ("name", "author", "summary", "version", "sizeFormatted", "ratingCount", "subscriberCount",
                  "tags", "updatedAt")


class WorkshopError(Exception):
    pass


class NotFound(WorkshopError):
    pass


def valid_id(mod_id):
    return bool(MOD_ID.match(mod_id or ""))


def workshop_url(mod_id):
    return f"https://reforger.armaplatform.com/workshop/{mod_id}"


def search_query(search="", sort="popularity", page=1):
    query = {"page": max(1, page), "sort": sort if sort in SORTS else "popularity"}
    text = " ".join(str(search or "").split())[:80]
    if text:
        query["search"] = text
    return query


def https_or_none(value):
    return value if isinstance(value, str) and value.startswith("https://") else None


def preview(raw):
    """One mod as the page shows it. Rows without a usable ID are dropped rather than linked to."""
    if not isinstance(raw, dict) or not valid_id(raw.get("id")):
        return None
    row = {key: raw[key] for key in PREVIEW_FIELDS if raw.get(key) not in (None, "")}
    row["id"] = raw["id"]
    row["url"] = f"/workshop/{raw['id']}"
    row["workshopUrl"] = workshop_url(raw["id"])
    row["imageUrl"] = https_or_none(raw.get("imageUrl"))
    rating = raw.get("rating")
    row["percent"] = round(rating * 100) if isinstance(rating, (int, float)) else None
    return row


def detail(raw):
    row = preview(raw)
    if row is None:
        raise NotFound("That mod isn't on the Workshop.")
    row["description"] = str(raw.get("description") or "")[:4000]
    row["license"] = raw.get("license") or ""
    row["dependencies"] = [{"id": dep["id"], "name": dep.get("name") or dep["id"], "url": f"/workshop/{dep['id']}"}
                           for dep in raw.get("dependencies") or []
                           if isinstance(dep, dict) and valid_id(dep.get("id"))]
    return row


class Workshop:
    def __init__(self, fetch=None, clock=time.monotonic):
        self._fetch = fetch or self._get
        self._clock = clock
        self._cache = {}

    async def _get(self, path, query):
        headers = {"User-Agent": AGENT, "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT, headers=headers) as http:
                async with http.get(f"{API}/{path}", params=query) as response:
                    if response.status == 404:
                        raise NotFound("That mod isn't on the Workshop.")
                    if response.status >= 400:
                        raise WorkshopError("The mod list is busy. Try again in a few minutes.")
                    return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise WorkshopError("The mod list didn't answer. Try again in a few minutes.") from None

    async def _cached(self, key, ttl, path, query):
        now = self._clock()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        data = await self._fetch(path, query)
        if len(self._cache) >= CACHE_SIZE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (now, data)
        return data

    async def search(self, search="", sort="popularity", page=1):
        query = search_query(search, sort, page)
        data = await self._cached(("search", tuple(sorted(query.items()))), SEARCH_TTL, "mods", query)
        meta = data.get("meta") if isinstance(data, dict) and isinstance(data.get("meta"), dict) else {}
        rows = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), list) else []
        return {"mods": [row for row in map(preview, rows) if row], "page": query["page"],
                "pages": meta.get("totalPages") or 1, "total": meta.get("totalMods"),
                "sort": query["sort"], "search": query.get("search", "")}

    async def mod(self, mod_id):
        if not valid_id(mod_id):
            raise NotFound("That mod isn't on the Workshop.")
        data = await self._cached(("mod", mod_id), DETAIL_TTL, f"mods/{mod_id}", None)
        raw = data.get("mod") if isinstance(data, dict) else None
        return detail(raw)
