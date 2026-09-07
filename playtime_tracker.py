"""Experimental, log-backed connected time. No Discord roles or XP are awarded."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3

LOG = logging.getLogger("reforger.playtime")
STAMP = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})")
JOIN = re.compile(r"### Updating player: PlayerId=\d+, Name=(.*?), rplIdentity=(0x[0-9a-fA-F]+), IdentityId=([0-9a-fA-F-]{36})")
LEAVE = re.compile(r"ServerImpl event: disconnected \(identity=(0x[0-9a-fA-F]+)")
COUNT = re.compile(r"FPS:.*?\bPlayer:\s*(\d+)")


class Tracker:
    def __init__(self, log_dir, database, server="server-1", max_gap=120):
        self.root = Path(log_dir).resolve()
        self.server = server
        self.max_gap = max_gap
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, timeout=10)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS totals (
                server TEXT, identity TEXT, name TEXT, seconds REAL NOT NULL,
                PRIMARY KEY(server, identity));
            CREATE TABLE IF NOT EXISTS sources (
                server TEXT, path TEXT, position INTEGER, state TEXT, anchor TEXT,
                PRIMARY KEY(server, path));
        ''')

    def close(self):
        self.db.close()

    def tick(self):
        paths = sorted(self.root.glob("logs_*/console.log"))
        if not paths and (self.root / "console.log").is_file():
            paths = [self.root / "console.log"]
        if not paths:
            return
        first = self.db.execute("SELECT MIN(path) FROM sources WHERE server=?",
                                (self.server,)).fetchone()[0]
        # First activation imports only the current server run. Subsequent runs
        # include intervening rotations, including while the tracker was stopped.
        first = first or str(paths[-1])
        for path in paths:
            if str(path) >= first:
                self.scan(path)

    def scan(self, path):
        # Cursor, parser state, and totals commit together. Concurrent readers
        # cannot cause duplicate awards, and a crash rolls the whole batch back.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT position,state,anchor FROM sources WHERE server=? AND path=?",
                (self.server, str(path))).fetchone()
            position, state, anchor = (row[0], json.loads(row[1]), row[2]) if row else (
                0, {"clock": None, "day": 0, "last": None, "active": {}}, "")
            with path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, position - 128))
                old = stream.read(min(position, 128))
                if size < position or (anchor and hashlib.sha256(old).hexdigest() != anchor):
                    LOG.error("Log rewritten/truncated; refusing replay to prevent duplicate time: %s", path)
                    return
                stream.seek(position)
                for _ in range(5000):
                    raw = stream.readline()
                    if not raw or not raw.endswith(b"\n"):
                        break  # Leave partial writes for the next poll.
                    position = stream.tell()
                    self.consume(state, raw.decode("utf-8", "replace"))
                stream.seek(max(0, position - 128))
                anchor = hashlib.sha256(stream.read(min(position, 128))).hexdigest()
            self.db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)",
                            (self.server, str(path), position, json.dumps(state), anchor))

    def consume(self, state, line):
        match = STAMP.match(line)
        if not match:
            return
        h, m, s, ms = map(int, match.groups())
        clock = h * 3600 + m * 60 + s + ms / 1000
        if state["clock"] is not None and state["clock"] - clock > 43200:
            state["day"] += 86400
        state["clock"] = clock
        now = clock + state["day"]
        join, leave, count = JOIN.search(line), LEAVE.search(line), COUNT.search(line)
        if not (join or leave or count):
            return
        active = state["active"]
        last = state["last"]
        delta = 0 if last is None else now - last
        # A mismatched count means at least one lifecycle event is missing.
        # Drop inferred connections until explicit player mappings reappear.
        mismatch = count is not None and int(count[1]) != len(set(active.values()))
        if mismatch:
            if active:
                LOG.warning("Player count mismatch; clearing uncertain connections")
            active.clear()
        elif 0 < delta <= self.max_gap:
            for identity in set(active.values()):
                self.db.execute("UPDATE totals SET seconds=seconds+? WHERE server=? AND identity=?",
                                (delta, self.server, identity))
        elif delta > self.max_gap:
            LOG.warning("Skipping %.0fs gap without player evidence", delta)
            active.clear()
        state["last"] = max(now, last) if last is not None else now
        if leave:
            active.pop(leave[1].lower(), None)
        if join:
            name, connection, identity = join.groups()
            identity = identity.lower()
            active[connection.lower()] = identity
            self.db.execute('''INSERT INTO totals VALUES (?,?,?,0)
                ON CONFLICT(server,identity) DO UPDATE SET name=excluded.name''',
                (self.server, identity, name))
            LOG.info("Tracking player %s on %s", name, self.server)

    async def run(self):
        LOG.info("Playtime test tracker started for %s; database checkpoints enabled", self.server)
        while True:
            try:
                self.tick()
            except (OSError, sqlite3.Error):
                LOG.exception("Playtime scan failed; retrying")
            await asyncio.sleep(2)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["watch", "report"])
    parser.add_argument("--database", default=os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"))
    parser.add_argument("--server", default=os.getenv("PLAYTIME_SERVER_ID", "server-1"))
    parser.add_argument("--log-dir", default=os.getenv("REFORGER_LOG_DIR"))
    args = parser.parse_args()
    if args.command == "report":
        if not Path(args.database).is_file():
            parser.error("No playtime database yet; start the tracker first")
        db = sqlite3.connect(Path(args.database).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT name,seconds FROM totals WHERE server=? ORDER BY seconds DESC",
                              (args.server,)).fetchall()
            print("Recorded connected time — " + args.server)
            for name, seconds in rows:
                print(f"{ascii(name)}: {int(seconds)//60}m {int(seconds)%60:02d}s")
            if not rows:
                print("No timestamped player mappings found yet. Join the test server.")
        finally:
            db.close()
        return
    if not args.log_dir:
        parser.error("Set REFORGER_LOG_DIR or --log-dir")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    tracker = Tracker(args.log_dir, args.database, args.server)
    try:
        asyncio.run(tracker.run())
    except KeyboardInterrupt:
        pass
    finally:
        tracker.close()


if __name__ == "__main__":
    main()
