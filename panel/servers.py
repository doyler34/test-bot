import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass, field

from .config import PanelConfig, ServerConfig
from . import alerts as alert_colours, memory
from .alerts import Alerts
from .connections import LogReader
from .db import PanelDB, now
from .rcon import RconClient, RconError

log = logging.getLogger("panel.servers")

UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
POWER_RCON = ("restart_mission", "shutdown")
POWER_SERVICE = ("start", "stop", "restart")


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
    expected_until: int = 0
    last_sample: int = 0
    was_online: bool | None = None


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
        self.states = {s.id: ServerState(s) for s in config.servers}
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._ip_kicked: dict[str, int] = {}
        self._ban_lock = asyncio.Lock()

    def start(self):
        for state in self.states.values():
            if state.config.configured:
                self._tasks.append(asyncio.create_task(self._run(state)))
            else:
                state.error = "RCON is not set up in panel.local.json"
        if any(s.config.service for s in self.states.values()):
            self._tasks.append(asyncio.create_task(self._watch_memory()))
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

    async def _watch_logs(self, state: ServerState):
        reader = LogReader(state.config.log_dir)
        pruned = 0
        while not self._stopping:
            try:
                await self.read_logs(state, reader)
                if now() - pruned > 3600:
                    pruned = now()
                    self.db.prune_connections()
            except Exception:
                log.exception("reading logs failed for %s", state.config.id)
            await asyncio.sleep(self.config.poll_seconds)

    async def read_logs(self, state: ServerState, reader: LogReader):
        positions = self.db.log_positions(state.config.id)
        events, moved = await asyncio.to_thread(reader.scan, positions)
        if moved:
            self.db.add_connections(state.config.id, events, moved)

    async def refresh_memory(self, state: ServerState):
        sample = await self.sampler(state.config.service)
        if sample is None:
            if state.pid:
                self._process_gone(state)
            state.pid = state.memory = state.process_age = 0
        else:
            if sample["pid"] != state.pid:
                state.fresh = 0
                started = now() - sample["age"]
                if state.pid:
                    self._process_gone(state, at=started)
                if sample["age"] < 300:
                    self.db.add_event(state.config.id, "started", "", at=started)
            state.pid, state.memory, state.process_age = sample["pid"], sample["rss"], sample["age"]
            if now() - state.last_sample >= 60:
                state.last_sample = now()
                self.db.add_memory(state.config.id, state.memory)
            if not state.fresh and self.settle <= state.process_age <= self.settle + memory.BASELINE_WINDOW:
                state.fresh = state.memory
        self._finish_check(state)

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
        await client.connect()
        state.client = client
        state.online = True
        state.error = ""
        state.online_since = now()
        log.info("%s connected", cfg.id)
        self._changed(state, True)

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
        self.db.add_event(state.config.id, "online" if online else "offline", "" if online else state.error)
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
        state.raw_players = output
        state.players = parse_players(output)
        state.updated = now()
        for player in state.players:
            if player["identity"]:
                self.db.saw_player(player["identity"], player["name"], state.config.id)

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
            hit = self.db.ip_ban_hit(identity)
            if hit is None:
                continue
            source = hit["name"] or hit["identity"]
            reason = f"{hit['reason']} (same IP as {source})"
            ban = self.ip_ban_account(identity, player["name"], reason, "panel", hit["expires_at"])
            await self.push_ban(ban)
            await self.kick_everywhere(identity)
            detail = f"joined from {hit['hit_ip']}, which is IP banned with {source} ({hit['reason']})"
            self.db.log("panel", "IP ban", state.config.id, player["name"], detail)
            self.alerts.send(f"Banned {player['name']} on {state.config.name}", detail.capitalize(),
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
            await self.command(state.config.id, text)
        except RconError as exc:
            return f"failed: {exc}"
        self.db.mark_synced(ban["id"], state.config.id, action)
        return "done"

    async def sync_bans(self, state: ServerState):
        async with self._ban_lock:
            for ban in self.db.pending_bans(state.config.id):
                await self._send_ban(state, ban, "ban")
            for ban in self.db.pending_unbans(state.config.id):
                await self._send_ban(state, ban, "unban")

    async def power(self, server_id: str, action: str) -> str:
        state = self.states[server_id]
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
