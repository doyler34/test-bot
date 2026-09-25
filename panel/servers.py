import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass, field

from .config import PanelConfig, ServerConfig
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


class ServerManager:
    def __init__(self, config: PanelConfig, db: PanelDB, client_factory=RconClient):
        self.config = config
        self.db = db
        self.client_factory = client_factory
        self.states = {s.id: ServerState(s) for s in config.servers}
        self._tasks: list[asyncio.Task] = []

    def start(self):
        for state in self.states.values():
            if state.config.configured:
                self._tasks.append(asyncio.create_task(self._run(state)))
            else:
                state.error = "RCON is not set up in panel.local.json"

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for state in self.states.values():
            if state.client:
                state.client.close()

    async def _run(self, state: ServerState):
        delay = 5
        while True:
            try:
                if not (state.client and state.client.connected):
                    await self._connect(state)
                    delay = 5
                await self.refresh_players(state)
                await self.sync_bans(state)
                await asyncio.sleep(self.config.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if state.online:
                    log.warning("%s went offline: %s", state.config.id, exc)
                self._offline(state, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _connect(self, state: ServerState):
        cfg = state.config
        client = self.client_factory(cfg.rcon_host, cfg.rcon_port, cfg.rcon_password)
        await client.connect()
        state.client = client
        state.online = True
        state.error = ""
        state.online_since = now()
        log.info("%s connected", cfg.id)

    def _offline(self, state: ServerState, exc):
        if state.client:
            state.client.close()
        state.client = None
        state.online = False
        state.online_since = 0
        state.players = []
        state.error = str(exc) or exc.__class__.__name__

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
        for ban in self.db.pending_bans(state.config.id):
            await self._send_ban(state, ban, "ban")
        for ban in self.db.pending_unbans(state.config.id):
            await self._send_ban(state, ban, "unban")

    async def power(self, server_id: str, action: str) -> str:
        state = self.states[server_id]
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
