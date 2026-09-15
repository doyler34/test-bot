import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import discord

from bot.storage.account_links import AccountLinks
from bot.tracking.combat_parser import parse_kill
from bot.storage.combat_store import (BOSTON, migrate, totals, faction_totals, longest_kill,
                                      recent_matches, record_match, stamp, week_start,
                                      window_standings, window_totals)
from bot.tracking.combat_ingestor import scan, ingest
from bot.discord.stats_command import StatsCommand, matches_embed, stats_embed, kd
from bot.discord.rank_command import RankCommand

VICTIM='11111111-2222-3333-4444-555555555555'
KILLER='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def event(clock='15:30:54.085', relation='ENEMY', killer='AI', name='Player',
          distance='17.8', damage='KINETIC'):
    # Based on the supplied live records; identifying values anonymised.
    attacker = 'AI' if killer == 'AI' else f'Other (playerID = 2 | UUID = {killer})'
    return (f'{clock}   SCRIPT       : INFO: KILL {relation}: {name} (playerID = 1 | UUID = {VICTIM}) '
            f'from US faction at <3926.26, 13.56, 8526.9> was killed by {attacker} from FIA faction '
            f"who was at that time at <3912.02, 13.8667, 8516.17> [{distance}m away from the corpse]. "
            f"With last inflicted damage type {damage} to the 'RArm' hit zone\n")


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.database=self.root/'account_links.sqlite3'
        self.links=AccountLinks(self.database)
        self.db=self.links.db
        self.links.verified_link(1,10,VICTIM,'admin:20')
        self.db.execute('CREATE TABLE rank_wallet_v2(guild,member,identity,credit,baseline,milliseconds)')
        self.db.execute('INSERT INTO rank_wallet_v2 VALUES (1,10,?,80,1000,599999)',(VICTIM,))
        self.db.commit()
        migrate(self.db)
        self.path=self.root/'logs_2026-09-07_14-44-37'/'console.log'
        self.path.parent.mkdir()

    def tearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def process(self,text,append=False,path=None,server='one'):
        path=path or self.path
        with path.open('a' if append else 'w',encoding='utf-8') as stream:
            stream.write(text)
        return scan(self.db,server,path)

    def test_repeated_reads_and_new_event_do_not_double_count(self):
        self.process(event(killer=KILLER)+event('15:32:57.397',killer=KILLER))
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)
        self.assertIsNone(totals(self.db,VICTIM)['ai_kills'])
        self.assertEqual(scan(self.db,'one',self.path),0)
        self.process(event(killer=KILLER),append=True)  # duplicate: ignored
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)
        self.process(event('15:33:00.000',killer=KILLER),append=True)  # new
        self.assertEqual(totals(self.db,VICTIM)['deaths'],3)

    def test_ai_kills_do_not_count_as_deaths(self):
        self.process(event())  # killed by AI -> not a death
        self.assertIsNone(totals(self.db,VICTIM))
        self.process(event('15:31:00.000',killer=KILLER),append=True)  # killed by a player
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)  # only the player death counts

    def test_player_kills_and_teamkills_separate(self):
        self.process(event(killer=KILLER)+event('15:31:00.000','TK',KILLER))
        result=totals(self.db,KILLER)
        self.assertEqual((result['player_kills'],result['teamkills'],result['deaths']),(1,1,0))
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)

    def test_faction_is_captured_per_side(self):
        # event(): victim fights for US, killer for FIA.
        self.process(event(killer=KILLER)+event('15:31:00.000','TK',KILLER))
        killer_by_faction={r['faction']:r for r in faction_totals(self.db,KILLER)}
        self.assertEqual((killer_by_faction['FIA']['player_kills'],killer_by_faction['FIA']['teamkills']),(1,1))
        victim_by_faction={r['faction']:r for r in faction_totals(self.db,VICTIM)}
        self.assertEqual(victim_by_faction['US']['deaths'],2)
        # Career totals stay identical to the per-faction sums.
        self.assertEqual(totals(self.db,KILLER)['player_kills'],1)

    def test_parse_kill_reads_both_factions(self):
        ev=parse_kill(event(killer=KILLER))
        self.assertEqual((ev.victim_faction,ev.killer_faction),('US','FIA'))

    def test_parse_kill_reads_distance_and_damage_type(self):
        ev=parse_kill(event(killer=KILLER))
        self.assertEqual((ev.distance,ev.damage_type),(17.8,'KINETIC'))

    def test_suicide_without_a_damage_suffix_still_parses(self):
        # Vanilla writes suicides both with and without the damage-type suffix.
        bare=event(killer=KILLER).split(' was killed by ')[0]+' killed himself!\n'
        ev=parse_kill(bare)
        self.assertEqual((ev.victim,ev.killer,ev.damage_type,ev.distance),(VICTIM,VICTIM,None,None))
        suffixed=bare.rstrip('\n')+" With last inflicted damage type TRUE to the 'LThigh' hit zone\n"
        self.assertEqual(parse_kill(suffixed).damage_type,'TRUE')

    def test_window_totals_and_standings_respect_the_window(self):
        self.process(event(killer=KILLER)+event('15:31:00.000','TK',KILLER))
        start, end = datetime(2026,9,7), datetime(2026,9,8)
        killer=window_totals(self.db,KILLER,start,end)
        self.assertEqual((killer['player_kills'],killer['teamkills'],killer['deaths']),(1,1,0))
        self.assertEqual(window_totals(self.db,VICTIM,start,end)['deaths'],2)
        # A window that closes before the events happened sees nothing at all.
        self.assertIsNone(window_totals(self.db,KILLER,datetime(2026,9,1),datetime(2026,9,2)))
        # Only VICTIM is a linked member of guild 1, so only VICTIM ranks.
        self.assertEqual([tuple(r[:3]) for r in window_standings(self.db,1,start,end)],[(10,0,2)])

    def test_window_excludes_ai_and_suicide_deaths(self):
        suicide=event(killer=KILLER).split(' was killed by ')[0]+' killed himself!\n'
        self.process(event()+suicide)  # an AI kill and a suicide, nothing else
        start, end = datetime(2026,9,7), datetime(2026,9,8)
        self.assertIsNone(window_totals(self.db,VICTIM,start,end))
        self.assertEqual(window_standings(self.db,1,start,end),[])

    def test_longest_kill_counts_gunfire_only(self):
        self.process(event(killer=KILLER,distance='250.5')
                     +event('15:31:00.000',killer=KILLER,distance='900.0',damage='EXPLOSIVE'))
        start, end = datetime(2026,9,7), datetime(2026,9,8)
        # The 900m explosive must not beat the 250.5m gunfire kill.
        self.assertEqual(longest_kill(self.db,KILLER,start,end),250.5)
        self.assertEqual(window_totals(self.db,KILLER,start,end)['longest_kill'],250.5)
        # Being shot never earns the victim a longest kill.
        self.assertIsNone(longest_kill(self.db,VICTIM,start,end))

    def test_week_starts_monday_midnight_in_boston(self):
        monday = datetime(2026,9,14,tzinfo=BOSTON)
        for moment in (datetime(2026,9,14,0,0,tzinfo=BOSTON),
                       datetime(2026,9,16,13,5,tzinfo=BOSTON),
                       datetime(2026,9,20,23,59,tzinfo=BOSTON)):
            self.assertEqual(week_start(moment), monday)

    def test_week_turns_over_on_boston_midnight_not_utc(self):
        # The box may run on UTC; 03:00 UTC Monday is still Sunday night in
        # Boston (EDT, UTC-4), so that kill belongs to the week just gone.
        self.assertEqual(week_start(datetime(2026,9,14,3,0,tzinfo=timezone.utc)),
                         datetime(2026,9,7,tzinfo=BOSTON))
        self.assertEqual(week_start(datetime(2026,9,14,5,0,tzinfo=timezone.utc)),
                         datetime(2026,9,14,tzinfo=BOSTON))

    def test_boundary_follows_boston_through_the_dst_change(self):
        # January is EST (UTC-5), an hour later in absolute terms than EDT.
        self.assertEqual(week_start(datetime(2027,1,11,6,0,tzinfo=timezone.utc)),
                         datetime(2027,1,11,tzinfo=BOSTON))
        self.assertEqual(week_start(datetime(2027,1,11,4,0,tzinfo=timezone.utc)),
                         datetime(2027,1,4,tzinfo=BOSTON))

    def test_recent_matches_skip_games_the_player_sat_out(self):
        # Three matches on the day the fixture log covers; the player only
        # appears in the first and third.
        base = datetime(2026,9,7,15,0)
        for n in range(3):
            opened = base + timedelta(hours=n)
            record_match(self.db,'one',f'Server {n}',opened,opened+timedelta(minutes=50))
        self.process(event('15:05:00.000',killer=KILLER)+event('17:05:00.000',killer=KILLER))
        played = recent_matches(self.db,VICTIM)
        self.assertEqual([m['name'] for m in played],['Server 2','Server 0'])
        self.assertEqual([(m['kills'],m['deaths']) for m in played],[(0,1),(0,1)])

    def test_recent_matches_are_capped_and_newest_first(self):
        base = datetime(2026,9,7,0,0)
        for n in range(12):
            opened = base + timedelta(minutes=n)
            record_match(self.db,'one',f'Server {n}',opened,opened+timedelta(minutes=1))
            clock = f'{opened:%H:%M:%S}.000'
            self.process(event(clock,killer=KILLER),append=n>0)
        played = recent_matches(self.db,VICTIM,limit=10)
        self.assertEqual(len(played),10)
        self.assertEqual(played[0]['name'],'Server 11')

    def test_linking_late_picks_up_this_week_but_not_last(self):
        # Kills are logged against the in-game identity whether or not anyone
        # has linked it, so linking is retroactive - but only inside the week.
        monday = datetime(2026,9,7)  # the fixture log's own day
        self.process(event('15:00:00.000',killer=KILLER))
        other = self.root/'logs_2026-09-06_15-00-00'/'console.log'
        other.parent.mkdir()
        self.process(event('15:00:00.000',killer=KILLER),path=other)  # the day before
        token = self.links.submit(1,42,KILLER,'Late Linker')
        self.links.review(1,token,99,True)
        rows = window_standings(self.db,1,monday)
        self.assertEqual([(r[0],r[1]) for r in rows if r[0]==42],[(42,1)])
        self.assertEqual(window_totals(self.db,KILLER,monday)['player_kills'],1)

    def test_restart_and_copied_log_do_not_duplicate(self):
        self.process(event(killer=KILLER))
        self.links.close()
        self.links=AccountLinks(self.database)
        self.db=self.links.db
        migrate(self.db)
        scan(self.db,'one',self.path)
        copied=self.root/'logs_2026-09-07_16-00-00'/'console.log'
        copied.parent.mkdir()
        self.process(event(killer=KILLER),path=copied)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)
        self.process(event('16:01:00.000',killer=KILLER),path=copied,append=True)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)

    def test_multiserver_disabled_and_name_changes(self):
        self.process(event(killer=KILLER))
        other=self.root/'server-two'/'logs_2026-09-07_14-44-37'/'console.log'
        other.parent.mkdir(parents=True)
        other.write_text(event('16:00:00.000',killer=KILLER,name='New Name'))
        config=[SimpleNamespace(id='two',enabled=False,log_dir=str(other.parent.parent))]
        ingest(str(self.database),config)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)
        config[0].enabled=True
        ingest(str(self.database),config)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)
        # VICTIM (deaths) and KILLER (kills) each get one totals row.
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM combat_totals').fetchone()[0],2)

    def test_partial_write_and_rewrite(self):
        self.process(event(killer=KILLER).rstrip('\n'))
        self.assertIsNone(totals(self.db,VICTIM))
        self.process('\n',append=True)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)
        self.process('rewritten\n')
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)

    def test_transaction_failure_retries_without_extra_credit(self):
        from bot.storage.combat_store import record
        def fail(*args):
            record(*args)
            raise RuntimeError('interrupted commit')
        with patch('bot.tracking.combat_ingestor.record',side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.process(event())
        self.assertIsNone(totals(self.db,VICTIM))
        self.assertEqual(scan(self.db,'one',self.path),1)

    def test_migration_backup_and_xp_untouched(self):
        self.process(event())
        migrate(self.db)
        self.assertEqual(self.db.execute('SELECT credit,milliseconds FROM rank_wallet_v2').fetchone(),(80,599999))
        self.assertEqual(self.links.lookup(1,10),VICTIM)
        with closing(sqlite3.connect(str(self.database)+'.before-combat-v1.sqlite3')) as backup:
            self.assertEqual(backup.execute('SELECT credit,milliseconds FROM rank_wallet_v2').fetchone(),(80,599999))

    def test_midnight_and_duplicate_previous_day_record(self):
        self.process(event('23:59:59.000',killer=KILLER)+event('00:00:01.000',killer=KILLER)
                     +event('23:59:59.000',killer=KILLER)+event('00:00:02.000',killer=KILLER))
        self.assertEqual(totals(self.db,VICTIM)['deaths'],3)

    def test_suicide_is_not_a_death_and_warning_format(self):
        text=event(killer=KILLER)
        text=text.split(' was killed by ')[0]+' killed himself!\n'
        self.process(text+event('15:32:00.000',killer=KILLER).replace('SCRIPT       : INFO','SCRIPT (W): WARNING'))
        # Only the player-vs-player kill counts; topping yourself is not a death.
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)
        self.assertEqual(totals(self.db,KILLER)['player_kills'],1)

    def test_warnings_invalid_uuid_and_ai_victim_not_counted(self):
        for line in ['SCRIPT (W): No instigator on death',' '+event(),event('99:99:99.000'),event().replace(VICTIM,'not-a-valid-identity'),
                     event().replace(f'Player (playerID = 1 | UUID = {VICTIM})','AI')]:
            self.assertIsNone(parse_kill(line))


