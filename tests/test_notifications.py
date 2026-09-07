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
from server_notifications import NotificationBot, information_embeds, readonly_overwrites


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
        self.assertLess(sum(len(e) for e in information_embeds(servers[0])), 6000)

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
        self.bot._connection.user = SimpleNamespace(id=99)
        self.role = SimpleNamespace(id=77)
        self.bot.roles_by_server["server-1"] = self.role
        role_patch = patch("server_notifications.prepare_role", new_callable=AsyncMock)
        role_patch.start()
        self.addCleanup(role_patch.stop)
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 50
        self.channel.topic = "OYB • server-1 • Settings, rules and match notifications"
        self.channel.history = Mock(side_effect=lambda **kwargs: history([]))
        self.channel.send = AsyncMock(return_value=message(100))
        self.channel.edit = AsyncMock()
        self.channel.fetch_message = AsyncMock()
        self.bot.get_channel = Mock(return_value=self.channel)
        self.guild = Mock()
        self.guild.default_role = Mock()
        self.guild.me = Mock()
        self.guild.text_channels = []
        self.guild.get_channel = Mock(return_value=self.channel)
        self.guild.create_text_channel = AsyncMock(return_value=self.channel)

    async def asyncTearDown(self):
        await self.bot.close()
        self.bot.store.close()
        self.tmp.cleanup()

    def enqueue(self):
        self.bot.store.enqueue("server-1", "session-1", 50, "Server 1", 970, 1000)
        return self.bot.store.pending()[0]

    async def test_create_readonly_channel_and_keep_information_on_restart(self):
        await self.bot.prepare_channel(self.guild, self.server)
        self.guild.create_text_channel.assert_awaited_once()
        kwargs = self.guild.create_text_channel.await_args.kwargs
        everyone = kwargs["overwrites"][self.guild.default_role]
        self.assertFalse(everyone.send_messages)
        self.assertFalse(everyone.create_public_threads)
        self.assertFalse(everyone.create_private_threads)
        self.assertFalse(everyone.send_messages_in_threads)
        self.assertTrue(everyone.view_channel)
        self.assertTrue(kwargs["overwrites"][self.guild.me].send_messages)
        self.assertTrue(self.channel.send.await_args.kwargs["silent"])
        info = message(100, information_embeds(self.server))
        self.channel.fetch_message.return_value = info
        await self.bot.prepare_channel(self.guild, self.server)
        self.channel.send.assert_awaited_once()
        info.edit.assert_awaited_once()
        self.assertEqual(self.bot.store.pending(), [])
        info.delete.assert_not_awaited()

    async def test_recovers_managed_channel_and_card_without_database_record(self):
        self.guild.text_channels = [self.channel]
        info = message(101, information_embeds(self.server))
        self.channel.history.side_effect = lambda **kwargs: history([info])
        await self.bot.prepare_channel(self.guild, self.server)
        self.guild.create_text_channel.assert_not_awaited()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.bot.store.channel("server-1")["info"], 101)

    async def test_never_repurposes_unrelated_saved_channel(self):
        self.bot.store.save_channel("server-1", 50, 100)
        self.channel.topic = "A community chat"
        with self.assertRaises(RuntimeError):
            await self.bot.prepare_channel(self.guild, self.server)
        self.channel.edit.assert_not_awaited()

    async def test_send_and_delete_after_30_minutes_not_before(self):
        row = self.enqueue()
        with patch("server_notifications.time.time", return_value=1000):
            await self.bot.deliver_or_delete(row)
        kwargs = self.channel.send.await_args.kwargs
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertEqual(kwargs["allowed_mentions"].roles, [self.role])
        self.assertEqual(kwargs["content"], "<@&77>")
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
        self.bot.prepare_channel = AsyncMock()
        self.bot.channels_by_server = {"server-1": self.channel}
        with patch("server_notifications.ReforgerMonitor.run", new_callable=AsyncMock):
            await self.bot.on_ready()
        self.assertEqual(self.bot.prepare_channel.await_count, 3)

        self.assertEqual(len(self.bot.monitors), 1)
        monitor = self.bot.monitors[0][1]
        monitor.session_key = "test-match"
        await monitor.on_session_start(1900)
        self.assertEqual(self.bot.store.pending(), [])
        await monitor.on_session_start(5)
        self.assertEqual(len(self.bot.store.pending()), 1)
        await self.bot.on_ready()
        self.assertEqual(self.bot.prepare_channel.await_count, 3)

    async def test_old_match_restores_voice_without_announcement(self):
        from dataclasses import replace
        self.bot.config = replace(self.config, voice_channel_id=123)
        self.bot.voice_channel_id = 123
        self.bot.get_guild = Mock(return_value=self.guild)
        self.bot.prepare_channel = AsyncMock()
        self.bot.channels_by_server = {"server-1": self.channel}
        self.bot.handle_session_start = AsyncMock()
        self.bot.handle_session_end = AsyncMock()
        with patch("server_notifications.ReforgerMonitor.run", new_callable=AsyncMock):
            await self.bot.on_ready()
        monitor = self.bot.monitors[0][1]
        monitor.session_key = "recovered"
        await monitor.on_session_start(2100)
        self.bot.handle_session_start.assert_awaited_once_with(2100)
        self.assertEqual(self.bot.store.pending(), [])
        await monitor.on_session_end()
        self.bot.handle_session_end.assert_awaited_once()
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
        self.bot._set_status.assert_awaited_once_with("🟢 Match live")
        self.assertIsNone(self.bot._status_task)
        await self.bot.handle_session_end()
        self.assertEqual(self.bot._set_status.await_args.args, ("",))

    async def test_permanent_card_keeps_match_time_and_buttons(self):
        self.bot.store.save_channel("server-1", 50, 100)
        self.bot.channels_by_server["server-1"] = self.channel
        card = SimpleNamespace(edit=AsyncMock())
        self.channel.get_partial_message.return_value = card
        self.bot.match_times["server-1"] = (1000, 50)
        self.bot._dirty_cards.add("server-1")
        await self.bot.refresh_information(self.server)
        fields = card.edit.await_args.kwargs["embeds"][0].fields
        self.assertIn("<t:1000:R>", next(f.value for f in fields if f.name == "Current match"))
        self.assertNotIn("view", card.edit.await_args.kwargs)
        self.assertEqual(self.bot._dirty_cards, set())
        self.bot.match_times.clear()
        await self.bot.refresh_information(self.server)
        fields = card.edit.await_args.kwargs["embeds"][0].fields
        self.assertIn("No live match", next(f.value for f in fields if f.name == "Current match"))

    async def test_delayed_queue_does_not_send_expired_match(self):
        self.bot.store.enqueue("server-1", "old", 50, "Server 1", 100, 1890)
        with patch("server_notifications.time.time", return_value=1901):
            await self.bot.deliver_or_delete(self.bot.store.pending()[0])
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.bot.store.pending(), [])


if __name__ == "__main__":
    unittest.main()
