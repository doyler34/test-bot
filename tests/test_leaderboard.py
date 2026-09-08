from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid
import discord
from account_links import AccountLinks
from combat_store import migrate
from leaderboard_command import LeaderboardCommand, LeaderboardView, leaderboard_embed, standings
from rank_command import RankCommand


def interaction(user=10, guild=1):
    return SimpleNamespace(user=SimpleNamespace(id=user), guild_id=guild,
        guild=SimpleNamespace(get_member=Mock(return_value=None)),
        app_permissions=SimpleNamespace(embed_links=True),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock(), is_done=Mock(return_value=False)),
        followup=SimpleNamespace(send=AsyncMock()))


def players(count):
    return [(f'Player {i}', count-i, i) for i in range(count)]


class LeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.links=AccountLinks(Path(self.tmp.name)/'links.db')
        migrate(self.links.db)
        self.bot=discord.Client(intents=discord.Intents.default())
        self.bot.config=SimpleNamespace(guild_id=1)
        self.bot.account_links=self.links
        self.bot.rank_command=RankCommand(self.bot)
        self.command=LeaderboardCommand(self.bot)

    async def asyncTearDown(self):
        await self.bot.close()
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
        embed=leaderboard_embed([('```\n@everyone\r\n\u202e'+'X'*200,4,3),('Éowyn 玩家',2,1)],0)
        self.assertEqual(embed.description.count('```'),2)
        self.assertNotIn('\u202e',embed.description)
        self.assertIn('Éowyn 玩家',embed.description)
        self.assertEqual(len(embed.description.splitlines()),5)

    async def test_navigation_boundaries_snapshot_and_owner(self):
        rows=players(31)
        view=LeaderboardView(10,rows)
        rows.clear()
        try:
            self.assertFalse(await view.interaction_check(interaction(user=11)))
            self.assertTrue(await view.interaction_check(interaction()))
            with patch('leaderboard_command.time.monotonic',side_effect=[0,2,4,6,8,10]):
                for step,expected in [(1,1),(1,2),(1,2),(-1,1),(-1,0),(-1,0)]:
                    await view.change_page(interaction(),step)
                    self.assertEqual(view.page,expected)
                    self.assertEqual(view.previous.disabled,expected==0)
                    self.assertEqual(view.next.disabled,expected==2)
        finally:
            view.stop()

    async def test_rapid_click(self):
        view=LeaderboardView(10,players(31))
        try:
            i=interaction()
            with patch('leaderboard_command.time.monotonic',side_effect=[0,0.1]):
                await view.change_page(i,1)
                await view.change_page(i,1)
            self.assertEqual(view.page,1)
            i.response.defer.assert_awaited_once()
        finally:
            view.stop()

    async def test_failed_edit_rolls_back_and_timeout_disables(self):
        view=LeaderboardView(10,players(16))
        try:
            i=interaction()
            i.response.edit_message.side_effect=RuntimeError('temporary')
            with self.assertRaises(RuntimeError):
                await view.change_page(i,1)
            self.assertEqual(view.page,0)
            self.assertTrue(view.previous.disabled)
            view.message=SimpleNamespace(edit=AsyncMock())
            await view.on_timeout()
            self.assertTrue(all(button.disabled for button in view.children))
            view.message.edit.assert_awaited_once()
        finally:
            view.stop()

    async def test_command_registration_empty_and_permissions(self):
        self.assertIsNotNone(self.bot.rank_command.tree.get_command('leaderboard',guild=discord.Object(id=1)))
        i=interaction(guild=2)
        await self.command.show(i)
        i.followup.send.assert_not_awaited()
        i=interaction()
        i.app_permissions.embed_links=False
        await self.command.show(i)
        i.followup.send.assert_not_awaited()
        i=interaction()
        await self.command.show(i)
        sent=i.followup.send.await_args.kwargs
        self.assertNotIn('view',sent)  # Webhook.send rejects view=None.
        self.assertIn('No linked players',sent['embed'].description)

    async def test_command_fifteen_and_sixteen_players(self):
        for member in range(1,16):
            self.add(member,member,1,name=f'Game {member}')
        i=interaction()
        await self.command.show(i)
        self.assertNotIn('view',i.followup.send.await_args.kwargs)
        self.add(16,16,1,name='Latest Name')
        await self.command.show(i)
        view=i.followup.send.await_args.kwargs['view']
        try:
            self.assertEqual(view.rows[0],('Latest Name',16,1))
            self.assertIs(view.message,i.followup.send.return_value)
            self.assertFalse(i.followup.send.await_args.kwargs['allowed_mentions'].everyone)
        finally:
            view.stop()
