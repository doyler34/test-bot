"""Who an IP address belongs to, from the registries' own records (RDAP, the
replacement for WHOIS), plus its reverse DNS name. rdap.org passes the
question to whichever registry holds the address: ARIN, RIPE and so on.
"""
import asyncio
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request

RDAP = "https://rdap.org/ip/{}"
CACHE_SECONDS = 3600
HEADERS = {"Accept": "application/rdap+json", "User-Agent": "OYB-Control"}
_cache: dict[str, tuple[float, dict]] = {}


def clean_ip(text):
    try:
        return str(ipaddress.ip_address((text or "").strip()))
    except ValueError:
        return None


def _vcard(entity, field):
    for item in (entity.get("vcardArray") or [None, []])[1]:
        if isinstance(item, list) and len(item) >= 4 and item[0] == field:
            value = item[3]
            return " ".join(v for v in value if v).strip() if isinstance(value, list) else str(value)
    return ""


def _entities(entities, found):
    for entity in entities or []:
        name = _vcard(entity, "fn") or entity.get("handle", "")
        for role in entity.get("roles") or []:
            found.append({"role": role, "name": name, "email": _vcard(entity, "email"),
                          "address": _vcard(entity, "adr")})
        _entities(entity.get("entities"), found)
    return found


def read(data):
    """The parts of an RDAP answer worth showing."""
    cidrs = [f"{c.get('v4prefix') or c.get('v6prefix')}/{c.get('length')}" for c in data.get("cidr0_cidrs") or []]
    events = {e.get("eventAction"): (e.get("eventDate") or "")[:10] for e in data.get("events") or []}
    contacts = _entities(data.get("entities"), [])
    remarks = [" ".join(r.get("description") or []) for r in data.get("remarks") or []]
    return {
        "name": data.get("name", ""), "handle": data.get("handle", ""), "type": data.get("type", ""),
        "country": data.get("country", ""), "range": f"{data.get('startAddress', '')} – {data.get('endAddress', '')}",
        "cidrs": cidrs, "registered": events.get("registration", ""), "changed": events.get("last changed", ""),
        "owner": next((c["name"] for c in contacts if c["role"] == "registrant"), "")
                 or next((c["name"] for c in contacts if c["name"]), ""),
        "abuse": next((c["email"] for c in contacts if c["role"] == "abuse" and c["email"]), ""),
        "contacts": contacts, "remarks": [r for r in remarks if r][:5],
    }


def _fetch(ip):
    request = urllib.request.Request(RDAP.format(ip), headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return read(json.load(reply))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LookupError("No registry has a record for this address.") from None
        raise LookupError(f"The registry refused the lookup (HTTP {exc.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LookupError(f"Couldn't reach the registry: {getattr(exc, 'reason', exc)}.") from None
    except ValueError:
        raise LookupError("The registry sent back something that isn't a record.") from None


def _reverse(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


async def lookup(ip):
    """WHOIS and reverse DNS for one address; an "error" instead when the
    registry can't be asked or has nothing."""
    hit = _cache.get(ip)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    address = ipaddress.ip_address(ip)
    if address.is_private or address.is_loopback or address.is_reserved:
        return {"error": "That's a private or reserved address: it isn't registered to anyone."}
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _fetch, ip)
    except LookupError as exc:
        return {"error": str(exc)}
    try:
        result["reverse"] = await asyncio.wait_for(loop.run_in_executor(None, _reverse, ip), 5)
    except asyncio.TimeoutError:
        result["reverse"] = ""
    _cache[ip] = (time.monotonic(), result)
    return result
