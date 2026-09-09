"""Canonical OYB progression, independent of Discord and rendering."""
from dataclasses import dataclass

SECONDS_PER_XP = 600
XP_PER_POST = 1


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


RANKS = tuple(Rank(name, i * 100) for i, name in enumerate((
    "Renegade", "Recruit", "Private", "Corporal", "Sergeant", "Lieutenant", "Captain", "Major")))


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
