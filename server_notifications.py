"""Create read-only server channels and delete match alerts after 30 minutes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
import time

import discord

from notification_store import NotificationStore
from reforger_monitor import ReforgerMonitor

logger = logging.getLogger("reforger.notifications")
ANNOUNCEMENT_TTL = 30 * 60


def information_embeds(server):
    about = discord.Embed(
        title=server.name,
        description="Server information" if server.enabled else "Coming soon — not active yet.",
        colour=0x2ECC71 if server.enabled else 0x95A5A6,
    )
    about.add_field(name="Settings", value=server.settings, inline=False)
    about.add_field(
        name="Match notifications",
        value="Open this channel's Notification Settings and choose **All Messages**. "
              "This channel is read-only. Start announcements disappear after 30 minutes. "
              "Your Discord and device notification settings still apply.",
        inline=False,
    )
    rules = discord.Embed(title="🎮 OYB · In-game rules", description=server.rules,
                          colour=0x5865F2)
    rules.set_footer(text=f"OYB • {server.id.replace('-', ' ').title()} • In-game rules")
    return [about, rules]


def readonly_overwrites(guild):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False,
            create_public_threads=False, create_private_threads=False,
            send_messages_in_threads=False, add_reactions=False,
            use_application_commands=False, use_external_apps=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=True,
            embed_links=True,
        ),
    }


def owns_message(message, user_id, marker):
    return message.author.id == user_id and any(
        embed.footer.text == marker for embed in message.embeds
    )


class NotificationBot(discord.Client):
    def __init__(self, config):
        super().__init__(intents=discord.Intents.default(),
                         allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.store = NotificationStore(config.state_path)
        self.channels_by_server = {}
        self.monitors = []
        self._jobs = []
        self._trackers = []
        self._boot_lock = asyncio.Lock()
        self._booted = False

    async def on_ready(self):
        async with self._boot_lock:
            if self._booted:
                return
            guild = self.get_guild(self.config.guild_id)
            if guild is None or guild.me is None:
                logger.error("Configured Discord server not found; invite the bot first.")
                await self.close()
                return
            try:
                for server in self.config.servers:
                    await self.prepare_channel(guild, server)
            except Exception:
                logger.exception("Channel setup failed. Check bot permissions.")
                await self.close()
                return
            self._booted = True
            for server in self.config.servers:
                if not server.enabled:
                    continue

                async def started(elapsed, server=server):
                    monitor = next(m for sid, m in self.monitors if sid == server.id)
                    # Recovered old matches must not trigger a fresh notification.
                    if elapsed >= ANNOUNCEMENT_TTL:
                        return
                    now = time.time()
                    self.store.enqueue(
                        server.id, monitor.session_key,
                        self.channels_by_server[server.id].id,
                        server.name, now - elapsed, now,
                    )

                async def ended():
                    # The information card stays; each alert has its own expiry.
                    pass

                monitor = ReforgerMonitor(
                    log_dir=server.log_dir, on_session_start=started,
                    on_session_end=ended, stale_seconds=self.config.stale_seconds,
                    a2s_host=server.a2s_host, a2s_port=server.a2s_port,
                )
                self.monitors.append((server.id, monitor))
                self._jobs.append(asyncio.create_task(monitor.run()))
                if os.getenv("PLAYTIME_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
                    from playtime_tracker import Tracker
                    tracker = Tracker(server.log_dir,
                                      os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"),
                                      server.id)
                    self._trackers.append(tracker)
                    self._jobs.append(asyncio.create_task(tracker.run()))
            self._jobs.append(asyncio.create_task(self.delivery_loop()))
            logger.info("Ready: three read-only channels; %s active game monitors",
                        len(self.monitors))

    async def prepare_channel(self, guild, server):
        marker = f"OYB • {server.id} • Settings, rules and match notifications"
        record = self.store.channel(server.id)
        channel = guild.get_channel(record["channel"]) if record else None
        if channel is not None and (
            not isinstance(channel, discord.TextChannel) or channel.topic != marker
        ):
            raise RuntimeError(f"Saved channel for {server.id} is no longer bot-managed.")
        if channel is None:
            matches = [c for c in guild.text_channels if c.topic == marker]
            if len(matches) > 1:
                raise RuntimeError(f"Multiple managed channels for {server.id}; resolve duplicates.")
            channel = matches[0] if matches else None
        overwrites = readonly_overwrites(guild)
        if channel is None:
            channel = await guild.create_text_channel(
                server.channel_name, topic=marker, overwrites=overwrites,
                reason="OYB read-only server information and match notifications",
            )
        else:
            await channel.edit(name=server.channel_name, overwrites=overwrites,
                               reason="Restore read-only server channel permissions")
        self.channels_by_server[server.id] = channel
        info_id = record["info"] if record and record["channel"] == channel.id else None
        self.store.save_channel(server.id, channel.id, info_id)
        info = None
        info_marker = f"OYB • {server.id.replace('-', ' ').title()} • In-game rules"
        if info_id:
            try:
                candidate = await channel.fetch_message(info_id)
                if owns_message(candidate, self.user.id, info_marker):
                    info = candidate
            except discord.NotFound:
                pass
        if info is None:
            async for candidate in channel.history(limit=None):
                if owns_message(candidate, self.user.id, info_marker):
                    info = candidate
                    break
        embeds = information_embeds(server)
        if info is None:
            info = await channel.send(embeds=embeds, silent=True,
                                      allowed_mentions=discord.AllowedMentions.none())
        else:
            await info.edit(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
        self.store.save_channel(server.id, channel.id, info.id)

    async def delivery_loop(self):
        while not self.is_closed():
            for row in self.store.pending():
                try:
                    await self.deliver_or_delete(row)
                except Exception:
                    logger.exception("Announcement retry pending for %s", row["server"])
            await asyncio.sleep(5)

    async def deliver_or_delete(self, row):
        channel = self.get_channel(row["channel"])
        if channel is None:
            try:
                channel = await self.fetch_channel(row["channel"])
            except discord.NotFound:
                self.store.finish(row)
                return
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Announcement channel is not a text channel.")
        if row["message"] is not None:
            if time.time() >= row["expires"]:
                try:
                    await channel.get_partial_message(row["message"]).delete()
                except discord.NotFound:
                    pass
                self.store.finish(row)
                logger.info("Deleted expired announcement for %s", row["server"])
            return

        # Recover an accepted send if the process died before saving its message ID.
        after = datetime.fromtimestamp(row["queued"] - 5, timezone.utc)
        message = None
        async for candidate in channel.history(limit=None, after=after):
            if candidate.author.id == self.user.id and any(
                e.timestamp is not None
                and int(e.timestamp.timestamp()) == int(row["started"])
                and e.title == f"🟢 {row['name']} — match started"
                for e in candidate.embeds
            ):
                message = candidate
                break
        if message is None:
            if time.time() - row["queued"] >= ANNOUNCEMENT_TTL:
                self.store.finish(row)
                return
            embed = discord.Embed(
                title=f"🟢 {row['name']} — match started",
                description=f"A new match is live. Join the server!\n"
                            f"Started <t:{int(row['started'])}:R>.\n\n"
                            "This announcement will be removed after 30 minutes.",
                colour=0x2ECC71,
            )
            embed.timestamp = datetime.fromtimestamp(int(row["started"]), timezone.utc)
            embed.set_footer(text="OYB • Match notifications")
            message = await channel.send(embed=embed,
                                         allowed_mentions=discord.AllowedMentions.none())
            logger.info("Discord accepted match announcement for %s", row["server"])
        expires = message.created_at.timestamp() + ANNOUNCEMENT_TTL
        self.store.sent(row, message.id, expires)
        if expires <= time.time():
            try:
                await message.delete()
            except discord.NotFound:
                pass
            self.store.finish(row)

    async def close(self):
        for task in self._jobs:
            task.cancel()
        if self._jobs:
            await asyncio.gather(*self._jobs, return_exceptions=True)
        await super().close()
        # The database stays open until the surrounding runner finishes callbacks.


async def run_notifications(config):
    import signal
    bot = NotificationBot(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        async with bot:
            run_task = asyncio.create_task(bot.start(config.token))
            stop_task = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if run_task in done:
                    await run_task
            finally:
                await bot.close()
                run_task.cancel()
                stop_task.cancel()
                await asyncio.gather(run_task, stop_task, return_exceptions=True)
    finally:
        for tracker in bot._trackers:
            tracker.close()
        bot.store.close()
