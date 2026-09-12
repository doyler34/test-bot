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


def staging_enabled() -> bool:
    """Hide the bot's public channels while a server is set up before launch."""
    return os.getenv("OYB_STAGING", "").strip().lower() in ("1", "true", "yes", "on")


def command_auto_clear_seconds() -> int:
    """Seconds before a /rank or /stats card auto-deletes (0 = keep). Default 5 min."""
    raw = os.getenv("COMMAND_AUTO_CLEAR_SECONDS", "300").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def configure_connection(db, *, busy_timeout_ms: int = 10000, wal: bool = True) -> None:
    """Apply shared SQLite pragmas so the many subsystems contend less.

    WAL lets readers and writers proceed concurrently (the event loop and the
    combat worker thread share the account-links file); ``synchronous=NORMAL``
    is durable under WAL with far fewer fsyncs; ``busy_timeout`` makes a
    transient writer lock wait and retry instead of failing immediately. The
    journal mode persists in the file header, so setting it on any one
    connection is enough, but applying it everywhere is harmless and explicit.
    """
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
    db.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")


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



def _optional_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}.")


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

    # Stay out of voice by default; publish the channel status via REST only.
    join_voice_channel: bool = False

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
        join_voice_channel=_optional_bool("JOIN_VOICE_CHANNEL", False),
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
