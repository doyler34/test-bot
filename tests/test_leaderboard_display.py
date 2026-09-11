import asyncio
import copy
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from bot.discord.leaderboard_command import leaderboard_embed
from bot.discord.leaderboard_display import (LeaderboardDisplay, CHANNEL_NAME, MARKER, overwrites,
                                 retry_delay, signature)
from bot.storage.notification_store import NotificationStore


def missing():
    return discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'gone')


class Target:
    def __init__(self, id, **permissions):
        self.id = id
        self.permissions = discord.Permissions(**permissions)


class Message:
    def __init__(self, channel, id, embed, author=99, pinned=False, content=''):
        self.channel, self.id = channel, id
        self.embeds, self.components = [embed], []
        self.author = SimpleNamespace(id=author)
        self.pinned, self.content = pinned, content
        self.type = discord.MessageType.default
        self.reference = None
        self.reactions = []
        self.edits = 0
        self.fail_edit = None

    def controls(self, view):
        self.components = [SimpleNamespace(to_dict=lambda data=copy.deepcopy(c): data)
                           for c in view.to_components()]

    async def edit(self, **kwargs):
        if self.fail_edit:
            raise self.fail_edit
        self.edits += 1
        if 'embed' in kwargs:
            self.embeds = [kwargs['embed']]
        if 'view' in kwargs:
            self.controls(kwargs['view'])
        self.content = kwargs.get('content', self.content) or ''
        return self

    async def pin(self, **kwargs):
        self.pinned = True

    async def delete(self):
        self.channel.messages.remove(self)

    async def clear_reactions(self):
        self.reactions.clear()


class Channel:
    def __init__(self, guild, id, name=CHANNEL_NAME, topic=MARKER, overwrites=None):
        self.guild, self.id, self.name, self.topic = guild, id, name, topic
        self.overwrites = overwrites or {}
        self.messages = []
        self.sends = 0
        self.edits = 0
        self.scans = 0

    async def edit(self, **kwargs):
        self.overwrites = kwargs['overwrites']
        self.edits += 1
        return self

    async def send(self, **kwargs):
        self.sends += 1
        m = Message(self, self.guild.next_id(), kwargs['embed'])
        m.controls(kwargs['view'])
        self.messages.append(m)
        return m

    async def fetch_message(self, id):
        for m in self.messages:
            if m.id == id:
                return m
        raise missing()

    async def history(self, **kwargs):
        self.scans += 1
        for m in list(reversed(self.messages)):
            yield m


class Guild:
    def __init__(self):
        self.id = 1
        self.me = Target(99, administrator=True)
        self.default_role = Target(1)
        self.channels = []
        self.serial = 100
        self.creates = 0

    def next_id(self):
        self.serial += 1
        return self.serial

    def get_member(self, id):
        return None

    def get_channel(self, id):
        return next((c for c in self.channels if c.id == id), None)

    async def fetch_channels(self):
        self.fetches = getattr(self, 'fetches', 0) + 1
        return list(self.channels)

    async def create_text_channel(self, name, **kwargs):
        self.creates += 1
        c = Channel(self, self.next_id(), name, kwargs['topic'], kwargs['overwrites'])
        self.channels.append(c)
        return c


