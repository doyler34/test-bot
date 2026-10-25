"""Spots a jump in the box's TCP resends, which means packets leaving the box are being lost on the way out."""
import statistics
from collections import deque

FLOOR = 40          # resends a minute; ordinary is under 20
FACTOR = 4          # and at least this many times the usual
IN_A_ROW = 2
COOLDOWN = 900
HISTORY = 240       # an hour of 15 second readings


class ResendWatch:
    def __init__(self):
        self.seen = deque(maxlen=HISTORY)
        self.high = 0
        self.last = {}
        self.alerted = -COOLDOWN

    def feed(self, value, at):
        """An alert dict when resends have jumped and stayed up, else None."""
        if value is None:
            return None
        usual = statistics.median(self.seen) if len(self.seen) >= 20 else None
        self.seen.append(value)
        if usual is None:
            return None
        if value >= max(FLOOR, FACTOR * usual):
            self.high += 1
            self.last = {"peak": max(value, self.last.get("peak", 0)) if self.high > 1 else value, "usual": usual}
        else:
            self.high = 0
            return None
        if self.high < IN_A_ROW or at - self.alerted < COOLDOWN:
            return None
        self.alerted = at
        return {"at": at, "peak": self.last["peak"], "usual": self.last["usual"]}
