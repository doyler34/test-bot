import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from account_links import AccountLinks
from notification_config import Server
from notification_store import NotificationStore
import server_stats
from server_stats import ServerStats

A = "11111111-2222-3333-4444-555555555555"
B = "22222222-2222-3333-4444-555555555555"


class FakeVoice:
    def __init__(self, guild, id, name):
        self.guild, self.id, self.name = guild, id, name
        self.members, self.category, self.edits = [], None, 0

    async def edit(self, **kwargs):
        self.edits += 1
        self.name = kwargs.get("name", self.name)
        return self


class FakeCategory:
    def __init__(self, guild, id, name, position=5):
        self.guild, self.id, self.name, self.position = guild, id, name, position

    async def edit(self, **kwargs):
        self.position = kwargs.get("position", self.position)
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
        self.categories.append(c)
        self._by_id[c.id] = c
        return c

    async def create_voice_channel(self, name, **kwargs):
        self.creates += 1
        v = FakeVoice(self, self._next(), name)
        v.category = kwargs.get("category")
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
        # server-1 has a live match started 65 seconds ago (real monotonic base).
        self.base = time.monotonic()
        self.bot = SimpleNamespace(
            store=self.store, account_links=SimpleNamespace(db=self.links.db),
            config=SimpleNamespace(guild_id=1, servers=self.servers),
            _trackers=self.trackers, match_times={"server-1": (self.now, self.base - 65)},
            get_guild=lambda i: self.guild)
        self.classes = patch.multiple(server_stats.discord, VoiceChannel=FakeVoice, CategoryChannel=FakeCategory)
        self.classes.start()
        self.clock = patch("server_stats.time.time", side_effect=lambda: self.now)
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

    async def test_per_server_shows_live_match_timer_and_arma_total(self):
        counts = self.stats._in_game()
        self.assertEqual(counts, {"server-1": 2, "server-2": 0, "server-3": 0})
        # Per-server tiles carry the live match counter (replaces the old categories).
        self.assertEqual(self.stats._desired_name(self.guild, "server-1", counts), "🟢 Classic · 1 min")
        self.assertEqual(self.stats._desired_name(self.guild, "server-2", counts), "⚪ 3x Everon · Waiting")
        self.assertEqual(self.stats._desired_name(self.guild, "server-3", counts), "🔴 Arland · Coming soon")
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
        # The match ages by six minutes; within the 5-minute window the rename waits.
        self.bot.match_times["server-1"] = (self.now, self.base - 65 - 6 * 60)
        await self.stats.tick()
        self.assertEqual(classic.edits, 0)
        # After the window it renames once, then not again while unchanged.
        self.now += 301
        await self.stats.tick()
        self.assertEqual(classic.name, "🟢 Classic · 7 min")
        self.assertEqual(classic.edits, 1)
        self.now += 301
        await self.stats.tick()
        self.assertEqual(classic.edits, 1)

    async def test_admins_tile_only_when_role_configured(self):
        self.assertNotIn("admins", self.stats.stat_keys())
        with patch.dict("os.environ", {"ADMIN_ROLE_ID": "4242"}):
            stats = ServerStats(self.bot)
        self.guild.roles[4242] = SimpleNamespace(id=4242, members=[object(), object(), object()])
        self.assertIn("admins", stats.stat_keys())
        self.assertEqual(stats._desired_name(self.guild, "admins", stats._in_game()), "🛡️ Admins: 3")


if __name__ == "__main__":
    unittest.main()
