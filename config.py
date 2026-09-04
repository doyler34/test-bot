"""
Configuration and logging setup.

All runtime configuration comes from environment variables (optionally loaded
from a .env file). See .env.example for the full list.
"""

import logging
import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set.")
    return value


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc


def _optional_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    discord_token: str
    guild_id: int
    voice_channel_id: int
    log_dir: str

    # Heartbeat watchdog: end the session if no server FPS heartbeat line is
    # seen for this many seconds (server likely crashed / logs stopped shipping).
    session_stale_seconds: int

    # How often to refresh the voice-channel status text while a match is live.
    status_refresh_seconds: int

    # Optional A2S liveness fallback.
    a2s_host: str | None
    a2s_port: int | None

    @property
    def a2s_enabled(self) -> bool:
        return bool(self.a2s_host and self.a2s_port)


def load_config() -> Config:
    """Load and validate configuration from the environment."""
    load_dotenv()

    a2s_host = os.getenv("A2S_HOST") or None
    a2s_port_raw = os.getenv("A2S_PORT")
    a2s_port = int(a2s_port_raw) if a2s_port_raw else None

    return Config(
        discord_token=_require("DISCORD_BOT_TOKEN"),
        guild_id=_require_int("GUILD_ID"),
        voice_channel_id=_require_int("VOICE_CHANNEL_ID"),
        log_dir=_require("REFORGER_LOG_DIR"),
        session_stale_seconds=_optional_int("SESSION_STALE_SECONDS", 120),
        status_refresh_seconds=_optional_int("STATUS_REFRESH_SECONDS", 60),
        a2s_host=a2s_host,
        a2s_port=a2s_port,
    )


def setup_logging() -> None:
    """Configure structured, timestamped logging to stdout."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)-18s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # discord.py is chatty at INFO; keep it at WARNING unless explicitly debugging.
    logging.getLogger("discord").setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    )
