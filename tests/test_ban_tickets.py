import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

from bot.discord import ban_tickets
from bot.discord.ban_tickets import BanTickets
from panel.db import PanelDB

HAVOC = "5f1c2a90-8b1e-4a57-9a3e-2d4b6c8e0f11"
HAVOC_PS = "0e9d8c7b-6a5f-4e3d-8c2b-1a0f9e8d7c6b"
PANEL, CATEGORY = 500, 600


class Member:
    def __init__(self, member_id, bot=False):
        self.id = member_id
        self.bot = bot
        self.mention = f"<@{member_id}>"


class Guild:
    id = 1

    def __init__(self, members):
        self.members = {m.id: m for m in members}
        self.me = Member(99, bot=True)
        self.panel = SimpleNamespace(id=PANEL, category_id=CATEGORY)

    def get_channel(self, channel_id):
        return self.panel if channel_id == PANEL else None

    def get_member(self, member_id):
        return None

    async def fetch_member(self, member_id):
        if member_id not in self.members:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")
        return self.members[member_id]


class Channel(discord.TextChannel):
    """Just enough of a text channel: its category, who it's shared with, and what's sent to it."""

    def __init__(self, guild, channel_id, category_id, overwrites):
        self.guild, self.id, self.category_id, self._shared = guild, channel_id, category_id, overwrites
        self.sent = []

    @property
    def overwrites(self):
        return {target: None for target in self._shared}

    async def send(self, embed=None, allowed_mentions=None):
        self.sent.append(embed)


class Thread:
    def __init__(self, guild, parent_id, members, mentions, owner_id=77):
        self.guild, self.parent_id, self.id, self.owner_id = guild, parent_id, 700, owner_id
        self._members, self._mentions = members, mentions
        self.sent = []

    async def fetch_members(self):
        if self._members is None:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")
        return [SimpleNamespace(id=m) for m in self._members]

    async def history(self, limit, oldest_first):
        yield SimpleNamespace(mentions=[SimpleNamespace(id=m) for m in self._mentions])

    async def send(self, embed=None, allowed_mentions=None):
        self.sent.append(embed)


class Links:
    def __init__(self, links):
        self.links = links

    def identities(self, guild, discord_id):
        return self.links.get(discord_id, [])


class BanTicketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name, "panel.sqlite3"))
        self.panel = PanelDB(self.path)
        self.addCleanup(self.panel.close)
        self.player, self.staff = Member(10), Member(20)
        self.guild = Guild([self.player, self.staff, Member(77, bot=True)])
        bot = SimpleNamespace(config=SimpleNamespace(guild_id=1),
                              account_links=Links({10: [HAVOC, HAVOC_PS], 20: []}))
        env = {"PANEL_DB": self.path, "BAN_TICKET_CHANNEL": str(PANEL), "PANEL_URL": "https://panel.example.com/"}
        with patch.dict(os.environ, env):
            self.tickets = BanTickets(bot)
        patcher = patch.object(ban_tickets, "SETTLE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def channel(self, *shared, category=CATEGORY):
        roles = [discord.Object(1)]
        return Channel(self.guild, 800, category, [*shared, *roles])

    async def test_banned_opener_gets_their_ban_posted(self):
        expires = int(time.time()) + 604800
        self.panel.add_ban(HAVOC_PS, "Havoc_PS", "Teamkilling (same IP as Havoc)", "burd", expires_at=expires)
        ticket = self.channel(Member(10), Member(77, bot=True))
        await self.tickets.channel_created(ticket)
        self.assertEqual(len(ticket.sent), 1)
        card = ticket.sent[0]
        self.assertEqual(card.title, "This player is banned")
        self.assertIn("<@10>", card.description)
        field = card.fields[0]
        self.assertEqual(field.name, "Havoc_PS")
        self.assertIn("**Length:** 7 days", field.value)
        self.assertIn("**Reason:** Teamkilling\n", field.value)
        self.assertIn(f"<t:{expires}:F>", field.value)
        self.assertIn("https://panel.example.com/player/" + HAVOC_PS, field.value)

    async def test_uncached_opener_still_found(self):
        self.panel.add_ban(HAVOC, "Havoc", "Cheating", "burd")
        ticket = self.channel(discord.Object(10))
        await self.tickets.channel_created(ticket)
        self.assertIn("**Length:** Permanent", ticket.sent[0].fields[0].value)

    async def test_ordinary_tickets_are_left_alone(self):
        ticket = self.channel(Member(10), Member(20))
        await self.tickets.channel_created(ticket)
        self.assertEqual(ticket.sent, [])

    async def test_every_listed_category(self):
        with patch.dict(os.environ, {"PANEL_DB": self.path, "BAN_TICKET_CHANNEL": str(PANEL), "BAN_TICKET_CATEGORY": "601, 602"}):
            tickets = BanTickets(self.tickets.bot)
        self.panel.add_ban(HAVOC, "Havoc", "Cheating", "burd")
        for category in (CATEGORY, 601, 602):
            ticket = self.channel(Member(10), category=category)
            await tickets.channel_created(ticket)
            self.assertEqual(len(ticket.sent), 1, category)
        other = self.channel(Member(10), category=603)
        await tickets.channel_created(other)
        self.assertEqual(other.sent, [])

    async def test_other_categories_ignored(self):
        self.panel.add_ban(HAVOC, "Havoc", "Cheating", "burd")
        ticket = self.channel(Member(10), category=601)
        await self.tickets.channel_created(ticket)
        self.assertEqual(ticket.sent, [])

    async def test_ticket_as_a_thread(self):
        self.panel.add_ban(HAVOC, "Havoc", "Cheating", "burd")
        thread = Thread(self.guild, PANEL, members=None, mentions=[10])
        await self.tickets.thread_created(thread)
        self.assertEqual(len(thread.sent), 1)
        elsewhere = Thread(self.guild, 999, members=[10], mentions=[])
        await self.tickets.thread_created(elsewhere)
        self.assertEqual(elsewhere.sent, [])

    async def test_off_without_settings(self):
        with patch.dict(os.environ, {"BAN_TICKET_CHANNEL": "", "BAN_TICKET_CATEGORY": ""}):
            tickets = BanTickets(self.tickets.bot)
        self.panel.add_ban(HAVOC, "Havoc", "Cheating", "burd")
        ticket = self.channel(Member(10))
        await tickets.channel_created(ticket)
        self.assertEqual(ticket.sent, [])


if __name__ == "__main__":
    unittest.main()
