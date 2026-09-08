import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import discord

from account_links import AccountLinks
from combat_parser import parse_kill
from combat_store import migrate, totals
from combat_ingestor import scan, ingest
from stats_command import StatsCommand, stats_embed, kd
from rank_command import RankCommand

VICTIM='11111111-2222-3333-4444-555555555555'
KILLER='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def event(clock='15:30:54.085', relation='ENEMY', killer='AI', name='Player'):
    # Based on the supplied live records; identifying values anonymised.
    attacker = 'AI' if killer == 'AI' else f'Other (playerID = 2 | UUID = {killer})'
    return (f'{clock}   SCRIPT       : INFO: KILL {relation}: {name} (playerID = 1 | UUID = {VICTIM}) '
            f'from US faction at <3926.26, 13.56, 8526.9> was killed by {attacker} from FIA faction '
            "who was at that time at <3912.02, 13.8667, 8516.17> [17.8m away from the corpse]. "
            "With last inflicted damage type KINETIC to the 'RArm' hit zone\n")


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

    def test_actual_ai_deaths_repeated_reads_and_new_event(self):
        self.process(event()+event('15:32:57.397'))
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)
        self.assertIsNone(totals(self.db,VICTIM)['ai_kills'])
        self.assertEqual(scan(self.db,'one',self.path),0)
        self.process(event(),append=True)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)
        self.process(event('15:33:00.000'),append=True)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],3)

    def test_player_kills_and_teamkills_separate(self):
        self.process(event(killer=KILLER)+event('15:31:00.000','TK',KILLER))
        result=totals(self.db,KILLER)
        self.assertEqual((result['player_kills'],result['teamkills'],result['deaths']),(1,1,0))
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)

    def test_restart_and_copied_log_do_not_duplicate(self):
        self.process(event())
        self.links.close()
        self.links=AccountLinks(self.database)
        self.db=self.links.db
        migrate(self.db)
        scan(self.db,'one',self.path)
        copied=self.root/'logs_2026-09-07_16-00-00'/'console.log'
        copied.parent.mkdir()
        self.process(event(),path=copied)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)
        self.process(event('16:01:00.000'),path=copied,append=True)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)

    def test_multiserver_disabled_and_name_changes(self):
        self.process(event())
        other=self.root/'server-two'/'logs_2026-09-07_14-44-37'/'console.log'
        other.parent.mkdir(parents=True)
        other.write_text(event('16:00:00.000',name='New Name'))
        config=[SimpleNamespace(id='two',enabled=False,log_dir=str(other.parent.parent))]
        ingest(str(self.database),config)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)
        config[0].enabled=True
        ingest(str(self.database),config)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM combat_totals').fetchone()[0],1)

    def test_partial_write_and_rewrite(self):
        self.process(event().rstrip('\n'))
        self.assertIsNone(totals(self.db,VICTIM))
        self.process('\n',append=True)
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)
        self.process('rewritten\n')
        self.assertEqual(totals(self.db,VICTIM)['deaths'],1)

    def test_transaction_failure_retries_without_extra_credit(self):
        from combat_store import record
        def fail(*args):
            record(*args)
            raise RuntimeError('interrupted commit')
        with patch('combat_ingestor.record',side_effect=fail):
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
        self.process(event('23:59:59.000')+event('00:00:01.000')+event('23:59:59.000')+event('00:00:02.000'))
        self.assertEqual(totals(self.db,VICTIM)['deaths'],3)

    def test_suicide_counts_death_only_and_warning_format(self):
        text=event(killer=KILLER)
        text=text.split(' was killed by ')[0]+' killed himself!\n'
        self.process(text+event('15:32:00.000',killer=KILLER).replace('SCRIPT       : INFO','SCRIPT (W): WARNING'))
        self.assertEqual(totals(self.db,VICTIM)['deaths'],2)
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
        self.assertIn('No Reforger combat stats',embed.fields[0].value)
        self.assertNotIn('Player Kills',[f.name for f in embed.fields])

    def test_clean_output_and_unavailable_ai(self):
        embed=stats_embed('Player',dict(player_kills=184,deaths=91,teamkills=4))
        fields={f.name:f.value for f in embed.fields}
        self.assertEqual(fields['AI Kills'],'Unavailable')
        self.assertEqual(fields['K/D'],'2.02')
        self.assertFalse({'XP','Rank','Playtime'} & fields.keys())


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
