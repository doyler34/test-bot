"""
Arma Reforger -> Discord voice-channel session timer.

Watches an (unmodded) Reforger dedicated server's shipped console.log for match
start/end and updates a voice channel's status with live match uptime.
Optionally joins/leaves voice for clients that display connection timers. See README.md for details.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from config import ConfigError, load_config, setup_logging
from reforger_monitor import ReforgerMonitor
from timer_bot import TimerBot

logger = logging.getLogger("reforger.main")


async def run_bot(config) -> None:

    bot = TimerBot(
        guild_id=config.guild_id,
        voice_channel_id=config.voice_channel_id,
        status_refresh_seconds=config.status_refresh_seconds,
        join_voice_channel=config.join_voice_channel,
    )

    monitor = ReforgerMonitor(
        log_dir=config.log_dir,
        on_session_start=bot.handle_session_start,
        on_session_end=bot.handle_session_end,
        stale_seconds=config.session_stale_seconds,
        a2s_host=config.a2s_host,
        a2s_port=config.a2s_port,
    )

    async def run_monitor() -> None:
        await bot.wait_until_ready()
        await monitor.run()

    # Graceful shutdown on SIGINT/SIGTERM.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # e.g. Windows

    async with bot:
        monitor_task = asyncio.create_task(run_monitor(), name="monitor")
        bot_task = asyncio.create_task(bot.start(config.discord_token), name="bot")
        stop_task = asyncio.create_task(stop.wait(), name="stop")

        done, _ = await asyncio.wait(
            {monitor_task, bot_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done:
            logger.info("Shutdown signal received")
        else:
            logger.warning("A core task exited; shutting down")

        # Best-effort: leave the VC and clear the channel status on the way out.
        try:
            await asyncio.wait_for(bot.handle_session_end(), timeout=10)
        except Exception:  # noqa: BLE001
            logger.debug("Cleanup during shutdown failed", exc_info=True)

        for task in (monitor_task, bot_task, stop_task):
            task.cancel()
        await asyncio.gather(monitor_task, bot_task, stop_task, return_exceptions=True)

    logger.info("Shutdown complete")


async def run() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    if os.getenv("SERVERS_CONFIG"):
        from notification_config import load_notification_config
        from server_notifications import run_notifications
        await run_notifications(load_notification_config())
        return
    config = load_config()
    if os.getenv("PLAYTIME_ENABLED", "").strip().lower() not in ("1", "true", "yes", "on"):
        await run_bot(config)
        return
    from playtime_tracker import Tracker
    tracker = Tracker(config.log_dir, os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"),
                      os.getenv("PLAYTIME_SERVER_ID", "server-1"))
    bot_task = asyncio.create_task(run_bot(config), name="timer")
    tracker_task = asyncio.create_task(tracker.run(), name="playtime")
    try:
        done, _ = await asyncio.wait(
            {bot_task, tracker_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in (bot_task, tracker_task):
            task.cancel()
        await asyncio.gather(bot_task, tracker_task, return_exceptions=True)
        tracker.close()


def main() -> None:
    setup_logging()
    try:
        asyncio.run(run())
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
