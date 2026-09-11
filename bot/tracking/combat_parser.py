"""Parse vanilla SCR_BaseGameMode.LogKillEvent records, not performance stats."""
from dataclasses import dataclass
import re
import uuid

HEADER = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d{3})\s+SCRIPT\s*(?:\(W\))?\s*:\s*(?:INFO|WARNING): KILL ([A-Z_]+): (.*)$')
PERSON = re.compile(r'^(.*?) \(playerID = [1-9]\d* \| UUID = ([0-9a-fA-F-]{36})\)')


@dataclass(frozen=True)
class KillEvent:
    clock: str
    relation: str
    victim: str
    killer: str | None


def person(text):
    match = PERSON.match(text)
    if not match:
        return None
    # Ambiguous identity-like text inside a display name must not select an ID.
    if 'UUID =' in match[1]:
        return None
    try:
        identity = str(uuid.UUID(match[2]))
    except ValueError:
        return None
    if identity == str(uuid.UUID(int=0)):
        return None
    return identity, text[match.end():]


def parse_kill(line):
    match = HEADER.fullmatch(line.rstrip('\r\n'))
    if not match:
        return None
    clock, relation, body = match.groups()
    h,m,s = map(int, clock[:8].split(':'))
    if h > 23 or m > 59 or s > 59:
        return None
    victim = person(body)
    if not victim:
        return None
    identity, rest = victim
    if ' killed himself!' in rest and ' was killed by ' not in rest:
        if 'UUID =' in rest:
            return None
        return KillEvent(clock, relation, identity, identity)
    pieces = rest.split(' was killed by ')
    if len(pieces) != 2 or 'UUID =' in pieces[0]:
        return None
    killer_text = pieces[1]
    if re.match(r'^AI(?:\s|$)', killer_text) and 'UUID =' not in killer_text:
        return KillEvent(clock, relation, identity, None)
    killer = person(killer_text)
    if not killer or 'UUID =' in killer[1]:
        return None
    return KillEvent(clock, relation, identity, killer[0])
