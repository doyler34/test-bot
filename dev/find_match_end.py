#!/usr/bin/env python3
"""What does a match actually write when it ends?

Dumps the lines around every POSTGAME transition, and anything in the log that
looks like it names a winner. Run on a host that has the real server logs:

    cd ~/Arma-bot && python3 dev/find_match_end.py

Optionally pass log dirs directly: python3 dev/find_match_end.py /path/to/logs
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

WINNER = re.compile(
    r"win|won|victor|defeat|lost|loser|outcome|endgame|game over|gameover|"
    r"SCR_GameModeEnd|EGameOver|scored|score:|result", re.I)
NOISE = re.compile(r"\bFPS:|UpdateEntities|rpl::|Streaming\(|INFO: KILL ")


def sessions(directory):
    return sorted(Path(directory).glob("logs_*/console.log"))


def server_dirs():
    load_dotenv()
    found = []
    config = os.getenv("SERVERS_CONFIG")
    if config and Path(config).is_file():
        for entry in json.loads(Path(config).read_text(encoding="utf-8")):
            if entry.get("enabled") and entry.get("log_dir"):
                found.append((entry.get("name") or entry.get("id"), os.path.expandvars(entry["log_dir"])))
    if not found and os.getenv("REFORGER_LOG_DIR"):
        found.append(("REFORGER_LOG_DIR", os.getenv("REFORGER_LOG_DIR")))
    return found


def inspect(name, directory):
    print(f"\n=== {name} :: {directory} ===")
    logs = sessions(directory)
    if not logs:
        print("  no console.log found")
        return
    ends = hits = 0
    for path in logs[-6:]:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines):
            if "OnGameStateChanged" in line and "POSTGAME" in line:
                ends += 1
                print(f"\n--- {path.name} line {number}: a match ended here ---")
                for near in lines[max(0, number - 12):number + 26]:
                    if not NOISE.search(near):
                        print(f"    {near.strip()[:200]}")
        for line in lines:
            if WINNER.search(line) and not NOISE.search(line):
                hits += 1
                if hits <= 25:
                    print(f"  [winner?] {line.strip()[:200]}")
    print(f"\n  {ends} match endings seen, {hits} lines mentioning a result")
    if not hits:
        print("  Nothing names a winner. Vanilla does not record one.")


targets = server_dirs()
for argument in sys.argv[1:]:
    targets.append((Path(argument).name, argument))
if not targets:
    print("No servers found. Set SERVERS_CONFIG/REFORGER_LOG_DIR or pass a log dir.")
for name, directory in targets:
    inspect(name, directory)
