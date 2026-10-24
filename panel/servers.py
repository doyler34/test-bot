import asyncio
import json
import logging
import os
import re
import shutil
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import PanelConfig, ServerConfig
from . import alerts as alert_colours, archive, attacks, memory, monitor, netcheck
from collections import Counter
from .alerts import Alerts
from .connections import LogReader
from .db import PanelDB, now
from .rcon import RconClient, RconError
from bot.discord import welcome_doc
from bot.discord.ban_roles import length_text
from .suspicion import DEFAULTS, Detector, check_settings
from .tk_burst import TkBurst

PLAYER_ROW = re.compile(r"\d+\s*;\s*[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}\s*;")

log = logging.getLogger("panel.servers")

UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
POWER_RCON = ("restart_mission", "shutdown")
POWER_SERVICE = ("start", "stop", "restart")
# Only act on what happened just now, never on old log lines read back after a restart.
AUTO_FRESH = 300
AUTO_KICK_GAP = 600


def valid_identity(text: str) -> bool:
    return re.fullmatch(UUID, text or "") is not None


def clean(text: str, limit=200) -> str:
    return " ".join(str(text).split())[:limit]


def parse_players(output: str) -> list[dict]:
    players = []
    for line in output.splitlines():
        match = re.match(r"^\s*#?(\d+)\b[\s;:,|.)-]*(.*)$", line)
        if not match:
            continue
        rest = match.group(2)
        identity = re.search(UUID, rest)
        name = re.sub(UUID, " ", rest) if identity else rest
        name = clean(name.strip(" \t;:,|-"), 64)
        if not name:
            continue
        players.append({"id": match.group(1), "name": name,
                        "identity": identity.group(0).lower() if identity else ""})
    return players


@dataclass
class ServerState:
    config: ServerConfig
    client: RconClient | None = None
    online: bool = False
    error: str = ""
    players: list = field(default_factory=list)
    raw_players: str = ""
    updated: int = 0
    online_since: int = 0
    pid: int = 0
    memory: int = 0
    process_age: int = 0
    fresh: int = 0
    check: dict | None = None
    ping_ms: int = 0
    fps: float = 0.0
    fps_at: int = 0
    expected_until: int = 0
    last_sample: int = 0
    was_online: bool | None = None
    detector: Detector = field(default_factory=Detector)
    recent: dict = field(default_factory=dict)
    running: dict = field(default_factory=dict)
    tk_burst: TkBurst = field(default_factory=TkBurst)
    punished: dict = field(default_factory=dict)
    admins: list = field(default_factory=list)
    # Since the last Monitor reading: disconnects by reason, and script errors.
    disconnects: Counter = field(default_factory=Counter)
    errors: int = 0
    cpu_ticks: dict | None = None
    sock_drops: dict | None = None
    # The game program found by its folder, for servers without a service set.
    found_pid: int = 0
    unreachable: int = 0
    query: dict = field(default_factory=dict)
    # When each player now on was first seen.
    joined: dict = field(default_factory=dict)


# How often the Monitor page gets a reading.
MONITOR_SECONDS = 15
# A ban or unban the game answers with one of these did not take. It is tried again after this long, not every poll.
GAME_REFUSED = re.compile(r"\b(fail(ed|ure)?|error|invalid|unknown|not found|cannot|can't|unable|denied)\b", re.I)
REFUSED_RETRY = 600
# Unanswered outside checks in a row before it counts as unreachable.
UNREACHABLE_AFTER = 4
# Failed logins from one address, within ATTACK_WINDOW seconds, that count as an attack.
ATTACK_LOGINS = 10
ATTACK_WINDOW = 600


