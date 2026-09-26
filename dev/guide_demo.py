"""A made-up OYB Control for the guide's screenshots: two fake servers, a
few dozen players, a cheater, bans, notes and a finished game in History.
Nothing in it is real.

    .venv/bin/python dev/guide_demo.py [port]

Logins: gazlagom (owner), burd (admin), rookie (moderator), all with the
password guide-demo-1. Then run dev/guide_shots.js to retake the pictures.
"""

import asyncio
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web  # noqa: E402

from bot.storage.account_links import AccountLinks  # noqa: E402
from bot.storage.combat_store import migrate as migrate_combat  # noqa: E402
from dev.fake_rcon import FakeRcon  # noqa: E402
from panel import auth  # noqa: E402
from panel.config import PanelConfig, ServerConfig  # noqa: E402
from panel.db import PanelDB, now  # noqa: E402
from panel.servers import ServerManager  # noqa: E402
from panel.web import create_app  # noqa: E402

PASSWORD = "guide-demo-1"
GB = 1024 ** 3

# name, identity, ip, side
PLAYERS = [
    ("Sgt Havoc", "5f1c2a90-8b1e-4a57-9a3e-2d4b6c8e0f11", "203.0.113.7", "US"),
    ("Pte Rook", "a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d", "198.51.100.4", "US"),
    ("Rook_PS5", "b1c2d3e4-f5a6-4b7c-8d9e-0f1a2b3c4d5e", "198.51.100.4", "US"),
    ("Cpl Fennel", "0e9d8c7b-6a5f-4e3d-8c2b-1a0f9e8d7c6b", "192.0.2.44", "USSR"),
    ("Lt Marsh", "1d2e3f40-5a6b-4c7d-8e9f-0a1b2c3d4e5f", "192.0.2.90", "USSR"),
    ("Kestrel", "2e3f4051-6b7c-4d8e-9f0a-1b2c3d4e5f60", "198.51.100.23", "US"),
    ("Dusty_Boots", "3f405162-7c8d-4e9f-8a1b-2c3d4e5f6071", "203.0.113.61", "USSR"),
    ("Dreadful", "40516273-8d9e-4f0a-9b2c-3d4e5f607182", "100.64.12.9", "USSR"),
]
ON_EU1 = [0, 1, 3, 4, 5, 7]
ON_EU2 = [6, 2]
BANNED = ("Cheater_X", "51627384-9e0f-4a1b-8c3d-4e5f60718293", "100.64.12.9")


def clock(t):
    return time.strftime("%H:%M:%S.000", time.localtime(t))


def person(p):
    return f"{p[0]} (playerID = {PLAYERS.index(p) + 1} | UUID = {p[1]})"


def arrive(t, p, n):
    c = clock(t)
    return (f"{c}  DEFAULT      : BattlEye Server: 'Player #{n} {p[0]} ({p[2]}:2304) connected'\n"
            f"{c}  DEFAULT      : BattlEye Server: 'Player #{n} {p[0]} - BE GUID: {p[1].replace('-', '')}'\n"
            f"{c}   NETWORK      : ### Updating player: PlayerId={n}, Name={p[0]}, rplIdentity=0x{n:x}, IdentityId={p[1]}\n"
            f"{c}   SCRIPT       : INFO: Faction: player {person(p)} has joined faction #AR-Faction_{p[3]} ({p[3]})\n")


def kill(t, killer, victim, metres, zone="Chest", damage="KINETIC", relation="ENEMY", at=(1200, 8800)):
    by = "AI" if killer is None else \
        f"{person(killer)} from {killer[3]} faction who was at that time at <{at[0]}, 30, {at[1]}>"
    return (f"{clock(t)}   SCRIPT       : INFO: KILL {relation}: {person(victim)} from {victim[3]} faction at "
            f"<{4560 + metres}, 30, 5010> was killed by {by} [{metres}m away from the corpse]. "
            f"With last inflicted damage type {damage} to the '{zone}' hit zone\n")


