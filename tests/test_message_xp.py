from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from account_links import AccountLinks
from rank_persistence import XPStore
from message_xp import award_message
from server_notifications import NotificationBot

IDENTITY = '11111111-2222-3333-4444-555555555555'


class MessageXPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'links.db'
        self.links=AccountLinks(self.path)
        self.links.verified_link(1,10,IDENTITY,'admin:22')
        self.wallet=XPStore(self.links.db)
        self.created=time.time()+10
        self.bot=SimpleNamespace(config=SimpleNamespace(guild_id=1), rank_sync=SimpleNamespace(wallet=self.wallet))

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    def message(self, id=100, guild=1, user=10, bot=False, webhook=None, type=discord.MessageType.default):
        # No content/attachments properties: awarding must not require content intent.
        return SimpleNamespace(id=id, guild=SimpleNamespace(id=guild) if guild else None,
            author=SimpleNamespace(id=user,bot=bot), webhook_id=webhook,type=type,
            created_at=datetime.fromtimestamp(self.created,timezone.utc))

    async def test_handler_default_reply_attachment_and_duplicate(self):
        await NotificationBot.on_message(self.bot,self.message())
        await award_message(self.bot,self.message())
        await award_message(self.bot,self.message(101,type=discord.MessageType.reply))
        attachment=self.message(102)
        attachment.attachments=[object()]
        await award_message(self.bot,attachment)
        self.assertEqual(self.wallet.cached(1,10),3)
        self.assertEqual(self.links.db.execute('SELECT COUNT(*) FROM discord_post_events').fetchone()[0],3)

    async def test_ignored_sources_and_unlinked(self):
        for args in [dict(bot=True),dict(webhook=22),dict(guild=None),dict(guild=2),dict(user=11),
                     dict(type=discord.MessageType.pins_add),dict(type=discord.MessageType.chat_input_command)]:
            await award_message(self.bot,self.message(**args))
        self.assertEqual(self.wallet.cached(1,10),0)
        self.assertEqual(self.links.db.execute('SELECT COUNT(*) FROM discord_post_events').fetchone()[0],0)

    async def test_old_preapproval_message_not_replayed_for_credit(self):
        self.assertEqual(self.wallet.award_post(1,10,100,0),0)
        self.assertEqual(self.wallet.post_xp(1,10),0)

    async def test_restart_dedup_and_changed_future_rate(self):
        await award_message(self.bot,self.message())
        self.links.close()
        self.links=AccountLinks(self.path)
        self.wallet=XPStore(self.links.db)
        self.assertEqual(self.wallet.award_post(1,10,100,self.created),0)
        with patch('rank_persistence.XP_PER_POST',5):
            self.assertEqual(self.wallet.award_post(1,10,101,self.created),5)
        self.assertEqual(self.wallet.cached(1,10),6)

    async def test_playtime_credit_partial_minutes_and_posts_combine(self):
        with self.links.db:
            self.links.db.execute('INSERT INTO rank_wallet_v2 VALUES (1,10,?,80,0,599999)',(IDENTITY,))
        self.wallet.award_post(1,10,100,self.created)
        self.assertEqual(self.wallet.read(1,10,IDENTITY,'unused',False),81)
        source=Path(self.tmp.name)/'playtime.db'
        with closing(sqlite3.connect(source)) as db:
            db.execute('CREATE TABLE global_time(identity,milliseconds)')
            db.execute('INSERT INTO global_time VALUES (?,600000)',(IDENTITY,))
            db.commit()
        self.assertEqual(self.wallet.read(1,10,IDENTITY,source,True),82)
        with closing(sqlite3.connect(source)) as db:
            db.execute('UPDATE global_time SET milliseconds=0')
            db.commit()
        self.assertEqual(self.wallet.read(1,10,IDENTITY,source,True),82)
        self.assertEqual(self.wallet.cached(1,10),82)

    async def test_transaction_rolls_back_then_retry_counts_once(self):
        self.links.db.execute("CREATE TRIGGER fail_post BEFORE INSERT ON discord_post_totals BEGIN SELECT RAISE(ABORT,'temporary'); END")
        with self.assertRaises(sqlite3.Error):
            self.wallet.award_post(1,10,100,self.created)
        self.assertEqual(self.links.db.execute('SELECT COUNT(*) FROM discord_post_events').fetchone()[0],0)
        self.links.db.execute('DROP TRIGGER fail_post')
        self.wallet.award_post(1,10,100,self.created)
        self.assertEqual(self.wallet.cached(1,10),1)

    async def test_bounded_retries_and_no_duplicate_after_uncertain_success(self):
        real=self.wallet.award_post
        first=True
        def uncertain(*args):
            nonlocal first
            result=real(*args)
            if first:
                first=False
                raise sqlite3.OperationalError('uncertain acknowledgement')
            return result
        with patch.object(self.wallet,'award_post',side_effect=uncertain), patch('message_xp.asyncio.sleep',new_callable=AsyncMock):
            await award_message(self.bot,self.message())
        self.assertEqual(self.wallet.cached(1,10),1)
        with patch.object(self.wallet,'award_post',side_effect=sqlite3.OperationalError('locked')) as award, patch('message_xp.asyncio.sleep',new_callable=AsyncMock):
            await award_message(self.bot,self.message(101))
        self.assertEqual(award.call_count,3)

    async def test_migration_backs_up_existing_xp_without_changes(self):
        with self.links.db:
            self.links.db.execute('DROP TABLE discord_post_events')
            self.links.db.execute('DROP TABLE discord_post_totals')
            self.links.db.execute('INSERT INTO rank_wallet_v2 VALUES (1,10,?,50,0,1200000)',(IDENTITY,))
        backup=Path(str(self.path)+'.before-discord-post-xp-v1.sqlite3')
        backup.unlink()  # Remove this test's initial empty migration backup.
        wallet=XPStore(self.links.db)
        XPStore(self.links.db)
        self.assertEqual(wallet.cached(1,10),52)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('SELECT credit,milliseconds FROM rank_wallet_v2').fetchone(),(50,1200000))
        self.assertEqual(self.links.lookup(1,10),IDENTITY)
