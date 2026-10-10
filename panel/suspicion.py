"""Flags kill patterns that looked like cheating in past investigations.

The case that shaped this: explosions killing players with the kill credited to
"AI" (so nothing in the kill feed), several players dying in the same second
in different places, a lot of them headshots, and a spike of NULL script errors,
all starting a minute after one player joined. Every such burst is an
incident: the server manager saves who was connected, so a player who keeps
turning up at incidents, on any server, stands out. Vanilla logs never say which
weapon was used, so a flag is a reason to spectate, not proof.

Deliberately ignored: kills credited from over 2 km away and COLLISION deaths,
which are attribution quirks (a crash credited to whoever last damaged the
vehicle), and long-range explosive kills by a named player, which are usually a
gunship.

Every kill also says where the killer stood, so a player who turns up far away
from their last kill sooner than anything in the game can carry them, without
dying in between, has teleported. A player's victims dying far apart from each
other in the same moment is the same thing seen from the other side.
"""

import math
from collections import Counter, defaultdict, deque

EXPLOSIVE = {"EXPLOSIVE", "FRAGMENTATION", "INCENDIARY"}
# An AP mine goes off under someone, so the game puts the hit in a foot.
FEET = {"LFoot", "RFoot"}

DEFAULTS = {
    "ai_explosions": 5,           # explosive deaths credited to AI ...
    "ai_explosions_window": 300,  # ... within this many seconds
    "same_second": 3,             # players killed by AI explosions in the same second
    "rapid_kills": 6,             # kills by one player ...
    "rapid_window": 30,           # ... within this many seconds
    "headshot_kills": 10,         # at least this many rifle kills in 15 minutes ...
    "headshot_share": 0.75,       # ... with this share of them to the head
    "teamkills": 3,               # teamkills by one player in 10 minutes
    "script_errors": 20,          # different seconds with script errors in 5 minutes
    "max_distance": 2000,         # kills credited from further than this are ignored
    "nearby": 150,                # metres: a named killer this close to the explosions is named
    "joined_before": 600,         # seconds: players who connected this long before are named
    "teleport_distance": 1000,    # metres a player moved between two of their kills ...
    "teleport_speed": 150,        # ... faster than this (m/s; a helicopter does about 80) without dying
    "long_shot": 800,             # metres: a bullet kill from further than this is a long shot ...
    "long_shots": 3,              # ... and this many by one player in 15 minutes is flagged
    "spread": 400,                # metres apart two of one player's victims were ...
    "spread_window": 2,           # ... dying within this many seconds of each other
    "cooldown": 600,              # seconds before the same flag fires again
    "settle": 120,                # seconds a burst waits, so killers nearby just after count too
    "mine_kills": 5,              # explosive kills to the feet by one player in one game (0 turns it off)
}


