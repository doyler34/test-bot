"""The weekly heli kills board: killer positions, the heli rule, and the self-editing message."""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.discord import heli_board
from bot.discord.heli_board import HeliBoard, board_embed, ours, standings, week_tag
from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import BOSTON, killer_spots, migrate, record
from bot.tracking.combat_ingestor import backfill_spots, scan
from bot.tracking.combat_parser import parse_kill
from bot.tracking.heli import heli_kills

PILOT = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
GROUNDED = 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'
VICTIM = '11111111-2222-3333-4444-555555555555'
WEEK = datetime(2026, 11, 9, tzinfo=BOSTON)


class Ground:
    """A map whose ground is `level` everywhere, or 300 m on the hill east of x=6000."""
    def __init__(self, level=10.0):
        self.level = level

    def at(self, x, z):
        return 300.0 if x > 6000 else self.level


def line(clock='15:30:54.085', killer=PILOT, at=(3912.0, 60.0, 8516.0), damage='EXPLOSIVE', victim=VICTIM):
    return (f'{clock}   SCRIPT       : INFO: KILL ENEMY: Bob (playerID = 1 | UUID = {victim}) '
            f'from US faction at <3926.26, 13.56, 8526.9> was killed by Al (playerID = 2 | UUID = {killer}) '
            f'from USSR faction who was at that time at <{at[0]}, {at[1]}, {at[2]}> [40.0m away from the corpse]. '
            f"With last inflicted damage type {damage} to the 'Chest' hit zone\n")


def guild(members):
    found = {i: SimpleNamespace(display_name=n) for i, n in members.items()}
    return SimpleNamespace(id=1, get_member=found.get)


def spot(occurred, killer=PILOT, at=(3912.0, 60.0, 8516.0), damage='EXPLOSIVE', server='one'):
    return (server, occurred, killer, damage, *at)


class ParserTests(unittest.TestCase):
    def test_the_killers_position_is_read(self):
        self.assertEqual(parse_kill(line()).killer_at, (3912.0, 60.0, 8516.0))

    def test_an_ai_kill_has_no_killer_position(self):
        text = line().replace(f'Al (playerID = 2 | UUID = {PILOT})', 'AI')
        self.assertIsNone(parse_kill(text).killer_at)


class HeliRuleTests(unittest.TestCase):
    def test_an_explosive_kill_from_high_up_is_a_heli_kill(self):
        self.assertEqual(heli_kills([spot('2026-11-10T10:00:00.000')], Ground())[PILOT], 1)

    def test_on_the_ground_it_is_not(self):
        self.assertEqual(heli_kills([spot('2026-11-10T10:00:00.000', at=(3912.0, 25.0, 8516.0))], Ground()), {})

    def test_standing_on_a_mountain_is_not_flying(self):
        # 310 m above sea level, but the ground there is 300 m.
        self.assertEqual(heli_kills([spot('2026-11-10T10:00:00.000', at=(7000.0, 310.0, 8516.0))], Ground()), {})

    def test_gunfire_from_a_tower_does_not_count(self):
        kills = [spot(f'2026-11-10T10:0{i}:00.000', damage='KINETIC') for i in range(3)]
        self.assertEqual(heli_kills(kills, Ground()), {})

    def test_gunfire_from_a_moving_heli_counts(self):
        kills = [spot('2026-11-10T10:00:00.000', damage='KINETIC'),
                 spot('2026-11-10T10:00:10.000', damage='KINETIC', at=(4212.0, 80.0, 8516.0))]
        self.assertEqual(heli_kills(kills, Ground())[PILOT], 2)

    def test_movement_only_counts_between_close_kills(self):
        kills = [spot('2026-11-10T10:00:00.000', damage='KINETIC'),
                 spot('2026-11-10T10:05:00.000', damage='KINETIC', at=(6012.0, 80.0, 8516.0))]
        self.assertEqual(heli_kills(kills, Ground()), {})

    def test_off_the_everon_map_nothing_counts(self):
        self.assertEqual(heli_kills([spot('2026-11-10T10:00:00.000', at=(13900.0, 90.0, 8516.0))], Ground()), {})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / 'account_links.sqlite3'
        self.links = AccountLinks(self.database)
        self.db = self.links.db
        self.links.verified_link(1, 10, PILOT, 'admin:20')
        self.links.verified_link(1, 11, GROUNDED, 'admin:20')
        migrate(self.db)
        self.path = self.root / 'logs_2026-11-10_14-44-37' / 'console.log'
        self.path.parent.mkdir()

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def test_new_kills_keep_the_killers_position(self):
        self.path.write_text(line(), encoding='utf-8')
        scan(self.db, 'one', self.path)
        (row,) = killer_spots(self.db, WEEK)
        self.assertEqual(row[2:], (PILOT, 'EXPLOSIVE', 3912.0, 60.0, 8516.0))

    def test_kills_saved_before_positions_get_them_from_the_log(self):
        old = parse_kill(line())
        with self.db:
            record(self.db, 'one', '2026-11-10T15:30:54.085', old.__class__(**{**old.__dict__, 'killer_at': None}))
        self.assertEqual(killer_spots(self.db, WEEK), [])
        self.path.write_text(line(), encoding='utf-8')
        server = SimpleNamespace(id='one', enabled=True, log_dir=str(self.root))
        self.assertEqual(backfill_spots(str(self.database), [server], WEEK.date()), 1)
        self.db.commit()
        self.assertEqual(len(killer_spots(self.db, WEEK)), 1)
        # Nothing is counted twice, and a second pass has nothing to do.
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM combat_events').fetchone()[0], 1)
        self.assertEqual(backfill_spots(str(self.database), [server], WEEK.date()), 0)

    def test_older_logs_are_left_alone(self):
        server = SimpleNamespace(id='one', enabled=True, log_dir=str(self.root))
        self.path.write_text(line(), encoding='utf-8')
        self.assertEqual(backfill_spots(str(self.database), [server], (WEEK + timedelta(days=3)).date()), 0)

    def test_standings_list_linked_heli_killers_only(self):
        self.path.write_text(line() + line('15:31:00.000') + line('15:32:00.000', killer=GROUNDED,
                             at=(3912.0, 12.0, 8516.0)), encoding='utf-8')
        scan(self.db, 'one', self.path)
        # No approved name on file, so their Discord name is shown; the grounded killer isn't listed.
        self.assertEqual(standings(self.db, guild({10: 'Atticus'}), Ground(), WEEK), [('Atticus', 2)])
        # Someone who has left the server keeps their place under their member number.
        self.assertEqual(standings(self.db, guild({}), Ground(), WEEK), [('Member 10', 2)])

    def test_last_weeks_kills_are_not_this_weeks(self):
        self.path.write_text(line(), encoding='utf-8')
        scan(self.db, 'one', self.path)
        self.assertEqual(standings(self.db, guild({}), Ground(), WEEK + timedelta(days=7)), [])


