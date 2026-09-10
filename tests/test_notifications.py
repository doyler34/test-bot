"""Read-only provisioning, durable delivery and 30-minute cleanup regressions."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from config import ConfigError
from notification_config import NotificationConfig, Server, read_servers
from server_notifications import (NotificationBot, readonly_overwrites, servers_embed,
                                  rules_embed, SERVERS_CARD_MARKER, ANNOUNCE_MARKER)


async def history(messages):
    for message in messages:
        yield message


def message(mid, embeds=None, created=1000):
    return SimpleNamespace(
        id=mid, author=SimpleNamespace(id=99), embeds=embeds or [],
        created_at=datetime.fromtimestamp(created, timezone.utc),
        edit=AsyncMock(), delete=AsyncMock(),
    )


class ConfigurationTests(unittest.TestCase):
    def test_bundled_configuration_has_only_one_active_monitor(self):
        path = Path(__file__).parents[1] / "servers.example.json"
        with patch.dict(os.environ, {"REFORGER_LOG_DIR": "/real/logs"}):
            servers = read_servers(str(path))
        self.assertEqual([s.enabled for s in servers], [True, False, False])
        self.assertEqual(servers[0].log_dir, "/real/logs")
        self.assertIn("400", servers[0].rules)
        self.assertIn("6 supply", servers[0].rules)
        # The combined status + universal rules card fits the 6000-char budget.
        bot = SimpleNamespace(config=SimpleNamespace(servers=servers), match_times={}, monitors=[])
        self.assertLess(len(servers_embed(bot)) + len(rules_embed(bot)), 6000)
        self.assertIn("400", rules_embed(bot).description)  # rules are shown

    def test_rejects_duplicate_ids_and_missing_active_paths(self):
        data = json.loads((Path(__file__).parents[1] / "servers.example.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "servers.json"
            data[0]["log_dir"] = ""
            path.write_text(json.dumps(data))
            with self.assertRaises(ConfigError):
                read_servers(str(path))
            data[0]["log_dir"] = "/real/logs"
            data[1]["id"] = data[0]["id"]
            path.write_text(json.dumps(data))
            with self.assertRaises(ConfigError):
                read_servers(str(path))


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = Server("server-1", "Server 1", "server-1", "/logs", "Vanilla", "No TK.")
        self.config = NotificationConfig(
            "token", 1, (self.server,), str(Path(self.tmp.name) / "state.sqlite3")
        )
        self.bot = NotificationBot(self.config)
        self.bot._set_status = AsyncMock()
        self.bot.category_timers.prepare = AsyncMock()
        self.bot.rank_sync.run = AsyncMock()
        self.bot._connection.user = SimpleNamespace(id=99)
        join_patch = patch("server_notifications.prepare_join_channel", new_callable=AsyncMock)
        join_patch.start()
        self.addCleanup(join_patch.stop)
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 50
        self.channel.topic = "OYB • Servers • Settings, rules and match notifications"
        self.channel.history = Mock(side_effect=lambda **kwargs: history([]))
        self.channel.send = AsyncMock(return_value=message(100))
        self.channel.edit = AsyncMock()
        self.channel.fetch_message = AsyncMock()
        self.bot.get_channel = Mock(return_value=self.channel)
        self.bot.announce_channel = self.channel  # match alerts go here
        self.guild = Mock()
        self.guild.default_role = Mock()
        self.guild.me = Mock()
        self.guild.text_channels = []
        self.guild.fetch_channels = AsyncMock(return_value=[])
        self.guild.get_channel = Mock(return_value=self.channel)
        self.guild.create_text_channel = AsyncMock(return_value=self.channel)

    async def asyncTearDown(self):
        await self.bot.close()
        self.bot.store.close()
        self.bot.account_links.close()
        self.tmp.cleanup()

    def enqueue(self):
        self.bot.store.enqueue("server-1", "session-1", 50, "Server 1", 970, 1000)
        return self.bot.store.pending()[0]

    async def test_creates_one_readonly_card_and_reuses_it_on_restart(self):
        await self.bot.prepare_servers(self.guild)
        self.guild.create_text_channel.assert_awaited_once()
        kwargs = self.guild.create_text_channel.await_args.kwargs
        everyone = kwargs["overwrites"][self.guild.default_role]
        self.assertFalse(everyone.send_messages)
        self.assertFalse(everyone.create_public_threads)
        self.assertTrue(everyone.view_channel)
        self.assertTrue(kwargs["overwrites"][self.guild.me].send_messages)
        self.assertTrue(self.channel.send.await_args.kwargs["silent"])
        # Status embed + universal rules embed, no notification buttons.
        embeds = self.channel.send.await_args.kwargs["embeds"]
        self.assertEqual(len(embeds), 2)
        self.assertNotIn("view", self.channel.send.await_args.kwargs)
        self.assertEqual(self.bot.store.channel("servers")["info"], 100)
        # Restart: the saved card is edited in place, not recreated.
        card = message(100, [servers_embed(self.bot)])
        self.channel.fetch_message.return_value = card
        await self.bot.prepare_servers(self.guild)
        self.channel.send.assert_awaited_once()
        card.edit.assert_awaited_once()

    async def test_recovers_card_from_history_without_database_record(self):
        self.guild.text_channels = [self.channel]
        card = message(101, [servers_embed(self.bot)])
        self.channel.history.side_effect = lambda **kwargs: history([card])
        await self.bot.prepare_servers(self.guild)
        self.guild.create_text_channel.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.bot.store.channel("servers")["info"], 101)

    async def test_single_card_lists_every_server_with_rules_and_no_buttons(self):
        from dataclasses import replace
        servers = tuple(replace(self.server, id=f"server-{i}", name=f"Server {i}") for i in range(1, 4))
        self.bot.config = replace(self.config, servers=servers)
        await self.bot.prepare_servers(self.guild)
        self.channel.send.assert_awaited_once()
        kwargs = self.channel.send.await_args.kwargs
        self.assertNotIn("view", kwargs)  # roles scrapped -> no buttons
        status, rules = kwargs["embeds"]
        self.assertEqual(len(status.fields), 3)  # one status row per server
        self.assertIn("In-game rules", rules.title)

    async def test_removes_retired_per_server_cards(self):
        rules = discord.Embed().set_footer(text="OYB • Server 1 • In-game rules")
        old = message(90, [rules])
        card = message(100, [servers_embed(self.bot)])
        self.channel.send.return_value = card
        self.channel.history.side_effect = lambda **kwargs: history([old])
        await self.bot.prepare_servers(self.guild)
        old.delete.assert_awaited_once()

    async def test_announcement_channel_reuses_existing_else_creates(self):
        existing = Mock(spec=discord.TextChannel)
        existing.id, existing.name = 71, "announcements"
        self.guild.text_channels = [existing]
        await self.bot.prepare_announcement_channel(self.guild)
        self.guild.create_text_channel.assert_not_awaited()
        self.assertIs(self.bot.announce_channel, existing)
        self.assertEqual(self.bot.store.channel("__announce__")["channel"], 71)
        # None present -> one is created with the announce marker.
        self.guild.text_channels = []
        self.bot.store.db.execute("DELETE FROM channels WHERE server='__announce__'")
        self.bot.store.db.commit()
        self.guild.get_channel.return_value = None
        await self.bot.prepare_announcement_channel(self.guild)
        self.guild.create_text_channel.assert_awaited_once()
        self.assertEqual(self.guild.create_text_channel.await_args.kwargs["topic"], ANNOUNCE_MARKER)

    async def test_send_and_delete_after_30_minutes_not_before(self):
        row = self.enqueue()
        with patch("server_notifications.time.time", return_value=1000):
            await self.bot.deliver_or_delete(row)
        kwargs = self.channel.send.await_args.kwargs
        self.assertTrue(kwargs["allowed_mentions"].everyone)
        self.assertEqual(kwargs["content"], "@everyone")
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertIn("<t:970:R>", kwargs["embed"].description)
        sent = self.bot.store.pending()[0]
        self.assertEqual(sent["expires"], 2770)
        deletion = AsyncMock()
        self.channel.get_partial_message.return_value = SimpleNamespace(delete=deletion)
        with patch("server_notifications.time.time", return_value=2769):
            await self.bot.deliver_or_delete(sent)
        deletion.assert_not_awaited()
        with patch("server_notifications.time.time", return_value=2771):
            await self.bot.deliver_or_delete(sent)
        deletion.assert_awaited_once()
        self.channel.get_partial_message.assert_called_once_with(100)
        self.assertEqual(self.bot.store.pending(), [])

    async def test_deletion_resumes_after_restart(self):
        row = self.enqueue()
        self.bot.store.sent(row, 123, 2800)
        # A new connection/process reads the persisted message and deadline.
        from notification_store import NotificationStore
        restarted = NotificationStore(self.config.state_path)
        try:
            saved = restarted.pending()[0]
            self.assertEqual(saved["message"], 123)
            deletion = AsyncMock()
            self.channel.get_partial_message.return_value = SimpleNamespace(delete=deletion)
            with patch("server_notifications.time.time", return_value=3000):
                await self.bot.deliver_or_delete(saved)
            deletion.assert_awaited_once()
            self.assertEqual(restarted.pending(), [])
        finally:
            restarted.close()

    async def test_duplicate_match_does_not_resend_even_after_deletion(self):
        row = self.enqueue()
        self.enqueue()
        self.assertEqual(len(self.bot.store.pending()), 1)
        self.bot.store.finish(row)
        self.bot.store.enqueue("server-1", "session-1", 50, "Server 1", 970, 1005)
        self.assertEqual(self.bot.store.pending(), [])

    async def test_interrupted_send_is_recovered_from_history(self):
        row = self.enqueue()
        embed = discord.Embed(title="🟢 Server 1 — match started",
                              timestamp=datetime.fromtimestamp(970, timezone.utc))
        existing = message(222, [embed])
        self.channel.history.side_effect = lambda **kwargs: history([existing])
        with patch("server_notifications.time.time", return_value=1010):
            await self.bot.deliver_or_delete(row)
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.bot.store.pending()[0]["message"], 222)
        self.assertEqual(self.bot.store.pending()[0]["expires"], 2770)

    async def test_failed_delete_is_kept_for_retry(self):
        row = self.enqueue()
        self.bot.store.sent(row, 100, 2800)
        deletion = AsyncMock(side_effect=RuntimeError("temporary failure"))
        self.channel.get_partial_message.return_value = SimpleNamespace(delete=deletion)
        with patch("server_notifications.time.time", return_value=3000):
            with self.assertRaises(RuntimeError):
                await self.bot.deliver_or_delete(self.bot.store.pending()[0])
        self.assertEqual(len(self.bot.store.pending()), 1)

    async def test_old_unsent_alert_is_not_delivered_late(self):
        row = self.enqueue()
        with patch("server_notifications.time.time", return_value=3000):
            await self.bot.deliver_or_delete(row)
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.bot.store.pending(), [])

    async def test_disabled_servers_have_channels_but_no_game_monitors(self):
        from dataclasses import replace
        self.bot.config = replace(self.config, servers=(
            self.server,
            replace(self.server, id="server-2", enabled=False),
            replace(self.server, id="server-3", enabled=False),
        ))
        self.bot.get_guild = Mock(return_value=self.guild)
        self.bot.prepare_servers = AsyncMock()
        self.bot.prepare_announcement_channel = AsyncMock()
        self.bot.channels_by_server = {"server-1": self.channel}
        with patch("server_notifications.ReforgerMonitor.run", new_callable=AsyncMock):
            await self.bot.on_ready()
        self.bot.prepare_servers.assert_awaited_once()

        self.assertEqual(len(self.bot.monitors), 1)
        monitor = self.bot.monitors[0][1]
        monitor.session_key = "test-match"
        await monitor.on_session_start(1900)
        self.assertEqual(self.bot.store.pending(), [])
        await monitor.on_session_start(5)
        self.assertEqual(len(self.bot.store.pending()), 1)
        await self.bot.on_ready()  # already booted -> no re-prepare
        self.bot.prepare_servers.assert_awaited_once()

    async def test_old_match_restores_card_without_voice_or_announcement(self):
        from dataclasses import replace
        self.bot.config = replace(self.config, voice_channel_id=123)
        self.bot.voice_channel_id = 123
        self.bot.get_guild = Mock(return_value=self.guild)
        self.bot.prepare_servers = AsyncMock()
        self.bot.prepare_announcement_channel = AsyncMock()
        self.bot.channels_by_server = {"server-1": self.channel}
        self.bot.handle_session_start = AsyncMock()
        self.bot.handle_session_end = AsyncMock()
        with patch("server_notifications.ReforgerMonitor.run", new_callable=AsyncMock):
            await self.bot.on_ready()
        monitor = self.bot.monitors[0][1]
        monitor.session_key = "recovered"
        await monitor.on_session_start(2100)
        self.bot.handle_session_start.assert_not_awaited()
        self.assertEqual(self.bot.store.pending(), [])
        await monitor.on_session_end()
        self.bot.handle_session_end.assert_not_awaited()
        await monitor.on_session_start(0)
        self.assertEqual(len(self.bot.store.pending()), 1)

    async def test_preupgrade_deadline_is_shortened(self):
        row = self.enqueue()
        self.bot.store.sent(row, 100, 2800)
        deletion = AsyncMock()
        self.channel.get_partial_message.return_value = SimpleNamespace(delete=deletion)
        with patch("server_notifications.time.time", return_value=2771):
            await self.bot.deliver_or_delete(self.bot.store.pending()[0])
        deletion.assert_awaited_once()

    async def test_sidebar_is_static_and_does_not_start_refresh_task(self):
        await self.bot.handle_session_start(764)
        self.bot._set_status.assert_not_awaited()
        self.assertIsNone(self.bot._status_task)
        await self.bot.handle_session_end()
        self.assertEqual(self.bot._set_status.await_args.args, ("",))

    async def test_card_reflects_live_and_offline_state(self):
        self.bot._server_channel = self.channel
        self.bot.store.save_channel("servers", 50, 100)
        card = SimpleNamespace(edit=AsyncMock())
        self.channel.get_partial_message.return_value = card
        self.bot.match_times["server-1"] = (1000, 50)
        self.bot._dirty_cards.add("server-1")
        await self.bot.refresh_servers()
        embeds = card.edit.await_args.kwargs["embeds"]
        self.assertIn("<t:1000:R>", embeds[0].fields[0].value)
        self.assertIn("In-game rules", embeds[1].title)  # rules stay on the card
        self.assertNotIn("view", card.edit.await_args.kwargs)
        self.assertEqual(self.bot._dirty_cards, set())
        self.bot.match_times.clear()
        await self.bot.refresh_servers()
        embeds = card.edit.await_args.kwargs["embeds"]
        self.assertIn("Offline", embeds[0].fields[0].value)

    async def test_delayed_queue_does_not_send_expired_match(self):
        self.bot.store.enqueue("server-1", "old", 50, "Server 1", 100, 1890)
        with patch("server_notifications.time.time", return_value=1901):
            await self.bot.deliver_or_delete(self.bot.store.pending()[0])
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.bot.store.pending(), [])


if __name__ == "__main__":
    unittest.main()
