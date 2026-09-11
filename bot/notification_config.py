"""Configuration for three read-only game-server announcement channels."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from dotenv import load_dotenv
from bot.config import ConfigError, _require, _require_int, _optional_int, _optional_bool


@dataclass(frozen=True)
class Server:
    id: str
    name: str
    channel_name: str
    log_dir: str
    settings: str
    rules: str
    enabled: bool = True
    a2s_host: str | None = None
    a2s_port: int | None = None


@dataclass(frozen=True)
class NotificationConfig:
    token: str
    guild_id: int
    servers: tuple[Server, ...]
    state_path: str
    stale_seconds: int = 120
    voice_channel_id: int = 0
    status_refresh_seconds: int = 60
    join_voice_channel: bool = False
    voice_server_id: str = "server-1"


def read_servers(path: str) -> tuple[Server, ...]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read SERVERS_CONFIG: {exc}") from exc
    if not isinstance(data, list) or len(data) != 3:
        raise ConfigError("SERVERS_CONFIG must contain exactly three servers.")
    servers = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ConfigError("Each server must be an object.")
        for key, limit in [("id", 40), ("name", 100), ("channel_name", 100),
                           ("settings", 1024), ("rules", 4096)]:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ConfigError(f"Each server needs {key}: 1–{limit} characters.")
        if not re.fullmatch(r"[a-z0-9-]+", entry["id"]):
            raise ConfigError("Server id must use lowercase letters, numbers or hyphens.")
        if not re.fullmatch(r"[a-z0-9-]+", entry["channel_name"]):
            raise ConfigError("Channel names must use lowercase letters, numbers or hyphens.")
        enabled = entry.get("enabled", True)
        if type(enabled) is not bool:
            raise ConfigError("enabled must be true or false.")
        log_dir = entry.get("log_dir", "")
        if not isinstance(log_dir, str):
            raise ConfigError("log_dir must be text.")
        log_dir = os.path.expandvars(log_dir)
        if enabled and (not log_dir.strip() or "$" in log_dir):
            raise ConfigError("Active servers need a log_dir; set REFORGER_LOG_DIR in .env.")
        host, port = entry.get("a2s_host"), entry.get("a2s_port")
        if (host is None) != (port is None):
            raise ConfigError("Supply both a2s_host and a2s_port, or neither.")
        if host is not None and (not isinstance(host, str) or not host.strip()):
            raise ConfigError("a2s_host must be non-empty text.")
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise ConfigError("a2s_port must be an integer from 1 to 65535.")
        servers.append(Server(**{key: entry[key] for key in
                       ("id", "name", "channel_name", "settings", "rules")},
                              log_dir=log_dir, enabled=enabled,
                              a2s_host=host, a2s_port=port))
    for key in ("id", "channel_name"):
        values = [getattr(server, key) for server in servers]
        if len(set(values)) != 3:
            raise ConfigError(f"Each server must have a distinct {key}.")
    active_paths = [str(Path(s.log_dir).resolve()) for s in servers if s.enabled]
    if len(set(active_paths)) != len(active_paths):
        raise ConfigError("Active servers must have distinct log directories.")
    return tuple(servers)


def load_notification_config(*, load_env_file: bool = True) -> NotificationConfig:
    if load_env_file:
        load_dotenv()
    stale = _optional_int("SESSION_STALE_SECONDS", 120)
    if stale < 1:
        raise ConfigError("SESSION_STALE_SECONDS must be positive.")
    return NotificationConfig(
        token=_require("DISCORD_BOT_TOKEN"), guild_id=_require_int("GUILD_ID"),
        servers=read_servers(_require("SERVERS_CONFIG")),
        state_path=os.getenv("NOTIFICATION_STATE_DB", "data/notifications.sqlite3"),
        stale_seconds=stale,
        voice_channel_id=_optional_int("VOICE_CHANNEL_ID", 0),
        status_refresh_seconds=max(15, _optional_int("STATUS_REFRESH_SECONDS", 60)),
        join_voice_channel=_optional_bool("JOIN_VOICE_CHANNEL", False),
        voice_server_id=os.getenv("VOICE_TIMER_SERVER_ID", "server-1"),
    )