class RenderTests(unittest.TestCase):
    def test_the_board_lists_heli_kills(self):
        embed = board_embed([('Pilot', 12), ('Gunner', 3)], WEEK)
        self.assertIn('HELI KILLS', embed.title)
        self.assertRegex(embed.description, r'1\s+Pilot\s+12')
        self.assertTrue(embed.footer.text.startswith(week_tag(WEEK)))
        self.assertIn('Resets Monday', embed.footer.text)

    def test_an_empty_week_says_so(self):
        self.assertIn('No heli kills yet', board_embed([], WEEK).description)

    def test_a_finished_week_reads_as_final(self):
        embed = board_embed([('Pilot', 12)], WEEK, final=True)
        self.assertIn('FINAL', embed.title)
        self.assertIn('Final result', embed.footer.text)

    def test_only_this_weeks_board_is_taken_back_over(self):
        message = SimpleNamespace(author=SimpleNamespace(id=5), embeds=[board_embed([], WEEK)])
        self.assertTrue(ours(message, 5, WEEK))
        self.assertFalse(ours(message, 5, WEEK + timedelta(days=7)))
        self.assertFalse(ours(message, 6, WEEK))


class History:
    def __init__(self, messages):
        self.messages = messages

    def __call__(self, limit):
        return self

    def __aiter__(self):
        self.items = iter(self.messages)
        return self

    async def __anext__(self):
        try:
            return next(self.items)
        except StopIteration:
            raise StopAsyncIteration


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.sent = MagicMock()
        self.sent.edit = AsyncMock()
        self.sent.id = 77
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.send = AsyncMock(return_value=self.sent)
        self.channel.history = History([])
        guild = MagicMock()
        guild.id = 1
        guild.get_channel.return_value = self.channel
        self.bot = SimpleNamespace(get_guild=lambda _: guild, config=SimpleNamespace(guild_id=1),
                                   user=SimpleNamespace(id=5), account_links=SimpleNamespace(db=None))
        self.rows = [('Pilot', 4)]
        patches = [patch.object(heli_board, 'heli_board_channel_id', return_value=123),
                   patch.object(heli_board.heights, 'load', return_value=Ground()),
                   patch.object(heli_board, 'standings', side_effect=lambda *a, **k: list(self.rows))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tick(self, board, now):
        asyncio.run(board.tick(now))

    def test_posts_once_then_edits_only_when_the_numbers_change(self):
        board = HeliBoard(self.bot)
        now = WEEK + timedelta(days=1)
        self.tick(board, now)
        self.channel.send.assert_awaited_once()
        self.tick(board, now)
        self.sent.edit.assert_not_awaited()
        self.rows = [('Pilot', 5)]
        self.tick(board, now)
        self.sent.edit.assert_awaited_once()
        self.assertEqual(self.channel.send.await_count, 1)

    def test_after_a_restart_this_weeks_board_is_taken_back_over(self):
        existing = MagicMock()
        existing.author = SimpleNamespace(id=5)
        existing.embeds = [board_embed([], WEEK)]
        existing.edit = AsyncMock()
        self.channel.history = History([existing])
        self.tick(HeliBoard(self.bot), WEEK + timedelta(days=1))
        self.channel.send.assert_not_awaited()
        existing.edit.assert_awaited_once()

    def test_a_new_week_finalises_the_old_board_and_starts_a_new_one(self):
        board = HeliBoard(self.bot)
        self.tick(board, WEEK + timedelta(days=6))
        self.tick(board, WEEK + timedelta(days=7, hours=1))
        final = self.sent.edit.await_args_list[0].kwargs['embed']
        self.assertIn('FINAL', final.title)
        self.assertEqual(self.channel.send.await_count, 2)

    def test_no_height_map_means_no_board_yet(self):
        with patch.object(heli_board.heights, 'load', return_value=None), \
             patch.object(heli_board.heights, 'ensure'):
            self.tick(HeliBoard(self.bot), WEEK + timedelta(days=1))
        self.channel.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