class OutputTests(unittest.TestCase):
    def test_kd_and_no_data(self):
        for k,d,result in [(184,91,'2.02'),(5,0,'5.00'),(0,0,'0.00'),(0,3,'0.00')]:
            self.assertEqual(kd(k,d),result)
        embed=stats_embed('Player',None)
        self.assertIn('this week',embed.fields[0].value)
        self.assertNotIn('Player Kills',[f.name for f in embed.fields])

    def test_clean_output_and_unavailable_ai(self):
        embed=stats_embed('Player',dict(player_kills=184,deaths=91,teamkills=4,longest_kill=312.4))
        fields={f.name:f.value for f in embed.fields}
        self.assertEqual(fields['AI Kills'],'Unavailable')
        self.assertEqual(fields['K/D'],'2.02')
        self.assertEqual(fields['Longest Kill'],'312 m')
        self.assertFalse({'XP','Rank','Playtime'} & fields.keys())

    def test_per_game_embed_lists_each_match(self):
        from datetime import datetime as when
        embed=matches_embed('Player',[dict(name='OYB Classic',started=when(2026,9,14,20,0),kills=3,deaths=1)])
        self.assertIn('OYB Classic',embed.description)
        self.assertRegex(embed.description,r'14 Sep 20:00\s+3\s+1')

    def test_per_game_embed_explains_an_empty_history(self):
        embed=matches_embed('Player',[])
        self.assertIn('No finished matches',embed.fields[0].name+embed.fields[0].value)

    def test_longest_kill_absent_reads_as_a_dash(self):
        embed=stats_embed('Player',dict(player_kills=1,deaths=0,teamkills=0,longest_kill=None))
        self.assertEqual({f.name:f.value for f in embed.fields}['Longest Kill'],'—')


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_tree_linking_and_other_member(self):
        with tempfile.TemporaryDirectory() as folder:
            bot=discord.Client(intents=discord.Intents.default())
            bot.config=SimpleNamespace(guild_id=1)
            bot.account_links=AccountLinks(Path(folder)/'links.db')
            bot.rank_command=RankCommand(bot)
            migrate(bot.account_links.db)
            command=StatsCommand(bot)
            interaction=SimpleNamespace(guild_id=1,user=SimpleNamespace(id=10,display_name='Player'),
                app_permissions=SimpleNamespace(embed_links=True),
                response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
            try:
                self.assertEqual({c.name for c in bot.rank_command.tree.get_commands(guild=discord.Object(id=1))},{'rank','stats'})
                await command.show(interaction)
                self.assertIn('#join-oyb',interaction.response.send_message.await_args.args[0])
                bot.account_links.verified_link(1,10,VICTIM,'admin:20')
                await command.show(interaction)
                self.assertIn('No recorded data',interaction.followup.send.await_args.kwargs['embed'].fields[0].name)
                await command.show(interaction,user=SimpleNamespace(id=30,display_name='Other'))
                self.assertIn('does not have',interaction.response.send_message.await_args.args[0])
            finally:
                await bot.close()
                bot.account_links.close()

    async def test_per_game_button_replies_privately_with_the_match_list(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as folder:
            bot=discord.Client(intents=discord.Intents.default())
            bot.config=SimpleNamespace(guild_id=1)
            bot.account_links=AccountLinks(Path(folder)/'links.db')
            bot.rank_command=RankCommand(bot)
            migrate(bot.account_links.db)
            command=StatsCommand(bot)
            try:
                opened=datetime(2026,9,7,15,0)
                record_match(bot.account_links.db,'one','OYB Classic',opened,opened+timedelta(minutes=50))
                with bot.account_links.db:
                    bot.account_links.db.execute(
                        "INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation)"
                        " VALUES ('one','k',?,?,?,'ENEMY')",
                        (stamp(opened+timedelta(minutes=5)),VICTIM,KILLER))
                interaction=SimpleNamespace(
                    response=SimpleNamespace(defer=AsyncMock(),is_done=lambda:True),
                    followup=SimpleNamespace(send=AsyncMock()))
                await command.per_game(interaction,KILLER,'Killer')
                self.assertTrue(interaction.response.defer.await_args.kwargs['ephemeral'])
                sent=interaction.followup.send.await_args
                self.assertTrue(sent.kwargs['ephemeral'])
                self.assertIn('OYB Classic',sent.kwargs['embed'].description)
            finally:
                await bot.close()
                bot.account_links.close()
