"""Keeps every finished game's whole log folder and reads past games back.

The game starts a new logs_<date> folder each time a server starts, so every
folder but the newest is a finished game. Each one (console.log, script.log,
error.log, crash.log, whatever is in it) is packed into a .tar.gz in the
panel's own data folder, where it stays however the game or AMP tidies its logs.
"""

import io
import os
import tarfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from .connections import FOLDER, folder_start, read_lines
from .suspicion import Detector


def valid_folder(name: str) -> bool:
    return bool(FOLDER.fullmatch(name))


def pack(folder: Path, out) -> None:
    with tarfile.open(fileobj=out, mode="w:gz", compresslevel=6) as tar:
        tar.add(folder, arcname=folder.name)


def pack_bytes(folder: Path) -> bytes:
    """A game still being played, packed on the spot for a download."""
    buffer = io.BytesIO()
    pack(folder, buffer)
    return buffer.getvalue()


def archive_game(folder: Path, dest: Path) -> dict:
    """Packs one finished game's log folder and returns its summary."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with open(partial, "wb") as out:
        pack(folder, out)
    os.replace(partial, dest)
    return summary(read_game(str(dest), folder.name, dest.stat().st_size, dest.stat().st_mtime, ()))


@contextmanager
def open_log(path: str):
    """console.log as text, from an archived game or a live log folder."""
    if not path.endswith(".tar.gz"):
        with open(Path(path, "console.log"), encoding="utf-8", errors="replace") as fh:
            yield fh
        return
    with tarfile.open(path, "r:gz") as tar:
        member = next((m for m in tar.getmembers() if m.isfile() and Path(m.name).name == "console.log"), None)
        raw = tar.extractfile(member) if member else io.BytesIO()
        yield io.TextIOWrapper(raw, encoding="utf-8", errors="replace")


@lru_cache(maxsize=6)
def read_game(path: str, folder: str, size: int, mtime: float, settings: tuple) -> dict:
    """One game's events, players and flags. Size and mtime are only there so a
    log that is still being written is read again once it grows."""
    started = folder_start(Path(folder))
    with open_log(path) as fh:
        events = read_lines(fh, started)
    detector = Detector(dict(settings))
    flags = [f for e in events for f in detector.feed(e)] + detector.flush(10 ** 12)
    players: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for e in events:
        if e["kind"] == "identity":
            p = players.setdefault(e["identity"], {"identity": e["identity"], "name": e["name"], "ip": "",
                                                   "guid": "", "first": e["at"], "last": e["at"],
                                                   "kills": 0, "deaths": 0, "teamkills": 0})
            p["name"], p["last"] = e["name"], max(p["last"], e["at"])
            p["ip"], p["guid"] = e["ip"] or p["ip"], e["guid"] or p["guid"]
            by_name[e["name"]] = p
        elif e["kind"] == "leave" and e.get("name") in by_name:
            by_name[e["name"]]["last"] = e["at"]
        elif e["kind"] in ("kill", "teamkill") and e.get("victim"):
            killer = players.get(e.get("killer") or "")
            if killer and e["killer"] != e["victim"]:
                killer["teamkills" if e["kind"] == "teamkill" else "kills"] += 1
            if e["victim"] in players:
                players[e["victim"]]["deaths"] += 1
    feed = [e for e in events if e.get("text")]
    feed += [{"at": f["at"], "kind": "sus", "text": f["text"], "ip": ""} for f in flags]
    feed.sort(key=lambda e: e["at"])
    ended = max((e["at"] for e in events), default=started)
    return {"started": started, "ended": ended, "players": sorted(players.values(), key=lambda p: p["first"]),
            "feed": feed, "flags": flags,
            "kills": sum(e["kind"] == "kill" and bool(e.get("victim")) and e.get("killer") != e["victim"]
                         for e in events),
            "teamkills": sum(e["kind"] == "teamkill" for e in events)}


def summary(game: dict) -> dict:
    return {"started": game["started"], "ended": game["ended"], "players": len(game["players"]),
            "kills": game["kills"], "flags": len(game["flags"])}
