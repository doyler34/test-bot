import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from bot.storage.account_links import AccountLinks
from bot.notification_config import Server
from bot.storage.notification_store import NotificationStore
import bot.discord.server_stats as server_stats
from bot.discord.server_stats import ServerStats

A = "11111111-2222-3333-4444-555555555555"
B = "22222222-2222-3333-4444-555555555555"


class FakeVoice:
    def __init__(self, guild, id, name):
        self.guild, self.id, self.name = guild, id, name
        self.members, self.category, self.edits, self.overwrites = [], None, 0, {}

    async def edit(self, **kwargs):
        self.edits += 1
        self.name = kwargs.get("name", self.name)
        self.overwrites = kwargs.get("overwrites", self.overwrites)
        return self

    async def delete(self, **kwargs):
        self.guild.voice_channels.remove(self)
        self.guild._by_id.pop(self.id, None)


class FakeCategory:
    def __init__(self, guild, id, name, position=5):
        self.guild, self.id, self.name, self.position = guild, id, name, position
        self.overwrites = {}

    async def edit(self, **kwargs):
        self.position = kwargs.get("position", self.position)
        self.overwrites = kwargs.get("overwrites", self.overwrites)
        return self

    async def move(self, **kwargs):
        if kwargs.get("beginning"):
            self.position = 0


class Hashable:  # real Roles/Members are hashable; SimpleNamespace is not
    def __init__(self, id):
        self.id = id


class FakeGuild:
    def __init__(self):
        self.id, self.serial, self.creates = 1, 1000, 0
        self.categories, self.voice_channels, self._by_id, self.roles = [], [], {}, {}
        self.default_role = Hashable(1)
        self.me = Hashable(99)

    def _next(self):
        self.serial += 1
        return self.serial

    def get_channel(self, id):
        return self._by_id.get(id)

    def get_role(self, id):
        return self.roles.get(id)

    async def create_category(self, name, **kwargs):
        c = FakeCategory(self, self._next(), name, position=kwargs.get("position", 5))
        c.overwrites = kwargs.get("overwrites", {})
        self.categories.append(c)
        self._by_id[c.id] = c
        return c

    async def create_voice_channel(self, name, **kwargs):
        self.creates += 1
        v = FakeVoice(self, self._next(), name)
        v.category = kwargs.get("category")
        v.overwrites = kwargs.get("overwrites", {})
        self.voice_channels.append(v)
        self._by_id[v.id] = v
        return v


def server(sid, name, enabled=True):
    return Server(id=sid, name=name, channel_name=sid, log_dir="/tmp/" + sid,
                  settings="s", rules="r", enabled=enabled)


class ServerStatsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = NotificationStore(root / "notifications.db")
        self.links = AccountLinks(root / "links.db")
        self.links.verified_link(1, 10, A, "admin:1")
        self.links.verified_link(1, 11, B, "admin:1")
        self.servers = (server("server-1", "Server One"), server("server-2", "Server Two"),
                        server("server-3", "Server Three", enabled=False))
        self.trackers = [SimpleNamespace(server="server-1", active_identities={A, B}),
                         SimpleNamespace(server="server-2", active_identities=set())]
        self.guild = FakeGuild()
        self.now = 1000.0
        # server-1 has a live match started 65 seconds ago (real monotonic base);
        # server-2 is up but between matches (online, no match).
        self.base = time.monotonic()
        self.monitors = [("server-1", SimpleNamespace(online=True)),
                         ("server-2", SimpleNamespace(online=True))]
        self.bot = SimpleNamespace(
            store=self.store, account_links=SimpleNamespace(db=self.links.db),
            config=SimpleNamespace(guild_id=1, servers=self.servers),
            _trackers=self.trackers, monitors=self.monitors,
            match_times={"server-1": (self.now, self.base - 65)},
            get_guild=lambda i: self.guild)
        self.classes = patch.multiple(server_stats.discord, VoiceChannel=FakeVoice, CategoryChannel=FakeCategory)
        self.classes.start()
        self.clock = patch("bot.discord.server_stats.time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.stats = ServerStats(self.bot)

    async def asyncTearDown(self):
        self.clock.stop()
        self.classes.stop()
        self.store.close()
        self.links.close()
        self.tmp.cleanup()

    async def test_prepare_creates_category_and_one_channel_per_stat(self):
        await self.stats.prepare(self.guild)
        self.assertEqual(len(self.guild.categories), 1)
        self.assertEqual(self.guild.categories[0].position, 0)  # pinned to the top
        # 3 servers + arma + vc (no admin role configured).
        self.assertEqual(set(self.stats.channels), {"server-1", "server-2", "server-3", "arma", "vc"})
        self.assertEqual(self.guild.creates, 5)

    async def test_prepare_adopts_existing_tiles_and_removes_duplicates(self):
        # First run creates the five tiles.
        await self.stats.prepare(self.guild)
        first = {k: v.id for k, v in self.stats.channels.items()}
        self.assertEqual(self.guild.creates, 5)
        # Simulate a wiped database (stored ids gone) plus a leftover duplicate tile.
        dup = await self.guild.create_voice_channel("🔴 Classic · Offline", category=self.stats.category)
        self.store.db.execute("DELETE FROM stat_channels")
        self.store.db.commit()
        self.guild.creates = 0
        stats2 = ServerStats(self.bot)
        await stats2.prepare(self.guild)
        # Nothing new created — the existing tiles were reused by name.
        self.assertEqual(self.guild.creates, 0)
        self.assertEqual({k: v.id for k, v in stats2.channels.items()}, first)
        # The stray duplicate was cleaned up; the five real tiles remain.
        self.assertNotIn(dup, self.guild.voice_channels)
        self.assertEqual(len([c for c in self.guild.voice_channels
                              if c.category and c.category.id == stats2.category.id]), 5)

    async def test_staging_hides_then_reveals_category_and_tiles(self):
        everyone = self.guild.default_role
        with patch.dict("os.environ", {"OYB_STAGING": "1"}):
            stats = ServerStats(self.bot)
            await stats.prepare(self.guild)
        # Staging on: category and every tile are hidden from @everyone.
        self.assertFalse(stats.category.overwrites[everyone].view_channel)
        for tile in stats.channels.values():
            self.assertFalse(tile.overwrites[everyone].view_channel)
        # Staging off: re-preparing adopts the same channels and reveals them.
        stats2 = ServerStats(self.bot)
        await stats2.prepare(self.guild)
        self.assertTrue(stats2.category.overwrites[everyone].view_channel)
        for tile in stats2.channels.values():
            self.assertTrue(tile.overwrites[everyone].view_channel)

    async def test_per_server_states_live_idle_offline_and_coming_soon(self):
        counts = self.stats._in_game()
        self.assertEqual(counts, {"server-1": 2, "server-2": 0, "server-3": 0})
        # Live match -> green timer.
        self.assertEqual(self.stats._desired_name(self.guild, "server-1", counts), "🟢 Classic · 1 min")
        # Up but no match -> yellow, clearly not the same as offline.
        self.assertEqual(self.stats._desired_name(self.guild, "server-2", counts), "🟡 3x Everon · Waiting for match")
        # Disabled server -> coming soon.
        self.assertEqual(self.stats._desired_name(self.guild, "server-3", counts), "⚫ Arland · Coming soon")
        # An enabled server whose process is down reads Offline, not white/idle.
        self.monitors[1] = ("server-2", SimpleNamespace(online=False))
        self.assertEqual(self.stats._desired_name(self.guild, "server-2", counts), "🔴 3x Everon · Offline")
        # Total linked players in-game stays on the Playing ArmA tile.
        self.assertEqual(self.stats._desired_name(self.guild, "arma", counts), "🎮 Playing ArmA: 2")

    async def test_users_in_vc_excludes_bots(self):
        vc = FakeVoice(self.guild, 5, "General")
        vc.members = [SimpleNamespace(bot=False), SimpleNamespace(bot=True), SimpleNamespace(bot=False)]
        self.guild.voice_channels.append(vc)
        self.assertEqual(self.stats._desired_name(self.guild, "vc", self.stats._in_game()), "🔊 Users in VC: 2")

    async def test_rename_is_paced_and_only_on_change(self):
        await self.stats.prepare(self.guild)
        classic = self.stats.channels["server-1"]
        self.assertEqual(classic.name, "🟢 Classic · 1 min")  # created with the live value
        # First real change applies right away (no prior rename to pace against).
        self.bot.match_times["server-1"] = (self.now, self.base - 65 - 6 * 60)
        await self.stats.tick()
        self.assertEqual(classic.name, "🟢 Classic · 7 min")
        self.assertEqual(classic.edits, 1)
        # A second change within the 5-minute window is deferred.
        self.bot.match_times["server-1"] = (self.now, self.base - 65 - 12 * 60)
        await self.stats.tick()
        self.assertEqual(classic.edits, 1)
        # After the window it applies once more.
        self.now += 301
        await self.stats.tick()
        self.assertEqual(classic.name, "🟢 Classic · 13 min")
        self.assertEqual(classic.edits, 2)

    async def test_match_state_change_bypasses_pacing(self):
        await self.stats.prepare(self.guild)
        everon = self.stats.channels["server-2"]  # created as "🟡 ... Waiting for match"
        self.assertTrue(everon.name.startswith("🟡"))
        # Match starts -> immediate rename despite being inside the 5-min window.
        self.bot.match_times["server-2"] = (self.now, self.base)
        await self.stats.tick()
        self.assertTrue(everon.name.startswith("🟢"))
        self.assertEqual(everon.edits, 1)
        # Match ends moments later -> state change again forces a rename.
        del self.bot.match_times["server-2"]
        await self.stats.tick()
        self.assertTrue(everon.name.startswith("🟡"))
        self.assertEqual(everon.edits, 2)

    async def test_admins_tile_only_when_role_configured(self):
        self.assertNotIn("admins", self.stats.stat_keys())
        with patch.dict("os.environ", {"ADMIN_ROLE_ID": "4242"}):
            stats = ServerStats(self.bot)
        self.guild.roles[4242] = SimpleNamespace(id=4242, members=[object(), object(), object()])
        self.assertIn("admins", stats.stat_keys())
        self.assertEqual(stats._desired_name(self.guild, "admins", stats._in_game()), "🛡️ Admins: 3")


if __name__ == "__main__":
    unittest.main()
