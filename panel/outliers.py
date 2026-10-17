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


def unusual(rows, incidents, min_kills=25, min_incidents=3, min_lift=2.0):
    """min_kills keeps the aim numbers fair. A player with fewer kills is still
    listed when they turn up at incidents far more than their hours explain,
    because the kills behind an incident are credited to AI, not to them."""
    hours_total = sum(r["seconds"] or 0 for r in rows) / 3600
    rate = sum(incidents.get(r["identity"], 0) for r in rows) / hours_total if hours_total else 0
    players = []
    for r in rows:
        hours = (r["seconds"] or 0) / 3600
        seen = incidents.get(r["identity"], 0)
        lift = seen / (max(hours, 1) * rate) if rate and seen else None
        judged = (r["rifle"] or 0) >= min_kills
        p = {
            "identity": r["identity"], "name": r["name"], "games": r["games"], "rifle": r["rifle"] or 0,
            "kills": r["kills"], "deaths": r["deaths"], "hours": hours, "judged": judged,
            "head_share": r["heads"] / r["rifle"] if judged else None,
            "long_share": (r["long"] / r["measured"] if r["measured"] else 0.0) if judged else None,
            "per_hour": r["kills"] / hours if judged and hours >= 1 else None,
            "kd": r["kills"] / max(r["deaths"], 1) if judged else None,
            "incidents": seen, "incident_lift": lift,
            "incidents_high": seen >= min_incidents and lift is not None and lift >= min_lift}
        if judged or p["incidents_high"]:
            players.append(p)
    cutoffs, typical = {}, {}
    for key, _ in METRICS:
        values = sorted(p[key] for p in players if p[key] is not None)
        typical[key] = values[len(values) // 2] if values else None
        # Too few players to say what the top 5% looks like.
        cutoffs[key] = values[min(int(len(values) * TOP), len(values) - 1)] if len(values) >= 10 else None
    for p in players:
        p["high"] = [key for key, _ in METRICS if cutoffs[key] is not None and p[key] is not None
                     and p[key] >= cutoffs[key] and p[key] > typical[key]]
        p["stands_out"] = bool(p["high"]) or p["incidents_high"]
    players.sort(key=lambda p: (-(len(p["high"]) + p["incidents_high"]), -(p["incident_lift"] or 0),
                                -(p["head_share"] or 0)))
    return players, typical
