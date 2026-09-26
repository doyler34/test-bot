"""Runs the suspicious-activity checks over a console.log and prints the flags.

    .venv/bin/python dev/scan_sus.py path/to/console.log [more logs...]

Nothing is written anywhere. Settings can be tried out with --set, e.g.
--set same_second=4 --set headshot_share=0.8
"""

import argparse
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from panel.connections import FOLDER, LogReader  # noqa: E402
from panel.suspicion import DEFAULTS, EXPLOSIVE, Detector  # noqa: E402


def scan(path: Path, settings: dict):
    # LogReader works out dates from the logs_YYYY-MM-DD_HH-MM-SS folder name.
    folder = path.parent.name if FOLDER.search(path.parent.name) else "logs_2000-01-01_00-00-00"
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, folder).mkdir()
        Path(tmp, folder, "console.log").symlink_to(path.resolve())
        events, _ = LogReader(tmp).scan({})
    detector = Detector(settings)
    flags = [f for e in events for f in detector.feed(e)] + detector.flush(10 ** 12)
    counts = Counter(e["kind"] for e in events)
    ai_blasts = sum(1 for e in events if e["kind"] in ("kill", "teamkill") and e.get("damage") in EXPLOSIVE
                    and e.get("by_ai"))
    print(f"\n{path}")
    print(f"  {counts['identity']} joins, {counts['kill'] + counts['teamkill']} kills "
          f"({counts['teamkill']} teamkills), {ai_blasts} explosive deaths credited to AI, "
          f"{counts['error']} script errors (NULL pointer / INSTIGATOR_OTHER)")
    if not flags:
        print("  Nothing flagged.")
    for f in sorted(flags, key=lambda f: f["at"]):
        print(f"  {time.strftime('%H:%M:%S', time.localtime(f['at']))}  {f['text']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    args = parser.parse_args()
    settings = {}
    for item in args.set:
        name, _, value = item.partition("=")
        if name not in DEFAULTS:
            parser.error(f"unknown setting {name}; use one of {', '.join(DEFAULTS)}")
        settings[name] = float(value) if "." in value else int(value)
    for path in args.logs:
        if path.is_dir():
            path = path / "console.log"
        if not path.is_file():
            print(f"\n{path}: not found")
            continue
        scan(path, settings)


if __name__ == "__main__":
    main()
