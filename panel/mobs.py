"""Where each side's main operating base is in the game now running, from the line the game writes when it
picks them, and the named places on the Everon map."""
import json
import re
import time
from functools import lru_cache
from pathlib import Path

PLACES = Path(__file__).resolve().parents[1] / "assets" / "mortar" / "everon_places.json"
LINE = re.compile(r"MOBsSelected: SCR_MOBSelectedData\{(.*?)\}")
SIDES = {"US": "US", "USSR": "RU", "FIA": "FIA"}
# The line is written close to the start of a game.
HEAD = 12 * 1024 * 1024
# A game that has only just begun hasn't written it yet, so an empty answer is asked again after this long.
RETRY = 30
_seen = {}


def listed(text, name):
    found = re.search(name + r"=\[([^\]]*)\]", text)
    return [part.strip() for part in found[1].split(",")] if found else []


def parse(text):
    """The main operating bases the game picked: [{"side": "US", "label": "US MOB", "x": ..., "z": ...}]."""
    found = LINE.search(text)
    if not found:
        return None
    body = found[1]
    factions, east, north = listed(body, "MOB_factions"), listed(body, "MOB_xs"), listed(body, "MOB_zs")
    if not factions or not len(factions) == len(east) == len(north):
        return None
    bases = []
    try:
        for faction, x, z in zip(factions, east, north):
            side = SIDES.get(faction.rsplit("_", 1)[-1], faction.rsplit("_", 1)[-1])
            bases.append({"side": side, "label": f"{side} MOB", "x": float(x), "z": float(z)})
    except ValueError:
        return None
    return bases


def read(path, at=None):
    """The bases picked for the game this log belongs to; [] until the game has written them."""
    at = time.monotonic() if at is None else at
    key = str(path)
    checked, bases = _seen.get(key, (None, None))
    if bases or (checked is not None and at - checked < RETRY):
        return bases or []
    try:
        with open(path, "rb") as log:
            bases = parse(log.read(HEAD).decode("utf-8", "replace"))
    except OSError:
        bases = None
    _seen[key] = (at, bases)
    return bases or []


@lru_cache(maxsize=1)
def places():
    """The named bases and towers on Everon, each with where it is."""
    return json.loads(PLACES.read_text(encoding="utf-8"))["places"]