def game_log(start, length, rng, late=()):
    lines = [f"{clock(start)}  ENGINE       : Arma Reforger server\n"]
    for n, p in enumerate(PLAYERS):
        if p not in late:
            lines.append(arrive(start + 60 + n * 45, p, n))
    t = start + 600
    while t < start + length:
        a, b = rng.sample(PLAYERS[:7], 2)
        if a[3] != b[3]:
            lines.append(kill(t, a, b, rng.randint(15, 320), rng.choice(["Chest", "Head", "LThigh", "RArm"])))
        t += rng.randint(40, 160)
        if rng.random() < 0.3:
            lines.append(f"{clock(t)}   ENGINE       : FPS: {rng.uniform(38, 52):.1f}\n")
    for n, p in enumerate(PLAYERS):
        if rng.random() < 0.5:
            lines.append(f"{clock(start + length - 900 + n * 60)}  DEFAULT      : BattlEye Server: "
                         f"'Player #{n} {p[0]} disconnected'\n")
    return lines


def write_game(root, start, lines):
    folder = Path(root, time.strftime("logs_%Y-%m-%d_%H-%M-%S", time.localtime(start)))
    folder.mkdir(parents=True)
    ordered = sorted(lines, key=lambda line: line[:8])
    (folder / "console.log").write_text("".join(ordered))
    (folder / "script.log").write_text("")


def seed_logs(root, rng):
    t = now()
    old = t - 30 * 3600
    write_game(Path(root, "eu1"), old, game_log(old, 5 * 3600, rng))
    live = t - 2 * 3600
    dread, rook, havoc, kestrel = PLAYERS[7], PLAYERS[1], PLAYERS[0], PLAYERS[5]
    lines = [line for line in game_log(live, 2 * 3600 - 300, rng, late=(dread,)) if "disconnected" not in line]
    lines.append(f"{clock(t - 20)}   ENGINE       : FPS: 47.3\n")
    burst = t - 25 * 60
    lines.append(arrive(burst - 240, dread, 7))
    lines += [kill(burst, None, v, 0, zone, "FRAGMENTATION", "KILLED_BY_NEUTRAL_OR_FACTIONLESS")
              for v, zone in ((rook, "Head"), (havoc, "Head"), (kestrel, "Chest"))]
    lines.append(kill(burst + 50, dread, PLAYERS[2], 70, "Head", at=(4590, 5020)))
    lines += [kill(t - 12 * 60 + n * 60, rook, PLAYERS[4], 30, relation="TK") for n in range(3)]
    write_game(Path(root, "eu1"), live, lines)
    write_game(Path(root, "eu2"), t - 3 * 3600, game_log(t - 3 * 3600, 3 * 3600 - 200, rng))


def seed_bot(data):
    data.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(data / "playtime.sqlite3") as db:
        db.execute("CREATE TABLE totals (server TEXT, identity TEXT, name TEXT, seconds REAL NOT NULL, PRIMARY KEY(server, identity))")
        db.execute("CREATE TABLE global_time(identity TEXT PRIMARY KEY, milliseconds INTEGER NOT NULL)")
        for n, p in enumerate(PLAYERS):
            db.execute("INSERT INTO totals VALUES (?, ?, ?, ?)", ("eu1", p[1], p[0], 36000 + n * 9000))
            db.execute("INSERT INTO global_time VALUES (?, ?)", (p[1], (36000 + n * 9000) * 1000))
    links = AccountLinks(str(data / "account_links.sqlite3"))
    migrate_combat(links.db)
    with links.db:
        for n, p in enumerate(PLAYERS[:5]):
            links.db.execute("INSERT INTO account_links VALUES (?, ?, ?, 0, 'demo')",
                             (n + 1, 391843351495700000 + n, p[1]))
        for n, p in enumerate(PLAYERS[:7]):
            links.db.execute("INSERT INTO combat_totals VALUES (?, ?, ?, ?, '2026-09-26')",
                             (p[1], 140 + n * 37, 120 + n * 21, n % 3))
    links.db.close()


