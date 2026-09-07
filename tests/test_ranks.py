import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock, patch

import discord
from account_links import AccountLinks
from rank_sync import RankSync, RANKS

IDENTITY = "11111111-2222-3333-4444-555555555555"


def role(i, name):
    r = MagicMock(spec=discord.Role)
    r.id, r.name, r.managed = i, name, False
    r.permissions = discord.Permissions.none()
    r.is_default.return_value = False
    r.__ge__.return_value = False
    return r


class RankTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.links = AccountLinks(self.root / "links.db")
        self.source = sqlite3.connect(self.root / "playtime.db")
        self.source.execute("CREATE TABLE totals (server TEXT, identity TEXT, seconds REAL)")
        self.source.execute("INSERT INTO totals VALUES ('server-1',?,1200)", (IDENTITY,))
        self.source.commit()
        self.env = patch.dict("os.environ", {"PLAYTIME_DB": str(self.root / "playtime.db")})
        self.env.start()
        self.member = SimpleNamespace(roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.guild = SimpleNamespace(id=1, fetch_member=AsyncMock(return_value=self.member))
        self.tracker = SimpleNamespace(initialized=True, caught_up=True)
        self.bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1),
                                   _trackers=[self.tracker], get_guild=Mock(return_value=self.guild))
        self.sync = RankSync(self.bot)
        self.sync.announcements.flush = AsyncMock()
        self.sync.roles = [role(i+100, name) for i, name in enumerate(RANKS)]
        self.guild.get_role = Mock(side_effect=lambda rid: next((r for r in self.sync.roles if r.id == rid), None))
        self.guild.me = SimpleNamespace(top_role=role(999, "Bot"))

    async def asyncTearDown(self):
        self.env.stop()
        self.source.close()
        self.links.close()
        self.tmp.cleanup()

    def advance(self, seconds):
        with self.source:
            self.source.execute("UPDATE totals SET seconds=seconds+?", (seconds,))

    async def test_pending_gets_nothing_approved_starts_recruit(self):
        token = self.links.submit(1, 10, IDENTITY, "Player")
        await self.sync.tick()
        self.guild.fetch_member.assert_not_awaited()
        self.links.review(1, token, 22, True)
        await self.sync.tick()
        self.member.add_roles.assert_awaited_once_with(self.sync.roles[0], reason="OYB rank: 0 XP", atomic=True)
        self.assertIn("0 XP", self.sync.status(10))
        self.assertEqual(self.source.execute("SELECT seconds FROM totals").fetchone()[0], 1200)

    async def test_full_minutes_promote_and_preserve_other_roles(self):
        self.links.verified_link(1, 10, IDENTITY, "admin:22")
        await self.sync.tick()
        self.member.roles = [self.sync.roles[0], role(77, "Server One"), role(88, "Admin")]
        self.advance(59)
        await self.sync.tick()
        self.assertEqual(self.member.add_roles.await_count, 1)
        self.advance(1)
        await self.sync.tick()
        self.member.add_roles.assert_awaited_with(self.sync.roles[1], reason="OYB rank: 10 XP", atomic=True)
        self.member.remove_roles.assert_awaited_once_with(self.sync.roles[0], reason="OYB rank promotion", atomic=True)
        self.member.roles = [self.sync.roles[1]]
        self.advance(60)
        await self.sync.tick()
        self.assertIn("OYB Corporal", self.sync.status(10))

    async def test_restart_offline_time_and_source_reset_do_not_reset_xp(self):
        self.links.verified_link(1, 10, IDENTITY, "admin:22")
        await self.sync.tick()
        self.advance(120)
        await self.sync.tick()
        self.links.close()
        self.links = AccountLinks(self.root / "links.db")
        self.bot.account_links = self.links
        restarted = RankSync(self.bot)
        self.assertEqual(restarted.progress(10, IDENTITY), 20)
        self.assertEqual(restarted.progress(10, IDENTITY), 20)
        with self.source:
            self.source.execute("UPDATE totals SET seconds=0")
        self.assertEqual(restarted.progress(10, IDENTITY), 20)

    async def test_historical_import_and_missing_database_defer_baseline(self):
        self.links.verified_link(1, 10, IDENTITY, "admin:22")
        self.tracker.caught_up = False
        await self.sync.tick()
        self.advance(10000)
        self.tracker.caught_up = True
        with patch.dict("os.environ", {"PLAYTIME_DB": str(self.root / "missing.db")}):
            self.assertEqual(self.sync.progress(10, IDENTITY), 0)
        self.assertEqual(self.sync.progress(10, IDENTITY), 0)
        self.advance(60)
        self.assertEqual(self.sync.progress(10, IDENTITY), 10)

    async def test_failed_role_change_retries_without_duplicate_xp(self):
        self.links.verified_link(1, 10, IDENTITY, "admin:22")
        self.member.add_roles.side_effect = RuntimeError("temporary failure")
        await self.sync.tick()
        self.assertNotIn(10, self.sync.applied)
        self.member.add_roles.side_effect = None
        await self.sync.tick()
        self.assertIn(10, self.sync.applied)
        self.assertIn("0 XP", self.sync.status(10))

    async def test_prepare_reuses_roles_and_rejects_privileged_role(self):
        roles = self.sync.roles
        self.guild.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True), top_role=role(999, "Bot"))
        self.guild.fetch_roles = AsyncMock(return_value=roles)
        self.guild.create_role = AsyncMock()
        await self.sync.prepare(self.guild)
        await self.sync.prepare(self.guild)
        self.guild.create_role.assert_not_awaited()
        roles[0].permissions = discord.Permissions(administrator=True)
        with self.assertRaises(RuntimeError):
            await self.sync.prepare(self.guild)

    async def test_create_roles_without_permissions(self):
        self.guild.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True), top_role=role(999, "Bot"))
        self.guild.fetch_roles = AsyncMock(return_value=[])
        self.guild.create_role = AsyncMock(side_effect=self.sync.roles)
        await self.sync.prepare(self.guild)
        self.assertEqual(self.guild.create_role.await_count, len(RANKS))
        for call in self.guild.create_role.await_args_list:
            self.assertEqual(call.kwargs["permissions"].value, 0)
            self.assertFalse(call.kwargs["mentionable"])

    async def test_import_must_finish_before_baseline(self):
        from playtime_tracker import Tracker
        log = self.root / "logs_2026-09-07_13-00-00"
        log.mkdir()
        (log / "console.log").write_text("Unrelated line\n" * 5001)
        tracker = Tracker(self.root, self.root / "import.db")
        try:
            self.assertFalse(tracker.initialized)
            tracker.tick()
            self.assertTrue(tracker.initialized)
            self.assertFalse(tracker.caught_up)
            tracker.tick()
            self.assertTrue(tracker.caught_up)
        finally:
            tracker.close()
