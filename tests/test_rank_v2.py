import sqlite3
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from rank_rules import rank_for_xp, xp_from_seconds
from rank_persistence import XPStore, migrate_time, record_interval
from rank_card import render_card
from playtime_tracker import Tracker
from test_playtime import join, heartbeat, leave, UID


class RuleTests(unittest.TestCase):
    def test_all_boundaries(self):
        names = ('Renegade', 'Recruit', 'Private', 'Corporal', 'Sergeant', 'Lieutenant', 'Captain', 'Major')
        for xp in (0, 1, 99, 100, 199, 200, 299, 300, 347, 399, 400, 499, 500, 599, 600, 699, 700, 1000):
            with self.subTest(xp=xp):
                self.assertEqual(rank_for_xp(xp).current.name, names[min(xp//100, 7)])

    def test_xp_fraction_and_maximum(self):
        for seconds, xp in ((599, 0), (600, 1), (1199, 1), (1200, 2), (1560, 2)):
            self.assertEqual(xp_from_seconds(seconds), xp)
        p = rank_for_xp(347)
        self.assertEqual((p.current.threshold, p.next.threshold, p.remaining, p.fraction), (300, 400, 53, .47))
        self.assertEqual(rank_for_xp(400).fraction, 0)
        p = rank_for_xp(1000)
        self.assertEqual((p.next, p.remaining, p.fraction), (None, None, 1))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = sqlite3.connect(self.root/'time.db')
        self.source.execute('CREATE TABLE totals(server TEXT, identity TEXT, seconds REAL)')
        self.db = sqlite3.connect(self.root/'links.db')

    def tearDown(self):
        self.db.close()
        self.source.close()
        self.tmp.cleanup()

    def test_legacy_credit_partial_and_new_time_survive_restart(self):
        self.source.execute("INSERT INTO totals VALUES ('one','player',2000)")
        self.source.commit()
        with patch('rank_persistence.time.time', return_value=10000):
            migrate_time(self.source)
        self.db.execute('CREATE TABLE rank_progress(guild, member, identity, baseline, seconds)')
        self.db.execute("INSERT INTO rank_progress VALUES(1,2,'player',0,179)")
        self.db.commit()
        wallet = XPStore(self.db)
        self.assertEqual(wallet.cached(1,2),20)
        # Time earned before the wallet first reads must also count.
        with self.source:
            record_interval(self.source, 'player',10000,10541)
        self.assertEqual(wallet.read(1,2,'player',self.root/'time.db',True),21)
        self.db.close()
        self.db = sqlite3.connect(self.root/'links.db')
        wallet = XPStore(self.db)
        migrate_time(self.source)
        self.assertEqual(wallet.read(1,2,'player',self.root/'time.db',True),21)
        self.assertEqual(wallet.read(1,2,'player',self.root/'missing.db',True),21)
        self.assertEqual(self.db.execute('SELECT seconds FROM rank_progress').fetchone()[0],179)
        self.assertEqual(len(list(self.root.glob('*.before-*.sqlite3'))),2)
        with closing(sqlite3.connect(self.root/'links.db.before-rank-v2.sqlite3')) as old:
            self.assertEqual(old.execute('SELECT seconds FROM rank_progress').fetchone()[0],179)

    def test_overlapping_intervals_and_transaction_rollback(self):
        migrate_time(self.source)
        with self.source:
            for a,b in ((0,600),(300,900),(100,400),(900,1200),(0,1200)):
                record_interval(self.source,'player',a,b)
        self.assertEqual(self.source.execute('SELECT milliseconds FROM global_time').fetchone()[0],1200000)
        self.assertEqual(self.source.execute('SELECT COUNT(*) FROM global_intervals').fetchone()[0],1)
        try:
            with self.source:
                record_interval(self.source,'player',1200,1400)
                raise RuntimeError('crash')
        except RuntimeError:
            pass
        wallet = XPStore(self.db)
        self.assertEqual(wallet.read(1,2,'player',self.root/'time.db',True),2)
        with self.assertRaises(ValueError):
            wallet.read(1,2,'different',self.root/'time.db',True)

    def test_two_real_trackers_overlap_restart_and_rotation(self):
        roots = [self.root/'one',self.root/'two']
        logs = []
        for root in roots:
            log = root/'logs_2026-09-07_13-00-00'/'console.log'
            log.parent.mkdir(parents=True)
            log.write_text(join('13:00:00')+heartbeat('13:01:00')+leave('13:02:00'))
            logs.append(log)
        for i,root in enumerate(roots):
            tracker = Tracker(root,self.root/'combined.db',str(i))
            tracker.tick()
            tracker.close()
        tracker = Tracker(roots[0],self.root/'combined.db','0')
        try:
            tracker.tick()
            self.assertEqual(tracker.db.execute('SELECT milliseconds FROM global_time').fetchone()[0],120000)
            self.assertEqual(tracker.db.execute('SELECT SUM(seconds) FROM totals').fetchone()[0],240)
            new = roots[0]/'logs_2026-09-07_14-00-00'/'console.log'
            new.parent.mkdir()
            new.write_text(heartbeat('14:00:00',0)+join('14:01:00')+heartbeat('14:02:00'))
            tracker.tick()
            self.assertEqual(tracker.db.execute('SELECT milliseconds FROM global_time').fetchone()[0],180000)
        finally:
            tracker.close()


class CardTests(unittest.TestCase):
    def test_cards_fit_and_major_has_no_next_rank(self):
        for name,xp in [('GazLagom',0),('GARETH',143),('GARETH',347),('GARETH',450),('GARETH',700),('Long name '*40,1000),('Gáréth · Ελληνικά · Игрок · 玩家',600)]:
            audit=[]
            with Image.open(BytesIO(render_card(name,xp,audit=audit))) as card:
                self.assertEqual(card.size,(960,320))
                self.assertEqual(card.format,'PNG')
            for text,box,limit in audit:
                self.assertGreaterEqual(box[0],limit[0]-2,text)
                self.assertLessEqual(box[2],limit[2]+2,text)
                self.assertLessEqual(box[3],320,text)
            labels=[t for t,_,_ in audit]
            self.assertEqual('MAX RANK' in labels,xp>=700)
            self.assertEqual('NEXT RANK' in labels,xp<700)

    def test_bad_avatar_and_missing_insignia(self):
        from rank_card import vector
        def missing(path,*args):
            if path.parent.name == 'ranks':
                raise ValueError('missing')
            return vector(path,*args)
        with patch('rank_card.vector',side_effect=missing):
            self.assertTrue(render_card('Player',347,b'broken').startswith(b'\x89PNG'))

    def test_gif_avatar(self):
        output=BytesIO()
        Image.new('RGB',(32,32),'red').save(output,format='GIF',save_all=True,append_images=[Image.new('RGB',(32,32),'blue')])
        with Image.open(BytesIO(render_card('Player',0,output.getvalue()))) as card:
            self.assertEqual(card.getpixel((117,142)),(255,0,0))

    def test_missing_template_and_fonts_use_local_fallbacks(self):
        import rank_card
        with tempfile.TemporaryDirectory() as directory:
            try:
                with patch.object(rank_card,'ASSETS',Path(directory)):
                    rank_card.template.cache_clear()
                    rank_card.font.cache_clear()
                    self.assertTrue(render_card('Player',347).startswith(b'\x89PNG'))
            finally:
                rank_card.template.cache_clear()
                rank_card.font.cache_clear()