def seed_panel(db, config):
    for name, role in (("gazlagom", "owner"), ("burd", "admin"), ("rookie", "moderator")):
        db.add_user(name, auth.hash_password(PASSWORD), role)
    t = now()
    for server in ("eu1", "eu2"):
        for n in range(24 * 12):
            at = t - n * 300
            rss = int((1.3 + 0.25 * (1 - (n % 72) / 72)) * GB)
            db.db.execute("INSERT OR REPLACE INTO memory_samples (server, at, rss) VALUES (?, ?, ?)", (server, at, rss))
        db.db.execute("INSERT INTO baselines (server, pid, started, rss) VALUES (?, ?, ?, ?)",
                      (server, 4242 if server == "eu1" else 4343, t - 2 * 3600, int(1.2 * GB)))
        db.add_event(server, "started", "", at=t - 2 * 3600)
        db.add_event(server, "stopped", "by an admin", at=t - 2 * 3600 - 20)
    db.add_event("eu1", "crashed", "", at=t - 4 * 86400)
    db.db.commit()
    name, identity, ip = BANNED
    db.saw_player(identity, name, "eu1", at=t - 3 * 86400)
    db.db.execute("INSERT INTO connections (identity, ip, guid, name, server, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (identity, ip, "0" * 32, name, "eu1", t - 3 * 86400, t - 3 * 86400))
    ban = db.add_ban(identity, name, "Spawning explosions", "burd", None)
    db.add_ip_bans(ban, [ip])
    db.log("burd", "ban", "", name, "Permanent — Spawning explosions")
    db.add_ban(PLAYERS[6][1], PLAYERS[6][0], "Teamkilling at main base", "gazlagom", t + 20 * 3600)
    db.log("gazlagom", "ban", "", PLAYERS[6][0], "1 day — Teamkilling at main base")
    db.add_note(PLAYERS[1][1], "burd", "Warned for teamkilling at the airfield. Next time is a 1 day ban.")
    db.add_note(PLAYERS[7][1], "gazlagom", "Named in two explosion flags. Spectate when he's on.")
    db.log("rookie", "kick", "eu1", "Pte Rook", "")
    db.log("gazlagom", "restart mission", "eu1", "", "")
    for n, at in enumerate((t - 5 * 86400, t - 2 * 86400)):
        db.add_incident("eu1" if n else "eu2", at, "3 players killed by explosions credited to AI in the same second",
                        [{"identity": PLAYERS[7][1], "name": PLAYERS[7][0]}, {"identity": PLAYERS[0][1], "name": "Sgt Havoc"}])


async def main(port):
    rng = random.Random(7)
    root = Path(tempfile.mkdtemp(prefix="oyb-guide-"))
    seed_logs(root / "logs", rng)
    seed_bot(root / "bot")
    loop = asyncio.get_running_loop()
    rcons = {}
    for server, picks, rcon_port in (("eu1", ON_EU1, 19991), ("eu2", ON_EU2, 19992)):
        players = [(str(n), PLAYERS[i][0], PLAYERS[i][1]) for n, i in enumerate(picks)]
        rcons[server] = FakeRcon("demo", players)
        await loop.create_datagram_endpoint(lambda s=server: rcons[s], local_addr=("127.0.0.1", rcon_port))
    servers = [
        ServerConfig("eu1", "OYB #1 Everon", rcon_port=19991, rcon_password="demo", service="reforger-eu1",
                     log_dir=str(root / "logs" / "eu1")),
        ServerConfig("eu2", "OYB #2 Arland", rcon_port=19992, rcon_password="demo", service="reforger-eu2",
                     log_dir=str(root / "logs" / "eu2")),
        ServerConfig("eu3", "OYB #3 Training", rcon_port=19993, rcon_password="demo"),
    ]
    config = PanelConfig(database=str(root / "data" / "panel.sqlite3"), cookie_secure=False, poll_seconds=2,
                         oyb_data=str(root / "bot"), servers=servers)
    db = PanelDB(config.database)
    seed_panel(db, config)

    async def sampler(unit):
        pid = 4242 if unit == "reforger-eu1" else 4343
        return {"pid": pid, "rss": int(1.45 * GB), "age": now() - (now() - 2 * 3600)}

    manager = ServerManager(config, db, sampler=sampler)
    runner = web.AppRunner(create_app(config, db, manager))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"Demo panel on http://127.0.0.1:{port} (data in {root})", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8099))
