"""Firing solutions: grid parsing, bearing, the range tables and the command."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from bot.discord.mortar_command import MortarModal, MortarView, height, solution_embed
from bot.mortar.plot import render
from bot.mortar.solution import (OUT_OF_RANGE, bearing, between, has_table, mil_circle,
                                 parse_grid, rings, solution, tube_names)

# range, elevation, flight time, mils per 100m of height
ROWS = {'1': {'dispersion': 14, 'rows': [[100, 1500, 10.0, 20], [200, 1400, 11.0, 24]]},
        '2': {'dispersion': 24, 'rows': [[100, 1550, 14.0, 30], [400, 1300, 16.0, 40]]}}


def table(rings_block, mils=6400):
    path = Path(tempfile.mkdtemp()) / 'tables.json'
    path.write_text(json.dumps({'m252': {'name': 'M252 81mm (US)', 'mils': mils,
                                         'shell': 'HE M821', 'rings': rings_block}}))
    return str(path)


class GridTests(unittest.TestCase):
    def test_a_grid_reads_at_the_precision_it_was_given(self):
        # 6 digits is 100m, 8 is 10m, 10 is 1m - the same as reading the map.
        self.assertEqual(parse_grid('042118'), (4200, 11800))
        self.assertEqual(parse_grid('04281183'), (4280, 11830))
        self.assertEqual(parse_grid('0428311834'), (4283, 11834))
        self.assertEqual(parse_grid('0411'), (4000, 11000))

    def test_spacing_and_punctuation_are_ignored(self):
        for text in ('0428 1183', '0428-1183', ' 0428,1183 '):
            self.assertEqual(parse_grid(text), (4280, 11830))

    def test_a_grid_that_is_not_a_grid_is_refused(self):
        for text in ('', '123', '0428118', 'north of the bridge', '1' * 12):
            with self.assertRaises(ValueError):
                parse_grid(text)


class BearingTests(unittest.TestCase):
    def test_the_cardinals_come_out_where_they_should(self):
        gun = (5000, 5000)
        for target, mils in [((5000, 6000), 0), ((6000, 5000), 1600),
                             ((5000, 4000), 3200), ((4000, 5000), 4800)]:
            self.assertAlmostEqual(bearing(gun, target)[0], mils, places=3)

    def test_each_tube_reads_its_own_mil_circle(self):
        # The M252 sight reads 6400 to the circle, the 2B14 reads 6000, so due
        # east is a different number on each.
        self.assertAlmostEqual(bearing((0, 0), (1000, 0), 6400)[0], 1600, places=3)
        self.assertAlmostEqual(bearing((0, 0), (1000, 0), 6000)[0], 1500, places=3)
        self.assertEqual(mil_circle('m252'), 6400)
        self.assertEqual(mil_circle('2b14'), 6000)

    def test_range_is_the_straight_line_distance(self):
        self.assertAlmostEqual(bearing((5000, 5000), (5300, 5400))[1], 500, places=6)


class TableTests(unittest.TestCase):
    def test_a_range_between_two_rows_is_split_between_them(self):
        rows = [[100, 1500, 10.0, 20], [200, 1400, 11.0, 24]]
        self.assertEqual(between(rows, 100, 1), 1500)
        self.assertEqual(between(rows, 150, 1), 1450)
        self.assertEqual(between(rows, 150, 2), 10.5)
        self.assertEqual(between(rows, 200, 1), 1400)

    def test_outside_the_table_nothing_is_extrapolated(self):
        rows = [[100, 1500, 10.0, 20], [200, 1400, 11.0, 24]]
        self.assertIsNone(between(rows, 99, 1))
        self.assertIsNone(between(rows, 260, 1))

    def test_the_lowest_ring_that_reaches_comes_first(self):
        path = table(ROWS)
        best, every = solution('m252', 150, 0, path)
        self.assertEqual(best.ring, '1')
        self.assertEqual([r.ring for r in every], ['1', '2'])
        # Past ring 1's last row only ring 2 is left.
        best, every = solution('m252', 300, 0, path)
        self.assertEqual([r.ring for r in every], ['2'])
        self.assertEqual(best.ring, '2')

    def test_no_ring_reaching_is_out_of_range(self):
        best, every = solution('m252', 5000, 0, table(ROWS))
        self.assertIsNone(best)
        self.assertEqual(every, [])

    def test_a_target_above_the_gun_lowers_the_elevation(self):
        path = table(ROWS)
        flat = rings('m252', 150, 0, path)[0]
        above = rings('m252', 150, 100, path)[0]
        below = rings('m252', 150, -100, path)[0]
        # 22 mils per 100m at this range, so 100m up takes 22 mils off.
        self.assertAlmostEqual(above.elevation, flat.elevation - 22)
        self.assertAlmostEqual(below.elevation, flat.elevation + 22)
        self.assertAlmostEqual(above.correction, -22)

    def test_flight_time_and_dispersion_come_off_the_table(self):
        ring = rings('m252', 150, 0, table(ROWS))[0]
        self.assertAlmostEqual(ring.flight, 10.5)
        self.assertEqual(ring.dispersion, 14)

    def test_a_missing_file_is_not_a_crash(self):
        self.assertEqual(solution('m252', 900, 0, '/nonexistent/tables.json'), (None, []))


class ShippedTableTests(unittest.TestCase):
    """The real M252 table, as copied out of the game."""

    def test_the_m252_covers_its_documented_span(self):
        self.assertTrue(has_table('m252'))
        self.assertIsNone(solution('m252', 40)[0])
        self.assertIsNotNone(solution('m252', 50)[0])
        self.assertIsNotNone(solution('m252', 2900)[0])
        self.assertIsNone(solution('m252', 2901)[0])

    def test_every_ring_reads_lower_as_the_range_grows(self):
        # A flatter tube throws further; a table that climbed would be corrupt.
        from bot.mortar.solution import tube
        for ring, block in tube('m252')['rings'].items():
            rows = sorted(block['rows'])
            self.assertEqual([r[1] for r in rows], sorted((r[1] for r in rows), reverse=True),
                             f'ring {ring} elevation is not falling')

    def test_the_2b14_is_left_empty_until_it_has_its_own_numbers(self):
        # It fires on a 6000 mil circle and must never borrow the M252's table.
        self.assertFalse(has_table('2b14'))
        self.assertIsNone(solution('2b14', 900)[0])


def responding():
    state = {'done': False}

    async def done(*_, **__):
        state['done'] = True

    return SimpleNamespace(send_message=AsyncMock(side_effect=done),
                           edit_message=AsyncMock(side_effect=done),
                           send_modal=AsyncMock(side_effect=done),
                           defer=AsyncMock(side_effect=done),
                           is_done=lambda: state['done'])


def interaction(attach=True):
    return SimpleNamespace(guild_id=1, user=SimpleNamespace(id=7), response=responding(),
                           followup=SimpleNamespace(send=AsyncMock()),
                           edit_original_response=AsyncMock(),
                           app_permissions=SimpleNamespace(attach_files=attach))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def submit(self, gun, target, attach=True, gun_alt='', target_alt=''):
        modal = MortarModal('m252')
        modal.gun_input._value, modal.target_input._value = gun, target
        modal.gun_height._value, modal.target_height._value = gun_alt, target_alt
        sent = interaction(attach)
        await modal.on_submit(sent)
        return sent

    async def test_two_grids_come_back_as_a_full_solution(self):
        sent = await self.submit('0428 1183', '0512 1096')
        # The click is taken first, so the render cannot run out the clock.
        sent.response.defer.assert_awaited()
        embed = sent.followup.send.await_args.kwargs['embed']
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields['Azimuth'].splitlines()[0], '**2418** mils')
        self.assertEqual(fields['Range'], '**1209** m')
        self.assertIn('ring 2', fields['Elevation'])
        self.assertIn('**1176** mils', fields['Elevation'])
        self.assertTrue(sent.followup.send.await_args.kwargs['ephemeral'])
        self.assertEqual(sent.followup.send.await_args.kwargs['file'].filename, 'mortar.png')

    async def test_altitudes_move_the_elevation(self):
        sent = await self.submit('0428 1183', '0512 1096', gun_alt='100', target_alt='200')
        fields = {f.name: f.value for f in sent.followup.send.await_args.kwargs['embed'].fields}
        self.assertIn('Target +100 m', fields['Height'])
        self.assertIn('mils', fields['Height'])

    async def test_a_typo_is_explained_not_swallowed(self):
        sent = await self.submit('not a grid', '0512 1096')
        self.assertIn('4, 6, 8 or 10 digits', sent.response.send_message.await_args.args[0])

    async def test_an_altitude_that_is_not_a_number_is_explained(self):
        sent = await self.submit('0428 1183', '0512 1096', gun_alt='about 100')
        self.assertIn('metres', sent.response.send_message.await_args.args[0])

    async def test_firing_on_your_own_position_is_refused(self):
        sent = await self.submit('0428 1183', '04281183')
        self.assertIn('same grid', sent.response.send_message.await_args.args[0])

    async def test_without_attach_files_the_numbers_still_arrive(self):
        sent = await self.submit('0428 1183', '0512 1096', attach=False)
        self.assertNotIn('file', sent.followup.send.await_args.kwargs)

    async def test_changing_tube_edits_the_same_private_message(self):
        view = MortarView('m252', (4280, 11830), (5120, 10960))
        select = view.children[0]
        select._values = ['2b14']
        sent = interaction()
        await select.callback(sent)
        sent.edit_original_response.assert_awaited()
        self.assertEqual(view.tube, '2b14')

    def test_out_of_range_is_said_plainly(self):
        embed = solution_embed('m252', 'M252 81mm (US)', (4280, 11830), (4280, 15830),
                               0, 4000, 0, 6400)
        elevation = next(f.value for f in embed.fields if f.name == 'Elevation')
        self.assertIn(OUT_OF_RANGE, elevation)
        self.assertNotIn('mils', elevation)  # no figure is offered at all

    def test_a_tube_with_no_table_cannot_produce_an_elevation(self):
        embed = solution_embed('2b14', '2B14 Podnos 82mm (USSR)', (4280, 11830),
                               (5120, 10960), 2267, 1209, 0, 6000)
        elevation = next(f.value for f in embed.fields if f.name == 'Elevation')
        self.assertIn(OUT_OF_RANGE, elevation)

    def test_blank_altitudes_are_flat_ground(self):
        self.assertEqual(height(''), 0.0)
        self.assertEqual(height(' 120 '), 120.0)
        with self.assertRaises(ValueError):
            height('high up')


class PlotTests(unittest.TestCase):
    def test_the_plot_renders_a_png(self):
        gun, target = parse_grid('04281183'), parse_grid('05121096')
        mils, distance = bearing(gun, target)
        data = render(gun, target, mils, distance, 'M252 81mm (US)')
        self.assertTrue(data.startswith(b'\x89PNG'))

    def test_a_short_shot_still_draws(self):
        # Two points a few metres apart must not collapse the scale to zero.
        data = render((5000, 5000), (5005, 5002), 1200, 5.4, 'M252 81mm (US)')
        self.assertTrue(data.startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
