import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from bot.storage.account_links import AccountLinks
from bot.ranks.rank_sync import RankSync
from bot.ranks.rank_announcements import RankAnnouncements


class AnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / "links.db")
        self.bot = SimpleNamespace(account_links=self.links, user=SimpleNamespace(id=99))
        self.alerts = RankSync(self.bot).announcements
        self.alerts.record(1, 10, 0, 0)
        self.messages = []
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id, self.channel.name = 50, "log"
        self.channel.guild = SimpleNamespace(id=1)
        self.channel.send = AsyncMock(return_value=SimpleNamespace(id=500))
        async def history(**kwargs):
            for m in self.messages:
                yield m
        self.channel.history = history
        self.guild = SimpleNamespace(id=1, text_channels=[self.channel], get_channel=Mock(return_value=self.channel))
        self.env = patch.dict("os.environ", {"RANK_LOG_CHANNEL_ID": ""})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        self.links.close()
        self.tmp.cleanup()

    async def test_recruit_quiet_promotion_mentions_only_member_once(self):
        self.alerts.record(1, 10, 0, 0)
        await self.alerts.flush(self.guild)
        self.channel.send.assert_not_awaited()
        self.alerts.record(1, 10, 1, 100)
        await self.alerts.flush(self.guild)
        args = self.channel.send.await_args.kwargs
        self.assertIn("<@10>", args["content"])
        self.assertIn("OYB Recruit", args["embed"].description)
        self.assertEqual(args["allowed_mentions"].to_dict(), {"users": [10], "parse": []})
        restarted = RankAnnouncements(self.bot)
        restarted.record(1, 10, 1, 100)
        await restarted.flush(self.guild)
        self.channel.send.assert_awaited_once()

    async def test_accepted_send_recovered_after_crash(self):
        self.alerts.record(1, 10, 2, 20)
        embed = discord.Embed().set_footer(text="OYB • Promotion v2 • 1:10:2")
        self.messages.append(SimpleNamespace(id=501, author=self.bot.user, embeds=[embed]))
        await self.alerts.flush(self.guild)
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.links.db.execute("SELECT message FROM rank_alerts_v2").fetchone()[0], 501)

    async def test_send_failure_stays_pending_and_retries(self):
        self.alerts.record(1, 10, 1, 10)
        self.channel.send.side_effect = RuntimeError("temporary failure")
        await self.alerts.flush(self.guild)
        self.assertIsNone(self.links.db.execute("SELECT message FROM rank_alerts_v2").fetchone()[0])
        self.channel.send.side_effect = None
        await RankAnnouncements(self.bot).flush(self.guild)
        self.assertEqual(self.links.db.execute("SELECT message FROM rank_alerts_v2").fetchone()[0], 500)

    async def test_ambiguous_log_does_not_send_and_override_selects_channel(self):
        self.alerts.record(1, 10, 1, 10)
        self.guild.text_channels = [self.channel, self.channel]
        await self.alerts.flush(self.guild)
        self.channel.send.assert_not_awaited()
        with patch.dict("os.environ", {"RANK_LOG_CHANNEL_ID": "50"}):
            await self.alerts.flush(self.guild)
        self.channel.send.assert_awaited_once()

    async def test_install_does_not_reannounce_existing_ranks(self):
        self.links.db.executescript("DROP TABLE rank_announced_v2; DROP TABLE rank_alerts_v2;")
        alerts = RankAnnouncements(self.bot)
        alerts.record(1, 10, 3, 30)
        await alerts.flush(self.guild)
        self.channel.send.assert_not_awaited()
        alerts.record(1, 10, 4, 40)
        await alerts.flush(self.guild)
        self.channel.send.assert_awaited_once()
