"""Spots one player teamkilling several people in a short space of time, so
the bot can tell the staff channel straight away."""
from collections import defaultdict, deque

COOLDOWN = 300
FRESH = 300


class TkBurst:
    def __init__(self):
        self.recent = defaultdict(deque)
        self.alerted = {}

    def feed(self, event, count, seconds, now):
        """An alert for this teamkill if it makes `count` within `seconds`, else None."""
        if event.get("kind") != "teamkill" or not event.get("killer") or now - event["at"] > FRESH:
            return None
        killer, at = event["killer"], event["at"]
        kills = self.recent[killer]
        kills.append((at, event.get("victim_name") or "someone"))
        while kills and at - kills[0][0] > seconds:
            kills.popleft()
        if len(kills) < count or at - self.alerted.get(killer, -COOLDOWN) < COOLDOWN:
            return None
        self.alerted[killer] = at
        took = max(1, kills[-1][0] - kills[0][0])
        return {"at": at, "identity": killer, "name": event.get("killer_name") or killer,
                "count": len(kills), "seconds": took, "victims": [v for _, v in kills]}
