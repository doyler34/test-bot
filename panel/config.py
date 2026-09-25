import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


class PanelConfigError(Exception):
    pass


COMMANDS = {
    "players": "#players",
    "kick": "#kick {player}",
    "ban": "#ban create {identity} {seconds} {reason}",
    "unban": "#ban remove {identity}",
    "restart_mission": "#restart",
    "shutdown": "#shutdown",
}


@dataclass
class ServerConfig:
    id: str
    name: str
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 0
    rcon_password: str = ""
    service: str = ""
    log_dir: str = ""
    commands: dict = field(default_factory=lambda: dict(COMMANDS))

    @property
    def configured(self) -> bool:
        return bool(self.rcon_port and self.rcon_password)


@dataclass
class PanelConfig:
    listen: str = "127.0.0.1"
    port: int = 8080
    database: str = "data/panel.sqlite3"
    cookie_secure: bool = True
    poll_seconds: float = 10.0
    discord_webhook: str = ""
    oyb_data: str = "data"
    servers: list[ServerConfig] = field(default_factory=list)

    def server(self, server_id: str) -> ServerConfig | None:
        return next((s for s in self.servers if s.id == server_id), None)


def bot_log_dirs() -> dict[str, str]:
    """The log folders the bot already follows, so the panel needn't be told twice."""
    path = Path(os.getenv("SERVERS_CONFIG", "servers.local.json"))
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    try:
        from dotenv import dotenv_values
        env = {**dotenv_values(".env"), **os.environ}
    except ImportError:
        env = dict(os.environ)
    expand = lambda text: re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: env.get(m[1] or m[2]) or m[0], text)
    found = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("enabled") and isinstance(entry.get("log_dir"), str):
            log_dir = expand(entry["log_dir"])
            if "$" not in log_dir:
                found[str(entry.get("id"))] = log_dir
    return found


def load_config(path: str | os.PathLike | None = None) -> PanelConfig:
    path = Path(path or os.getenv("PANEL_CONFIG", "panel.local.json"))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PanelConfigError(f"{path} not found; copy panel.example.json to {path} and fill it in") from None
    except json.JSONDecodeError as exc:
        raise PanelConfigError(f"{path} is not valid JSON: {exc}") from None
    webhook = str(raw.get("discord_webhook") or "")
    if webhook and not webhook.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        raise PanelConfigError("discord_webhook must be a Discord webhook URL (https://discord.com/api/webhooks/...)")
    servers = []
    from_bot = bot_log_dirs()
    for entry in raw.get("servers", []):
        server_id = str(entry.get("id", "")).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", server_id):
            raise PanelConfigError(f"server id {server_id!r} may only use letters, digits, - and _")
        if any(s.id == server_id for s in servers):
            raise PanelConfigError(f"server id {server_id} is listed twice")
        service = str(entry.get("service", "")).strip()
        if service and not re.fullmatch(r"[a-zA-Z0-9_.@-]+", service):
            raise PanelConfigError(f"{server_id}: service name {service!r} is not a valid systemd unit")
        servers.append(ServerConfig(
            id=server_id,
            name=str(entry.get("name") or server_id),
            rcon_host=str(entry.get("rcon_host") or "127.0.0.1"),
            rcon_port=int(entry.get("rcon_port") or 0),
            rcon_password=str(entry.get("rcon_password") or ""),
            service=service,
            log_dir=str(entry.get("log_dir") or from_bot.get(server_id, "")),
            commands={**COMMANDS, **entry.get("commands", {})},
        ))
    return PanelConfig(
        listen=str(raw.get("listen", "127.0.0.1")),
        port=int(raw.get("port", 8080)),
        database=str(raw.get("database", "data/panel.sqlite3")),
        cookie_secure=bool(raw.get("cookie_secure", True)),
        poll_seconds=float(raw.get("poll_seconds", 10)),
        discord_webhook=str(raw.get("discord_webhook") or ""),
        oyb_data=str(raw.get("oyb_data") or "data"),
        servers=servers,
    )
