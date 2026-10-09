import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.discord import feedback, welcome_doc


class Channel(discord.TextChannel):
    def __init__(self, channel_id):
        self.id, self.name, self.sent = channel_id, "staff-feedback", []

    async def send(self, content=None, embed=None, view=None, allowed_mentions=None):
        self.sent.append(SimpleNamespace(content=content, embed=embed, view=view))


def member(member_id, dms=True):
    send = AsyncMock() if dms else AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=403, reason=""), ""))
    return SimpleNamespace(id=member_id, display_name=f"Player{member_id}", mention=f"<@{member_id}>",
                           display_avatar=SimpleNamespace(url="https://cdn/a.png"), send=send,
                           __str__=lambda self: "player")


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = Channel(700)
        self.rook = member(7)
        self.admin = SimpleNamespace(id=1, name="Admin", mention="<@&1>")
        self.guild = SimpleNamespace(name="OYB", roles=[self.admin],
                                     get_channel=lambda i: self.channel if i == 700 else None,
                                     get_member=lambda i: self.rook if i == 7 else None)
        feedback.SETTINGS.clear()
        feedback._sent.clear()
        patcher = patch.dict(os.environ, {"FEEDBACK_CHANNEL_ID": "700"})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_feedback_lands_in_the_staff_channel_with_who_sent_it(self):
        feedback.SETTINGS.update(ping_role="Admin")
        said = await feedback.submit(self.guild, self.rook, "Server 2", "Spawns are too far out")
        self.assertEqual(said, welcome_doc.FEEDBACK_THANKS)
        post = self.channel.sent[0]
        self.assertEqual(post.embed.title, "💬 Server 2")
        self.assertIn("<@7>", post.embed.fields[0].value)
        self.assertEqual(post.content, "<@&1>")
        self.assertEqual([b.custom_id for b in post.view.children], ["oyb:fb:reply:7", "oyb:fb:done:7"])
        self.assertGreater(feedback.wait_left(7), 0)

    async def test_no_channel_means_nothing_is_lost_silently(self):
        with patch.dict(os.environ, {"FEEDBACK_CHANNEL_ID": ""}):
            said = await feedback.submit(self.guild, self.rook, "x", "y")
        self.assertIn("isn't set up", said)
        self.assertEqual(self.channel.sent, [])

    async def test_done_thanks_them_and_reply_goes_by_dm(self):
        await feedback.submit(self.guild, self.rook, "Server 2", "Spawns")
        embed = self.channel.sent[0].embed
        message = SimpleNamespace(embeds=[embed], edit=AsyncMock())
        staff = SimpleNamespace(display_name="Gaz", mention="<@2>")
        said = await feedback.reply(SimpleNamespace(guild=self.guild, user=staff), 7, message, "Moved them closer")
        self.assertEqual(said, "Sent to them by DM.")
        self.assertEqual(self.rook.send.await_args.kwargs["embed"].description, "Moved them closer")
        self.assertEqual(embed.fields[-1].name, "Reply from Gaz")
        edited = {}

        async def edit_message(**kwargs):
            edited.update(kwargs)
        interaction = SimpleNamespace(guild=self.guild, user=staff, message=message,
                                      response=SimpleNamespace(edit_message=edit_message))
        await feedback.mark_done(interaction, 7)
        self.assertIn("**Server 2**", self.rook.send.await_args.kwargs["content"])
        self.assertIsNone(edited["view"])
        self.assertEqual(edited["embed"].colour.value, feedback.DONE)
        self.assertIn("thanked them by DM", edited["embed"].fields[-1].value)

    async def test_closed_dms_are_noted(self):
        self.rook = member(7, dms=False)
        message = SimpleNamespace(embeds=[discord.Embed(title="💬 x")], edit=AsyncMock())
        said = await feedback.reply(SimpleNamespace(guild=self.guild, user=SimpleNamespace(display_name="Gaz")),
                                    7, message, "hi")
        self.assertIn("Couldn't DM", said)
        self.assertIn("not delivered", message.embeds[0].fields[0].name)

    def test_feedback_button_on_a_post(self):
        doc, problems = welcome_doc.check_post({"channel_id": "123456789012345678", "title": "Feedback",
                                                "sections": [{"heading": "", "text": "Tell us"}],
                                                "buttons": [{"type": "feedback", "label": "Give feedback"}]})
        self.assertEqual(problems, [])
        self.assertEqual(doc["buttons"][0]["type"], "feedback")
        doc, problems = welcome_doc.check_feedback({"on": False, "thanks": "", "done_dm": "", "ping_role": " Admin "})
        self.assertEqual(doc, {"on": False, "thanks": welcome_doc.FEEDBACK_THANKS, "done_dm": "", "ping_role": "Admin"})


if __name__ == "__main__":
    unittest.main()
