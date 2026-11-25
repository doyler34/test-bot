"""Fixes to the panel's live loop: running feed lines per game, RCON refusals, and the save cache."""
import tempfile
import unittest
from pathlib import Path

from panel import save_world
from panel.alerts import Alerts
from panel.config import PanelConfig, ServerConfig
from panel.db import PanelDB, now
from panel.rcon import RconError, RconReply
from panel.servers import ServerManager


class Client:
    def __init__(self, error):
        self.error = error

    async def command(self, text):
        raise self.error

    def close(self):
        pass


class Loop(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = PanelDB(":memory:")
        self.addCleanup(self.db.close)
        self.manager = ServerManager(PanelConfig(servers=[ServerConfig("one", "Server 1")]), self.db, alerts=Alerts(""))
        self.state = self.manager.states["one"]

    def test_a_new_game_starts_the_running_lines_afresh(self):
        self.state.detector.game = "game 1"
        self.state.running[("mines", "someone")] = 1
        self.manager.check(self.state, [{"kind": "leave", "name": "Buford", "at": now(), "game": "game 2"}])
        self.assertEqual(self.state.running, {})

    def test_the_same_game_keeps_them(self):
        self.state.detector.game = "game 1"
        self.state.running[("mines", "someone")] = 1
        self.manager.check(self.state, [{"kind": "leave", "name": "Buford", "at": now(), "game": "game 1"}])
        self.assertEqual(self.state.running, {("mines", "someone"): 1})

    async def test_a_refused_command_leaves_the_server_online(self):
        self.state.online, self.state.client = True, Client(RconReply("Unknown command"))
        self.state.players = [{"id": "7", "identity": "x", "name": "Buford"}]
        with self.assertRaises(RconReply):
            await self.manager.command("one", "#nope")
        self.assertTrue(self.state.online)
        self.assertEqual(len(self.state.players), 1)

    async def test_a_dead_connection_still_goes_offline(self):
        self.state.online, self.state.client = True, Client(RconError("command timed out"))
        with self.assertRaises(RconError):
            await self.manager.command("one", "#players")
        self.assertFalse(self.state.online)


class SaveCache(unittest.TestCase):
    def test_two_servers_saves_stay_cached_side_by_side(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        one, two = Path(folder.name, "one.blob"), Path(folder.name, "two.blob")
        one.write_bytes(b"1")
        two.write_bytes(b"2")
        reads, cache = [], {}

        def reader(path):
            reads.append(path)
            return str(path)
        for _ in range(3):
            save_world.cached(one, reader, cache)
            save_world.cached(two, reader, cache)
        self.assertEqual(reads, [one, two])

    def test_only_the_newest_few_are_kept(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = {}
        for n in range(save_world.CACHED_SAVES + 3):
            path = Path(folder.name, f"{n}.blob")
            path.write_bytes(b"x")
            save_world.cached(path, str, cache)
        self.assertEqual(len(cache), save_world.CACHED_SAVES)


if __name__ == "__main__":
    unittest.main()