def interaction(guild, message, user=10):
    return SimpleNamespace(guild_id=guild.id, guild=guild, message=message,
        user=SimpleNamespace(id=user), response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class DisplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'notifications.db'
        self.store = NotificationStore(self.path)
        self.guild = Guild()
        self.bot = SimpleNamespace(store=self.store, config=SimpleNamespace(guild_id=1),
            user=SimpleNamespace(id=99), add_view=Mock(), get_guild=lambda id:self.guild)
        self.now = 1000
        self.rows = [(f'Player {i}',100-i,i) for i in range(31)]
        self.clock = patch('bot.discord.leaderboard_display.time.time', side_effect=lambda:self.now)
        self.clock.start()
        self.channels = patch('bot.discord.leaderboard_display.discord.TextChannel', Channel)
        self.channels.start()
        self.displays = []
        self.display = self.new_display()

    def new_display(self):
        d = LeaderboardDisplay(self.bot)
        d.rows = Mock(side_effect=lambda guild:list(self.rows))
        d.register()
        self.displays.append(d)
        return d

    async def asyncTearDown(self):
        for d in self.displays:
            d.view.stop()
        self.clock.stop()
        self.channels.stop()
        self.store.close()
        self.tmp.cleanup()

    async def initial(self):
        await self.display.tick()
        return self.guild.channels[0].messages[0]

    async def test_create_pinned_persistent_and_restart_reuse(self):
        message = await self.initial()
        self.assertTrue(message.pinned)
        self.assertTrue(self.display.view.is_persistent())
        self.display.register()
        self.bot.add_view.assert_called_once()
        saved = self.store.leaderboard(1)
        self.assertEqual((saved['channel'], saved['message']), (message.channel.id, message.id))
        self.store.close()
        self.store = NotificationStore(self.path)
        self.bot.store = self.store
        restart = self.new_display()
        await restart.tick()
        self.assertEqual(self.guild.creates, 1)
        self.assertEqual(message.channel.sends, 1)
        self.assertEqual(message.edits, 0)
        self.assertEqual(restart.message_id, message.id)

    async def test_shared_buttons_keep_page_after_refresh_and_restart(self):
        message = await self.initial()
        i = interaction(self.guild,message,user=555)
        self.assertTrue(await self.display.view.interaction_check(i))
        await self.display.view.next.callback(i)
        self.assertEqual(self.store.leaderboard(1)['page'], 1)
        self.assertEqual(message.channel.sends, 1)
        self.assertEqual(message.edits, 1)
        restart = self.new_display()
        self.now += 300
        await restart.tick()
        self.assertEqual(restart.view.indicator.label, 'Page 2 / 3')
        self.rows = self.rows[:16]
        self.now += 300
        await restart.tick()
        self.assertEqual(self.store.leaderboard(1)['page'], 1)
        self.assertTrue(restart.view.next.disabled)
        self.rows = self.rows[:1]
        self.now += 300
        await restart.tick()
        self.assertEqual(self.store.leaderboard(1)['page'], 0)
        self.assertTrue(restart.view.previous.disabled)

    async def test_debounce_changes_unchanged_skip_and_reconcile(self):
        message = await self.initial()
        for seconds, kills in [(15,201),(30,202),(44,203)]:
            self.now = 1000 + seconds
            self.rows[0] = ('Player 0',kills,0)
            await self.display.tick()
            self.assertEqual(message.edits,0)
        self.now = 1045
        await self.display.tick()
        self.assertEqual(message.edits,1)
        self.assertIn('203',message.embeds[0].description)
        self.now = 1300
        await self.display.tick()
        self.assertEqual(message.edits,1)
        self.assertEqual(message.channel.sends,1)

    async def test_reconcile_without_change_skips_scan_edit_and_channel_list(self):
        message = await self.initial()
        channel = message.channel
        scans_before, fetches_before = channel.scans, getattr(self.guild, 'fetches', 0)
        self.now += 300  # force a routine reconcile
        await self.display.tick()
        self.assertEqual(message.edits, 0)             # unchanged -> no Discord edit
        self.assertEqual(channel.scans, scans_before)  # recovery-only full history scan
        self.assertEqual(getattr(self.guild, 'fetches', 0), fetches_before)  # cached channel, no REST list

    async def test_missing_message_and_channel_recovery(self):
        message = await self.initial()
        await message.delete()
        self.now += 300
        await self.display.tick()
        replacement = self.guild.channels[0].messages[0]
        self.assertNotEqual(message.id,replacement.id)
        self.assertTrue(replacement.pinned)
        self.guild.channels.clear()
        self.now += 300
        await self.display.tick()
        self.assertEqual(len(self.guild.channels),1)
        self.assertEqual(len(self.guild.channels[0].messages),1)
        self.assertEqual(self.store.leaderboard(1)['message'],self.guild.channels[0].messages[0].id)

    async def test_existing_normalized_channel_and_pinned_adoption_cleanup_safe(self):
        c = Channel(self.guild,500,name=CHANNEL_NAME.lower(),topic=None)
        self.guild.channels.append(c)
        old = Message(c,600,leaderboard_embed(self.rows,0))
        pinned = Message(c,601,leaderboard_embed(self.rows,1),pinned=True)
        human = Message(c,602,leaderboard_embed(self.rows,0),author=5)
        unrelated = Message(c,603,discord.Embed(title='Other bot information'))
        notice = Message(c,604,discord.Embed())
        notice.type = discord.MessageType.pins_add
        notice.reference = SimpleNamespace(message_id=pinned.id)
        c.messages = [old,pinned,human,unrelated,notice]
        await self.display.tick()
        self.assertEqual(self.guild.creates,0)
        self.assertEqual(self.display.message_id,601)
        self.assertEqual([m.id for m in c.messages],[601,602,603])

    async def test_unpinned_orphan_after_failed_save_does_not_duplicate(self):
        c = Channel(self.guild,500)
        self.guild.channels.append(c)
        c.messages.append(Message(c,600,leaderboard_embed(self.rows,0)))
        await self.display.tick()
        self.assertEqual(c.sends,0)
        self.assertTrue(c.messages[0].pinned)

    async def test_rate_limit_persisted_honoured_after_restart(self):
        message = await self.initial()
        error = discord.HTTPException(SimpleNamespace(status=429,reason='rate limit',headers={'Retry-After':'120'}), 'retry')
        message.fail_edit = error
        self.rows[0] = ('Changed',100,0)
        self.now = 1300
        await self.display.tick()
        self.assertEqual(self.store.leaderboard(1)['retry_at'],1420)
        restart = self.new_display()
        await restart.tick()
        restart.rows.assert_not_called()
        self.now = 1420
        message.fail_edit = None
        await restart.tick()
        self.assertEqual(message.edits,1)
        self.assertEqual(self.store.leaderboard(1)['retry_at'],0)

    async def test_database_failure_keeps_display_then_recovers(self):
        message = await self.initial()
        self.display.rows.side_effect = sqlite3.OperationalError('locked')
        self.now += 15
        await self.display.tick()
        self.assertEqual(message.edits,0)
        self.assertEqual(len(message.channel.messages),1)
        self.display.rows.side_effect = lambda guild:list(self.rows)
        self.now += 30
        await self.display.tick()
        self.assertEqual(self.display.failures,0)
        self.assertEqual(message.edits,0)

    async def test_busy_and_rapid_buttons_do_not_queue_edits(self):
        message = await self.initial()
        i = interaction(self.guild,message)
        async with self.display.lock:
            await self.display.turn_page(i,1)
        self.assertEqual(message.edits,0)
        await self.display.turn_page(i,1)
        await self.display.turn_page(i,1)
        self.assertEqual(message.edits,1)
        self.assertEqual(self.store.leaderboard(1)['page'],1)

    async def test_unchanged_after_permission_reconciliation_and_empty_data(self):
        self.rows=[]
        message=await self.initial()
        self.assertTrue(self.display.view.previous.disabled)
        self.assertTrue(self.display.view.next.disabled)
        self.now += 300
        await self.display.tick()
        self.assertEqual(message.edits,0)
        self.assertEqual(message.channel.edits,0)

    async def test_real_database_updates_and_xp_untouched(self):
        from bot.storage.account_links import AccountLinks
        from bot.storage.combat_store import migrate
        links=AccountLinks(Path(self.tmp.name)/'links.db')
        try:
            migrate(links.db)
            identity='11111111-2222-3333-4444-555555555555'
            token=links.submit(1,10,identity,'Test Player')
            links.review(1,token,99,True)
            with links.db:
                links.db.execute('CREATE TABLE rank_wallet_v2 (credit, milliseconds)')
                links.db.execute('INSERT INTO rank_wallet_v2 VALUES (123,456)')
                links.db.execute('INSERT INTO combat_totals VALUES (?,1,2,0,?)',(identity,'now'))
            self.bot.account_links=links
            self.display.rows=LeaderboardDisplay.rows.__get__(self.display)
            message=await self.initial()
            with links.db:
                links.db.execute('UPDATE combat_totals SET player_kills=7 WHERE identity=?',(identity,))
            self.now+=15
            await self.display.tick()
            self.now+=30
            await self.display.tick()
            self.assertEqual(message.edits,1)
            self.assertIn('Test Player',message.embeds[0].description)
            self.assertRegex(message.embeds[0].description,r'Test Player\s+7\s+2')
            self.assertEqual(links.db.execute('SELECT * FROM rank_wallet_v2').fetchone(),(123,456))
            self.assertEqual(links.db.execute('SELECT player_kills,deaths FROM combat_totals').fetchone(),(7,2))
        finally:
            links.close()

    async def test_state_write_failure_after_send_adopts_same_message(self):
        real_save=self.store.save_leaderboard
        once=True
        def save(state):
            nonlocal once
            if state['message'] and once:
                once=False
                raise sqlite3.OperationalError('interrupted state write')
            real_save(state)
        with patch.object(self.store,'save_leaderboard',side_effect=save):
            await self.display.tick()
        channel=self.guild.channels[0]
        self.assertEqual(channel.sends,1)
        self.now+=30
        await self.new_display().tick()
        self.assertEqual(channel.sends,1)
        self.assertTrue(channel.messages[0].pinned)

    async def test_restart_buttons_and_reactions_are_reconciled(self):
        message=await self.initial()
        message.reactions=['old reaction']
        restart=self.new_display()
        await restart.tick()
        self.assertEqual(message.reactions,[])
        i=interaction(self.guild,message,user=77)
        self.assertTrue(await restart.view.interaction_check(i))
        await restart.view.next.callback(i)
        self.assertEqual(self.store.leaderboard(1)['page'],1)
        self.assertEqual(len(message.channel.messages),1)


class PermissionTests(unittest.TestCase):
    def test_regular_role_and_member_overrides_denied_mods_preserved(self):
        guild=Guild()
        role=Target(2)
        member=Target(3)
        moderator=Target(4,manage_messages=True)
        existing={role:discord.PermissionOverwrite(send_messages=True),
                  member:discord.PermissionOverwrite(create_public_threads=True),
                  moderator:discord.PermissionOverwrite(send_messages=True,view_channel=True)}
        actual=overwrites(guild,existing)
        for target in [guild.default_role,role,member]:
            for flag in ['send_messages','create_public_threads','create_private_threads',
                         'send_messages_in_threads','add_reactions','use_external_stickers','use_external_apps']:
                self.assertIs(getattr(actual[target],flag),False)
            self.assertTrue(actual[target].view_channel)
            self.assertTrue(actual[target].read_message_history)
        self.assertEqual(actual[moderator],existing[moderator])
        self.assertTrue(actual[guild.me].pin_messages)
        self.assertTrue(actual[guild.me].manage_messages)
        self.assertTrue(existing[role].send_messages)

    def test_retry_sources_backoff_and_invalid_values(self):
        self.assertEqual(retry_delay(discord.RateLimited(123),1),123)
        self.assertEqual(retry_delay(RuntimeError(),2),60)
        self.assertEqual(retry_delay(RuntimeError(),100),900)
        e=RuntimeError()
        e.text='{"retry_after":1800}'
        self.assertEqual(retry_delay(e,1),1800)
        e.retry_after=float('nan')
        e.text='invalid'
        self.assertEqual(retry_delay(e,1),30)
