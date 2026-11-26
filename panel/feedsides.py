import re

# The game's names for the sides that get a chip of their own. Anyone who
# hasn't picked one, or picked something else (civilians), gets the pink one.
SIDE_CHIPS = {"US": "us", "USSR": "ussr", "FIA": "fia"}

JOINED = re.compile(r"(.+) joined (\w+)")
CONNECTION = re.compile(r"(.+) (connected|disconnected)")
KILLED_THEMSELVES = re.compile(r"(.+) killed themselves")
KILLED = re.compile(r"(.+?) (killed|teamkilled) (.+?)((?: \(\d+ m\))?(?: · .*)?)")


def sides_from(texts):
    """Each player's newest side from the feed's "<name> joined <side>" rows, given newest first."""
    picked = {}
    for text in texts:
        match = JOINED.fullmatch(text)
        if match:
            picked.setdefault(match[1], match[2])
    return picked


class SidesAt:
    """The side each player was on at one moment, from their side picks over time, so a line from before
    someone switched still shows the side they were on then."""

    def __init__(self, picks, at):
        self.picks, self.at = picks, at

    def get(self, name):
        side = None
        for when, picked in self.picks.get(name, ()):
            if when > self.at:
                break
            side = picked
        return side


def picks_from(rows):
    """{name: [(at, side), ...] oldest first} from the feed's "<name> joined <side>" rows."""
    picks = {}
    for at, text in sorted(rows):
        match = JOINED.fullmatch(text)
        if match:
            picks.setdefault(match[1], []).append((at, match[2]))
    return picks


def player(name, side):
    """A name with its chip; the AI has no side to show."""
    if name == "AI":
        return "", name
    return SIDE_CHIPS.get(side, "none"), name


def feed_parts(kind, text, sides):
    """A feed row's text cut into (chip, text) pieces. The chip is the side to draw before a
    player's name and empty for everything else."""
    if kind in ("join", "leave") and (match := CONNECTION.fullmatch(text)):
        return [player(match[1], sides.get(match[1])), ("", f" {match[2]}")]
    if kind == "side" and (match := JOINED.fullmatch(text)):
        return [player(match[1], match[2]), ("", f" joined {match[2]}")]
    if kind in ("kill", "teamkill"):
        if match := KILLED_THEMSELVES.fullmatch(text):
            return [player(match[1], sides.get(match[1])), ("", " killed themselves")]
        if match := KILLED.fullmatch(text):
            return [player(match[1], sides.get(match[1])), ("", f" {match[2]} "),
                    player(match[3], sides.get(match[3])), ("", match[4])]
    return [("", text)]
