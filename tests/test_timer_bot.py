"""Timer visibility regression tests; Discord requests are mocked."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from config import ConfigError, load_config
from timer_bot import TimerBot


class ConfigTests(unittest.TestCase):
    def test_voice_mode_configuration(self):
        required = {
            "DISCORD_BOT_TOKEN": "test-token",
            "GUILD_ID": "1",
            "VOICE_CHANNEL_ID": "2",
            "REFORGER_LOG_DIR": "/logs",
        }
        for raw, expected in [(None, False), ("false", False), ("true", True),
                              (" OFF ", False), ("1", True)]:
            with self.subTest(raw=raw), patch.dict(os.environ, required, clear=True):
                if raw is not None:
                    os.environ["JOIN_VOICE_CHANNEL"] = raw
                with patch("config.load_dotenv"):
                    self.assertEqual(load_config().join_voice_channel, expected)
        with patch.dict(os.environ, {**required, "JOIN_VOICE_CHANNEL": "typo"}, clear=True):
            with patch("config.load_dotenv"), self.assertRaises(ConfigError):
                load_config()


class TimerVisibilityTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, **kwargs):
        bot = TimerBot(guild_id=1, voice_channel_id=2, **kwargs)
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = 2
        channel.name = "SERVER TIME"
        channel.guild = Mock()
        channel.guild.voice_client = None
        channel.connect = AsyncMock()
        bot._voice_channel = Mock(return_value=channel)
        bot.http.request = AsyncMock()
        bot._connection.user = SimpleNamespace(id=3)
        return bot, channel

    async def test_default_never_joins_during_start_reconnect_refresh_or_end(self):
        bot, channel = self.make_bot(status_refresh_seconds=0)
        async def finish_refresh(*args, **kwargs):
            bot._desired_live = False
        try:
            await bot.handle_session_start()
            self.assertEqual(bot.http.request.await_args.kwargs["json"]["status"],
                             "🟢 LIVE · 00h 00m")
            await bot.on_ready()
            bot.http.request.side_effect = finish_refresh
            await asyncio.wait_for(bot._status_task, timeout=1)
            await bot.handle_session_end()
            self.assertEqual(bot.http.request.await_args.kwargs["json"], {"status": ""})
            channel.connect.assert_not_awaited()
            bot._voice_channel.assert_not_called()
            self.assertIsNone(bot._match_start)
            self.assertIsNone(bot._status_task)
        finally:
            bot._stop_status_loop()
            await bot.close()

    async def test_voice_mode_still_joins_muted_and_leaves(self):
        bot, channel = self.make_bot(join_voice_channel=True)
        vc = Mock()
        vc.is_connected.return_value = True
        vc.channel = channel
        vc.disconnect = AsyncMock()
        try:
            await bot.handle_session_start()
            channel.connect.assert_awaited_once_with(self_deaf=True, self_mute=True)
            channel.guild.voice_client = vc
            await bot.on_ready()
            channel.connect.assert_awaited_once()
            await bot.handle_session_end()
            vc.disconnect.assert_awaited_once_with(force=True)
        finally:
            bot._stop_status_loop()
            await bot.close()

    async def test_missing_permissions_logs_fix_without_joining(self):
        bot, channel = self.make_bot()
        bot.http.request.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
        )
        try:
            with self.assertLogs("reforger.bot", level="ERROR") as logs:
                await bot._refresh_status()  # no match yet: no request
                await bot.handle_session_start()
            self.assertIn("Manage Channels", "\n".join(logs.output))
            channel.connect.assert_not_awaited()
        finally:
            bot._stop_status_loop()
            await bot.close()


if __name__ == "__main__":
    unittest.main()
