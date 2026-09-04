"""
Discord side of the session timer.

On a match start the bot joins the configured voice channel (which starts
Discord's own voice session — the per-user timer that AllCallTimers-style
clients display) and sets the channel's native Voice Channel Status text so
that *every* member sees a readout without any client plugin. On match end it
clears the status and leaves, which resets the voice session.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import discord
from discord.http import Route

logger = logging.getLogger("reforger.bot")


def _format_elapsed(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


class TimerBot(discord.Client):
    def __init__(
        self,
        guild_id: int,
        voice_channel_id: int,
        status_refresh_seconds: int = 60,
    ) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        super().__init__(intents=intents)

        self.guild_id = guild_id
        self.voice_channel_id = voice_channel_id
        self.status_refresh_seconds = status_refresh_seconds

        self._desired_live = False
        self._match_start: Optional[float] = None
        self._status_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # --- Discord lifecycle ---------------------------------------------------

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (id: %s)", self.user, self.user.id)
        # Re-assert desired state after a (re)connect.
        if self._desired_live:
            await self._ensure_connected()

    # --- Public API called by the monitor ------------------------------------

    async def handle_session_start(self) -> None:
        async with self._lock:
            self._desired_live = True
            self._match_start = time.monotonic()
            await self._ensure_connected()
            await self._refresh_status()
            self._start_status_loop()

    async def handle_session_end(self) -> None:
        async with self._lock:
            self._desired_live = False
            self._match_start = None
            self._stop_status_loop()
            await self._clear_status()
            await self._disconnect()

    # --- Voice + status helpers ----------------------------------------------

    def _voice_channel(self) -> Optional[discord.VoiceChannel]:
        channel = self.get_channel(self.voice_channel_id)
        if channel is None:
            logger.error("Voice channel %s not found", self.voice_channel_id)
            return None
        if not isinstance(channel, discord.VoiceChannel):
            logger.error("Channel %s is not a voice channel", self.voice_channel_id)
            return None
        return channel

    async def _ensure_connected(self) -> None:
        channel = self._voice_channel()
        if channel is None:
            return
        vc = channel.guild.voice_client
        if vc and vc.is_connected():
            if vc.channel and vc.channel.id == channel.id:
                return
            await vc.move_to(channel)
            return
        try:
            await channel.connect(self_deaf=True, self_mute=True)
            logger.info("Joined voice channel '%s'", channel.name)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to join voice channel")

    async def _disconnect(self) -> None:
        channel = self._voice_channel()
        guild = channel.guild if channel else self.get_guild(self.guild_id)
        vc = guild.voice_client if guild else None
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
                logger.info("Left voice channel")
            except Exception:  # noqa: BLE001
                logger.exception("Failed to leave voice channel")

    async def _set_status(self, text: str) -> None:
        """Set the native Voice Channel Status via the raw REST route.

        Uses a raw Route for compatibility across discord.py versions. Requires
        the 'Set Voice Channel Status' permission and the bot to be connected to
        the channel.
        """
        try:
            route = Route(
                "PUT",
                "/channels/{channel_id}/voice-status",
                channel_id=self.voice_channel_id,
            )
            await self.http.request(route, json={"status": text})
        except Exception:  # noqa: BLE001
            logger.exception("Failed to set voice channel status")

    async def _refresh_status(self) -> None:
        if self._match_start is None:
            return
        elapsed = time.monotonic() - self._match_start
        await self._set_status(f"🟢 Match live · {_format_elapsed(elapsed)}")

    async def _clear_status(self) -> None:
        await self._set_status("")

    # --- Status refresh loop -------------------------------------------------

    def _start_status_loop(self) -> None:
        if self._status_task is None or self._status_task.done():
            self._status_task = asyncio.create_task(self._status_loop())

    def _stop_status_loop(self) -> None:
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
        self._status_task = None

    async def _status_loop(self) -> None:
        try:
            while self._desired_live:
                await asyncio.sleep(self.status_refresh_seconds)
                if not self._desired_live:
                    break
                # Self-heal the voice connection if it dropped.
                await self._ensure_connected()
                await self._refresh_status()
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Status loop error")
