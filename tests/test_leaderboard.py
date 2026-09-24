from pathlib import Path
import tempfile
import unittest
import uuid
from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate, stamp, week_start
from bot.discord.leaderboard_command import PAGE_SIZE, leaderboard_embed, standings, table


def players(count):
    return [(f'Player {i}', count-i, i) for i in range(count)]


class LeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.links=AccountLinks(Path(self.tmp.name)/'links.db')
        migrate(self.links.db)
        self.seq=0

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def add(self, member, kills=0, deaths=0, guild=1, name='Player', combat=True):
        identity=str(uuid.UUID(int=member))
        token=self.links.submit(guild,member,identity,name)
        self.links.review(guild,token,999,True)
        if combat:
            self.events(identity,kills,deaths)

    def events(self, identity, kills=0, deaths=0):
        """Standings are computed from this week's raw events, not from totals."""
        other='99999999-9999-9999-9999-999999999999'
        when=stamp(week_start())
        sql=("INSERT INTO combat_events (server,event_key,occurred,victim,killer,relation)"
             " VALUES ('s',?,?,?,?,'ENEMY')")
        with self.links.db:
            for victim,killer in [(other,identity)]*kills + [(identity,other)]*deaths:
                self.seq+=1
                self.links.db.execute(sql,(f'e{self.seq}',when,victim,killer))

    async def test_sort_scope_and_read_only(self):
        for args in [(12,10,2),(11,10,2),(13,10,1),(14,11,9)]:
            self.add(*args)
        self.add(15,999,guild=2)
        self.add(16,combat=False)
        self.events('00000000-0000-0000-0000-0000000000ff',999,0)  # never linked
        before=list(self.links.db.iterdump())
        self.assertEqual([row[0] for row in standings(self.links.db,1)],[14,13,11,12])
        self.assertEqual(before,list(self.links.db.iterdump()))

    async def test_page_sizes_and_numbering(self):
        # Derived from PAGE_SIZE so raising it never silently breaks this.
        for count,pages in [(0,1),(1,1),(PAGE_SIZE,1),(PAGE_SIZE+1,2),
                            (PAGE_SIZE*2,2),(PAGE_SIZE*2+1,3)]:
            for page in range(pages):
                embed=leaderboard_embed(players(count),page)
                self.assertIn(f'Page {page+1}/{pages}',embed.footer.text)
                if count:
                    lines=embed.description.splitlines()[2:-1]
                    self.assertEqual(len(lines),min(PAGE_SIZE,count-page*PAGE_SIZE))
                    self.assertEqual(int(lines[0].split()[0]),page*PAGE_SIZE+1)
                    self.assertLess(len(embed.description),4096)
                else:
                    self.assertIn('No combat recorded this week',embed.description)

    def test_every_page_is_the_same_width(self):
        # A three-digit position used to widen the row by a character, so the
        # later pages wrapped on a phone while the first ones read fine.
        widths = {len(line) for offset in (0, 75, 999)
                  for line in table([('Vo1d-SaNdStOrMz5', 16, 50), ('x', 9999, 1234)],
                                    offset).splitlines()[1:-1]}
        self.assertEqual(len(widths), 1)
        self.assertLessEqual(widths.pop(), 32)

    async def test_names_cannot_escape_table(self):
        embed=leaderboard_embed([('```\n@everyone\r\n‮'+'X'*200,4,3),('Éowyn 玩家',2,1)],0)
        self.assertEqual(embed.description.count('```'),2)
        self.assertNotIn('‮',embed.description)
        self.assertIn('Éowyn 玩家',embed.description)
        self.assertEqual(len(embed.description.splitlines()),5)
