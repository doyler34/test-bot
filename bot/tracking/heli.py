"""Which kills came from a helicopter, worked out from vanilla kill lines alone.

The game never names the weapon or vehicle, but it logs where the killer was.
Taking the Everon ground height off that gives how high they were. Over five
weeks of OYB logs, killers more than 20 m up were almost all in a UH-1H or
Mi-8: 83% of those kills were rockets or explosions, 96% of the killers had
taken a heli that game, and between kills they moved at heli speed.

Two things can put someone that high without a heli: a tall tower and a
cliff edge where the 10 m height grid is off. So a kill also needs to be an
explosive one, or the killer has to be moving faster than anyone runs. A
door gunner shooting from a hovering heli with no kill either side is missed.

Everon only: on another map the heights are wrong, so nothing counts.
"""
from collections import Counter
from datetime import datetime

AIRBORNE = 20          # metres above the ground
BLAST = {'EXPLOSIVE', 'FRAGMENTATION', 'INCENDIARY'}
MOVING = 9             # metres a second between two kills
NEIGHBOUR = 90         # seconds: kills further apart than this say nothing about speed
EVERON = 12800


def _moment(occurred):
    return datetime.fromisoformat(occurred).timestamp()


def heli_kills(spots, ground):
    """{killer identity: heli kills} from killer_spots() rows, using the Everon heights."""
    by_killer = {}
    for server, occurred, killer, damage, x, y, z in spots:
        by_killer.setdefault((server, killer), []).append((_moment(occurred), damage, x, y, z))
    counted = Counter()
    for (server, killer), kills in by_killer.items():
        for index, (at, damage, x, y, z) in enumerate(kills):
            if not (0 <= x <= EVERON and 0 <= z <= EVERON):
                continue
            if y - ground.at(x, z) <= AIRBORNE:
                continue
            if damage in BLAST or _moving(kills, index):
                counted[killer] += 1
    return counted


def _moving(kills, index):
    at, _, x, _, z = kills[index]
    for other in (index - 1, index + 1):
        if not 0 <= other < len(kills):
            continue
        then, _, ox, _, oz = kills[other]
        gap = abs(at - then)
        if 0 < gap <= NEIGHBOUR and ((x - ox) ** 2 + (z - oz) ** 2) ** 0.5 / gap > MOVING:
            return True
    return False
