"""The public OYB website: who we are, the servers and how to join, and the
Discord invite. It is served only on the domains listed in site_hosts, and
on those domains nothing of the panel is reachable.

The wording is edited in the panel (Website) and kept with the Discord
messages; which servers show, and what game each is, too.
"""
import re

DISCORD_INVITE = re.compile(r"^https://(discord\.gg|discord\.com/invite)/[\w-]+/?$")
OYB_DISCORD = "https://discord.gg/oyb"


def default_site():
    return {"name": "OYB", "tagline": "Old Young Bastards.",
            "about": "Old heads and young guns, same squad. We play hard, play fair, and don't take "
                     "ourselves too seriously.\n"
                     "OYB started as a group of mates who wanted servers run their way. Some of us have been gaming "
                     "since dial-up, some since they could hold a controller. Doesn't matter: turn up, play fair, "
                     "and you're one of us.\n"
                     "Want to see your kills, deaths and the rest? Link your game account in the Discord. Once "
                     "you're connected you're on the leaderboards: a live one for the game you're in, one for past "
                     "games, and a weekly one to settle who's actually the best.\n"
                     "Our admins actually watch the servers. Cheaters get caught and banned from every OYB server "
                     "at once, so the games stay worth playing.",
            "discord": OYB_DISCORD, "servers": {}}


def check_site(raw, server_ids):
    if not isinstance(raw, dict):
        return default_site(), ["That couldn't be read. Try again."]
    text = lambda key, limit: str(raw.get(key, "") or "").replace("\r\n", "\n").strip()[:limit]
    doc = {"name": text("name", 40) or "OYB", "tagline": text("tagline", 200), "about": text("about", 2000),
           "discord": text("discord", 200) or OYB_DISCORD, "servers": {}}
    problems = []
    if doc["discord"] and not DISCORD_INVITE.match(doc["discord"]):
        problems.append("The Discord invite should look like https://discord.gg/abc123.")
        doc["discord"] = ""
    servers = raw.get("servers") if isinstance(raw.get("servers"), dict) else {}
    for server_id in server_ids:
        given = servers.get(server_id) if isinstance(servers.get(server_id), dict) else {}
        doc["servers"][server_id] = {"show": bool(given.get("show", True)),
                                     "name": re.sub(r"\s+", " ", str(given.get("name", "") or "")).strip()[:60],
                                     "game": str(given.get("game", "") or "").strip()[:60] or "Arma Reforger",
                                     "join": str(given.get("join", "") or "").strip()[:200]}
    return doc, problems


def is_site_host(host, hosts):
    host = (host or "").split(":")[0].lower().rstrip(".")
    return any(host in (h, "www." + h) for h in hosts)


def plain(text):
    """The bot's server settings are Discord markdown; the site shows them plain."""
    return re.sub(r"[*_`]", "", text or "").strip()


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


FACT = re.compile(r"^([A-Za-z][A-Za-z ]{1,24}):\s*(.+)$")


def facts(settings):
    """The "Key: value" lines of a server's settings card, lower-cased by key. Lines with no
    key, such as the bot's own note that settings haven't been supplied, never reach visitors."""
    found = {}
    for line in str(settings or "").splitlines():
        match = FACT.match(plain(line))
        if match and match.group(2).strip():
            found.setdefault(match.group(1).strip().lower(), match.group(2).strip())
    return found


def rules(text):
    """The bot's rules card as plain lines for the site: no Discord markdown, no bullet marks."""
    lines = []
    for line in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[•\-*>]+|\d+[.)])\s*", "", plain(line))
        if line:
            lines.append(line)
    return lines


def view(doc, servers, states, bridge, live=None):
    """What the site shows: every shown server, the busiest first. No player names, IPs or
    anything else from the panel: just who's online, how many are playing and what the
    server is running. `live` is what the servers last reported about themselves."""
    reported = {s.get("id"): s for s in bridge.get("servers", [])}
    live = live or {}
    shown, online, playing = [], 0, 0
    for config in servers:
        look = doc["servers"].get(config.id) or {"show": True, "game": "Arma Reforger", "join": ""}
        if not look["show"]:
            continue
        state = states.get(config.id)
        up = bool(state and state.online)
        count = len(state.players) if up else 0
        online += up
        playing += count
        name = look.get("name") or (reported.get(config.id) or {}).get("label") or config.name
        card = facts((reported.get(config.id) or {}).get("settings", ""))
        mine = live.get(config.id) or {}
        shown.append({
            "id": config.id, "name": name, "online": up, "players": count, "game": look["game"],
            "slots": mine.get("slots") or None,
            "map": (mine.get("map") or card.get("map", "")) if up else card.get("map", ""),
            "mode": card.get("mode") or card.get("game mode", ""), "setup": card.get("setup", ""),
            "version": mine.get("version", "") if up else "", "join": look["join"]})
    shown.sort(key=lambda s: (not s["online"], -s["players"], s["name"].lower()))
    seen = set()
    for s in shown:
        s["slug"] = slugify(s["name"]) or slugify(s["id"])
        if s["slug"] in seen:
            s["slug"] += "-" + slugify(s["id"])
        seen.add(s["slug"])
    known = [s["slots"] for s in shown if s["slots"]]
    health = ("unknown" if not shown else "healthy" if online == len(shown) else "partial" if online else "down")
    return {"servers": shown, "online": online, "playing": playing, "count": len(shown),
            "slots": sum(known) if known else None, "slots_all_known": len(known) == len(shown) and bool(shown),
            "health": health}


def status(view_data):
    return {"online": view_data["online"], "playing": view_data["playing"],
            "servers": [{"id": s["id"], "online": s["online"], "players": s["players"], "slots": s["slots"]}
                        for s in view_data["servers"]]}
