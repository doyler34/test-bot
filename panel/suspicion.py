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
"""

import math
from collections import defaultdict, deque

EXPLOSIVE = {"EXPLOSIVE", "FRAGMENTATION", "INCENDIARY"}

DEFAULTS = {
    "ai_explosions": 5,           # explosive deaths credited to AI ...
    "ai_explosions_window": 300,  # ... within this many seconds
    "same_second": 3,             # players killed by AI explosions in the same second
    "rapid_kills": 6,             # kills by one player ...
    "rapid_window": 30,           # ... within this many seconds
    "headshot_kills": 10,         # at least this many rifle kills in 15 minutes ...
    "headshot_share": 0.75,       # ... with this share of them to the head
    "teamkills": 3,               # teamkills by one player in 10 minutes
    "script_errors": 50,          # NULL script errors in 5 minutes
    "max_distance": 2000,         # kills credited from further than this are ignored
    "nearby": 150,                # metres: a named killer this close to the explosions is named
    "joined_before": 600,         # seconds: players who connected this long before are named
    "cooldown": 600,              # seconds before the same flag fires again
    "settle": 120,                # seconds a burst waits, so killers nearby just after count too
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

    def feed(self, event: dict) -> list[dict]:
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
        at, s = e["at"], self.s
        if e.get("damage") == "COLLISION" or (e.get("distance") or 0) > s["max_distance"]:
            return []
        if e.get("killer") == e["victim"]:
            return []
        flags = []
        if e.get("damage") in EXPLOSIVE and (e.get("killer") is None or "NEUTRAL" in e.get("relation", "")):
            self.ai_blasts.append(e)
            trim(self.ai_blasts, at - s["ai_explosions_window"])
            same = [b for b in self.ai_blasts if b["at"] == at]
            if len(same) >= s["same_second"]:
                flags += self._flag(("same_second",), at,
                                    f"{len(same)} players killed by explosions credited to AI in the same second",
                                    self.ai_blasts)
            if len(self.ai_blasts) >= s["ai_explosions"]:
                window = s["ai_explosions_window"] // 60
                flags += self._flag(("ai_explosions",), at,
                                    f"{len(self.ai_blasts)} explosive deaths credited to AI in {window} min "
                                    "(these don't show in the kill feed)", self.ai_blasts)
            return flags
        if not e.get("killer") or e.get("killer_name") is None:
            return []
        self.named.append(e)
        trim(self.named, at - 600)
        mine = self.kills[e["killer"]]
        mine.append(e)
        trim(mine, at - 900)
        who = e["killer_name"]
        if e["kind"] == "teamkill":
            tks = [k for k in mine if k["kind"] == "teamkill" and k["at"] >= at - 600]
            if len(tks) >= s["teamkills"]:
                flags += self._flag(("teamkills", e["killer"]), at, f"{who}: {len(tks)} teamkills in 10 min",
                                    identity=e["killer"])
            return flags
        rifle = [k for k in mine if k["kind"] == "kill" and k.get("damage") == "KINETIC"]
        rapid = [k for k in rifle if k["at"] >= at - s["rapid_window"]]
        if len(rapid) >= s["rapid_kills"]:
            flags += self._flag(("rapid", e["killer"]), at, f"{who}: {len(rapid)} kills in {s['rapid_window']} s",
                                identity=e["killer"])
        heads = sum(k.get("zone") == "Head" for k in rifle)
        if len(rifle) >= s["headshot_kills"] and heads / len(rifle) >= s["headshot_share"]:
            flags += self._flag(("headshots", e["killer"]), at,
                                f"{who}: {heads} of {len(rifle)} kills were headshots in 15 min",
                                identity=e["killer"])
        return flags

    def _error(self, e):
        self.errors.append(e["at"])
        trim_times(self.errors, e["at"] - 300)
        if len(self.errors) >= self.s["script_errors"]:
            return self._flag(("errors",), e["at"], f"{len(self.errors)} NULL / INSTIGATOR_OTHER script errors in 5 min",
                              [{"at": self.errors[0]}])
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
            details.append(f"{heads} of {len(blasts)} to the head")
        joined = self._joined_before(blasts[0]["at"], {b.get("victim") for b in blasts})
        if joined:
            details.append("joined just before: " + ", ".join(joined))
        if blasts[0].get("victim"):
            near = self._nearby(blasts)
            if near:
                details.append("killing nearby: " + ", ".join(near))
        if details:
            text += ". " + "; ".join(details)
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


def trim(items: deque, before: int):
    while items and (items[0][0] if isinstance(items[0], tuple) else items[0]["at"]) < before:
        items.popleft()


def trim_times(items: deque, before: int):
    while items and items[0] < before:
        items.popleft()
