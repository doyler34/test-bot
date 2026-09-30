import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

from bot.discord import welcome_doc
from bot.discord.welcome import PREFIX, Welcome, build_view
from panel.db import PanelDB


class Role:
    def __init__(self, role_id, name, position, permissions=0, managed=False):
        self.id, self.name, self.position, self.managed = role_id, name, position, managed
        self.permissions = SimpleNamespace(value=permissions)
        self.colour = SimpleNamespace(value=0)

    def is_default(self):
        return self.id == 1

    def __ge__(self, other):
        return self.position >= other.position

    def __lt__(self, other):
        return self.position < other.position


class Member:
    def __init__(self, member_id):
        self.id, self.roles, self.bot = member_id, [], False
        self.mention, self.display_name = f"<@{member_id}>", f"Player{member_id}"
        self.sent = []

    async def add_roles(self, *roles, reason=None):
        self.roles += [r for r in roles if r not in self.roles]

    async def remove_roles(self, *roles, reason=None):
        self.roles = [r for r in self.roles if r not in roles]

    async def send(self, text, allowed_mentions=None):
        self.sent.append(text)


class Message:
    def __init__(self, message_id, embed, view):
        self.id, self.embeds, self.view = message_id, [embed], view
        self.author = SimpleNamespace(id=99)

    async def edit(self, embed=None, view=None, allowed_mentions=None):
        self.embeds, self.view = [embed], view


class Channel(discord.TextChannel):
    category = None

    def __init__(self, channel_id, name):
        self.id, self.name, self.messages, self.said = channel_id, name, [], []

    async def send(self, text=None, embed=None, view=None, silent=False, allowed_mentions=None):
        if text is not None:
            self.said.append(text)
            return None
        message = Message(1000 + len(self.messages), embed, view)
        self.messages.append(message)
        return message

    async def fetch_message(self, message_id):
        for message in self.messages:
            if message.id == message_id:
                return message
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")

    async def history(self, limit):
        for message in self.messages:
            yield message


class Guild:
    id = 1
    name = "OYB"
    member_count = 120

    def __init__(self):
        self.roles = [Role(1, "@everyone", 0), Role(10, "Events", 2), Role(11, "Admin", 3, permissions=8),
                      Role(20, "Squad A", 2), Role(21, "Squad B", 2)]
        self.me = SimpleNamespace(top_role=Role(50, "OYB Bot", 10),
                                  guild_permissions=SimpleNamespace(manage_roles=True))
        self.start = Channel(300, "start-here")
        self.text_channels = [self.start]

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)

    def get_channel(self, channel_id):
        return next((c for c in self.text_channels if c.id == channel_id), None)

    async def create_role(self, name, **kwargs):
        role = Role(100 + len(self.roles), name, 1)
        self.roles.append(role)
        return role


class Interaction:
    type = discord.InteractionType.component

    def __init__(self, guild, member, button_id):
        self.guild, self.user, self.guild_id = guild, member, guild.id
        self.data = {"custom_id": PREFIX + button_id}
        self.replies = []
        done = []
        self.response = SimpleNamespace(is_done=lambda: bool(done), defer=self._defer(done),
                                        send_message=self._reply)
        self.followup = SimpleNamespace(send=self._reply)

    @staticmethod
    def _defer(done):
        async def defer(**kwargs):
            done.append(True)
        return defer

    async def _reply(self, text, **kwargs):
        self.replies.append(text)


class Links:
    def __init__(self, linked=()):
        self.linked = set(linked)

    def identities(self, guild, discord_id):
        return ["x"] if discord_id in self.linked else []