def metres(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Detector:
    """Fed every log event for one server; returns the flags they raise."""

    def __init__(self, settings: dict | None = None):
        self.s = {**DEFAULTS, **(settings or {})}
        self.ai_blasts: deque = deque()
        self.kills: dict[str, deque] = defaultdict(deque)
        self.named: deque = deque()
        self.joins: deque = deque()
        self.errors: deque = deque()
        self.fired: dict[tuple, int] = {}
        self.seen: dict[str, int] = {}
        self.left: dict[str, int] = {}
        self.pending: list[dict] = []
        self.spots: dict[str, tuple] = {}
        self.teamkills: dict[str, dict] = {}
        self.mines: dict[str, dict] = {}
        self.names: dict[str, str] = {}
        self.game = None

    def feed(self, event: dict) -> list[dict]:
        if event.get("game") and event["game"] != self.game:
            # Teamkill and mine tallies are per game.
            self.game = event["game"]
            self.teamkills.clear()
            self.mines.clear()
        return self.flush(event["at"]) + self._feed(event)

    def flush(self, at: int) -> list[dict]:
        """Bursts whose settle time has passed, with who joined before and who was killing nearby."""
        ready = [p for p in self.pending if at - p["at"] >= self.s["settle"]]
        self.pending = [p for p in self.pending if p not in ready]
        return [self._describe(p) for p in ready]

    def _feed(self, event: dict) -> list[dict]:
        kind = event["kind"]
        if kind == "identity":
            at, identity = event["at"], event["identity"]
            # A timeout and reconnect with the same UUID isn't a new arrival.
            if identity not in self.seen or at - self.seen[identity] > 3600:
                self.joins.append((at, event["name"], identity, event.get("ip", "")))
            self.seen[identity] = at
            self.names[event["name"]] = identity
            self.spots.pop(identity, None)
            if len(self.seen) > 5000:
                self.seen = {k: v for k, v in self.seen.items() if at - v < 3600}
            trim(self.joins, at - self.s["joined_before"] - 3600)
        elif kind == "leave":
            self.left[event["name"]] = event["at"]
        elif kind == "error":
            return self._error(event)
        elif kind in ("kill", "teamkill") and event.get("victim"):
            return self._kill(event)
        return []

    def _kill(self, e):
        # Mines are counted before the distance filter: their owner is often kilometres away.
        return self._mine(e) + self._kill_checks(e)

    def _mine(self, e):
        """Explosive kills to the feet by one player, all game: most likely AP mines.
        Once it flags, one running line keeps the count and who they killed."""
        limit, owner = self.s["mine_kills"], e.get("killer_label")
        if not limit or e.get("zone") not in FEET or e.get("damage") not in EXPLOSIVE:
            return []
        if not owner or e.get("by_ai") or owner == e.get("victim_name"):
            return []
        key = e.get("killer") or self.names.get(owner) or f"name:{owner}"
        tally = self.mines.setdefault(key, {"victims": Counter(), "tk": 0, "enemy": 0, "after": 0, "flagged": False})
        tally["victims"][e.get("victim_name") or "someone"] += 1
        side = {"TK": "tk", "ENEMY": "enemy"}.get(e.get("relation"), "after")
        tally[side] += 1
        count = sum(tally["victims"].values())
        if not tally["flagged"] and count < limit:
            return []
        first = not tally["flagged"]
        tally["flagged"] = True
        split = [plural(tally["tk"], "teammate"), plural(tally["enemy"], "enemy", "enemies")]
        if tally["after"]:
            split.append(f"{tally['after']} after they'd left or switched side")
        names = ", ".join(name if n == 1 else f"{name} ({n}x)" for name, n in tally["victims"].most_common())
        text = (f"{owner} has killed {plural(count, 'player')} this game with explosions to the feet, most likely "
                f"AP mines ({', '.join(split)}). Killed: {names}")
        return [{"at": e["at"], "identity": "" if key.startswith("name:") else key, "incident": False,
                 "key": ("mines", key), "count": count, "first": first, "mines": True, "name": owner, "text": text}]

    def _kill_checks(self, e):
        at, s = e["at"], self.s
        # Dying lets a player respawn anywhere, so their next kill starts afresh.
        self.spots.pop(e["victim"], None)
        if e.get("damage") == "COLLISION" or (e.get("distance") or 0) > s["max_distance"]:
            return []
        if e.get("killer") == e["victim"]:
            return []
        flags = []
        if e.get("damage") in EXPLOSIVE and e.get("by_ai"):
            self.ai_blasts.append(e)
            trim(self.ai_blasts, at - s["ai_explosions_window"])
            same = [b for b in self.ai_blasts if b["at"] == at]
            if len(same) >= s["same_second"]:
                flags += self._flag(("same_second",), at,
                                    f"{len(same)} players were blown up in the same second by explosions the game "
                                    "credits to AI, so they don't show in the kill feed", self.ai_blasts)
            if len(self.ai_blasts) >= s["ai_explosions"]:
                window = s["ai_explosions_window"] // 60
                flags += self._flag(("ai_explosions",), at,
                                    f"{len(self.ai_blasts)} players were killed by explosions in the last {window} "
                                    "minutes that the game credits to AI, so they don't show in the kill feed",
                                    self.ai_blasts)
            return flags
        if not e.get("killer") or e.get("killer_name") is None:
            return []
        self.named.append(e)
        trim(self.named, at - 600)
        mine = self.kills[e["killer"]]
        mine.append(e)
        trim(mine, at - 900)
        who = e["killer_name"]
        flags += self._teleport(e, who) + self._spread(e, mine, who)
        if e["kind"] == "teamkill":
            return flags + self._teamkill(e, mine, who)
        rifle = [k for k in mine if k["kind"] == "kill" and k.get("damage") == "KINETIC"]
        rapid = [k for k in rifle if k["at"] >= at - s["rapid_window"]]
        if len(rapid) >= s["rapid_kills"]:
            flags += self._flag(("rapid", e["killer"]), at,
                                f"{who} got {len(rapid)} kills in {s['rapid_window']} seconds", identity=e["killer"])
        longs = [k for k in rifle if (k.get("distance") or 0) >= s["long_shot"]]
        if len(longs) >= s["long_shots"]:
            far = max(k["distance"] for k in longs)
            text = (f"{who} shot and killed {len(longs)} players from over {s['long_shot']} m away in 15 minutes. "
                    f"The longest was {far:,.0f} m")
            heads = sum(k.get("zone") == "Head" for k in longs)
            text += f" and {plural(heads, 'was a headshot', 'were headshots')}." if heads else "."
            flags += self._flag(("long", e["killer"]), at, text, identity=e["killer"])
        heads = sum(k.get("zone") == "Head" for k in rifle)
        if len(rifle) >= s["headshot_kills"] and heads / len(rifle) >= s["headshot_share"]:
            flags += self._flag(("headshots", e["killer"]), at,
                                f"{who} got {len(rifle)} kills in 15 minutes and {heads} of them were headshots",
                                identity=e["killer"])
        return flags

    def _teleport(self, e, who):
        if not e.get("killer_at"):
            return []
        last = self.spots.get(e["killer"])
        self.spots[e["killer"]] = (e["at"], e["killer_at"])
        if last is None or e["at"] - last[0] > 120:
            return []
        gap, seconds = metres(last[1], e["killer_at"]), max(e["at"] - last[0], 1)
        if gap < self.s["teleport_distance"] or gap / seconds < self.s["teleport_speed"]:
            return []
        return self._flag(("teleport", e["killer"]), e["at"],
                          f"{who} got a kill {gap:,.0f} m away from their last one only "
                          f"{plural(seconds, 'second')} later, without dying in between. Nothing in the game "
                          "moves that fast",
                          identity=e["killer"])

    def _spread(self, e, mine, who):
        if not e.get("victim_at") or e.get("damage") in ("BLEEDING", "FIRE"):
            return []
        for k in mine:
            if k is e or not 0 <= e["at"] - k["at"] <= self.s["spread_window"] or not k.get("victim_at") \
                    or k.get("damage") in ("BLEEDING", "FIRE"):
                continue
            apart = metres(k["victim_at"], e["victim_at"])
            if apart >= self.s["spread"]:
                return self._flag(("spread", e["killer"]), e["at"],
                                  f"{who} killed two players who were {apart:,.0f} m apart from each other "
                                  + ("in the same second" if e["at"] == k["at"]
                                     else f"within {plural(e['at'] - k['at'], 'second')}"),
                                  identity=e["killer"])
        return []

    def _teamkill(self, e, mine, who):
        """Once someone has teamkilled enough to flag, every teamkill after
        that in the same game updates one running line for them rather than
        adding another."""
        tally = self.teamkills.setdefault(e["killer"], {"victims": Counter(), "flagged": False})
        tally["victims"][e.get("victim_name") or "someone"] += 1
        recent = [k for k in mine if k["kind"] == "teamkill" and k["at"] >= e["at"] - 600]
        if not tally["flagged"] and len(recent) < self.s["teamkills"]:
            return []
        tally["flagged"] = True
        count = sum(tally["victims"].values())
        names = ", ".join(name if n == 1 else f"{name} ({n}x)" for name, n in tally["victims"].most_common())
        return [{"at": e["at"], "identity": e["killer"], "incident": False, "key": ("teamkills", e["killer"]),
                 "count": count, "text": f"{who} has teamkilled {plural(count, 'time')} this game. Killed: {names}"}]

    def _error(self, e):
        # AI behaviour scripts can loop on one broken vehicle and throw dozens a
        # frame; that's a game bug, not a player. Counting distinct seconds keeps
        # one looping frame from looking like a stream of failing spawns.
        where = e.get("where") or ""
        if where.startswith("SCR_AI"):
            return []
        self.errors.append((e["at"], where))
        trim(self.errors, e["at"] - 300)
        seconds = {at for at, _ in self.errors}
        if len(seconds) >= self.s["script_errors"]:
            common = Counter(w for _, w in self.errors if w).most_common(1)
            text = f"The server hit script errors in {len(seconds)} separate seconds over the last 5 minutes"
            if common:
                text += f", mostly in {common[0][0]}"
            return self._flag(("errors",), e["at"], text, [{"at": self.errors[0][0]}])
        return []

    def _flag(self, key, at, text, blasts=(), identity=""):
        if at - self.fired.get(key, -10 ** 9) < self.s["cooldown"]:
            return []
        self.fired[key] = at
        if blasts:
            self.pending.append({"at": at, "text": text, "blasts": list(blasts)})
            return []
        return [{"at": at, "text": text, "identity": identity, "incident": False}]

    def _describe(self, pending):
        text, blasts, details = pending["text"], pending["blasts"], []
        heads = sum(b.get("zone") == "Head" for b in blasts)
        if heads:
            details.append(f"{heads} of the {len(blasts)} were hit in the head.")
        joined = self._joined_before(blasts[0]["at"], {b.get("victim") for b in blasts})
        if joined:
            details.append("Joined shortly before: " + ", ".join(joined) + ".")
        if blasts[0].get("victim"):
            near = self._nearby(blasts)
            if near:
                details.append("Getting kills close by at the time: " + ", ".join(near) + ".")
        text += ". " + " ".join(details) if details else "."
        return {"at": pending["at"], "text": text, "identity": "", "incident": True}

    def _joined_before(self, start, victims):
        """New arrivals shortly before, minus anyone who was a victim or had already left."""
        window = self.s["joined_before"]
        found = []
        for at, name, identity, ip in self.joins:
            if not start - window <= at <= start or identity in victims:
                continue
            if self.left.get(name, 0) >= at and self.left[name] <= start:
                continue
            label = f"{name} ({ip})" if ip else name
            if label not in found:
                found.append(label)
        return found[-5:]

    def _nearby(self, blasts):
        counts: dict[str, int] = {}
        for b in blasts:
            if not b.get("victim_at"):
                continue
            for k in self.named:
                if k.get("killer_at") and abs(k["at"] - b["at"]) <= 120 \
                        and metres(k["killer_at"], b["victim_at"]) <= self.s["nearby"]:
                    counts[k["killer_name"]] = counts.get(k["killer_name"], 0) + 1
        return [name for name, _ in sorted(counts.items(), key=lambda c: -c[1])][:5]


def plural(n, one, many=None):
    if many is None:
        return f"{n:,} {one}" + ("" if n == 1 else "s")
    return f"{n:,} {one if n == 1 else many}"


def trim(items: deque, before: int):
    while items and (items[0][0] if isinstance(items[0], tuple) else items[0]["at"]) < before:
        items.popleft()
