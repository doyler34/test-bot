import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord import app_commands

from bot.discord.help_command import HelpCommand, HelpView, pages
from bot.discord.rank_command import RankCommand


class PageTests(unittest.TestCase):
    def test_an_overview_then_a_page_for_every_command_in_order(self):
        found = pages({"mortar": "mortar text", "rank": "rank text", "stats": "stats text", "help": "help text"})
        self.assertEqual([e.title for e in found], ["OYB BOT COMMANDS", "/rank", "/stats", "/mortar", "/help"])
        self.assertIn("**/rank**: rank text", found[0].description)
        self.assertIn("Page 3/5", found[2].footer.text)

    def test_a_command_that_is_not_registered_has_no_page(self):
        found = pages({"rank": "r", "help": "h"})
        self.assertEqual([e.title for e in found], ["OYB BOT COMMANDS", "/rank", "/help"])

    def test_a_command_with_no_written_page_still_gets_its_own_description(self):
        found = pages({"rank": "r", "newthing": "Does a new thing"})
        self.assertEqual(found[-1].title, "/newthing")
        self.assertEqual(found[-1].description, "Does a new thing")

    def test_the_linking_step_is_explained_where_a_link_is_needed(self):
        found = {e.title: e.description for e in pages({"rank": "r", "stats": "s", "lifetime": "l", "help": "h"})}
        for name in ("/rank", "/stats", "/lifetime"):
            self.assertIn("Link Reforger account", found[name])


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_and_previous_walk_the_pages_and_stop_at_the_ends(self):
        embeds = pages({"rank": "r", "help": "h"})
        view = HelpView(embeds)
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next.disabled)
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.next.callback(interaction)
        self.assertIs(interaction.response.edit_message.await_args.kwargs["embed"], embeds[1])
        await view.next.callback(interaction)
        self.assertTrue(view.next.disabled)
        self.assertFalse(view.previous.disabled)
        await view.next.callback(interaction)
        self.assertIs(interaction.response.edit_message.await_args.kwargs["embed"], embeds[2])
        await view.previous.callback(interaction)
        self.assertIs(interaction.response.edit_message.await_args.kwargs["embed"], embeds[1])


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = discord.Client(intents=discord.Intents.default())
        self.bot.config = SimpleNamespace(guild_id=1)
        self.bot.rank_command = RankCommand(self.bot)
        self.help = HelpCommand(self.bot)

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_help_is_registered_and_lists_the_commands_that_are(self):
        tree, guild = self.bot.rank_command.tree, self.bot.rank_command.guild
        self.assertIsNotNone(tree.get_command("help", guild=guild))
        self.assertEqual(set(self.help.commands()), {"rank", "help"})

    async def test_help_answers_privately_with_the_first_page(self):
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        await self.help.show(interaction)
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertEqual(sent["embed"].title, "OYB BOT COMMANDS")
        self.assertIsInstance(sent["view"], HelpView)

    async def test_a_cooldown_is_explained(self):
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock(), is_done=Mock(return_value=False)))
        error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 5), 3.0)
        await self.help.error(interaction, error)
        self.assertIn("again in 3 seconds", interaction.response.send_message.await_args.args[0])