class ServerManager:
    def __init__(self, config: PanelConfig, db: PanelDB, client_factory=RconClient, sampler=memory.sample_service,
                 alerts: Alerts | None = None):
        self.config = config
        self.db = db
        self.alerts = alerts or Alerts(config.discord_webhook)
        self.client_factory = client_factory
        self.sampler = sampler
        self.settle = memory.SETTLE_SECONDS
        self.box_total = memory.box_total()
        self.states = {s.id: ServerState(s, detector=Detector(config.suspicion)) for s in config.servers}
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._ip_kicked: dict[str, int] = {}
        self._refused_at: dict[tuple[int, str], int] = {}
        self._ban_lock = asyncio.Lock()
        self._config_admins: dict[str, tuple] = {}

    def start(self):
        for state in self.states.values():
            if state.config.configured:
                self._tasks.append(asyncio.create_task(self._run(state)))
            else:
                state.error = "RCON is not set up in panel.local.json"
        if any(s.config.service for s in self.states.values()):
            self._tasks.append(asyncio.create_task(self._watch_memory()))
        self._tasks.append(asyncio.create_task(self._watch_monitor()))
        self._tasks.append(asyncio.create_task(self._watch_attacks()))
        for state in self.states.values():
            if state.config.log_dir:
                self._tasks.append(asyncio.create_task(self._watch_logs(state)))

    async def stop(self):
        # On 3.11 a cancel that lands as a wait_for finishes can be swallowed,
        # so the loops also check _stopping and we keep cancelling until they end.
        self._stopping = True
        while self._tasks:
            for task in self._tasks:
                task.cancel()
            _, pending = await asyncio.wait(self._tasks, timeout=1)
            self._tasks = list(pending)
        for state in self.states.values():
            if state.client:
                state.client.close()
        await self.alerts.close()

    async def _run(self, state: ServerState):
        delay = 5
        while not self._stopping:
            try:
                if not (state.client and state.client.connected):
                    await self._connect(state)
                    delay = 5
                await self.refresh_players(state)
                await self.sync_bans(state)
                await self.enforce_ip_bans(state)
                await asyncio.sleep(self.config.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if state.online:
                    log.warning("%s went offline: %s", state.config.id, exc)
                self._offline(state, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _watch_memory(self):
        while not self._stopping:
            for state in self.states.values():
                if state.config.service:
                    try:
                        await self.refresh_memory(state)
                    except Exception:
                        log.exception("memory check failed for %s", state.config.id)
            await asyncio.sleep(self.config.poll_seconds)

    async def _watch_monitor(self, proc_root="/proc"):
        before = await asyncio.to_thread(monitor.read_box, proc_root)
        due = time.monotonic()
        beat = 0
        while not self._stopping:
            # Keep to the beat: a slow outside check mustn't push every later reading back.
            due += MONITOR_SECONDS
            await asyncio.sleep(max(due - time.monotonic(), 0))
            try:
                after = await asyncio.to_thread(monitor.read_box, proc_root)
                self.db.add_box_sample(monitor.box_rates(before, after))
                before = after
                if beat % 4 == 0:
                    await self.find_programs(proc_root)
                beat += 1
                await asyncio.gather(*(self.monitor_server(state, proc_root) for state in self.states.values()))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("monitor reading failed")

    async def _watch_attacks(self, reader=None):
        reader = reader or attacks.Reader()
        self.attack_reader = reader
        reported: dict[str, float] = {}
        while not self._stopping:
            try:
                await self.check_attacks(reader, reported)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("reading failed logins failed")
            await asyncio.sleep(30)

    async def check_attacks(self, reader, reported):
        """Stores new failed logins and reports any address that's hammering a login."""
        found = await asyncio.to_thread(reader.read)
        if found:
            self.db.add_attempts(found)
        since = now() - ATTACK_WINDOW
        recent = {row["ip"] for row in self.db.all("SELECT DISTINCT ip FROM login_attempts WHERE at >= ?", since)}
        for ip in recent:
            if time.monotonic() - reported.get(ip, -1e9) < 3600:
                continue
            row = self.db.attempts_from(ip, since)
            if row["n"] >= ATTACK_LOGINS:
                reported[ip] = time.monotonic()
                users = ", ".join(u for u in (row["users"] or "").split(",") if u)[:200]
                self.alerts.send(f"Login attack on {row['sources'].replace(',', ' and ')}",
                                 f"{row['n']} failed logins from {ip} in the last {ATTACK_WINDOW // 60} minutes."
                                 + (f"\nTried: {users}" if users else "")
                                 + "\nLook it up and see everything it tried under Data → Login attacks in the panel.",
                                 alert_colours.RED)

    async def find_programs(self, proc_root="/proc"):
        """Matches running game programs to servers that have no service set."""
        missing = [st for st in self.states.values() if not st.config.service]
        if not missing:
            return
        games = await asyncio.to_thread(netcheck.find_games, proc_root)
        found = netcheck.match_games([(st.config.id, st.config.name, 0, None) for st in missing],
                                     {st.config.id: st.config.log_dir for st in missing}, games)
        for st in missing:
            st.found_pid = found.get(st.config.id) or 0

    async def monitor_server(self, state: ServerState, proc_root="/proc"):
        """One Monitor reading for one server."""
        row = {"players": len(state.players) if state.online else None, "rss": state.memory or None,
               "fps": state.fps if state.fps_at and now() - state.fps_at < 120 else None,
               "disconnects": sum(state.disconnects.values()), "errors": state.errors,
               "reasons": json.dumps(dict(state.disconnects)) if state.disconnects else None}
        state.disconnects, state.errors = Counter(), 0
        pid = state.pid or state.found_pid
        ticks = monitor.read_process(pid, proc_root) if pid else None
        row["proc_cpu"] = monitor.process_cpu(state.cpu_ticks, ticks) \
            if state.cpu_ticks and ticks and state.cpu_ticks.get("pid") == pid else None
        row["threads"] = ticks["threads"] if ticks else None
        state.cpu_ticks = {**ticks, "pid": pid} if ticks else None
        if not state.memory and pid:
            sample = memory.process_memory(pid, proc_root)
            row["rss"] = sample["rss"] if sample else None
        # Packets the kernel threw away on the game's own UDP ports, per minute.
        sockets = netcheck.game_sockets(pid, proc_root) if pid else None
        reading = {"pid": pid, "at": time.monotonic(), "drops": sum(x["drops"] for x in sockets)} if sockets else None
        last = state.sock_drops
        if reading and last and last["pid"] == reading["pid"] and reading["at"] > last["at"]:
            row["sock_drops"] = round(max(reading["drops"] - last["drops"], 0) * 60 / (reading["at"] - last["at"]), 1)
        state.sock_drops = reading
        if state.config.query_host and state.config.query_port:
            state.query = await monitor.query(state.config.query_host, state.config.query_port)
            row["query_ok"] = 1 if state.query["ok"] else 0
            row["query_ms"] = state.query.get("ms")
            self.reachability(state)
        self.db.add_server_sample(state.config.id, row)

    def reachability(self, state: ServerState):
        """Says so when the server is running but the outside can't reach it,
        and again when it can."""
        running = state.online or state.pid
        if state.query["ok"] or not running:
            if state.unreachable >= UNREACHABLE_AFTER:
                self.db.add_event(state.config.id, "online", "reachable from outside again")
                self.alerts.send(f"{state.config.name} is reachable again",
                                 "The server browser can see it again.", alert_colours.GREEN)
            state.unreachable = 0
            return
        state.unreachable += 1
        if state.unreachable == UNREACHABLE_AFTER:
            why = state.query.get("error", "")
            self.db.add_event(state.config.id, "offline", f"running but not answering from outside ({why})")
            self.alerts.send(f"{state.config.name} can't be reached",
                             f"The server is running, but its query port hasn't answered for a minute ({why}). "
                             "Players probably can't see or join it: check the port and the network.",
                             alert_colours.RED)

    async def _watch_logs(self, state: ServerState):
        reader = LogReader(state.config.log_dir)
        pruned = 0
        while not self._stopping:
            try:
                await self.read_logs(state, reader)
                if now() - pruned > 3600:
                    pruned = now()
                    await self.archive_logs(state, reader)
                    self.db.prune_connections()
                    self.db.prune_feed()
                    self.db.prune_incidents()
                    self.db.prune_kills()
            except Exception:
                log.exception("reading logs failed for %s", state.config.id)
            await asyncio.sleep(self.config.poll_seconds)

    async def read_logs(self, state: ServerState, reader: LogReader):
        positions = self.db.log_positions(state.config.id)
        events, moved = await asyncio.to_thread(reader.scan, positions)
        for event in events:
            if event["kind"] == "fps" and event["at"] >= state.fps_at:
                state.fps, state.fps_at = event["fps"], event["at"]
            elif event["kind"] == "disconnect":
                state.disconnects[event["reason"]] += 1
            elif event["kind"] == "error":
                state.errors += 1
        if moved:
            self.db.add_connections(state.config.id, events, moved)
            self.db.add_kills(state.config.id, archive.kill_rows(events))
            # Don't wait for the next player list: a join from a banned IP is dealt with as it's read.
            for event in events:
                if event.get("kind", "identity") == "identity" and event.get("ip") and now() - event["at"] < 300:
                    await self.ban_for_banned_ip(state, event["identity"], event["name"])
        for action in self.check(state, events) + self.mass_teamkills(state, events):
            await self.auto_punish(state, *action)

    def check(self, state: ServerState, events) -> list:
        """Runs the cheating checks over new log lines; AP mine spam also goes to the staff channel.
        Returns who's reached an automatic kick or ban."""
        settings = self.staff_alert_settings()
        state.detector.s.update(self.sus_settings(), mine_kills=settings["mines"])
        flags = [f for event in events for f in state.detector.feed(event)]
        actions = []
        for flag in flags + state.detector.flush(now()):
            self.suspicious(state, flag)
            if not flag.get("mines") or now() - flag["at"] >= 900:
                continue
            if flag.get("first") and settings["mines_on"]:
                self.db.add_staff_alert(state.config.id, flag["at"], f"AP mine spam on {state.config.name}",
                                        flag["text"].replace(flag["name"], f"**{flag['name']}**", 1))
            if flag["identity"] and now() - flag["at"] < AUTO_FRESH:
                actions.append(("mines", flag["identity"], flag["name"], flag["count"], settings))
        return actions

    def sus_settings(self) -> dict:
        """The Sus checks' settings: the defaults, then panel.local.json, then the Sus settings page."""
        row = self.db.discord_doc("sus")
        try:
            saved = json.loads(row["published"]) if row and row["published"] else {}
        except ValueError:
            saved = {}
        return {**DEFAULTS, **self.config.suspicion, **check_settings(saved)[0]}

    def staff_alert_settings(self) -> dict:
        row = self.db.discord_doc("staffalerts")
        try:
            raw = json.loads(row["published"]) if row and row["published"] else welcome_doc.default_staffalerts()
        except ValueError:
            raw = welcome_doc.default_staffalerts()
        return welcome_doc.check_staffalerts(raw)[0]

    def mass_teamkills(self, state: ServerState, events) -> list:
        """Several teamkills by one player in a short time: queue it for the bot's staff channel.
        Returns who's reached an automatic kick or ban."""
        if not any(e["kind"] == "teamkill" for e in events):
            return []
        settings = self.staff_alert_settings()
        acting = settings["tk_kick"] or settings["tk_ban"]
        if not settings["on"] and not acting:
            return []
        actions = []
        for event in events:
            burst = state.tk_burst.feed(event, settings["count"] if settings["on"] else 0, settings["seconds"], now())
            if burst:
                victims = ", ".join(burst["victims"])
                text = (f"**{burst['name']}** teamkilled {burst['count']} players in {burst['seconds']} seconds: "
                        f"{victims}.")
                self.db.add_staff_alert(state.config.id, burst["at"], f"Mass teamkill on {state.config.name}", text)
            killer = event.get("killer")
            if acting and event["kind"] == "teamkill" and killer and now() - event["at"] < AUTO_FRESH:
                actions.append(("tk", killer, event.get("killer_name") or killer, state.tk_burst.in_window(killer),
                                settings))
        return actions

    async def auto_punish(self, state: ServerState, kind, identity, name, count, settings):
        """Kicks or bans someone who's reached the count set on the Staff alerts page. Never an admin."""
        ban_at, kick_at = settings[f"{kind}_ban"], settings[f"{kind}_kick"]
        action = "ban" if ban_at and count >= ban_at else "kick" if kick_at and count >= kick_at else ""
        if not action or identity in self.staff_ids() or self.db.active_ban(identity):
            return
        done = state.punished.get((kind, identity))
        if done and (done[0] == "ban" or (action == "kick" and now() - done[1] < AUTO_KICK_GAP)):
            return
        state.punished[(kind, identity)] = (action, now())
        what = (f"teamkilling {count} players in {settings['seconds']} seconds" if kind == "tk"
                else f"killing {count} players with AP mines this game")
        if action == "ban":
            seconds = settings[f"{kind}_ban_length"]
            reason = "Mass teamkilling (automatic ban)" if kind == "tk" else "AP mine spam (automatic ban)"
            ban = self.db.ban(self.db.add_ban(identity, name, reason, "Automatic", now() + seconds if seconds else None))
            await self.push_ban(ban)
            await self.kick_everywhere(identity)
            length = length_text(seconds) if seconds else "permanently"
            title = f"Auto-banned on {state.config.name}"
            text = f"**{name}** was banned {'for ' + length if seconds else length} for {what}."
        else:
            player = next((p for p in state.players if p["identity"] == identity), None)
            if player is None:
                state.punished.pop((kind, identity))
                return
            try:
                await self.kick(state.config.id, player["id"])
            except RconError as exc:
                log.warning("Automatic kick failed on %s: %s", state.config.id, exc)
                state.punished.pop((kind, identity))
                return
            title = f"Auto-kicked on {state.config.name}"
            text = f"**{name}** was kicked for {what}."
        self.db.log("Automatic", f"auto {action}", state.config.id, name, text.replace("**", ""))
        self.db.add_staff_alert(state.config.id, now(), title, text)
        self.db.add_feed(state.config.id, "sus", text.replace("**", ""))
        self.alerts.send(title, text, alert_colours.RED, [("Identity", identity)])

    @property
    def archive_root(self) -> Path:
        return Path(self.config.database).resolve().parent / "log-archive"

    async def archive_logs(self, state: ServerState, reader: LogReader):
        """Packs every finished game's log folder into the panel's archive."""
        done = self.db.archived(state.config.id)
        for folder in reader.folders()[:-1]:
            if folder.name in done or not (folder / "console.log").is_file():
                continue
            dest = self.archive_root / state.config.id / f"{folder.name}.tar.gz"
            try:
                game = await asyncio.to_thread(archive.archive_game, folder, dest)
            except (OSError, tarfile.TarError) as exc:
                log.warning("Couldn't archive %s: %s", folder, exc)
                continue
            self.db.add_archive(state.config.id, folder.name, str(dest), dest.stat().st_size, game)
        # Games archived before the per-player numbers existed get them a few at a time.
        for row in self.db.unscored_games(state.config.id):
            try:
                info = Path(row["path"]).stat()
                game = await asyncio.to_thread(archive.read_game, row["path"], row["folder"], info.st_size,
                                               info.st_mtime, ())
            except (OSError, tarfile.TarError, EOFError) as exc:
                log.warning("Couldn't read %s for player numbers: %s", row["path"], exc)
                game = {"players": []}
            self.db.add_game_players(state.config.id, row["folder"], row["started"], game["players"])
        # Likewise every kill in games saved before the kill log existed.
        for row in self.db.unread_kill_games(state.config.id):
            try:
                info = Path(row["path"]).stat()
                game = await asyncio.to_thread(archive.read_game, row["path"], row["folder"], info.st_size,
                                               info.st_mtime, ())
            except (OSError, tarfile.TarError, EOFError) as exc:
                log.warning("Couldn't read %s for the kill log: %s", row["path"], exc)
                game = {"kill_rows": []}
            self.db.add_kills(state.config.id, game["kill_rows"], row["folder"])
        # And where each of those kills happened, for games read before places were kept.
        for row in self.db.unread_place_games(state.config.id):
            try:
                info = Path(row["path"]).stat()
                game = await asyncio.to_thread(archive.read_game, row["path"], row["folder"], info.st_size,
                                               info.st_mtime, ())
            except (OSError, tarfile.TarError, EOFError) as exc:
                log.warning("Couldn't read %s for kill places: %s", row["path"], exc)
                game = {"kill_rows": []}
            self.db.add_kills(state.config.id, game["kill_rows"], row["folder"])
        # And who was on PC or console, with Steam IDs, in games saved before that was read.
        for row in self.db.unread_platform_games(state.config.id):
            try:
                info = Path(row["path"]).stat()
                game = await asyncio.to_thread(archive.read_game, row["path"], row["folder"], info.st_size,
                                               info.st_mtime, ())
            except (OSError, tarfile.TarError, EOFError) as exc:
                log.warning("Couldn't read %s for platforms: %s", row["path"], exc)
                game = {"platforms": []}
            self.db.add_platforms(game["platforms"], state.config.id, row["folder"])

    def suspicious(self, state: ServerState, flag: dict):
        """Puts a flag in the live feed; a fresh one also goes to Discord, and a
        burst saves who was on in the last few minutes so repeat faces across
        incidents stand out."""
        text, fields = flag["text"], []
        fresh = now() - flag["at"] < 900
        if flag.get("key") in state.running:
            # A running tally (a teamkiller's total): keep the one line up to date.
            self.db.update_feed(state.running[flag["key"]], text, flag["at"])
            if fresh and flag.get("count") == 10:
                self.alerts.send(f"Suspicious on {state.config.name}", text, alert_colours.GOLD,
                                 [("Identity", flag["identity"])])
            return
        present = [{"identity": i, "name": n} for i, (n, seen) in state.recent.items() if now() - seen <= 300]
        if flag["incident"] and fresh and present:
            incident = self.db.add_incident(state.config.id, flag["at"], text, present)
            repeat = self.db.repeat_suspects(incident, skip=self.staff_ids())
            if repeat:
                text += " At more incidents than their playtime explains: " + ", ".join(
                    f"{r['name']} ({r['times']} times{', ' + format(r['lift'], '.1f') + 'x normal' if r['lift'] else ''})"
                    for r in repeat[:5]) + "."
                fields = [(r["name"], f"{r['identity']}\n{r['times']} incidents in {r['hours']:.0f} h played")
                          for r in repeat[:5]]
        elif flag["identity"]:
            fields = [("Identity", flag["identity"])]
        row = self.db.add_feed(state.config.id, "sus", text, at=flag["at"])
        if flag.get("key"):
            state.running[flag["key"]] = row
        if fresh:
            self.alerts.send(f"Suspicious on {state.config.name}", text, alert_colours.GOLD, fields)

    async def refresh_memory(self, state: ServerState):
        sample = await self.sampler(state.config.service)
        if sample is None:
            if state.pid:
                self._process_gone(state)
            state.pid = state.memory = state.process_age = 0
        else:
            if sample["pid"] != state.pid:
                started = now() - sample["age"]
                state.fresh = self._saved_baseline(state, sample["pid"], started)
                if state.pid:
                    self._process_gone(state, at=started)
                    # A new server process starts with clean memory, so an old warning no longer applies.
                    if state.check and state.check["status"] != "waiting":
                        state.check = None
                if sample["age"] < 300:
                    self.db.add_event(state.config.id, "started", "", at=started)
            state.pid, state.memory, state.process_age = sample["pid"], sample["rss"], sample["age"]
            if now() - state.last_sample >= 60:
                state.last_sample = now()
                self.db.add_memory(state.config.id, state.memory)
            if not state.fresh and self.settle <= state.process_age <= self.settle + memory.BASELINE_WINDOW:
                state.fresh = state.memory
                self.db.save_baseline(state.config.id, state.pid, now() - state.process_age, state.memory)
        self._finish_check(state)

    def _saved_baseline(self, state: ServerState, pid: int, started: float) -> int:
        """The fresh-start reading for this server process, if one was taken
        before the panel itself last restarted."""
        saved = self.db.baseline(state.config.id)
        if saved and saved["pid"] == pid and abs(saved["started"] - started) <= 5:
            return saved["rss"]
        return 0

    def _process_gone(self, state: ServerState, at=None):
        if now() < state.expected_until:
            self.db.add_event(state.config.id, "stopped", "by an admin", at=at)
            return
        detail = f"used {memory.gb(state.memory)} when it went" if state.memory else ""
        self.db.add_event(state.config.id, "crashed", detail, at=at)
        self.alerts.send(f"{state.config.name} crashed or restarted on its own",
                         f"The server program ended without anyone pressing a button. {detail}".strip(),
                         alert_colours.RED)

    async def start_mission_check(self, server_id: str, username: str):
        state = self.states[server_id]
        if not state.config.service:
            return
        await self.refresh_memory(state)
        if state.pid:
            state.check = {"status": "waiting", "by": username, "started": now(), "pid": state.pid,
                           "before": state.memory, "fresh": state.fresh}

    def _finish_check(self, state: ServerState):
        check = state.check
        if not check or check["status"] != "waiting" or now() - check["started"] < self.settle:
            return
        if state.pid != check["pid"]:
            status, message = "ok", "The server program restarted since, so its memory was fully cleared."
        elif not state.pid:
            return
        else:
            status, message = memory.verdict(check["before"], state.memory, check["fresh"])
        check.update(status=status, after=state.memory, message=message, finished=now())
        self.db.log(check["by"], "mission restart check", state.config.id, detail=message, ok=status != "leak")
        if status == "leak":
            self.alerts.send(f"{state.config.name}: memory didn't clear", message, alert_colours.GOLD)

    async def _connect(self, state: ServerState):
        cfg = state.config
        client = self.client_factory(cfg.rcon_host, cfg.rcon_port, cfg.rcon_password)
        client.on_message = lambda text: self._rcon_message(state, text)
        await client.connect()
        state.client = client
        state.online = True
        state.error = ""
        state.online_since = now()
        log.info("%s connected", cfg.id)
        self._changed(state, True)

    def _rcon_message(self, state: ServerState, text: str):
        if text.strip() and not text.startswith("Logged In!") and not PLAYER_ROW.search(text):
            self.db.add_feed(state.config.id, "rcon", " ".join(text.split()))

    def _offline(self, state: ServerState, exc):
        if state.client:
            state.client.close()
        state.client = None
        state.online = False
        state.online_since = 0
        state.players = []
        state.error = str(exc) or exc.__class__.__name__
        self._changed(state, False)

    def expect_restart(self, server_id: str, seconds: int = 300):
        """An admin action is about to take this server down; don't call it a crash."""
        self.states[server_id].expected_until = now() + seconds

    def _changed(self, state: ServerState, online: bool):
        if state.was_online == online:
            return
        first = state.was_online is None
        state.was_online = online
        name = state.config.name
        kind = "online" if online else "offline"
        last = self.db.events(state.config.id, 1, ("online", "offline"))
        if first and last and last[0]["kind"] == kind:
            return
        self.db.add_event(state.config.id, kind, "" if online else state.error)
        self.db.add_feed(state.config.id, "server", f"{name} is {'online' if online else 'offline'}")
        if first:
            return
        expected = now() < state.expected_until
        if online:
            self.alerts.send(f"{name} is back online", colour=alert_colours.GREEN)
        elif not expected:
            self.alerts.send(f"{name} went offline", f"RCON stopped answering: {state.error}", alert_colours.RED)

    def state(self, server_id: str) -> ServerState | None:
        return self.states.get(server_id)

    async def command(self, server_id: str, text: str) -> str:
        state = self.states.get(server_id)
        if state is None:
            raise RconError("unknown server")
        if not (state.online and state.client):
            raise RconError(state.error or "server is offline")
        try:
            return await state.client.command(text)
        except RconError as exc:
            self._offline(state, exc)
            raise

    async def refresh_players(self, state: ServerState):
        output = await self.command(state.config.id, state.config.commands["players"])
        state.ping_ms = state.client.ping_ms if state.client else 0
        state.raw_players = output
        state.players = parse_players(output)
        state.updated = now()
        for player in state.players:
            if player["identity"]:
                self.db.saw_player(player["identity"], player["name"], state.config.id)
                state.recent[player["identity"]] = (player["name"], now())
        state.recent = {i: seen for i, seen in state.recent.items() if now() - seen[1] <= 600}
        on = {p["identity"] or p["name"] for p in state.players}
        state.joined = {k: state.joined.get(k, now()) for k in on}
        staff = self.staff_ids()
        state.admins = [p for p in state.players if p["identity"] in staff]

    def config_admins(self) -> set[str]:
        """Admin IDs from each server's own serverconfig.json (AMP keeps it beside the
        logs). Only Reforger IDs count; Steam IDs there can't be matched to a player."""
        found = set()
        for state in self.states.values():
            found |= {a for a in self.server_config_admins(state) or () if valid_identity(a)}
        return found

    def server_config_admins(self, state) -> set[str] | None:
        """Every ID in one server's serverconfig.json as it is now, or None if there's no file."""
        if not state.config.log_dir:
            return None
        path = Path(state.config.log_dir).parent.parent / "Configs" / "serverconfig.json"
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
        cached = self._config_admins.get(str(path))
        if not cached or cached[0] != stamp:
            try:
                admins = json.loads(path.read_text(encoding="utf-8")).get("game", {}).get("admins", [])
            except (OSError, ValueError, AttributeError):
                admins = []
            cached = (stamp, {str(a).strip().lower() for a in admins if str(a).strip()})
            self._config_admins[str(path)] = cached
        return cached[1]

    def staff_ids(self) -> set[str]:
        return self.db.staff_ids() | self.config_admins()

    async def enforce_ip_bans(self, state: ServerState):
        """Kick banned accounts that are still connected, and ban any other
        account that joins from an IP-banned address."""
        for player in state.players:
            identity = player["identity"]
            if not identity or now() - self._ip_kicked.get(identity, 0) < 30:
                continue
            own = self.db.active_ban(identity)
            if own:
                self._ip_kicked[identity] = now()
                try:
                    await self.kick(state.config.id, player["id"])
                except RconError as exc:
                    log.warning("Kick of banned %s failed: %s", player["name"], exc)
                    continue
                self.db.log("panel", "banned player kick", state.config.id, player["name"],
                            f"still connected while banned ({own['reason']})")
                continue
            await self.ban_for_banned_ip(state, identity, player["name"])

    async def ban_for_banned_ip(self, state: ServerState, identity: str, name: str):
        """Ban an account that has connected from an IP-banned address, and kick it if it's on."""
        if self.db.active_ban(identity):
            return
        hit = self.db.ip_ban_hit(identity)
        if hit is None:
            return
        source = hit["name"] or hit["identity"]
        reason = f"{hit['reason']} (same IP as {source})"
        ban = self.ip_ban_account(identity, name, reason, "panel", hit["expires_at"])
        await self.push_ban(ban)
        await self.kick_everywhere(identity)
        detail = f"joined from an IP that is banned with {source} ({hit['reason']})"
        self.db.log("panel", "IP ban", state.config.id, name, detail)
        self.alerts.send(f"Banned {name} on {state.config.name}", detail.capitalize(),
                         alert_colours.RED, [("Identity", identity), ("Same IP as", source)])

    def ip_ban_account(self, identity, name, reason, by, expires_at):
        """Ban one account and every IP it has used."""
        ban_id = self.db.add_ban(identity, name, reason, by, expires_at)
        self.db.add_ip_bans(ban_id, [c["ip"] for c in self.db.ips(identity)])
        return self.db.ban(ban_id)

    async def kick_everywhere(self, identity: str) -> list[str]:
        """Kick this account from every server it is on right now."""
        kicked = []
        for state in self.states.values():
            for player in list(state.players):
                if player["identity"] == identity:
                    self._ip_kicked[identity] = now()
                    try:
                        await self.kick(state.config.id, player["id"])
                        kicked.append(state.config.id)
                    except RconError as exc:
                        log.warning("Kick after ban failed on %s: %s", state.config.id, exc)
        return kicked

    async def kick(self, server_id: str, player_id: str) -> str:
        if not player_id.isdigit():
            raise RconError("player id must be a number")
        state = self.states[server_id]
        return await self.command(server_id, state.config.commands["kick"].format(player=player_id))

    def _ban_command(self, state: ServerState, ban) -> str:
        seconds = 0
        if ban["expires_at"]:
            seconds = max(ban["expires_at"] - now(), 1)
        reason = clean(ban["reason"]) or "Banned"
        return state.config.commands["ban"].format(identity=ban["identity"], seconds=seconds, reason=reason)

    async def push_ban(self, ban) -> dict[str, str]:
        return await self._push(ban, "ban")

    async def push_unban(self, ban) -> dict[str, str]:
        return await self._push(ban, "unban")

    async def _push(self, ban, action: str) -> dict[str, str]:
        async with self._ban_lock:
            return await self._push_locked(ban, action)

    async def _push_locked(self, ban, action: str) -> dict[str, str]:
        results = {}
        for state in self.states.values():
            if not state.config.configured:
                continue
            if action == "unban" and "ban" not in self.db.synced(ban["id"]).get(state.config.id, set()):
                continue
            results[state.config.id] = await self._send_ban(state, ban, action)
        return results

    async def _send_ban(self, state: ServerState, ban, action: str) -> str:
        if not state.online:
            return "queued until the server is back"
        if action == "ban":
            text = self._ban_command(state, ban)
        else:
            text = state.config.commands["unban"].format(identity=ban["identity"])
        try:
            reply = clean(await self.command(state.config.id, text), 120)
        except RconError as exc:
            return f"failed: {exc}"
        if GAME_REFUSED.search(reply):
            self._refused_at[(ban["id"], state.config.id)] = now()
            self.db.log("panel", f"{action} refused by the game", state.config.id, ban["name"] or ban["identity"], reply)
            return f"refused by the game: {reply}"
        self.db.mark_synced(ban["id"], state.config.id, action)
        return f"done: {reply}" if reply else "done"

    async def reban(self, ban) -> dict[str, str]:
        """Put a changed ban back on every server: lift the old one, then ban again with the new
        length. A server that's offline gets it when it's back."""
        async with self._ban_lock:
            self.db.clear_synced(ban["id"])
            results = {}
            for state in self.states.values():
                if not state.config.configured:
                    continue
                if state.online:
                    try:
                        await self.command(state.config.id,
                                           state.config.commands["unban"].format(identity=ban["identity"]))
                    except RconError:
                        pass
                results[state.config.id] = await self._send_ban(state, ban, "ban")
            return results

    async def sync_bans(self, state: ServerState):
        async with self._ban_lock:
            for ban in self.db.pending_bans(state.config.id) + self.db.pending_unbans(state.config.id):
                if now() - self._refused_at.get((ban["id"], state.config.id), 0) < REFUSED_RETRY:
                    continue
                await self._send_ban(state, ban, "ban" if not ban["removed_at"] else "unban")

    async def power(self, server_id: str, action: str) -> str:
        state = self.states[server_id]
        if action in POWER_RCON and not state.config.commands.get(action):
            raise RconError("that button is switched off for this server")
        if action != "restart_mission":
            self.expect_restart(server_id)
        if action in POWER_RCON:
            return await self.command(server_id, state.config.commands[action])
        if action in POWER_SERVICE:
            if not state.config.service:
                raise RconError("no systemd service is set for this server")
            return await systemctl(action, state.config.service)
        raise RconError(f"unknown action {action}")


async def systemctl(action: str, unit: str) -> str:
    args = [shutil.which("systemctl") or "/usr/bin/systemctl", action, unit]
    if os.geteuid() != 0:
        args = ["sudo", "-n"] + args
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    output, _ = await proc.communicate()
    text = output.decode(errors="replace").strip()
    if proc.returncode != 0:
        raise RconError(text or f"systemctl {action} failed")
    return text or f"{unit}: {action} sent"
