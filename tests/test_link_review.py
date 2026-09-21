import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from bot.storage.account_links import AccountLinks
import bot.discord.link_review as link_review
from bot.discord.link_review import (AdminPanelView, ReviewButtons, TOKEN_PREFIX,
                                     HANDLED_MARKER, CONTROL_MARKER, post_request_alert, prune_handled)

IDENT = "11111111-2222-3333-4444-555555555555"


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=1, **kwargs)


class Role:
    def __init__(self, id):
        self.id = id
        self.mention = f"<@&{id}>"


class Guild:
    def __init__(self, id, channel=None, role=None):
        self.id, self._channel, self._role = id, channel, role

    def get_channel(self, id):
        return self._channel

    def get_role(self, id):
        return self._role


def interaction(*, admin=True, user=99, embeds=None, guild=None):
    return SimpleNamespace(
        guild_id=1, guild=guild,
        user=SimpleNamespace(id=user),
        permissions=discord.Permissions(manage_guild=True) if admin else discord.Permissions.none(),
        message=SimpleNamespace(embeds=embeds or [], edit=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(),
                                 defer=AsyncMock(), is_done=lambda: False))


def alert_embed(token):
    embed = discord.Embed(description="x")
    embed.set_footer(text=TOKEN_PREFIX + token)
    return embed


class FakeMsg:
    def __init__(self, id, author_id, footer):
        self.id = id
        self.author = SimpleNamespace(id=author_id)
        e = discord.Embed(description="x")
        e.set_footer(text=footer)
        self.embeds = [e]
        self.deleted = False

    async def delete(self):
        self.deleted = True


class HistoryChannel:
    def __init__(self, messages):
        self.messages = messages

    async def history(self, limit=None, before=None):
        for m in self.messages:
            yield m


class PruneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / "l.db")
        self.links.save_review_settings(1, channel=42, control=7)
        self.bot = SimpleNamespace(account_links=self.links, user=SimpleNamespace(id=500))

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    async def test_removes_handled_keeps_pending_control_and_others(self):
        handled = FakeMsg(1, 500, HANDLED_MARKER)
        pending = FakeMsg(2, 500, TOKEN_PREFIX + "abc")
        control = FakeMsg(7, 500, CONTROL_MARKER)
        foreign = FakeMsg(3, 999, HANDLED_MARKER)  # another user's message
        channel = HistoryChannel([handled, pending, control, foreign])
        guild = SimpleNamespace(id=1, get_channel=lambda i: channel)
        with patch.object(link_review.discord, "TextChannel", HistoryChannel):
            removed = await prune_handled(self.bot, guild)
        self.assertEqual(removed, 1)
        self.assertTrue(handled.deleted)
        self.assertFalse(pending.deleted)
        self.assertFalse(control.deleted)
        self.assertFalse(foreign.deleted)


class ReviewSettingsTests(unittest.TestCase):
    def test_partial_update_keeps_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            links = AccountLinks(Path(tmp) / "l.db")
            try:
                links.save_review_settings(1, channel=10, reviewer_role=20)
                links.save_review_settings(1, control=30)  # partial: only control
                cfg = links.review_settings(1)
                self.assertEqual((cfg["channel"], cfg["control"], cfg["reviewer_role"]), (10, 30, 20))
            finally:
                links.close()


class AlertTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / "links.db")
        self.token = self.links.submit(1, 10, IDENT, "Player One", "CoolNickname")
        self.links.save_review_settings(1, channel=42, reviewer_role=555)
        self.channel = FakeChannel()
        self.role = Role(555)
        self.bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1))
        self.tc = patch.object(link_review.discord, "TextChannel", FakeChannel)
        self.tc.start()

    async def asyncTearDown(self):
        self.tc.stop()
        self.links.close()
        self.tmp.cleanup()

    async def test_alert_carries_token_and_pings_reviewer_role(self):
        await post_request_alert(self.bot, Guild(1, self.channel, self.role), self.token)
        self.assertEqual(len(self.channel.sent), 1)
        sent = self.channel.sent[0]
        self.assertEqual(sent["content"], "<@&555>")
        self.assertTrue(sent["embed"].footer.text.endswith(self.token))
        self.assertIn("Player One", sent["embed"].description)
        self.assertIn("CoolNickname", sent["embed"].description)  # Discord name, not just the ID
        self.assertEqual(sent["allowed_mentions"].roles, [self.role])

    async def test_no_channel_configured_is_safe(self):
        # guild 2 has no saved review settings -> nothing is sent, no error.
        await post_request_alert(self.bot, Guild(2, self.channel, self.role), "missing")
        self.assertEqual(len(self.channel.sent), 0)

    async def test_approve_button_links_the_account(self):
        i = interaction(embeds=[alert_embed(self.token)])
        await ReviewButtons(self.bot)._decide(i, True)
        self.assertEqual(self.links.lookup(1, 10), IDENT)
        i.response.edit_message.assert_awaited()

    async def test_reject_button_leaves_unlinked(self):
        i = interaction(embeds=[alert_embed(self.token)])
        await ReviewButtons(self.bot)._decide(i, False)
        self.assertIsNone(self.links.lookup(1, 10))

    async def test_non_admin_cannot_decide(self):
        i = interaction(admin=False, embeds=[alert_embed(self.token)])
        await ReviewButtons(self.bot)._decide(i, True)
        i.response.send_message.assert_awaited()  # denied
        self.assertIsNone(self.links.lookup(1, 10))

    async def test_already_handled_request_disables_buttons(self):
        first = interaction(embeds=[alert_embed(self.token)])
        await ReviewButtons(self.bot)._decide(first, True)  # approve once
        second = interaction(embeds=[alert_embed(self.token)])
        await ReviewButtons(self.bot)._decide(second, True)  # same token again
        second.response.send_message.assert_awaited()  # "already handled" message
        second.message.edit.assert_awaited()  # buttons removed

    async def test_unapprovable_request_is_auto_rejected_and_the_member_told(self):
        # The game account belongs to somebody else, so this request can never
        # be approved. It closes itself rather than sitting in the queue.
        self.links.verified_link(1, 77, IDENT, "admin:1")
        member = SimpleNamespace(id=10, send=AsyncMock())
        guild = SimpleNamespace(id=1, get_member=lambda i: member, get_channel=lambda i: None)
        i = interaction(embeds=[alert_embed(self.token)], guild=guild)
        await ReviewButtons(self.bot)._decide(i, True)
        self.assertEqual(self.links.request(1, self.token)[3], "rejected")
        member.send.assert_awaited_once()
        self.assertIn("already linked to another Discord account", member.send.await_args.args[0])
        edited = i.message.edit.await_args.kwargs["embed"]
        self.assertEqual(edited.footer.text, HANDLED_MARKER)
        self.assertEqual(edited.fields[-1].name, "\U0001F6AB Auto-rejected")

    async def test_closed_dms_fall_back_to_the_join_channel(self):
        self.links.verified_link(1, 77, IDENT, "admin:1")
        member = SimpleNamespace(id=10, send=AsyncMock(side_effect=discord.HTTPException(Mock(), "closed")))
        channel = FakeChannel()
        with self.links.db:
            self.links.db.execute("CREATE TABLE IF NOT EXISTS join_channel (guild INTEGER PRIMARY KEY, channel INTEGER, message INTEGER)")
            self.links.db.execute("INSERT OR REPLACE INTO join_channel VALUES (1, 77, NULL)")
        guild = SimpleNamespace(id=1, get_member=lambda i: member, get_channel=lambda i: channel)
        i = interaction(embeds=[alert_embed(self.token)], guild=guild)
        await ReviewButtons(self.bot)._decide(i, True)
        self.assertIn("<@10>", channel.sent[0]["content"])

    async def test_admin_panel_carries_every_control_and_refuses_members(self):
        view = link_review.AdminPanelView(self.bot)
        self.assertTrue(view.is_persistent())
        ids = {child.custom_id for child in view.children}
        self.assertEqual(ids, {"oyb:admin:pending", "oyb:admin:forcelink",
                               "oyb:admin:unlink", "oyb:linkalerts:toggle"})
        denied = interaction(admin=False)
        await view.pending.callback(denied)
        denied.response.send_message.assert_awaited()

    async def test_panel_embed_reports_the_queue(self):
        self.assertIn("**1** waiting", link_review.admin_panel_embed(self.links, 1).description)
        self.links.review(1, self.token, 99, False)
        self.assertIn("nothing waiting", link_review.admin_panel_embed(self.links, 1).description)

    async def test_toggle_adds_then_removes_reviewer_role(self):
        member = SimpleNamespace(id=99, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        guild = SimpleNamespace(get_role=lambda i: self.role)
        i = SimpleNamespace(guild_id=1, guild=guild, user=member,
                            permissions=discord.Permissions(manage_guild=True),
                            followup=SimpleNamespace(send=AsyncMock()),
                            response=SimpleNamespace(send_message=AsyncMock(),
                                                     defer=AsyncMock(), is_done=lambda: False))
        view = AdminPanelView(self.bot)
        await view.toggle.callback(i)
        member.add_roles.assert_awaited_once()
        member.roles = [self.role]  # now subscribed
        await view.toggle.callback(i)
        member.remove_roles.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
