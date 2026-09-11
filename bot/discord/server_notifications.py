"""Create read-only server channels and delete match alerts after 30 minutes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
import time
from pathlib import Path

import discord

from bot.config import staging_enabled
from bot.storage.notification_store import NotificationStore
from bot.discord.server_stats import label_for
from bot.tracking.reforger_monitor import ReforgerMonitor
from bot.discord.timer_bot import TimerBot
from bot.discord.category_timer import CategoryTimers
from bot.storage.account_links import AccountLinks
from bot.discord.join_oyb import prepare_join_channel
from bot.ranks.rank_sync import RankSync
from bot.discord.rank_command import RankCommand
from bot.storage.combat_store import migrate as migrate_combat
from bot.tracking.combat_ingestor import CombatIngestor
from bot.discord.stats_command import StatsCommand
from bot.discord.leaderboard_display import LeaderboardDisplay
from bot.ranks.message_xp import award_message
from bot.storage.maintenance import Maintenance
from bot.discord.server_stats import ServerStats

logger = logging.getLogger("reforger.notifications")
ANNOUNCEMENT_TTL = 30 * 60
SERVERS_MARKER = "OYB • Servers • Settings, rules and match notifications"
SERVERS_CARD_MARKER = "OYB • Servers overview"
RULES_MARKER = "OYB • In-game rules"
ANNOUNCE_MARKER = "OYB • Match announcements"


def server_status_line(bot, server):
    if not server.enabled:
        return "⚫ Coming soon — not active yet."
    match = getattr(bot, "match_times", {}).get(server.id)
    if match is not None:
        return f"🟢 **Match live** — started <t:{int(match[0])}:t> · <t:{int(match[0])}:R>"
    monitor = next((m for sid, m in getattr(bot, "monitors", []) if sid == server.id), None)
    if monitor is not None and getattr(monitor, "online", False):
        return "🟡 Online — waiting for a match to start."
    return "🔴 Offline — server is not running."


def servers_embed(bot):
    """One combined card: every server's live status and settings."""
    embed = discord.Embed(
        title="🎮 OYB Servers",
        description="Live status for all OYB servers. **@everyone** is pinged in the "
                    "announcements channel when a match starts. This channel is read-only.",
        colour=0x2ECC71)
    for server in bot.config.servers:
        value = f"{server_status_line(bot, server)}\n**Settings:** {server.settings}"
        embed.add_field(name=label_for(server), value=value[:1024], inline=False)
    embed.set_footer(text=SERVERS_CARD_MARKER)
    return embed


def rules_embed(bot):
    """Universal in-game rules (identical across all servers), like the old cards."""
    rules = next((s.rules for s in bot.config.servers if s.enabled),
                 bot.config.servers[0].rules)
    embed = discord.Embed(title="🎮 OYB · In-game rules", description=rules[:4096],
                          colour=0x5865F2)
    embed.set_footer(text=RULES_MARKER)
    return embed


def readonly_overwrites(guild, hidden=False):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=not hidden, read_message_history=True, send_messages=False,
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


