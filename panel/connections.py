"""Who connected from where, read from the Reforger console logs.

BattlEye logs the IP when a player connects; the game logs their identity a
moment later under the same name. Pairing the two gives identity, name and IP
for every connection, which is what finds alt accounts sharing an address.
"""

import re
import time
from pathlib import Path

STAMP = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
FOLDER = re.compile(r"logs_(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$")
CONNECT = re.compile(r"BattlEye Server: 'Player #\d+ (.+) \((.+):\d+\) connected'")
GUID = re.compile(r"BattlEye Server: 'Player #\d+ (.+) - BE GUID: ([0-9a-fA-F]{32})'")
IDENTITY = re.compile(r"### Updating player: PlayerId=\d+, Name=(.*?), rplIdentity=0x[0-9a-fA-F]+, "
                      r"IdentityId=([0-9a-fA-F-]{36})")
PENDING_SECONDS = 600


def folder_start(folder: Path) -> float | None:
    match = FOLDER.search(folder.name)
    if not match:
        return None
    return time.mktime(tuple(int(x) for x in match.groups()) + (0, 0, -1))


class LogReader:
    """Follows every logs_*/console.log under one server's log directory."""

    def __init__(self, log_dir: str):
        self.root = Path(log_dir)
        self.files: dict[str, dict] = {}

    def folders(self) -> list[Path]:
        try:
            found = [p for p in self.root.iterdir() if p.is_dir() and folder_start(p) is not None]
        except OSError:
            return []
        return sorted(found, key=folder_start)

    def scan(self, positions: dict[str, int]) -> tuple[list[dict], dict[str, int]]:
        events, moved = [], {}
        for folder in self.folders():
            path = folder / "console.log"
            key = str(path)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            start = positions.get(key, 0)
            if size < start:
                start = 0
            if size == start:
                continue
            state = self.files.setdefault(key, {"base": folder_start(folder), "clock": None, "day": 0, "pending": {}})
            with open(path, "rb") as fh:
                fh.seek(start)
                data = fh.read(size - start)
            end = data.rfind(b"\n") + 1
            if not end:
                continue
            for line in data[:end].decode("utf-8", errors="replace").splitlines():
                event = self._line(line, state)
                if event:
                    events.append(event)
            moved[key] = start + end
        return events, moved

    def _line(self, line: str, state: dict) -> dict | None:
        stamp = STAMP.match(line)
        if not stamp:
            return None
        clock = int(stamp[1]) * 3600 + int(stamp[2]) * 60 + int(stamp[3])
        if state["clock"] is None:
            base = time.localtime(state["base"])
            state["start_clock"] = base.tm_hour * 3600 + base.tm_min * 60 + base.tm_sec
            if clock < state["start_clock"] - 60:
                state["day"] += 1
        elif clock < state["clock"] - 3600:
            state["day"] += 1
        state["clock"] = clock
        at = int(state["base"] - state["start_clock"] + clock + state["day"] * 86400)
        pending = state["pending"]
        if match := CONNECT.search(line):
            pending[match[1]] = {"ip": match[2].strip("[]"), "guid": "", "at": at}
        elif match := GUID.search(line):
            if match[1] in pending:
                pending[match[1]]["guid"] = match[2].lower()
        elif match := IDENTITY.search(line):
            name, identity = match[1], match[2].lower()
            seen = pending.pop(name, None)
            if seen and at - seen["at"] > PENDING_SECONDS:
                seen = None
            return {"identity": identity, "name": name, "at": at,
                    "ip": seen["ip"] if seen else "", "guid": seen["guid"] if seen else ""}
        return None
