"""Canonical OYB progression, independent of Discord and rendering."""
from dataclasses import dataclass

SECONDS_PER_XP = 600
XP_PER_POST = 1
# Chat is a trickle, not a ladder: without a cap a member can post their way to
# the top rank in an evening.
POSTS_PER_DAY = 10
XP_PER_KILL = 2
XP_PER_TEAMKILL = -2
XP_PER_MATCH = 5


@dataclass(frozen=True)
class Rank:
    name: str
    threshold: int
    @property
    def slug(self):
        return self.name.lower()
    @property
    def role_name(self):
        return "OYB " + self.name


# The first steps stay small so a new player moves off Renegade on their first
# night; the gaps widen so the senior ranks stay worth holding.
RANKS = tuple(Rank(name, threshold) for name, threshold in (
    ("Renegade", 0), ("Recruit", 100), ("Private", 250), ("Corporal", 450),
    ("Sergeant", 700), ("Lieutenant", 1000), ("Captain", 1400), ("Major", 1900)))


def xp_from_seconds(seconds):
    return max(0, int(seconds // SECONDS_PER_XP))


@dataclass(frozen=True)
class RankProgress:
    xp: int
    tier: int
    @property
    def current(self):
        return RANKS[self.tier]
    @property
    def next(self):
        return RANKS[self.tier+1] if self.tier+1 < len(RANKS) else None
    @property
    def fraction(self):
        return (self.xp-self.current.threshold)/(self.next.threshold-self.current.threshold) if self.next else 1.0
    @property
    def remaining(self):
        return self.next.threshold-self.xp if self.next else None


def rank_for_xp(xp):
    xp = max(0, int(xp))
    return RankProgress(xp, max(i for i, rank in enumerate(RANKS) if xp >= rank.threshold))
