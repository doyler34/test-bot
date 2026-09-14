#!/usr/bin/env python3
"""One-shot match-monitor diagnostic.

Reads your servers exactly like the bot does and prints, per server, whether it
sees a live match and WHY — so we can root-cause 'stuck on Waiting' without
guessing. Run on the bot host:

    cd /root/test-bot && python3 dev/diagnose_monitor.py

Optionally pass log dirs directly: python3 dev/diagnose_monitor.py /path/to/logs
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402
from bot.tracking.reforger_monitor import resolve_session_file, parse_line, LineEvent  # noqa: E402

STATE_TXT = re.compile(r"OnGameStateChanged\s*=\s*([A-Za-z]+)")
FPS_TXT = re.compile(r"\bFPS:\s*[0-9.]+")


def server_dirs():
    load_dotenv()
    found = []
    cfg = os.getenv("SERVERS_CONFIG")
    if cfg and Path(cfg).is_file():
        for entry in json.loads(Path(cfg).read_text(encoding="utf-8")):
            if entry.get("enabled") and entry.get("log_dir"):
                found.append((entry.get("name") or entry.get("id"), os.path.expandvars(entry["log_dir"])))
    if not found and os.getenv("REFORGER_LOG_DIR"):
        found.append(("REFORGER_LOG_DIR", os.getenv("REFORGER_LOG_DIR")))
    return found


def inspect(name, log_dir):
    print(f"\n=== {name} :: {log_dir} ===")
    path = resolve_session_file(log_dir)
    if not path:
        print("  no console.log found (server not started / logs not shipped)")
        return
    age = time.time() - os.path.getmtime(path)
    print(f"  newest log : {path}")
    print(f"  written    : {age:.0f}s ago  ({'FRESH' if age < 120 else 'STALE'})")

    game = post = beats = 0
    live_open = False
    raw_states = []
    raw_fps = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            state = "OnGameStateChanged" in line
            fps = "FPS:" in line
            if not (state or fps):
                continue
            parsed = parse_line(line)
            if parsed:
                if parsed.event is LineEvent.GAME_START:
                    game += 1
                    live_open = True
                elif parsed.event is LineEvent.GAME_END:
                    post += 1
                    live_open = False
                elif parsed.event is LineEvent.HEARTBEAT:
                    beats += 1
            if state:
                m = STATE_TXT.search(line)
                raw_states.append((line.split()[0] if line.split() else "?", m.group(1) if m else "?"))
            if fps and raw_fps is None:
                raw_fps = line.strip()[:100]

    print(f"  parser saw : GAME={game}  POSTGAME={post}  heartbeats={beats}")
    print(f"  VERDICT    : {'LIVE (open match)' if live_open else 'WAITING (last state was POSTGAME / no GAME)'}")
    print(f"  raw state-change lines (any format): {len(raw_states)}")
    for clock, state in raw_states[-5:]:
        print(f"       {clock}  = {state}")
    print(f"  raw FPS line: {raw_fps or 'NONE — this build writes no heartbeat'}")


targets = server_dirs()
for arg in sys.argv[1:]:
    targets.append((Path(arg).name, arg))
if not targets:
    print("No servers found. Set SERVERS_CONFIG/REFORGER_LOG_DIR or pass a log dir.")
for name, directory in targets:
    inspect(name, directory)
