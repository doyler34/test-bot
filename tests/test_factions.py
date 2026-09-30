import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.storage.account_links import AccountLinks
from bot.discord.factions import (FACTIONS, FactionView, apply_faction,
                                   ensure_faction_roles)


class Role:
    def __init__(self, id, name):
        self.id, self.name = id, name


class StorageTests(unittest.TestCase):
    def test_faction_choice_and_role_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            links = AccountLinks(Path(tmp) / "l.db")
            try:
                self.assertIsNone(links.faction(1, 10))
                links.set_faction(1, 10, "US")
                self.assertEqual(links.faction(1, 10), "US")
                links.set_faction(1, 10, "USSR")  # switching overwrites
                self.assertEqual(links.faction(1, 10), "USSR")
                links.save_faction_role(1, "US", 555)
                self.assertEqual(links.faction_role(1, "US"), 555)
            finally:
                links.close()


def clicked(guild, member):
    """A click that behaves like Discord's: once deferred, replies go through
    followup, and reply() reads back whichever route was taken."""
    state = {"done": False}

    async def defer(**_):
        state["done"] = True

    click = SimpleNamespace(
        guild_id=1, guild=guild, user=member,
        followup=SimpleNamespace(send=AsyncMock()),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(side_effect=defer),
                                 is_done=lambda: state["done"]))
    click.reply = lambda: (click.followup.send.await_args or click.response.send_message.await_args).args[0]
    return click


class ApplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.links = AccountLinks(Path(self.tmp.name) / "l.db")
        self.roles = {}
        for rid, (name, _, _) in zip(range(100, 103), FACTIONS):
            self.links.save_faction_role(1, name, rid)
            self.roles[rid] = Role(rid, name)
        self.bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1))
        self.guild = SimpleNamespace(id=1, get_role=lambda i: self.roles.get(i))

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    async def test_pick_assigns_role_and_saves_choice(self):
        member = SimpleNamespace(id=10, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        await apply_faction(self.bot, self.guild, member, "US")
        member.add_roles.assert_awaited_once()
        member.remove_roles.assert_not_awaited()
        self.assertEqual(self.links.faction(1, 10), "US")

    async def test_switch_removes_the_old_faction_role(self):
        us = self.roles[100]
        member = SimpleNamespace(id=10, roles=[us], add_roles=AsyncMock(), remove_roles=AsyncMock())
        await apply_faction(self.bot, self.guild, member, "USSR")
        member.remove_roles.assert_awaited_once()
        member.add_roles.assert_awaited_once()
        self.assertEqual(self.links.faction(1, 10), "USSR")

    async def test_ensure_creates_the_three_roles_when_missing(self):
        created = []
        async def create_role(**kwargs):
            role = Role(200 + len(created), kwargs["name"])
            created.append(role)
            return role
        with tempfile.TemporaryDirectory() as tmp:
            links = AccountLinks(Path(tmp) / "fresh.db")
            bot = SimpleNamespace(account_links=links, config=SimpleNamespace(guild_id=1))
            guild = SimpleNamespace(id=1, roles=[], me=SimpleNamespace(top_role=None),
                                    get_role=lambda i: None, create_role=create_role)
            try:
                await ensure_faction_roles(bot, guild)
                self.assertEqual([r.name for r in created], ["US", "USSR", "FIA"])
                self.assertEqual(links.faction_role(1, "US"), created[0].id)
            finally:
                links.close()

    async def test_view_button_applies_faction(self):
        view = FactionView(self.bot)
        self.assertEqual(len(view.children), 3)
        interaction = clicked(self.guild, SimpleNamespace(id=10, roles=[]))
        with patch("bot.discord.factions.apply_faction", new=AsyncMock()) as applied:
            await view.children[0].callback(interaction)
        applied.assert_awaited_once()
        self.assertEqual(applied.await_args.args[3], "US")  # first button = US
        # The click is taken before the two role calls, then answered.
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited()

    async def test_a_second_pick_is_refused_and_points_at_an_admin(self):
        view = FactionView(self.bot)
        held = self.guild.get_role(self.links.faction_role(1, "USSR"))
        interaction = clicked(self.guild, SimpleNamespace(id=10, roles=[held]))
        with patch("bot.discord.factions.apply_faction", new=AsyncMock()) as applied:
            await view.children[0].callback(interaction)  # try to switch to US
        applied.assert_not_awaited()
        text = interaction.reply()
        self.assertIn("locked to **USSR**", text)
        self.assertIn("admin", text)

    async def test_an_admin_removing_the_role_lifts_the_lock(self):
        view = FactionView(self.bot)
        interaction = clicked(self.guild, SimpleNamespace(id=10, roles=[]))
        with patch("bot.discord.factions.apply_faction", new=AsyncMock()) as applied:
            await view.children[0].callback(interaction)
        applied.assert_awaited_once()

    async def test_a_missing_role_is_not_reported_as_success(self):
        # It used to save the pick and say "you're US now" with no role given,
        # so the member saw nothing happen and nobody knew why.
        from bot.discord.factions import apply_faction
        self.links.db.execute('DELETE FROM faction_roles')
        self.links.db.commit()
        member = SimpleNamespace(id=10, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.assertIsNone(await apply_faction(self.bot, self.guild, member, 'US'))
        member.add_roles.assert_not_awaited()
        # And the pick is not saved, so nothing claims they are US.
        self.assertIsNone(self.links.faction(1, 10))

    async def test_the_button_says_so_rather_than_claiming_it_worked(self):
        self.links.db.execute('DELETE FROM faction_roles')
        self.links.db.commit()
        view = FactionView(self.bot)
        interaction = clicked(self.guild, SimpleNamespace(id=10, roles=[]))
        await view.children[0].callback(interaction)
        self.assertIn('role is missing', interaction.reply())

    async def test_a_successful_pick_returns_the_role_it_gave(self):
        from bot.discord.factions import apply_faction
        member = SimpleNamespace(id=10, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        given = await apply_faction(self.bot, self.guild, member, 'US')
        self.assertIsNotNone(given)
        self.assertEqual(self.links.faction(1, 10), 'US')

    async def test_picker_emoji_are_the_three_flags(self):
        from bot.discord.factions import FACTIONS
        self.assertEqual([e for _, _, e in FACTIONS], ["🇺🇸", "🇷🇺", "🏳️"])


class LookTests(unittest.IsolatedAsyncioTestCase):
    """Names, colours and emojis published from OYB Control."""

    async def asyncSetUp(self):
        from bot.discord import factions
        self.factions = factions
        factions.LOOK.update({"US": {"name": "NATO", "colour": "#112233", "emoji": "🦅"}})
        self.addCleanup(factions.LOOK.clear)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = AccountLinks(Path(self.tmp.name) / "l.db")
        self.addCleanup(self.links.close)

    async def test_roles_are_renamed_and_recoloured(self):
        edits = []

        class Role:
            def __init__(self, rid, name, colour, position=1):
                self.id, self.name, self.colour, self.managed, self.position = rid, name, SimpleNamespace(value=colour), False, position

            def __ge__(self, other):
                return self.position >= other.position

            async def edit(self, **kwargs):
                edits.append((self.id, kwargs["name"], kwargs["colour"].value))

        roles = {100: Role(100, "US", 0x3B5B8C), 101: Role(101, "USSR", 0xB23A32), 102: Role(102, "FIA", 0x8A7B3F, 9)}
        self.factions.LOOK["FIA"] = {"name": "Guerrillas", "colour": "#8A7B3F", "emoji": ""}
        for rid, key in zip(roles, ("US", "USSR", "FIA")):
            self.links.save_faction_role(1, key, rid)
        bot = SimpleNamespace(account_links=self.links)
        guild = SimpleNamespace(id=1, roles=list(roles.values()), get_role=roles.get,
                                me=SimpleNamespace(top_role=Role(0, "bot", 0, 5)))
        problems = await self.factions.ensure_faction_roles(bot, guild)
        self.assertEqual(edits, [(100, "NATO", 0x112233)])
        self.assertEqual(len(problems), 1)
        self.assertIn("above the bot's role", problems[0])

    async def test_buttons_and_replies_use_the_new_name(self):
        bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1))
        view = FactionView(bot)
        us = view.children[0]
        self.assertEqual((us.label, str(us.emoji), us.custom_id), ("NATO", "🦅", "oyb:faction:US"))
        self.assertEqual(self.factions.label("USSR"), "USSR")
        held = Role(100, "NATO")
        self.links.save_faction_role(1, "US", 100)
        guild = SimpleNamespace(id=1, get_role=lambda i: held if i == 100 else None)
        interaction = clicked(guild, SimpleNamespace(id=10, roles=[held]))
        await view.children[1].callback(interaction)
        self.assertIn("locked to **NATO**", interaction.reply())

    def test_the_panel_check(self):
        from bot.discord.welcome_doc import check_factions, default_factions
        doc, problems = check_factions(default_factions())
        self.assertEqual(problems, [])
        raw = default_factions()
        raw["factions"]["USSR"].update(name="us", colour="red")
        doc, problems = check_factions(raw)
        self.assertIn("USSR's colour should look like #3B5B8C.", problems)
        self.assertIn("US and USSR are both called us; each faction needs its own name.", problems)


class CardTests(unittest.TestCase):
    def test_render_card_themes_for_every_faction(self):
        from bot.ranks.rank_card import render_card
        for f in [None, "US", "USSR", "FIA"]:
            png = render_card("GARETH", 347, faction=f)
            self.assertTrue(png.startswith(b"\x89PNG"), f)  # renders, no crash

    def test_each_faction_has_its_own_artwork(self):
        from bot.ranks.rank_card import ASSETS, DEFAULT_LAYERS, FACTION_THEMES
        artwork = [layers for _, _, layers in FACTION_THEMES.values()]
        self.assertEqual(len(set(artwork)), len(FACTION_THEMES))  # no two share a look
        self.assertNotIn(DEFAULT_LAYERS, artwork)
        for layers in artwork + [DEFAULT_LAYERS]:
            for name in layers:
                self.assertTrue((ASSETS / name).is_file(), name)

    def test_served_reads_as_hours_and_minutes(self):
        from bot.ranks.rank_card import served
        for milliseconds, text in [(None, "0m"), (0, "0m"), (60_000, "1m"),
                                   (9_240_000, "2h 34m"), (360_000_000, "100h 00m")]:
            self.assertEqual(served(milliseconds), text)


if __name__ == "__main__":
    unittest.main()
