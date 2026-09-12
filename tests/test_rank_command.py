import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

import discord
from bot.storage.account_links import AccountLinks
from bot.discord.rank_command import RankCommand


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name)/'links.db')
        self.bot = discord.Client(intents=discord.Intents.default())
        self.bot.config = SimpleNamespace(guild_id=1)
        self.bot.account_links = self.links
        self.bot.rank_sync = SimpleNamespace(progress=Mock(return_value=347))
        self.command = RankCommand(self.bot)
        self.avatar = Mock()
        self.avatar.replace.return_value.read = AsyncMock(return_value=b'bad avatar')
        self.interaction = SimpleNamespace(guild_id=1,
            user=SimpleNamespace(id=2,display_name='GARETH',display_avatar=self.avatar),
            app_permissions=SimpleNamespace(attach_files=True),
            response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock(),is_done=Mock(return_value=False)),
            followup=SimpleNamespace(send=AsyncMock()))

    async def asyncTearDown(self):
        await self.bot.close()
        self.links.close()
        self.tmp.cleanup()

    def link(self):
        self.links.verified_link(1,2,'11111111-2222-3333-4444-555555555555','admin:3')

    async def test_guild_registration_and_command_contract(self):
        self.assertEqual(self.command.tree.get_command('rank',guild=discord.Object(id=1)).name,'rank')
        self.assertEqual(len(self.command.command.checks),1)
        with patch.object(self.command.tree,'sync',new=AsyncMock()) as sync:
            await self.command.register()
            self.assertEqual(sync.await_args.kwargs['guild'].id,1)

    async def test_full_bot_startup_hook_registers_and_closes_cleanly(self):
        import main  # Verify the production entry point and its imports too.
        from bot.discord.server_notifications import NotificationBot
        from bot.notification_config import NotificationConfig
        bot = NotificationBot(NotificationConfig('not-a-real-token',1,(),str(Path(self.tmp.name)/'notification.db')))
        try:
            with patch.object(bot.rank_command.tree,'sync',new=AsyncMock()) as sync:
                await bot.setup_hook()
                await bot._jobs[0]
                sync.assert_awaited_once()
        finally:
            await bot.close()
            bot.store.close()
            bot.account_links.close()

    async def test_unlinked_uses_existing_workflow(self):
        await self.command.show(self.interaction)
        self.assertIn('#join-oyb',self.interaction.response.send_message.await_args.args[0])
        self.interaction.response.defer.assert_not_awaited()
        self.bot.rank_sync.progress.assert_not_called()

    async def test_linked_response_is_png_and_avatar_timeout_is_safe(self):
        self.link()
        self.avatar.replace.return_value.read.side_effect=TimeoutError()
        sent = SimpleNamespace(delete=AsyncMock())
        async def receive(**kwargs):
            self.assertEqual(kwargs['file'].filename,'oyb-rank.png')
            self.assertTrue(kwargs['file'].fp.read().startswith(b'\x89PNG'))
            self.assertEqual(kwargs['allowed_mentions'].to_dict(),{'parse':[]})
            return sent
        self.interaction.followup.send.side_effect=receive
        await self.command.show(self.interaction)
        self.interaction.response.defer.assert_awaited_once()
        self.interaction.followup.send.assert_awaited_once()
        self.avatar.replace.assert_called_once_with(format='png',size=256)
        sent.delete.assert_awaited_once()  # public card is scheduled to auto-clear
        self.assertEqual(sent.delete.await_args.kwargs['delay'],300)

    async def test_wrong_guild_and_missing_permission(self):
        self.interaction.guild_id=99
        await self.command.show(self.interaction)
        self.assertIn('OYB Discord',self.interaction.response.send_message.await_args.args[0])
        self.interaction.guild_id=1
        self.link()
        self.interaction.app_permissions.attach_files=False
        await self.command.show(self.interaction)
        self.assertIn('Attach Files',self.interaction.response.send_message.await_args.args[0])

    async def test_renderer_failure_has_useful_response(self):
        self.link()
        with patch('bot.discord.rank_command.render_card',side_effect=RuntimeError('render failed')):
            await self.command.show(self.interaction)
        self.assertIn('try again',self.interaction.followup.send.await_args.args[0])