class DocTests(unittest.TestCase):
    def test_the_default_is_valid_and_reads_like_before(self):
        doc, problems = welcome_doc.check_welcome(welcome_doc.default_welcome())
        self.assertEqual(problems, [])
        text = welcome_doc.description(doc)
        self.assertTrue(text.startswith("**1 — Link your Reforger account**\nPlay a round"))
        self.assertTrue(text.endswith("Stuck? **My progress** shows what you are missing."))
        self.assertEqual([b["type"] for b in doc["buttons"]],
                         ["link", "progress", "faction", "faction", "faction", "no_faction"])

    def test_problems_an_owner_can_fix(self):
        raw = {"title": "", "sections": [], "colour": "blue", "buttons": [
            {"type": "role", "label": "Events"},
            {"type": "url", "label": "Rules", "url": "rules page"},
            {"type": "pick", "label": "", "emoji": "", "role_id": "10"},
        ] + [{"type": "progress", "label": f"B{n}", "line": 3} for n in range(6)]}
        _, problems = welcome_doc.check_welcome(raw)
        self.assertIn("Give the message a title or some text.", problems)
        self.assertIn("The colour should look like #A9BC8C.", problems)
        self.assertIn("Button 1 (Events) needs a role: pick one, or type a name for a new one.", problems)
        self.assertIn("Button 2 (Rules) needs a web address starting with https://.", problems)
        self.assertIn("Button 3 needs a label or an emoji.", problems)
        self.assertIn("Line 3 has more than 5 buttons; move some to another line.", problems)

    def test_greeting(self):
        _, problems = welcome_doc.check_greeting({"enabled": True, "where": "channel", "text": "Hi"})
        self.assertEqual(problems, ["Pick which channel the greeting goes in."])
        text = welcome_doc.fill_greeting("Hi {user} ({name}), welcome to {server}, member {members}. See {start}",
                                         "<@5>", "Gaz", "OYB", 120, "<#300>")
        self.assertEqual(text, "Hi <@5> (Gaz), welcome to OYB, member 120. See <#300>")


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.panel = PanelDB(str(Path(self.tmp.name, "panel.sqlite3")))
        self.addCleanup(self.panel.close)
        self.guild = Guild()
        self.bot = SimpleNamespace(config=SimpleNamespace(guild_id=1), get_guild=lambda _: self.guild,
                                   account_links=Links(linked={7}), user=SimpleNamespace(id=99),
                                   intents=SimpleNamespace(members=False), is_closed=lambda: False)
        env = {"PANEL_DB": str(Path(self.tmp.name, "panel.sqlite3")),
               "PANEL_BRIDGE": str(Path(self.tmp.name, "bridge.json"))}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.welcome = Welcome(self.bot)

    def publish(self, **changes):
        doc = {"channel_id": 300, "title": "Welcome", "colour": "#D9A441",
               "sections": [{"heading": "Roles", "text": "Pick what you want."}],
               "buttons": [
                   {"id": "events", "type": "role", "label": "Events", "role_id": 10, "line": 1},
                   {"id": "a", "type": "pick", "label": "Squad A", "role_id": 20, "group": "squad", "line": 2},
                   {"id": "b", "type": "pick", "label": "Squad B", "role_id": 21, "group": "squad", "line": 2},
                   {"id": "vip", "type": "role", "label": "Linked only", "role_name": "Veteran",
                    "linked_only": True, "line": 3},
                   {"id": "rules", "type": "url", "label": "Rules", "url": "https://example.com/rules", "line": 3},
               ]}
        doc.update(changes)
        clean, problems = welcome_doc.check_welcome(doc)
        self.assertEqual(problems, [])
        self.panel.publish_discord_doc("welcome", json.dumps(clean), "gaz")

    async def press(self, member, button_id):
        interaction = Interaction(self.guild, member, button_id)
        self.assertTrue(await self.welcome.handle(interaction))
        return interaction.replies[-1]

    async def test_publishes_then_edits_the_same_message(self):
        self.publish()
        await self.welcome.tick()
        self.assertEqual(len(self.guild.start.messages), 1)
        message = self.guild.start.messages[0]
        self.assertEqual(message.embeds[0].title, "Welcome")
        items = message.view.children
        self.assertEqual([i.custom_id for i in items[:3]], [PREFIX + "events", PREFIX + "a", PREFIX + "b"])
        self.assertEqual(items[4].url, "https://example.com/rules")
        self.assertIn("Veteran", [r.name for r in self.guild.roles])
        self.publish(title="Welcome to OYB")
        await self.welcome.tick()
        self.assertEqual(len(self.guild.start.messages), 1)
        self.assertEqual(message.embeds[0].title, "Welcome to OYB")
        bridge = json.loads(Path(self.tmp.name, "bridge.json").read_text())
        self.assertEqual((bridge["welcome"]["version"], bridge["welcome"]["problems"]), (2, []))
        self.assertEqual([r["name"] for r in bridge["roles"] if r["problem"]], ["Admin"])

    async def test_role_and_group_buttons(self):
        self.publish()
        await self.welcome.tick()
        member = Member(7)
        self.assertEqual(await self.press(member, "events"), "You've got **Events**. Press again to remove it.")
        self.assertEqual(await self.press(member, "events"), "Removed **Events**.")
        await self.press(member, "a")
        self.assertEqual(await self.press(member, "b"), "You've got **Squad B** (swapped from Squad A).")
        self.assertEqual([r.name for r in member.roles], ["Squad B"])

    async def test_locked_group(self):
        self.publish(buttons=[
            {"id": "a", "type": "pick", "label": "A", "role_id": 20, "group": "squad", "locked": True},
            {"id": "b", "type": "pick", "label": "B", "role_id": 21, "group": "squad"}])
        await self.welcome.tick()
        member = Member(7)
        self.assertIn("That's locked in now.", await self.press(member, "b"))
        self.assertEqual(await self.press(member, "a"),
                         "You're locked to **Squad B**. Ask an admin if you need to change.")

    async def test_linked_only(self):
        self.publish()
        await self.welcome.tick()
        self.assertEqual(await self.press(Member(8), "vip"), "Link your Reforger account first, then press this again.")
        self.assertIn("Veteran", await self.press(Member(7), "vip"))

    async def test_never_hands_out_a_role_with_permissions(self):
        self.publish(buttons=[{"id": "boss", "type": "role", "label": "Boss", "role_id": 11}])
        await self.welcome.tick()
        member = Member(7)
        self.assertEqual(await self.press(member, "boss"), "That button isn't working right now. Give an admin a shout.")
        self.assertEqual(member.roles, [])
        bridge = json.loads(Path(self.tmp.name, "bridge.json").read_text())
        self.assertIn("can't hand out Admin: it has permissions", bridge["welcome"]["problems"][0])

    async def test_old_buttons_and_other_bots_buttons(self):
        self.publish()
        await self.welcome.tick()
        self.assertEqual(await self.press(Member(7), "gone"), "That button has been changed. Scroll up to the latest message.")
        other = Interaction(self.guild, Member(7), "x")
        other.data = {"custom_id": "oyb:onboard:link"}
        self.assertFalse(await self.welcome.handle(other))

    async def test_greeting(self):
        self.panel.publish_discord_doc("greeting", json.dumps(
            {"enabled": True, "where": "channel", "channel_id": 300, "text": "Welcome {user} to {server}!"}), "gaz")
        member = Member(5)
        member.guild = self.guild
        await self.welcome.tick()
        await self.welcome.greet(member)
        self.assertEqual(self.guild.start.said, ["Welcome <@5> to OYB!"])
        self.panel.publish_discord_doc("greeting", json.dumps(
            {"enabled": True, "where": "dm", "channel_id": None, "text": "Hi {name}"}), "gaz")
        await self.welcome.tick()
        await self.welcome.greet(member)
        self.assertEqual(member.sent, ["Hi Player5"])

    async def test_nothing_published_changes_nothing(self):
        await self.welcome.tick()
        self.assertEqual(self.guild.start.messages, [])
        self.assertFalse(self.welcome.published("welcome"))

    async def test_server_names_reach_everything(self):
        from bot.discord import server_stats
        self.addCleanup(server_stats.OVERRIDES.clear)
        calls = []

        async def stats_tick():
            calls.append("stats")

        async def refresh():
            calls.append("card")
        self.bot.server_stats = SimpleNamespace(tick=stats_tick)
        self.bot.refresh_servers = refresh
        three = SimpleNamespace(id="server-3", name="Server 3", enabled=True)
        self.bot.config.servers = [three]
        self.panel.publish_discord_doc("names", json.dumps({"names": {"server-3": "Classic #2"}}), "gaz")
        await self.welcome.tick()
        self.assertEqual(server_stats.label_for(three), "Classic #2")
        self.assertEqual(calls, ["stats", "card"])
        await self.welcome.tick()
        self.assertEqual(calls, ["stats", "card"])
        bridge = json.loads(Path(self.tmp.name, "bridge.json").read_text())
        self.assertEqual([{k: v for k, v in s.items() if k != "settings"} for s in bridge["servers"]],
                         [{"id": "server-3", "default": "Classic #2", "label": "Classic #2", "enabled": True}])
        # A fresh bot knows the names before it first draws anything.
        server_stats.OVERRIDES.clear()
        Welcome(self.bot)
        self.assertEqual(server_stats.label_for(three), "Classic #2")

    async def test_server_info_redraws_the_card(self):
        from bot.discord import server_notifications
        self.addCleanup(server_notifications.SERVER_INFO.clear)
        redrawn = []

        async def refresh():
            redrawn.append(True)
        self.bot.refresh_servers = refresh
        one = SimpleNamespace(id="server-1", name="Server 1", enabled=True, settings="Old settings", rules="Old rules",
                              status="online")
        self.bot.config.servers = [one]
        doc, problems = welcome_doc.check_serverinfo({"intro": "Hello", "settings": {"server-1": "Everon, 128 players"},
                                                      "rules": "• Be nice", "title": "", "rules_title": ""})
        self.assertEqual(problems, [])
        self.panel.publish_discord_doc("serverinfo", json.dumps(doc), "gaz")
        await self.welcome.tick()
        self.assertEqual(redrawn, [True])
        with patch.object(server_notifications, "server_status_line", lambda bot, server: "🟢 up"):
            card = server_notifications.servers_embed(self.bot)
        self.assertEqual((card.title, card.description), (server_notifications.SERVERS_TITLE, "Hello"))
        self.assertEqual(card.fields[0].value, "🟢 up\n**Settings:** Everon, 128 players")
        rules = server_notifications.rules_embed(self.bot)
        self.assertEqual((rules.title, rules.description), (server_notifications.RULES_TITLE, "• Be nice"))

    def test_server_settings_fit_a_discord_field(self):
        _, problems = welcome_doc.check_serverinfo({"settings": {"server-1": "x" * 950}})
        self.assertEqual(problems, ["server-1's settings are 950 characters; keep them under 900."])

    def test_names_cannot_use_the_channel_separator(self):
        doc, problems = welcome_doc.check_names({"names": {"server-3": "Classic #2 · Night"}})
        self.assertEqual(problems, ["server-3: names can't contain · (the bot uses it in the channel names)."])

    def test_names_must_differ(self):
        doc, problems = welcome_doc.check_names({"names": {"server-1": " Classic ", "server-3": "classic", "bad id!": "x"}})
        self.assertEqual(doc, {"names": {"server-1": "Classic", "server-3": "classic"}})
        self.assertEqual(problems, ["server-1 and server-3 are both called classic; give each its own name."])

    def post(self, post_id, **changes):
        doc = {"channel_id": 300, "title": "Event night", "colour": "#D9A441",
               "sections": [{"heading": "Friday 8pm", "text": "Bring a squad."}],
               "buttons": [{"id": "going", "type": "role", "label": "I'm going", "role_name": "Event night"},
                           {"id": "rules", "type": "url", "label": "Rules", "url": "https://example.com"}]}
        doc.update(changes)
        clean, problems = welcome_doc.check_post(doc)
        self.assertEqual(problems, [])
        self.panel.publish_discord_doc(f"post:{post_id}", json.dumps(clean), "gaz")

    async def test_posts_go_up_get_edited_and_come_down(self):
        self.post("ev1")
        await self.welcome.tick()
        self.assertEqual(len(self.guild.start.messages), 1)
        message = self.guild.start.messages[0]
        self.assertEqual(message.embeds[0].title, "Event night")
        self.assertIsNone(message.embeds[0].footer.text)
        self.assertEqual(message.view.children[0].custom_id, "oyb:p:ev1:going")
        member = Member(7)
        interaction = Interaction(self.guild, member, "x")
        interaction.data = {"custom_id": "oyb:p:ev1:going"}
        self.assertTrue(await self.welcome.handle(interaction))
        self.assertEqual(interaction.replies[-1], "You've got **Event night**. Press again to remove it.")
        self.post("ev1", title="Event night (moved to Saturday)")
        await self.welcome.tick()
        self.assertEqual(len(self.guild.start.messages), 1)
        self.assertEqual(message.embeds[0].title, "Event night (moved to Saturday)")
        deleted = []

        async def delete():
            deleted.append(True)
            self.guild.start.messages.remove(message)
        message.delete = delete
        self.panel.publish_discord_doc("post:ev1", json.dumps({"deleted": True}), "gaz")
        await self.welcome.tick()
        self.assertEqual(deleted, [True])

    async def test_emoji_with_a_variation_selector_is_retried_plain(self):
        sent = self.guild.start.send
        tries = []

        async def picky(text=None, embed=None, view=None, **kwargs):
            emojis = [str(item.emoji) for item in (view.children if view else []) if item.emoji]
            tries.append(emojis)
            if any("\ufe0f" in e for e in emojis):
                raise discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"),
                    "Invalid Form Body\nIn components.0.components.0.emoji.name: Invalid emoji")
            return await sent(text, embed=embed, view=view, **kwargs)
        self.guild.start.send = picky
        self.post("cafe", buttons=[{"id": "tea", "type": "role", "label": "Server role", "emoji": "☕\ufe0f",
                                    "role_id": 10}])
        await self.welcome.tick()
        self.assertEqual(tries, [["☕\ufe0f"], ["☕"]])
        self.assertEqual(len(self.guild.start.messages), 1)
        self.assertEqual(self.welcome.state["posts"]["cafe"]["problems"], [])

    async def test_an_emoji_discord_refuses_is_named(self):
        async def refuse(*args, **kwargs):
            raise discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"),
                "Invalid Form Body\nIn components.1.components.0.emoji.name: Invalid emoji")
        self.guild.start.send = refuse
        self.post("bad", buttons=[{"id": "a", "type": "role", "label": "First", "role_id": 10, "line": 1},
                                  {"id": "b", "type": "role", "label": "Odd one", "emoji": "x", "role_id": 20, "line": 3}])
        await self.welcome.tick()
        self.assertEqual(self.welcome.state["posts"]["bad"]["problems"],
                         ["Discord doesn't accept the emoji on the Odd one button. Pick another emoji, or leave it blank."])

    def test_posts_only_take_role_pick_and_link_buttons(self):
        doc = welcome_doc.default_post()
        doc["buttons"] = [{"type": "link", "label": "Link"}]
        _, problems = welcome_doc.check_post(doc)
        self.assertIn("The Link button can't be used on a post.", problems)
        self.assertIn("Pick which channel to post it in.", problems)

    def test_view_lines(self):
        doc, _ = welcome_doc.check_welcome(welcome_doc.default_welcome())
        rows = [item.row for item in build_view(doc).children]
        self.assertEqual(rows, [0, 0, 1, 1, 1, 1])


if __name__ == "__main__":
    unittest.main()
