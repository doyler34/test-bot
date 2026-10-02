"""The public OYB website: who we are, the servers and how to join, and the
Discord invite. It is served only on the domains listed in site_hosts, and
on those domains nothing of the panel is reachable.

The wording is edited in the panel (Website) and kept with the Discord
messages; which servers show, and what game each is, too.
"""
import re

DISCORD_INVITE = re.compile(r"^https://(discord\.gg|discord\.com/invite)/[\w-]+/?$")


def default_site():
    return {"name": "OYB", "tagline": "Old Young Bastards.",
            "about": "Old heads and young guns, same squad. We run our own servers, play hard, and don't take "
                     "ourselves too seriously.\n"
                     "OYB started as a group of mates who wanted servers run their way. Some of us have been gaming "
                     "since dial-up, some since they could hold a controller. Doesn't matter: turn up, play fair, "
                     "and you're one of us.\n"
                     "Want to see your kills, deaths and the rest? Link your game account in the Discord. Once "
                     "you're connected you're on the leaderboards: a live one for the game you're in, one for past "
                     "games, and a weekly one to settle who's actually the best.\n"
                     "Our admins actually watch the servers. Cheaters get caught and banned from every OYB server "
                     "at once, so the games stay worth playing.",
            "discord": "", "servers": {}}


def check_site(raw, server_ids):
    if not isinstance(raw, dict):
        return default_site(), ["That couldn't be read. Try again."]
    text = lambda key, limit: str(raw.get(key, "") or "").replace("\r\n", "\n").strip()[:limit]
    doc = {"name": text("name", 40) or "OYB", "tagline": text("tagline", 200), "about": text("about", 2000),
           "discord": text("discord", 200), "servers": {}}
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


def view(doc, servers, states, bridge):
    """What the site shows, grouped by game. No player names, IPs or anything
    else from the panel: just who's online and how many are playing."""
    reported = {s.get("id"): s for s in bridge.get("servers", [])}
    games, online, playing = {}, 0, 0
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
        games.setdefault(look["game"], []).append({
            "id": config.id, "name": name, "online": up, "players": count,
            "settings": plain((reported.get(config.id) or {}).get("settings", "")),
            "join": look["join"] or f"Search for “{name}” in the server browser."})
    return {"games": games, "online": online, "playing": playing,
            "count": sum(len(s) for s in games.values())}


def status(view_data):
    return {"online": view_data["online"], "playing": view_data["playing"],
            "servers": [{"id": s["id"], "online": s["online"], "players": s["players"]}
                        for group in view_data["games"].values() for s in group]}
