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


# Renegade sits outside the ladder: it is where someone waits until they pick a
# side. Recruit is the real first rung, and the gaps widen from there so the
# senior ranks stay worth holding.
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
        if not self.next:
            return 1.0
        span = self.next.threshold - self.current.threshold
        # A Renegade can be holding more XP than Recruit asks for, so clamp
        # rather than draw a bar past its own end.
        return min(1.0, max(0.0, (self.xp - self.current.threshold) / span))
    @property
    def remaining(self):
        return max(0, self.next.threshold - self.xp) if self.next else None


def rank_for_xp(xp, faction=None):
    """Renegade means not on a side yet, not bottom of the ladder.

    US, USSR or FIA starts you at Recruit and the XP ladder runs from there.
    With no faction the ladder does not apply at all: you are Renegade until
    you pick one, however much XP you are carrying.
    """
    xp = max(0, int(xp))
    if not faction:
        return RankProgress(xp, 0)
    earned = max(i for i, rank in enumerate(RANKS) if xp >= rank.threshold)
    return RankProgress(xp, max(1, earned))
