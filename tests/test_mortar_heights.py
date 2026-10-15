import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.mortar import heights
from bot.web import server

# A 4 x 4 grid over a 30 m square, 10 m apart, laid out the way GeNeFRAG's file
# is: the outer list runs east, the inner one runs south from the top.
GRID = [["0", "0", "0", "0"],
        ["10", "10", "10", "10"],
        ["20", "20", "20", "20"],
        ["30", "30", "30", "30"]]


class HeightTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name, "h.bin")
        self.patch = mock.patch.object(heights, "FILE", self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_reads_like_genefrags_engine_and_between_points(self):
        self.assertIsNone(heights.load())
        heights.save(GRID)
        ground = heights.load()
        ground.size, ground.cell = 30, (10, 10)
        self.assertEqual(ground.at(0, 30), 0)        # west edge
        self.assertEqual(ground.at(20, 30), 20)       # 2 cells east
        self.assertEqual(ground.at(25, 0), 25)        # between points, any row
        south_north = [[0, 1, 2, 3]] * 4                # rising southwards from the top
        heights.save(south_north)
        ground = heights.load()
        ground.size, ground.cell = 30, (10, 10)
        self.assertEqual(ground.at(0, 30), 0)          # top row is the north edge
        self.assertEqual(ground.at(0, 0), 3)           # bottom row is the south edge

    def test_map_taps_get_the_height_difference_and_typed_heights_win(self):
        grid = [[str(e // 100) for _ in range(129)] for e in range(0, 12900, 100)]   # 1 m up per 100 m east
        heights.save(grid)
        body = {'tube': 'm252', 'round': 'he', 'gun': {'east': 5000, 'north': 6000},
                'target': {'east': 6000, 'north': 6000}}
        answer = server.calculate(body, None)
        self.assertAlmostEqual(answer['climb_m'], 10, delta=0.2)
        self.assertAlmostEqual(answer['target_height_m'] - answer['gun_height_m'], 10, delta=0.2)
        flat = server.calculate({**body, 'climb': 0}, None)
        self.assertEqual(flat['climb_m'], 0)
        self.assertNotIn('gun_height_m', flat)
        self.assertNotEqual(answer['elevation_mils'], flat['elevation_mils'])

    def test_without_heights_nothing_changes(self):
        body = {'tube': 'm252', 'round': 'he', 'gun': {'east': 5000, 'north': 6000},
                'target': {'east': 6000, 'north': 6000}}
        answer = server.calculate(body, None)
        self.assertEqual(answer['climb_m'], 0)
        self.assertNotIn('gun_height_m', answer)
