"""Firing solutions: grids, the shared engine, per-weapon profiles, the command."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from bot.discord.mortar_command import MortarModal, MortarView, height, solution_embed
from bot.mortar.plot import render
from bot.mortar.solution import (OUT_OF_RANGE, bearing, between, parse_grid, profile,
                                 profiles, rings, solution)

# range, elevation, flight time, mils per 100m of height
ROWS = {'1': {'dispersion': 14, 'rows': [[100, 1500, 10.0, 20], [200, 1400, 11.0, 24]]},
        '2': {'dispersion': 24, 'rows': [[100, 1550, 14.0, 30], [400, 1300, 16.0, 40]]}}


def made_up(rings_block=None, mils=6400, **extra):
    """A profile built from a table file of our own, so the engine is tested
    without leaning on whatever the shipped tables happen to say."""
    entry = {'name': 'Test tube', 'faction': 'US', 'shell': 'HE TEST', 'mils': mils,
             'rings': ROWS if rings_block is None else rings_block, **extra}
    path = Path(tempfile.mkdtemp()) / 'tables.json'
    path.write_text(json.dumps({'test': entry}))
    return profile('test', str(path))


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
            self.assertAlmostEqual(bearing(gun, target, 6400)[0], mils, places=3)

    def test_the_sight_decides_the_number_not_the_engine(self):
        # Due east is 1600 on a 6400 sight and 1500 on a 6000 one.
        self.assertAlmostEqual(bearing((0, 0), (1000, 0), 6400)[0], 1600, places=3)
        self.assertAlmostEqual(bearing((0, 0), (1000, 0), 6000)[0], 1500, places=3)

    def test_range_is_the_straight_line_distance(self):
        self.assertAlmostEqual(bearing((5000, 5000), (5300, 5400), 6400)[1], 500, places=6)


class EngineTests(unittest.TestCase):
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
        weapon = made_up()
        best, every = solution(weapon, 150)
        self.assertEqual(best.ring, '1')
        self.assertEqual([r.ring for r in every], ['1', '2'])
        # Past ring 1's last row only ring 2 is left.
        best, every = solution(weapon, 300)
        self.assertEqual([r.ring for r in every], ['2'])

    def test_no_ring_reaching_is_out_of_range(self):
        self.assertEqual(solution(made_up(), 5000), (None, []))

    def test_a_target_above_the_gun_lowers_the_elevation(self):
        weapon = made_up()
        flat = rings(weapon, 150)[0]
        above = rings(weapon, 150, 100)[0]
        below = rings(weapon, 150, -100)[0]
        # 22 mils per 100m at this range, so 100m up takes 22 mils off.
        self.assertAlmostEqual(above.elevation, flat.elevation - 22)
        self.assertAlmostEqual(below.elevation, flat.elevation + 22)
        self.assertAlmostEqual(above.correction, -22)

    def test_flight_time_and_dispersion_come_off_the_table(self):
        ring = rings(made_up(), 150)[0]
        self.assertAlmostEqual(ring.flight, 10.5)
        self.assertEqual(ring.dispersion, 14)

    def test_a_ring_with_one_row_is_not_usable(self):
        weapon = made_up({'0': {'dispersion': 6, 'rows': [[100, 1500, 10.0, 20]]}})
        self.assertFalse(weapon.loaded)
        self.assertIsNone(weapon.span)
        self.assertEqual(solution(weapon, 100), (None, []))

    def test_a_tube_with_no_mil_circle_is_not_offered(self):
        # Guessing 6400 would throw every azimuth out by the 6400/6000 gap, so
        # the tube is left out rather than assumed to be NATO.
        path = Path(tempfile.mkdtemp()) / 'tables.json'
        path.write_text(json.dumps({'nosight': {'name': 'No sight', 'rings': ROWS},
                                    'good': {'name': 'Good', 'mils': 6000, 'rings': ROWS}}))
        self.assertEqual(list(profiles(str(path))), ['good'])

    def test_a_missing_file_leaves_no_profiles(self):
        self.assertEqual(profiles('/nonexistent/tables.json'), {})
        self.assertIsNone(profile('m252', '/nonexistent/tables.json'))
        self.assertEqual(solution(None, 900), (None, []))


class ProfileTests(unittest.TestCase):
    """The shipped tables: two weapons, each entirely its own."""

    def test_both_tubes_load_with_their_own_sight_shell_and_reach(self):
        us, ru = profile('m252'), profile('2b14')
        self.assertEqual((us.faction, us.mils, us.shell), ('US', 6400, 'HE M821'))
        self.assertEqual((ru.faction, ru.mils, ru.shell), ('USSR', 6000, 'HE O-832DU'))
        self.assertEqual(us.span, (50, 2900))
        self.assertEqual(ru.span, (50, 2300))
        for weapon in (us, ru):
            self.assertEqual([ring for ring, _, _ in weapon.rings], ['0', '1', '2', '3', '4'])

    def test_the_two_tables_are_not_the_same_numbers(self):
        # Same target, different weapon: nothing may be shared or converted.
        us, ru = solution(profile('m252'), 1200)[0], solution(profile('2b14'), 1200)[0]
        self.assertNotAlmostEqual(us.elevation, ru.elevation, places=0)
        self.assertNotAlmostEqual(us.flight, ru.flight, places=0)

    def test_each_tube_stops_at_its_own_maximum(self):
        for key, reach in (('m252', 2900), ('2b14', 2300)):
            weapon = profile(key)
            self.assertIsNotNone(solution(weapon, reach)[0], key)
            self.assertIsNone(solution(weapon, reach + 1)[0], key)
            self.assertIsNone(solution(weapon, 40)[0], key)
            self.assertIsNotNone(solution(weapon, 50)[0], key)

    def test_every_ring_reads_lower_as_the_range_grows(self):
        # A flatter tube throws further; a table that climbed would be corrupt.
        for key in ('m252', '2b14'):
            for ring, _, rows in profile(key).rings:
                elevations = [row[1] for row in rows]
                self.assertEqual(elevations, sorted(elevations, reverse=True),
                                 f'{key} ring {ring} elevation is not falling')


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
    async def submit(self, gun, target, attach=True, gun_alt='', target_alt='', tube='m252'):
        modal = MortarModal(tube)
        modal.gun_input._value, modal.target_input._value = gun, target
        modal.gun_height._value, modal.target_height._value = gun_alt, target_alt
        sent = interaction(attach)
        await modal.on_submit(sent)
        return sent

    def fields(self, sent):
        return {f.name: f.value for f in sent.followup.send.await_args.kwargs['embed'].fields}

    async def test_two_grids_come_back_as_a_full_solution(self):
        sent = await self.submit('0428 1183', '0512 1096')
        # The click is taken first, so the render cannot run out the clock.
        sent.response.defer.assert_awaited()
        fields = self.fields(sent)
        self.assertEqual(fields['Azimuth'].splitlines()[0], '**2418** mils')
        self.assertEqual(fields['Range'], '**1209** m')
        self.assertIn('**1176** mils on **ring 2**', fields['Elevation'])
        self.assertTrue(sent.followup.send.await_args.kwargs['ephemeral'])
        self.assertEqual(sent.followup.send.await_args.kwargs['file'].filename, 'mortar.png')

    async def test_the_soviet_tube_answers_on_its_own_sight_and_table(self):
        sent = await self.submit('0428 1183', '0512 1096', tube='2b14')
        fields = self.fields(sent)
        self.assertEqual(fields['Azimuth'].splitlines()[0], '**2267** mils')   # 6000 circle
        self.assertIn('6000 mil sight', fields['Tube'])
        self.assertIn('**989** mils on **ring 2**', fields['Elevation'])

    async def test_altitudes_move_the_elevation(self):
        sent = await self.submit('0428 1183', '0512 1096', gun_alt='100', target_alt='200')
        self.assertIn('Target +100 m', self.fields(sent)['Height'])

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
        self.assertEqual([option.value for option in select.options], ['m252', '2b14'])
        select._values = ['2b14']
        sent = interaction()
        await select.callback(sent)
        sent.edit_original_response.assert_awaited()
        self.assertEqual(view.tube, '2b14')

    def test_out_of_range_names_the_reach_of_that_tube(self):
        weapon = profile('2b14')
        embed = solution_embed(weapon, (4280, 11830), (4280, 14330), 0, 2500)
        elevation = next(f.value for f in embed.fields if f.name == 'Elevation')
        self.assertIn(OUT_OF_RANGE, elevation)
        self.assertIn('50-2300 m', elevation)
        self.assertNotIn('ring 4', elevation)  # no figure is offered at all

    def test_blank_altitudes_are_flat_ground(self):
        self.assertEqual(height(''), 0.0)
        self.assertEqual(height(' 120 '), 120.0)
        with self.assertRaises(ValueError):
            height('high up')


class PlotTests(unittest.TestCase):
    def test_the_plot_renders_a_png(self):
        gun, target = parse_grid('04281183'), parse_grid('05121096')
        mils, distance = bearing(gun, target, 6400)
        self.assertTrue(render(gun, target, mils, distance, 'M252 81mm', 6400)
                        .startswith(b'\x89PNG'))

    def test_a_short_shot_still_draws(self):
        # Two points a few metres apart must not collapse the scale to zero.
        self.assertTrue(render((5000, 5000), (5005, 5002), 1200, 5.4, '2B14 82mm', 6000)
                        .startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
