from pathlib import Path
import tempfile
import unittest
import uuid
from bot.storage.account_links import AccountLinks
from bot.storage.combat_store import migrate
from bot.discord.leaderboard_command import leaderboard_embed, standings


def players(count):
    return [(f'Player {i}', count-i, i) for i in range(count)]


class LeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.links=AccountLinks(Path(self.tmp.name)/'links.db')
        migrate(self.links.db)

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def add(self, member, kills=0, deaths=0, guild=1, name='Player', combat=True):
        identity=str(uuid.UUID(int=member))
        token=self.links.submit(guild,member,identity,name)
        self.links.review(guild,token,999,True)
        if combat:
            with self.links.db:
                self.links.db.execute('INSERT INTO combat_totals VALUES (?,?,?,?,?)',(identity,kills,deaths,0,'now'))

    async def test_sort_scope_and_read_only(self):
        for args in [(12,10,2),(11,10,2),(13,10,1),(14,11,9)]:
            self.add(*args)
        self.add(15,999,guild=2)
        self.add(16,combat=False)
        with self.links.db:
            self.links.db.execute("INSERT INTO combat_totals VALUES ('unlinked',999,0,0,'now')")
        before=list(self.links.db.iterdump())
        self.assertEqual([row[0] for row in standings(self.links.db,1)],[14,13,11,12])
        self.assertEqual(before,list(self.links.db.iterdump()))

    async def test_page_sizes_and_numbering(self):
        for count,pages in [(0,1),(1,1),(15,1),(16,2),(30,2),(31,3)]:
            for page in range(pages):
                embed=leaderboard_embed(players(count),page)
                self.assertIn(f'Page {page+1}/{pages}',embed.footer.text)
                if count:
                    lines=embed.description.splitlines()[2:-1]
                    self.assertEqual(len(lines),min(15,count-page*15))
                    self.assertEqual(int(lines[0].split()[0]),page*15+1)
                    self.assertLess(len(embed.description),4096)
                else:
                    self.assertIn('No linked players',embed.description)

    async def test_names_cannot_escape_table(self):
        embed=leaderboard_embed([('```\n@everyone\r\n‮'+'X'*200,4,3),('Éowyn 玩家',2,1)],0)
        self.assertEqual(embed.description.count('```'),2)
        self.assertNotIn('‮',embed.description)
        self.assertIn('Éowyn 玩家',embed.description)
        self.assertEqual(len(embed.description.splitlines()),5)
