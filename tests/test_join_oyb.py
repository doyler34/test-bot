import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import sqlite3
import discord

from bot.storage.account_links import AccountLinks
from bot.discord.join_oyb import (JoinView, ReviewDecision, ConfirmUnlink, ForceLinkChoice,
                                  find_identity, find_candidates, prepare_join_channel,
                                  resolve_identity, known_name)


def responding():
    """Mimics Discord: is_done() flips once the click has been acknowledged,
    after which a reply has to go through followup."""
    state = {"done": False}

    async def defer(**_):
        state["done"] = True

    return SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(),
                           defer=AsyncMock(side_effect=defer), is_done=lambda: state["done"])


def replied(interaction):
    """The private reply, whichever route it took."""
    return interaction.followup.send.await_args or interaction.response.send_message.await_args


class JoinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.links = AccountLinks(self.root / "links.sqlite3")
        self.bot = SimpleNamespace(account_links=self.links, config=SimpleNamespace(guild_id=1),
                                   user=SimpleNamespace(id=99))

    async def asyncTearDown(self):
        self.links.close()
        self.tmp.cleanup()

    async def test_only_admin_can_approve(self):
        identity = "11111111-2222-3333-4444-555555555555"
        token = self.links.submit(1, 10, identity, "Player")
        interaction = SimpleNamespace(guild_id=1, user=SimpleNamespace(id=22),
            permissions=SimpleNamespace(manage_guild=False, administrator=False),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(), response=responding())
        view = ReviewDecision(self.bot, token, 22)
        await view.decide(interaction, True)
        self.assertIsNone(self.links.lookup(1, 10))
        self.assertTrue(replied(interaction).kwargs["ephemeral"])
        interaction.permissions.manage_guild = True
        await view.decide(interaction, True)
        self.assertEqual(self.links.lookup(1, 10), identity)

    async def test_create_reuse_card_and_persistent_buttons(self):
        channel = Mock(spec=discord.TextChannel)
        channel.id = 55
        from bot.discord.join_oyb import MARKER
        channel.topic = MARKER
        info = SimpleNamespace(id=66, author=self.bot.user, embeds=[], edit=AsyncMock())
        channel.send = AsyncMock(return_value=info)
        channel.fetch_message = AsyncMock(return_value=info)
        channel.edit = AsyncMock()
        async def empty(**kwargs):
            if False:
                yield
        channel.history = empty
        guild = SimpleNamespace(id=1, text_channels=[], get_channel=Mock(return_value=channel),
                                create_text_channel=AsyncMock(return_value=channel))
        await prepare_join_channel(self.bot, guild, {})
        info.embeds = [channel.send.await_args.kwargs["embed"]]
        view = channel.send.await_args.kwargs["view"]
        self.assertTrue(view.is_persistent())
        self.assertEqual(len(view.children), 2)  # members see linking only; admin buttons moved
        await prepare_join_channel(self.bot, guild, {})
        guild.create_text_channel.assert_awaited_once()
        channel.send.assert_awaited_once()
        info.edit.assert_awaited_once()
        self.assertTrue(channel.send.await_args.kwargs["silent"])

    async def test_admin_unlink_removes_link_and_strips_rank_roles(self):
        identity = "11111111-2222-3333-4444-555555555555"
        self.links.verified_link(1, 10, identity, "admin:1")
        role = SimpleNamespace(id=7)
        member = SimpleNamespace(id=10, roles=[role], remove_roles=AsyncMock())
        self.bot.rank_sync = SimpleNamespace(roles=[role], applied={10: ("x", 0)})
        guild = SimpleNamespace(fetch_member=AsyncMock(return_value=member))
        interaction = SimpleNamespace(guild_id=1, guild=guild, user=SimpleNamespace(id=1),
            permissions=SimpleNamespace(manage_guild=True, administrator=False),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(), response=responding())
        await ConfirmUnlink(self.bot, 1, 10).confirm.callback(interaction)
        self.assertIsNone(self.links.lookup(1, 10))
        member.remove_roles.assert_awaited_once()
        self.assertNotIn(10, self.bot.rank_sync.applied)

    async def test_unlink_requires_admin_and_leaves_link(self):
        identity = "11111111-2222-3333-4444-555555555555"
        self.links.verified_link(1, 10, identity, "admin:1")
        interaction = SimpleNamespace(guild_id=1, user=SimpleNamespace(id=22),
            permissions=SimpleNamespace(manage_guild=False, administrator=False),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(), response=responding())
        await ConfirmUnlink(self.bot, 1, 10).confirm.callback(interaction)
        interaction.followup.send.assert_awaited()  # denied, after the click was taken
        self.assertEqual(self.links.lookup(1, 10), identity)

    def test_find_candidates_groups_playtime_and_orders_by_most_played(self):
        path = self.root / "playtime.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.execute("CREATE TABLE totals (server TEXT, identity TEXT, name TEXT, seconds REAL)")
            db.executemany("INSERT INTO totals VALUES (?,?,?,?)", [
                ("s1", "id-a", "LOGAN", 3600), ("s2", "id-a", "LOGAN", 1800),
                ("s1", "id-b", "LOGAN", 120)])
            db.commit()
        finally:
            db.close()
        cands = find_candidates(path, "logan")  # case-insensitive, like the linker
        self.assertEqual([c["identity"] for c in cands], ["id-a", "id-b"])
        self.assertEqual(cands[0]["seconds"], 5400)
        self.assertEqual(find_candidates(self.root / "missing.sqlite3", "logan"), [])

    async def test_force_link_creates_the_chosen_link(self):
        ident = "11111111-2222-3333-4444-555555555555"
        view = ForceLinkChoice(self.bot, owner=1, discord_id=10,
                               candidates=[dict(identity=ident, seconds=5400, servers="s1")])
        select = view.children[0]
        select._values = [ident]
        i = SimpleNamespace(guild_id=1, user=SimpleNamespace(id=1),
            permissions=SimpleNamespace(manage_guild=True, administrator=False),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(), response=responding())
        await select.callback(i)
        self.assertEqual(self.links.lookup(1, 10), ident)
        i.edit_original_response.assert_awaited()

    async def test_force_link_requires_admin(self):
        ident = "11111111-2222-3333-4444-555555555555"
        view = ForceLinkChoice(self.bot, owner=1, discord_id=10,
                               candidates=[dict(identity=ident, seconds=1, servers="s1")])
        select = view.children[0]
        select._values = [ident]
        i = SimpleNamespace(guild_id=1, user=SimpleNamespace(id=2),
            permissions=SimpleNamespace(manage_guild=False, administrator=False),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(), response=responding())
        await select.callback(i)
        i.followup.send.assert_awaited()  # denied, after the click was taken
        self.assertIsNone(self.links.lookup(1, 10))

    async def test_identity_id_is_accepted_when_a_name_is_ambiguous(self):
        path = self.root / "playtime.sqlite3"
        one = "11111111-2222-3333-4444-555555555555"
        two = "66666666-7777-8888-9999-000000000000"
        db = sqlite3.connect(path)
        try:
            db.execute("CREATE TABLE totals (identity TEXT, name TEXT, seconds REAL)")
            db.executemany("INSERT INTO totals VALUES (?,?,?)",
                           [(one, "Player", 900), (two, "Player", 100), (one, "OldName", 60)])
            db.commit()
            # Two players share the name, so the name route refuses...
            with self.assertRaises(ValueError):
                resolve_identity(path, "Player")
            # ...but either of them can identify themselves by ID.
            self.assertEqual(resolve_identity(path, two), two)
            self.assertEqual(resolve_identity(path, "  " + one.upper() + " "), one)
            # An ID nobody here has played under is refused, not queued.
            with self.assertRaises(ValueError):
                resolve_identity(path, "deadbeef-0000-0000-0000-000000000000")
            # Admins see a name for an ID-based request, not just the raw ID.
            self.assertEqual(known_name(path, one), "Player")
            self.assertIsNone(known_name(path, "deadbeef-0000-0000-0000-000000000000"))
        finally:
            db.close()

    async def test_name_lookup_is_unique_and_does_not_change_totals(self):
        path = self.root / "playtime.sqlite3"
        db = sqlite3.connect(path)
        try:
            db.execute("CREATE TABLE totals (identity TEXT, name TEXT, seconds REAL)")
            db.execute("INSERT INTO totals VALUES ('id-one','Player',123)")
            db.commit()
            self.assertEqual(find_identity(path, "player"), "id-one")
            db.execute("INSERT INTO totals VALUES ('id-two','Player',456)")
            db.commit()
            with self.assertRaises(ValueError):
                find_identity(path, "Player")
            self.assertEqual(db.execute("SELECT SUM(seconds) FROM totals").fetchone()[0], 579)
        finally:
            db.close()