class NotificationBot(TimerBot):
    def __init__(self, config):
        super().__init__(config.guild_id, config.voice_channel_id,
                         config.status_refresh_seconds, config.join_voice_channel)
        self.allowed_mentions = discord.AllowedMentions.none()
        self.config = config
        self.store = NotificationStore(config.state_path)
        self.account_links = AccountLinks(os.getenv("ACCOUNT_LINKS_DB", str(Path(config.state_path).with_name("account_links.sqlite3"))))
        self.channels_by_server = {}
        self._server_channel = None
        self._servers_card_id = None
        self.announce_channel = None
        self.roles_by_server = {}
        # Wall clock for Discord timestamps; monotonic time for precise elapsed.
        self.match_times = {}
        self.category_timers = CategoryTimers(self)
        self._dirty_cards = set()
        self.notification_role_lock = asyncio.Lock()
        self.monitors = []
        self._jobs = []
        self._trackers = []
        self.rank_sync = RankSync(self)
        self.rank_command = RankCommand(self)
        migrate_combat(self.account_links.db)
        self.stats_command = StatsCommand(self)
        self.leaderboard_display = LeaderboardDisplay(self)
        self.combat_ingestor = CombatIngestor(self)
        self.server_stats = ServerStats(self)
        self.maintenance = Maintenance(self)
        self._boot_lock = asyncio.Lock()
        self._booted = False

    async def setup_hook(self):
        self.leaderboard_display.register()
        from bot.discord.link_review import AlertsControlView, ReviewButtons
        self.add_view(AlertsControlView(self))
        self.add_view(ReviewButtons(self))
        self._jobs.append(asyncio.create_task(self.rank_command.register()))

    async def on_message(self, message):
        await award_message(self, message)

    async def on_ready(self):
        if self.voice_channel_id:
            await self._clear_status()
            await self._disconnect()
        async with self._boot_lock:
            if self._booted:
                return
            guild = self.get_guild(self.config.guild_id)
            if guild is None or guild.me is None:
                logger.error("Configured Discord server not found; invite the bot first.")
                await self.close()
                return
            try:
                await self.prepare_servers(guild)
                try:
                    await self.prepare_announcement_channel(guild)
                except Exception:
                    logger.exception("Announcements channel unavailable; alerts fall back to #servers")
                await prepare_join_channel(self, guild, readonly_overwrites(guild, hidden=staging_enabled()))
                try:
                    from bot.discord.link_review import prepare_review_channel
                    await prepare_review_channel(self, guild)
                except Exception:
                    logger.exception("Link-request review channel unavailable; check Manage Channels/Roles")
                from bot.discord.server_layout import cleanup_legacy_layout, remove_timer_categories
                await cleanup_legacy_layout(self, guild)
                # Remove the retired per-server timer categories; the SERVER STATS
                # channels now carry the live match counter.
                await remove_timer_categories(self, guild)
                try:
                    await self.server_stats.prepare(guild)
                except Exception:
                    logger.exception("Live server-stats channels unavailable; check Manage Channels")
            except Exception:
                logger.exception("Channel setup failed. Check bot permissions.")
                await self.close()
                return
            self._booted = True
            for server in self.config.servers:
                if not server.enabled:
                    continue

                async def started(elapsed, server=server):
                    age = max(0.0, elapsed)
                    self.match_times[server.id] = (time.time() - age, time.monotonic() - age)
                    self._dirty_cards.add(server.id)
                    await self.refresh_servers()
                    await self.server_stats.tick()  # push the state change immediately
                    monitor = next(m for sid, m in self.monitors if sid == server.id)
                    # Recovered old matches must not trigger a fresh notification.
                    if elapsed >= ANNOUNCEMENT_TTL:
                        return
                    now = time.time()
                    channel = self.announce_channel or self.channels_by_server.get(server.id)
                    if channel is None:
                        return
                    self.store.enqueue(
                        server.id, monitor.session_key, channel.id,
                        server.name, now - elapsed, now,
                    )

                async def ended(server=server):
                    self.match_times.pop(server.id, None)
                    self._dirty_cards.add(server.id)
                    await self.refresh_servers()
                    await self.server_stats.tick()  # push the state change immediately

                monitor = ReforgerMonitor(
                    log_dir=server.log_dir, on_session_start=started,
                    on_session_end=ended, stale_seconds=self.config.stale_seconds,
                    a2s_host=server.a2s_host, a2s_port=server.a2s_port,
                )
                self.monitors.append((server.id, monitor))
                self._jobs.append(asyncio.create_task(monitor.run()))
                if os.getenv("PLAYTIME_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
                    from bot.tracking.playtime_tracker import Tracker
                    tracker = Tracker(server.log_dir,
                                      os.getenv("PLAYTIME_DB", "data/playtime.sqlite3"),
                                      server.id)
                    self._trackers.append(tracker)
                    self._jobs.append(asyncio.create_task(tracker.run()))
            self._jobs.append(asyncio.create_task(self.delivery_loop()))
            self._jobs.append(asyncio.create_task(self.rank_sync.run()))
            self._jobs.append(asyncio.create_task(self.combat_ingestor.run()))
            self._jobs.append(asyncio.create_task(self.leaderboard_display.run()))
            self._jobs.append(asyncio.create_task(self.server_stats.run()))
            self._jobs.append(asyncio.create_task(self.maintenance.run()))
            logger.info("Ready: one combined servers card + announcements channel; %s active game monitors",
                        len(self.monitors))

    async def _servers_channel(self, guild):
        matches = [c for c in guild.text_channels if c.topic == SERVERS_MARKER]
        if len(matches) > 1:
            raise RuntimeError("Multiple managed #servers channels; resolve duplicates.")
        channel = matches[0] if matches else None
        if channel is None:
            record = self.store.channel("servers")
            saved = guild.get_channel(record["channel"]) if record else None
            if isinstance(saved, discord.TextChannel) and saved.topic == SERVERS_MARKER:
                channel = saved
        overwrites = readonly_overwrites(guild, hidden=staging_enabled())
        if channel is None:
            channel = await guild.create_text_channel(
                "servers", topic=SERVERS_MARKER, overwrites=overwrites,
                reason="OYB read-only server information and match notifications")
        else:
            await channel.edit(name="servers", overwrites=overwrites, category=None,
                               sync_permissions=False, reason="Restore read-only server channel permissions")
        return channel

    async def prepare_servers(self, guild):
        """One combined #servers card: every server's live status, settings and rules."""
        channel = await self._servers_channel(guild)
        self._server_channel = channel
        for server in self.config.servers:
            self.channels_by_server[server.id] = channel
        record = self.store.channel("servers")
        card = None
        if record and record["channel"] == channel.id and record["info"]:
            try:
                candidate = await channel.fetch_message(record["info"])
                if owns_message(candidate, self.user.id, SERVERS_CARD_MARKER):
                    card = candidate
            except discord.NotFound:
                pass
        if card is None:
            async for candidate in channel.history(limit=50):
                if owns_message(candidate, self.user.id, SERVERS_CARD_MARKER):
                    card = candidate
                    break
        embeds = [servers_embed(self), rules_embed(self)]
        if card is None:
            card = await channel.send(embeds=embeds, silent=True,
                                      allowed_mentions=discord.AllowedMentions.none())
        else:
            await card.edit(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
        self._servers_card_id = card.id
        self.store.save_channel("servers", channel.id, card.id)
        # Remove the retired per-server cards (each carried an 'In-game rules' embed).
        async for old in channel.history(limit=50):
            if old.id != card.id and old.author.id == self.user.id and any(
                    e.footer and e.footer.text and "In-game rules" in e.footer.text for e in old.embeds):
                try:
                    await old.delete()
                except discord.NotFound:
                    pass

    async def prepare_announcement_channel(self, guild):
        """Resolve where match-start alerts post: MATCH_ALERT_CHANNEL_ID, else the
        announcements channel, else a created #announcements."""
        record = self.store.channel("__announce__")
        channel = guild.get_channel(record["channel"]) if record else None
        if not isinstance(channel, discord.TextChannel):
            channel = None
        if channel is None:
            configured = os.getenv("MATCH_ALERT_CHANNEL_ID", "").strip()
            if configured.isdigit():
                candidate = guild.get_channel(int(configured))
                if isinstance(candidate, discord.TextChannel):
                    channel = candidate
        if channel is None:
            channel = next((c for c in guild.text_channels
                            if "announcement" in c.name.casefold()), None)
        if channel is None:
            channel = await guild.create_text_channel(
                "announcements", topic=ANNOUNCE_MARKER, overwrites=readonly_overwrites(guild),
                reason="OYB match-start announcements")
        self.announce_channel = channel
        self.store.save_channel("__announce__", channel.id)
        return channel

    async def _refresh_status(self):
        pass  # Match time is displayed in the category and information card.

    def _start_status_loop(self):
        # The sidebar changes only with match state or a gateway reconnect.
        # Discord renders relative times in the permanent information card.
        pass

    async def refresh_servers(self):
        record = self.store.channel("servers")
        if not record or not record["info"] or self._server_channel is None:
            return
        try:
            message = self._server_channel.get_partial_message(record["info"])
            await message.edit(embeds=[servers_embed(self), rules_embed(self)],
                               allowed_mentions=discord.AllowedMentions.none())
            self._dirty_cards.clear()
        except discord.HTTPException:
            logger.exception("Could not refresh the servers card; retry pending")

    async def delivery_loop(self):
        cleaned = 0
        while not self.is_closed():
            if self._dirty_cards:
                await self.refresh_servers()
            for row in self.store.pending():
                try:
                    await self.deliver_or_delete(row)
                except Exception:
                    logger.exception("Announcement retry pending for %s", row["server"])
            if time.monotonic() - cleaned >= 300:
                from bot.discord.server_layout import cleanup_legacy_layout
                guild = self.get_guild(self.config.guild_id)
                if guild:
                    await cleanup_legacy_layout(self, guild)
                cleaned = time.monotonic()
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
            if time.time() >= row["started"] + ANNOUNCEMENT_TTL:
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
            if time.time() >= row["started"] + ANNOUNCEMENT_TTL:
                self.store.finish(row)
                return
            embed = discord.Embed(
                title=f"🟢 {row['name']} — match started",
                description=f"A new match is live. Join the server!\n"
                            f"Started <t:{int(row['started'])}:R>.\n\n"
                            f"Expires <t:{int(row['started'] + ANNOUNCEMENT_TTL)}:R> "
                            "(30 minutes after match start).",
                colour=0x2ECC71,
            )
            embed.timestamp = datetime.fromtimestamp(int(row["started"]), timezone.utc)
            embed.set_footer(text="OYB • Match notifications")
            message = await channel.send(content="@everyone", embed=embed,
                                         allowed_mentions=discord.AllowedMentions(
                                             everyone=True, users=False, roles=False, replied_user=False))
            logger.info("Discord accepted match announcement for %s", row["server"])
        expires = row["started"] + ANNOUNCEMENT_TTL
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
        if self.voice_channel_id and self._desired_live:
            try:
                await asyncio.wait_for(self.handle_session_end(), timeout=10)
            except Exception:
                logger.exception("Could not clear voice timer during shutdown")
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
        bot.account_links.close()
