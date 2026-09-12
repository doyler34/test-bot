"""
External monitor for an unmodded Arma Reforger dedicated server.

It tails the vanilla server ``console.log`` (as shipped to a readable directory)
and detects match/session lifecycle from stock log lines — no Reforger mod
required. The regexes are ported from ReforgerJS
(github.com/ZSU-GG-Reforger/ReforgerJS, reforger-server/log-parser/regexHandlers).

Signals used:
  * ``SCR_BaseGameMode::OnGameStateChanged = GAME``      -> session start
  * ``SCR_BaseGameMode::OnGameStateChanged = POSTGAME``  -> session end
  * ``FPS: .., Mem: .. kB, Player: ..,``                 -> liveness heartbeat

``SCR_BaseGameMode`` cycles GAME -> POSTGAME -> GAME within the same process, so
``= GAME`` is a genuine per-match trigger (Conflict match uptime), not just a
boot event. A new ``logs_*`` folder appearing means the server process
restarted/crashed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from glob import glob
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("reforger.monitor")

# --- Vanilla log-line patterns (ported verbatim from ReforgerJS) --------------

GAME_START_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+SCRIPT\s+:\s+"
    r"SCR_BaseGameMode::OnGameStateChanged\s+=\s+GAME"
)
GAME_END_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+SCRIPT\s+:\s+"
    r"SCR_BaseGameMode::OnGameStateChanged\s+=\s+POSTGAME"
)
HEARTBEAT_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+\S+\s+:\s+FPS:\s+([\d\.]+),"
    r".*?Mem:\s+(\d+)\s+kB,.*?Player:\s+(\d+),"
)


class LineEvent(Enum):
    GAME_START = "game_start"
    GAME_END = "game_end"
    HEARTBEAT = "heartbeat"


@dataclass
class ParsedLine:
    event: LineEvent
    players: Optional[int] = None


def parse_line(line: str) -> Optional[ParsedLine]:
    """Classify a single log line. Returns None if it is not of interest."""
    if GAME_START_RE.match(line):
        return ParsedLine(LineEvent.GAME_START)
    if GAME_END_RE.match(line):
        return ParsedLine(LineEvent.GAME_END)
    m = HEARTBEAT_RE.match(line)
    if m:
        return ParsedLine(LineEvent.HEARTBEAT, players=int(m.group(4)))
    return None


# --- Log file resolution ------------------------------------------------------

LOG_FILENAME = "console.log"


def resolve_session_file(log_dir: str) -> Optional[str]:
    """Return the path to the newest session's console.log.

    Handles two layouts:
      * ``log_dir`` is Reforger's ``profile/logs`` containing ``logs_*`` folders
        (a new folder per server run) -> pick the most recent folder.
      * ``log_dir`` is a single session folder that directly contains
        ``console.log`` -> use it as-is.
    """
    session_dirs = sorted(
        (d for d in glob(os.path.join(log_dir, "logs_*")) if os.path.isdir(d))
    )
    candidate_dir = session_dirs[-1] if session_dirs else log_dir
    path = os.path.join(candidate_dir, LOG_FILENAME)
    return path if os.path.isfile(path) else None


# --- Monitor ------------------------------------------------------------------

Callback = Callable[[], Awaitable[None]]
StartCallback = Callable[[float], Awaitable[None]]
TIMESTAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})")


@dataclass
class ReforgerMonitor:
    log_dir: str
    on_session_start: StartCallback
    on_session_end: Callback
    stale_seconds: int = 120
    poll_interval: float = 2.0
    a2s_host: Optional[str] = None
    a2s_port: Optional[int] = None

    # internal state
    session_key: str = field(default="", init=False)
    initialized: bool = field(default=False, init=False)
    _live: bool = field(default=False, init=False)
    _current_path: Optional[str] = field(default=None, init=False)
    _pos: int = field(default=0, init=False)
    _last_heartbeat: float = field(default=0.0, init=False)

    _clock_day: float = field(default=0.0, init=False)
    _last_clock: Optional[float] = field(default=None, init=False)
    _log_clock: float = field(default=0.0, init=False)
    _log_mtime: float = field(default=0.0, init=False)

    def _advance_clock(self, line: str) -> float:
        """Unwrap time-of-day stamps using every log line, including midnight."""
        match = TIMESTAMP_RE.match(line)
        if match:
            h, m, s, ms = map(int, match.groups())
            clock = h * 3600 + m * 60 + s + ms / 1000
            if self._last_clock is not None and self._last_clock - clock > 43200:
                self._clock_day += 86400
            self._last_clock = clock
            self._log_clock = max(self._log_clock, self._clock_day + clock)
        return self._log_clock

    @property
    def online(self) -> bool:
        """True when the server is up: a recent FPS heartbeat, or failing that a
        log file that is still being written (covers builds that log the
        heartbeat line differently). A down server stops writing, so its log
        goes stale and this reads offline.
        """
        if self._last_heartbeat and (time.monotonic() - self._last_heartbeat) < self.stale_seconds:
            return True
        return bool(self._log_mtime) and (time.time() - self._log_mtime) < self.stale_seconds

    async def run(self) -> None:
        """Main loop. Runs until cancelled."""
        logger.info("Monitor starting; watching %s", self.log_dir)
        while True:
            try:
                await self._tick()
                self.initialized = True
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the monitor alive
                logger.exception("Monitor tick failed; retrying")
            await asyncio.sleep(self.poll_interval)

    async def _tick(self) -> None:
        path = resolve_session_file(self.log_dir)
        if path is None:
            # No log file yet (server never started or logs not shipped).
            if self._live:
                logger.warning("Log file disappeared; ending session")
                await self._end()
            # Re-read history if the same path returns after a wipe or a gap.
            self._current_path = None
            return

        try:
            self._log_mtime = os.path.getmtime(path)
        except OSError:
            self._log_mtime = 0.0

        if path != self._current_path:
            await self._on_rotation(path)

        await self._read_new_lines()
        await self._check_staleness()

    async def _on_rotation(self, path: str) -> None:
        """A new session folder appeared (or first run): reset and re-scan."""
        first = self._current_path is None
        if not first:
            logger.info("New log session detected (%s); server restarted", path)
            if self._live:
                await self._end()
        self._current_path = path
        self._pos = 0
        self._clock_day = 0.0
        self._last_clock = None
        self._log_clock = 0.0
        # Establish current state from the existing file contents, then tail.
        await self._read_new_lines(initial=True)

    async def _read_new_lines(self, initial: bool = False) -> None:
        path = self._current_path
        if not path or not os.path.isfile(path):
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        if size < self._pos:
            logger.info("Log truncated; rebuilding session from remaining history")
            if self._live:
                await self._end()
            self._pos = 0
            self._clock_day = 0.0
            self._last_clock = None
            self._log_clock = 0.0
            initial = True
        if size == self._pos:
            return

        loop = asyncio.get_running_loop()
        chunk, new_pos, modified = await loop.run_in_executor(
            None, self._read_from, path, self._pos
        )
        self._pos = new_pos
        events = []
        for line in chunk.splitlines():
            clock = self._advance_clock(line)
            parsed = parse_line(line)
            if parsed is not None:
                events.append((parsed.event, clock))

        # Relative log times avoid dependence on the server's time zone.
        # mtime supplies time since the last write; shipped logs should preserve it.
        tail_age = max(0.0, time.time() - modified)
        now = time.monotonic()

        def age(clock: float) -> float:
            return max(0.0, self._log_clock - clock + tail_age)

        if initial:
            # Reduce history first. Do not replay old matches into Discord.
            live_start = None
            heartbeat = None
            for event, clock in events:
                if event is LineEvent.GAME_START:
                    if live_start is None:
                        live_start = clock
                    heartbeat = clock
                elif event is LineEvent.GAME_END:
                    live_start = None
                elif event is LineEvent.HEARTBEAT:
                    heartbeat = clock
            if heartbeat is not None:
                self._last_heartbeat = now - age(heartbeat)
            if live_start is not None:
                fresh_log = bool(self._log_mtime) and (time.time() - self._log_mtime) < self.stale_seconds
                fresh_beat = heartbeat is not None and age(heartbeat) < self.stale_seconds
                if fresh_beat or fresh_log or await self._server_alive_via_a2s():
                    if not fresh_beat:
                        self._last_heartbeat = now
                    await self._start(age(live_start), f"{path}:{live_start:.3f}")
            logger.info("Initial scan complete for %s (live=%s)", path, self._live)
            return

        for event, clock in events:
            if event is LineEvent.HEARTBEAT:
                self._last_heartbeat = now - age(clock)
            elif event is LineEvent.GAME_START:
                self._last_heartbeat = now - age(clock)
                if not self._live:
                    await self._start(age(clock), f"{path}:{clock:.3f}")
            elif event is LineEvent.GAME_END and self._live:
                await self._end()

        if chunk and not events:
            # Fresh log lines the parser doesn't recognize still mean the server
            # is writing; keep the session's liveness current.
            self._last_heartbeat = now

    @staticmethod
    def _read_from(path: str, pos: int) -> tuple[str, int, float]:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(pos)
            data = fh.read()
            return data, fh.tell(), os.fstat(fh.fileno()).st_mtime

    async def _check_staleness(self) -> None:
        if not self._live:
            return
        fresh_log = bool(self._log_mtime) and (time.time() - self._log_mtime) < self.stale_seconds
        fresh_beat = bool(self._last_heartbeat) and (time.monotonic() - self._last_heartbeat) < self.stale_seconds
        if fresh_log or fresh_beat:
            return
        if not self._log_mtime and self._last_heartbeat == 0.0:
            return  # no liveness signal yet; nothing to judge by

        logger.warning("Server log went stale (threshold %ds)", self.stale_seconds)
        if await self._server_alive_via_a2s():
            # Logs stalled (shipping hiccup) but server is up: avoid flapping.
            logger.info("A2S reports server up; keeping session alive")
            self._last_heartbeat = time.monotonic()
            return
        logger.warning("Server appears down; ending session")
        await self._end()

    async def _server_alive_via_a2s(self) -> bool:
        if not (self.a2s_host and self.a2s_port):
            return False
        try:
            import a2s  # type: ignore

            await a2s.ainfo((self.a2s_host, self.a2s_port), timeout=5.0)
            return True
        except ImportError:
            logger.debug("python-a2s not installed; skipping A2S check")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.debug("A2S query failed: %s", exc)
            return False

    async def _start(self, elapsed_seconds: float = 0.0, session_key: str = "") -> None:
        self.session_key = session_key
        self._live = True
        logger.info("Session START detected (recovered elapsed: %.0fs)", elapsed_seconds)
        await self.on_session_start(elapsed_seconds)

    async def _end(self) -> None:
        self._live = False
        logger.info("Session END detected")
        await self.on_session_end()
