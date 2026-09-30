"""Players whose numbers stand out over many games.

A cheater careful enough to stay under the live flags still ends up with
numbers nobody else has once enough games are added up. Each player is
compared with everyone else who has enough kills to judge, and a number in
the top 5% is marked. Good players will show up here too: it is a list of who
to spectate, not of who to ban.
"""

METRICS = (("head_share", "Headshots"), ("long_share", "Long range"), ("per_hour", "Kills per hour"),
           ("kd", "K/D"))
TOP = 0.95


def unusual(rows, incidents, min_kills=25):
    players = []
    for r in rows:
        if (r["rifle"] or 0) < min_kills:
            continue
        hours = (r["seconds"] or 0) / 3600
        players.append({
            "identity": r["identity"], "name": r["name"], "games": r["games"], "rifle": r["rifle"],
            "kills": r["kills"], "deaths": r["deaths"], "hours": hours,
            "head_share": r["heads"] / r["rifle"],
            "long_share": r["long"] / r["measured"] if r["measured"] else 0.0,
            "per_hour": r["kills"] / hours if hours >= 1 else None,
            "kd": r["kills"] / max(r["deaths"], 1),
            "incidents": incidents.get(r["identity"], 0)})
    cutoffs, typical = {}, {}
    for key, _ in METRICS:
        values = sorted(p[key] for p in players if p[key] is not None)
        typical[key] = values[len(values) // 2] if values else None
        # Too few players to say what the top 5% looks like.
        cutoffs[key] = values[min(int(len(values) * TOP), len(values) - 1)] if len(values) >= 10 else None
    for p in players:
        p["high"] = [key for key, _ in METRICS if cutoffs[key] is not None and p[key] is not None
                     and p[key] >= cutoffs[key] and p[key] > typical[key]]
    players.sort(key=lambda p: (-len(p["high"]), -p["incidents"], -p["head_share"]))
    return players, typical
