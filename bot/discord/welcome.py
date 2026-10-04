"""Draws the Start here message and the join greeting the way they were last
published from OYB Control, and answers their buttons.

The panel owns the wording: this only reads its database. What the panel
needs back (the server's roles and channels, and whether publishing worked)
goes in a small file next to the bot's other data, which the panel reads.
"""
import asyncio
from contextlib import closing
import json
import logging
import os
import re
from pathlib import Path
import sqlite3
import time

import discord

from bot.config import member_role_name, onboarding_channel_id
from bot.discord import server_stats, welcome_doc
from bot.discord import factions
from bot.discord.factions import ensure_faction_roles
from bot.discord.interactions import ack, say
from bot.discord.onboarding import MARKER, by_name, pick_faction, progress, skip_faction

LOG = logging.getLogger("reforger.welcome")
INTERVAL = 30
PREFIX = "oyb:w:"
STYLES = {"green": discord.ButtonStyle.success, "blurple": discord.ButtonStyle.primary,
          "grey": discord.ButtonStyle.secondary, "red": discord.ButtonStyle.danger}


def read_doc(path, key):
    """(doc, version) as last published, or None."""
    if not Path(path).is_file():
        return None
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            row = db.execute("SELECT published, version FROM discord_docs WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0]), row[1]
    except ValueError:
        return None


def read_posts(path):
    """{post id: (doc, version)} for every published post."""
    if not Path(path).is_file():
        return {}
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute("SELECT key, published, version FROM discord_docs"
                              " WHERE key LIKE 'post:%' AND published IS NOT NULL").fetchall()
    except sqlite3.Error:
        return {}
    posts = {}
    for key, text, version in rows:
        try:
            posts[key[5:]] = (json.loads(text), version)
        except ValueError:
            pass
    return posts


def read_link_actions(path, after):
    """Link decisions made in OYB Control since the last one carried out.
    Anything older than a day is left alone, so a lost state file can't
    replay an old unlink over a newer link."""
    if not Path(path).is_file():
        return []
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            return db.execute("SELECT id, kind, target, identity, by FROM link_actions"
                              " WHERE id > ? AND at > ? ORDER BY id", (after, time.time() - 86400)).fetchall()
    except sqlite3.Error:
        return []


def safe_role(guild, role):
    """Why a role can't go on a button, or None if it can. A button must never
    hand out power, and the bot can only give roles below its own."""
    me = guild.me
    if role.managed or role.is_default():
        return "it belongs to Discord or another bot"
    if role.permissions.value:
        return "it has permissions; button roles must have none"
    if me is None or not me.guild_permissions.manage_roles:
        return "the bot needs Manage Roles"
    if role >= me.top_role:
        return "it sits above the bot's role; move the bot's role higher"
    return None


def build_view(doc, prefix=PREFIX):
    view = discord.ui.View(timeout=None)
    for button in doc["buttons"]:
        kwargs = {"label": button["label"] or None, "row": button["line"] - 1}
        if button["emoji"]:
            kwargs["emoji"] = discord.PartialEmoji.from_str(button["emoji"])
        if button["type"] == "url":
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, url=button["url"], **kwargs))
        else:
            view.add_item(discord.ui.Button(style=STYLES[button["style"]],
                                            custom_id=prefix + button["id"], **kwargs))
    return view


def plain_emojis(doc):
    """Phones add an invisible variation selector to many emojis (☕️); Discord
    refuses some of them with it, so this is what's retried without it."""
    return {**doc, "buttons": [{**b, "emoji": b["emoji"].replace("\ufe0f", "")} for b in doc["buttons"]]}


def emoji_culprit(doc, text):
    """Which button Discord named in "components.R.components.I.emoji", by label."""
    match = re.search(r"components\.(\d+)\.components\.(\d+)\.emoji", text or "")
    if not match:
        return None
    rows = [[b for b in doc["buttons"] if b["line"] == line] for line in range(1, 6)]
    rows = [row for row in rows if row]
    row, index = int(match[1]), int(match[2])
    if row < len(rows) and index < len(rows[row]):
        button = rows[row][index]
        return button["label"] or button["emoji"]
    return None


async def put_message(channel, message, doc, footer, prefix):
    """Send or edit, retrying once without variation selectors if Discord
    rejects an emoji. Returns the message; raises HTTPException otherwise."""
    for attempt in (doc, plain_emojis(doc)):
        embed, view = build_embed(attempt, footer=footer), build_view(attempt, prefix=prefix)
        try:
            if message is None:
                return await channel.send(embed=embed, view=view, silent=True,
                                          allowed_mentions=discord.AllowedMentions.none())
            await message.edit(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
            return message
        except discord.HTTPException as exc:
            if "emoji" not in (exc.text or "").lower() or attempt is not doc:
                raise
    raise RuntimeError("unreachable")


def refused(doc, exc):
    culprit = emoji_culprit(doc, exc.text)
    if culprit:
        return f"Discord doesn't accept the emoji on the {culprit} button. Pick another emoji, or leave it blank."
    return (f"Discord refused the message ({exc.text or exc.status}). "
            "Check the emojis and that the bot can post in that channel.")


def build_embed(doc, footer=MARKER):
    embed = discord.Embed(title=doc["title"] or None, description=welcome_doc.description(doc) or None,
                          colour=int(doc["colour"][1:], 16))
    return embed.set_footer(text=footer) if footer else embed


class Welcome:
    def __init__(self, bot):
        self.bot = bot
        self.path = os.getenv("PANEL_DB", "data/panel.sqlite3")
        self.bridge = Path(os.getenv("PANEL_BRIDGE", "data/panel_bridge.json"))
        self.docs = {}
        self.posts = {}
        self.applied = {}
        self.state = self._load_state()
        # Channels and roles chosen in OYB Control count as if they were in
        # the .env; the bot sets its channels up at start, so they apply then.
        found = read_doc(self.path, "channels")
        if found:
            for key, value in {**found[0].get("channels", {}), **found[0].get("roles", {})}.items():
                if key in dict(welcome_doc.CHANNEL_SETTINGS) or key in dict(welcome_doc.ROLE_SETTINGS):
                    os.environ[key] = str(value)
            self.state["channels"] = {"version": found[1], "at": int(time.time()), "problems": []}
            LOG.info("Using the channels and roles published in OYB Control (version %s)", found[1])
        # Names and wording are needed before anything is first drawn at boot.
        found = read_doc(self.path, "names")
        if found:
            self._use_names(found)
        found = read_doc(self.path, "factions")
        if found:
            factions.LOOK.clear()
            factions.LOOK.update(found[0]["factions"])
        from bot.discord import server_notifications
        for key, target in (("serverinfo", server_notifications.SERVER_INFO),
                            ("matchping", server_notifications.MATCH_PING)):
            found = read_doc(self.path, key)
            if found:
                target.clear()
                target.update(found[0])

    def _use_names(self, found):
        self.docs["names"] = found
        server_stats.OVERRIDES.clear()
        server_stats.OVERRIDES.update(found[0].get("names", {}))

    def published(self, key):
        found = read_doc(self.path, key)
        if found:
            self.docs[key] = found
        return found is not None

    async def run(self):
        while not self.bot.is_closed():
            try:
                await self.tick()
            except Exception:
                LOG.exception("Welcome sync failed; retrying")
            await asyncio.sleep(INTERVAL)

    async def tick(self):
        guild = self.bot.get_guild(self.bot.config.guild_id)
        if guild is None:
            return
        for key in ("welcome", "greeting", "names", "serverinfo", "bans", "matchping", "factions"):
            found = await asyncio.to_thread(read_doc, self.path, key)
            if found:
                self.docs[key] = found
        names = self.docs.get("names")
        if names and self.state.get("names", {}).get("version") != names[1]:
            await self.apply_names(guild, names)
        info = self.docs.get("serverinfo")
        if info and self.state.get("serverinfo", {}).get("version") != info[1]:
            await self.apply_serverinfo(info)
        looks = self.docs.get("factions")
        if looks and self.state.get("factions", {}).get("version") != looks[1]:
            await self.apply_factions(guild, looks)
        welcome = self.docs.get("welcome")
        if welcome and self.state.get("welcome", {}).get("version") != welcome[1]:
            await self.publish_welcome(guild)
        self.posts = await asyncio.to_thread(read_posts, self.path)
        applied = self.state.setdefault("posts", {})
        for post_id, (doc, version) in self.posts.items():
            if applied.get(post_id, {}).get("version") != version:
                await self.publish_post(guild, post_id, doc, version)
        await self.link_actions(guild)
        ping = self.docs.get("matchping")
        if ping:
            from bot.discord import server_notifications
            server_notifications.MATCH_PING.clear()
            server_notifications.MATCH_PING.update(ping[0])
            self.state["matchping"] = {"version": ping[1], "at": int(time.time()), "problems": []}
        bans = self.docs.get("bans")
        if bans:
            # The ban DMs and ticket cards read these as they go; nothing to redraw.
            self.state["bans"] = {"version": bans[1], "at": int(time.time()), "problems": []}
        greeting = self.docs.get("greeting")
        if greeting:
            self.state["greeting"] = {"version": greeting[1], "at": int(time.time()), "problems": []}
        self._write_bridge(guild)

    async def link_actions(self, guild):
        links = getattr(self.bot, "account_links", None)
        if links is None:
            return
        from bot.discord.link_review import panel_decision
        done = self.state.setdefault("links", {"last": 0, "results": {}})
        for action_id, kind, target, identity, by in await asyncio.to_thread(
                read_link_actions, self.path, done["last"]):
            try:
                worked, text = await panel_decision(self.bot, guild, kind, target, identity, by)
            except Exception:
                LOG.exception("Link action %s from OYB Control failed", action_id)
                worked, text = False, "Something went wrong; the bot's log has the details."
            LOG.info("Link action %s (%s %s by %s): %s", action_id, kind, target, by, text)
            done["last"] = action_id
            done["results"][str(action_id)] = {"ok": worked, "text": text, "at": int(time.time())}
        for old in sorted(done["results"], key=int)[:-50]:
            del done["results"][old]

    def _link_report(self, guild):
        links = getattr(self.bot, "account_links", None)
        if links is None:
            return None

        def who(discord_id, fallback):
            member = guild.get_member(discord_id)
            return member.display_name if member else (fallback or "")
        pending = [{"token": t, "discord_id": str(d), "member": who(d, n2), "identity": i, "name": n,
                    "created": int(c)} for t, d, i, n, n2, c in links.waiting(guild.id)]
        linked = [{"discord_id": str(d), "member": who(d, n2), "identity": i, "name": n or "",
                   "at": int(a), "how": v} for d, i, a, v, n, n2 in links.linked(guild.id)]
        return {"pending": pending, "linked": linked}

    async def apply_names(self, guild, found):
        """Everywhere a server's name shows: the stat channel, the #servers
        card, the notification buttons; match posts pick it up as they go."""
        self._use_names(found)
        problems = []
        stats = getattr(self.bot, "server_stats", None)
        if stats is not None:
            try:
                await stats.tick()
            except discord.HTTPException:
                problems.append("Couldn't rename the SERVER STATUS channels yet; the bot will keep trying.")
        refresh = getattr(self.bot, "refresh_servers", None)
        if refresh is not None:
            await refresh()
        channel = getattr(self.bot, "notification_channel", None)
        if channel is not None:
            try:
                from bot.discord.notification_roles import prepare_notifications
                await prepare_notifications(self.bot, guild, channel)
            except discord.HTTPException:
                problems.append("Couldn't update the match-notification buttons; check Manage Roles.")
        self.state["names"] = {"version": found[1], "at": int(time.time()), "problems": problems}
        LOG.info("Server names now %s", found[0].get("names", {}))

    async def apply_factions(self, guild, found):
        """Rename and recolour the faction roles and redraw the faction picker."""
        factions.LOOK.clear()
        factions.LOOK.update(found[0]["factions"])
        try:
            problems = await ensure_faction_roles(self.bot, guild)
            await factions.refresh_picker(self.bot, guild)
        except discord.HTTPException:
            LOG.exception("Couldn't update the faction roles")
            problems = ["Couldn't update the faction roles; the bot needs Manage Roles."]
        self.state["factions"] = {"version": found[1], "at": int(time.time()), "problems": problems}

    async def apply_serverinfo(self, found):
        from bot.discord import server_notifications
        server_notifications.SERVER_INFO.clear()
        server_notifications.SERVER_INFO.update(found[0])
        problems = []
        refresh = getattr(self.bot, "refresh_servers", None)
        if refresh is not None:
            await refresh()
        else:
            problems.append("The #servers card isn't set up on this bot.")
        self.state["serverinfo"] = {"version": found[1], "at": int(time.time()), "problems": problems}

    async def publish_post(self, guild, post_id, doc, version):
        """Post it, edit it in place, move it, or take it down."""
        old = self.state["posts"].get(post_id, {})
        record = {"version": version, "at": int(time.time()), "problems": [],
                  "channel_id": old.get("channel_id"), "message_id": old.get("message_id")}
        self.state["posts"][post_id] = record
        before = guild.get_channel(old.get("channel_id") or 0)
        if doc.get("deleted") or (old.get("channel_id") and old["channel_id"] != doc.get("channel_id")):
            if isinstance(before, discord.TextChannel) and old.get("message_id"):
                try:
                    await (await before.fetch_message(old["message_id"])).delete()
                except discord.HTTPException:
                    pass
            record["channel_id"] = record["message_id"] = None
            if doc.get("deleted"):
                return
        channel = guild.get_channel(doc.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            record["problems"].append("Pick a channel the bot can see and post in.")
            return
        for button in doc["buttons"]:
            if button["type"] in ("role", "pick"):
                _, why = await self._role(guild, button)
                if why:
                    record["problems"].append(f"The {button['label'] or button['emoji']} button: {why}")
        try:
            message = None
            if record["message_id"]:
                try:
                    message = await channel.fetch_message(record["message_id"])
                except discord.NotFound:
                    message = None
            message = await put_message(channel, message, doc, None, f"oyb:p:{post_id}:")
            record["channel_id"], record["message_id"] = channel.id, message.id
            LOG.info("Published post %s version %s in #%s", post_id, version, channel.name)
        except discord.HTTPException as exc:
            LOG.warning("Discord refused post %s: %s", post_id, exc.text)
            record["problems"].append(refused(doc, exc))

    async def publish_welcome(self, guild):
        doc, version = self.docs["welcome"]
        problems = []
        channel_id = doc.get("channel_id") or onboarding_channel_id()
        channel = guild.get_channel(channel_id) if channel_id else None
        record = {"version": version, "at": int(time.time()), "problems": problems,
                  "channel_id": channel_id, "message_id": self.state.get("welcome", {}).get("message_id")}
        if not isinstance(channel, discord.TextChannel):
            problems.append("Pick a channel the bot can see and post in.")
            self.state["welcome"] = record
            return
        if any(b["type"] in ("faction", "no_faction") for b in doc["buttons"]):
            try:
                await ensure_faction_roles(self.bot, guild)
            except discord.HTTPException:
                problems.append("Couldn't set up the faction roles; the bot needs Manage Roles.")
        for button in doc["buttons"]:
            if button["type"] in ("role", "pick"):
                role, why = await self._role(guild, button)
                if why:
                    problems.append(f"The {button['label'] or button['emoji']} button: {why}")
        try:
            message = await self._find_message(guild, channel, record["message_id"])
            message = await put_message(channel, message, doc, MARKER, PREFIX)
            record["message_id"], record["channel_id"] = message.id, channel.id
            LOG.info("Published Start here version %s in #%s", version, channel.name)
        except discord.HTTPException as exc:
            LOG.warning("Discord refused Start here: %s", exc.text)
            problems.append(refused(doc, exc))
        self.state["welcome"] = record
        self._write_bridge(guild)

    async def _find_message(self, guild, channel, message_id):
        old = self.state.get("welcome", {})
        if message_id and old.get("channel_id") and old["channel_id"] != channel.id:
            # Moved to another channel: take the old one down.
            before = guild.get_channel(old["channel_id"])
            if isinstance(before, discord.TextChannel):
                try:
                    await (await before.fetch_message(message_id)).delete()
                except discord.HTTPException:
                    pass
            message_id = None
        if message_id:
            try:
                return await channel.fetch_message(message_id)
            except discord.HTTPException:
                pass
        async for message in channel.history(limit=50):
            if message.author.id == self.bot.user.id and any(
                    e.footer and e.footer.text == MARKER for e in message.embeds):
                return message
        return None

    async def _role(self, guild, button):
        """The button's role, made if it's new. (role, problem)."""
        role = guild.get_role(button["role_id"]) if button.get("role_id") else None
        if role is None and button.get("role_name"):
            role = discord.utils.get(guild.roles, name=button["role_name"])
            if role is None:
                try:
                    role = await guild.create_role(name=button["role_name"], permissions=discord.Permissions.none(),
                                                   mentionable=False, reason="OYB Control button role")
                except discord.HTTPException:
                    return None, f"couldn't create the {button['role_name']} role; the bot needs Manage Roles"
        if role is None:
            return None, "its role no longer exists; pick another"
        why = safe_role(guild, role)
        return role, (f"can't hand out {role.name}: {why}" if why else None)

    # Buttons

    async def handle(self, interaction):
        custom_id = (interaction.data or {}).get("custom_id", "")
        if interaction.type != discord.InteractionType.component or not custom_id.startswith((PREFIX, "oyb:p:")):
            return False
        if interaction.guild_id != self.bot.config.guild_id:
            await say(interaction, "Use this in the OYB server.")
            return True
        if custom_id.startswith("oyb:p:"):
            post_id, _, button_id = custom_id[6:].partition(":")
            found = self.posts.get(post_id)
            button = next((b for b in found[0]["buttons"] if b["id"] == button_id), None) if found else None
            if button is None or button["type"] not in ("link", "progress", "role", "pick"):
                await say(interaction, "That button has been changed. Scroll up to the latest message.")
                return True
            if button["type"] == "link":
                from bot.discord.join_oyb import LinkModal
                await interaction.response.send_modal(LinkModal(self.bot))
            elif button["type"] == "progress":
                await ack(interaction)
                await say(interaction, progress(self.bot, interaction.guild, interaction.user))
            else:
                await ack(interaction)
                await say(interaction, await self._press_role(interaction, button, found[0]))
            return True
        found = self.docs.get("welcome")
        button = next((b for b in found[0]["buttons"] if b["id"] == custom_id[len(PREFIX):]), None) if found else None
        if button is None:
            await say(interaction, "That button has been changed. Scroll up to the latest message.")
            return True
        kind = button["type"]
        if kind == "link":
            from bot.discord.join_oyb import LinkModal
            await interaction.response.send_modal(LinkModal(self.bot))
        elif kind == "progress":
            await ack(interaction)
            await say(interaction, progress(self.bot, interaction.guild, interaction.user))
        elif kind == "faction":
            await pick_faction(self.bot, interaction, button["faction"])
        elif kind == "no_faction":
            await skip_faction(self.bot, interaction)
        elif kind in ("role", "pick"):
            await ack(interaction)
            await say(interaction, await self._press_role(interaction, button, found[0]))
        return True

    async def _press_role(self, interaction, button, doc):
        guild, member = interaction.guild, interaction.user
        if button["linked_only"] and not self.bot.account_links.identities(guild.id, member.id):
            return "Link your Reforger account first, then press this again."
        role, why = await self._role(guild, button)
        if why:
            LOG.warning("Button %s: %s", button["id"], why)
            return "That button isn't working right now. Give an admin a shout."
        try:
            if button["type"] == "role":
                if role in member.roles:
                    await member.remove_roles(role, reason="OYB button")
                    return f"Removed **{role.name}**."
                await member.add_roles(role, reason="OYB button")
                return f"You've got **{role.name}**. Press again to remove it."
            group = [b for b in doc["buttons"] if b["type"] == "pick" and b["group"] == button["group"]]
            others = []
            for other in group:
                if other["id"] != button["id"]:
                    other_role, other_why = await self._role(guild, other)
                    if other_role is not None and not other_why and other_role in member.roles:
                        others.append(other_role)
            locked = any(b["locked"] for b in group)
            if role in member.roles:
                if locked:
                    return f"You're locked to **{role.name}**. Ask an admin if you need to change."
                await member.remove_roles(role, reason="OYB button")
                return f"Removed **{role.name}**."
            if others and locked:
                return f"You're locked to **{others[0].name}**. Ask an admin if you need to change."
            if others:
                await member.remove_roles(*others, reason="OYB button: swapped in group")
            await member.add_roles(role, reason="OYB button")
            extra = f" (swapped from {', '.join(r.name for r in others)})" if others else ""
            return f"You've got **{role.name}**{extra}." + (" That's locked in now." if locked else "")
        except discord.HTTPException:
            return "I couldn't change your roles. Give an admin a shout."

    # Join greeting

    async def greet(self, member):
        if member.bot or member.guild.id != self.bot.config.guild_id:
            return
        found = await asyncio.to_thread(read_doc, self.path, "greeting") or self.docs.get("greeting")
        if not found:
            LOG.info("%s joined; no greeting has been published", member.display_name)
            return
        doc = found[0]
        if not doc.get("enabled") or not doc.get("text"):
            LOG.info("%s joined; the greeting is turned off", member.display_name)
            return
        start = self.state.get("welcome", {}).get("channel_id") or onboarding_channel_id()
        text = welcome_doc.fill_greeting(doc["text"], member.mention, member.display_name, member.guild.name,
                                         member.guild.member_count or 0, f"<#{start}>" if start else "Start here")
        mentions = discord.AllowedMentions(users=[member], roles=False, everyone=False)
        try:
            if doc["where"] == "dm":
                await member.send(text, allowed_mentions=mentions)
                LOG.info("Greeted %s by DM", member.display_name)
            else:
                channel = member.guild.get_channel(doc.get("channel_id") or 0)
                if not isinstance(channel, discord.TextChannel):
                    LOG.warning("%s joined; can't find the greeting channel %s", member.display_name,
                                doc.get("channel_id"))
                    return
                await channel.send(text, allowed_mentions=mentions)
                LOG.info("Greeted %s in #%s", member.display_name, channel.name)
        except discord.HTTPException as exc:
            LOG.warning("Couldn't greet %s: %s (DMs closed, or the bot can't post there)", member.display_name,
                        exc.text or exc.status)

    # What the panel reads back

    def _load_state(self):
        try:
            data = json.loads(self.bridge.read_text())
            return {k: data[k] for k in ("welcome", "greeting", "names", "serverinfo", "bans", "posts", "matchping", "links", "factions")
                    if isinstance(data.get(k), dict)}
        except (OSError, ValueError):
            return {}

    def _write_bridge(self, guild):
        me = guild.me
        roles = []
        for role in sorted(guild.roles, key=lambda r: -r.position):
            if role.is_default() or role.managed:
                continue
            roles.append({"id": str(role.id), "name": role.name, "colour": f"#{role.colour.value:06x}",
                          "problem": safe_role(guild, role)})
        channels = [{"id": str(c.id), "name": c.name, "category": c.category.name if c.category else ""}
                    for c in guild.text_channels]
        servers = [{"id": s.id, "default": server_stats.default_label(s), "label": server_stats.label_for(s),
                    "enabled": s.enabled, "settings": getattr(s, "settings", "")}
                   for s in getattr(self.bot.config, "servers", [])]
        from bot.discord import server_notifications
        configured = [s for s in getattr(self.bot.config, "servers", []) if hasattr(s, "rules")]
        card = {"title": server_notifications.SERVERS_TITLE, "intro": server_notifications.SERVERS_INTRO,
                "rules_title": server_notifications.RULES_TITLE,
                "rules": server_notifications.default_rules(self.bot) if configured else ""}
        data = {"updated": int(time.time()), "guild": guild.name, "roles": roles, "channels": channels,
                "servers": servers, "card": card,
                "categories": [{"id": str(c.id), "name": c.name} for c in getattr(guild, "categories", [])],
                "ban_settings": _ban_settings(self.path),
                "settings_now": settings_now(self.bot),
                "member_events": bool(self.bot.intents.members),
                "member_role": member_role_name(),
                "start_channel": str(onboarding_channel_id() or ""),
                "link_requests": self._link_report(guild),
                **{k: v for k, v in self.state.items()}}
        try:
            self.bridge.parent.mkdir(parents=True, exist_ok=True)
            partial = self.bridge.with_name(self.bridge.name + ".part")
            partial.write_text(json.dumps(data))
            partial.replace(self.bridge)
        except OSError:
            LOG.warning("Couldn't write %s for OYB Control", self.bridge)


def _ban_settings(path):
    from bot.discord.ban_roles import ban_settings
    settings = dict(ban_settings(path))
    settings["ticket_channel"] = str(settings["ticket_channel"] or "")
    settings["ticket_categories"] = [str(c) for c in settings["ticket_categories"]]
    return settings


def settings_now(bot):
    """The channels and roles the bot is using right now, as the panel shows them."""
    from bot import config
    now = {key: os.getenv(key, "") for key, _ in welcome_doc.CHANNEL_SETTINGS}
    for key, read in (("ONBOARDING_CHANNEL_ID", config.onboarding_channel_id),
                      ("LIVE_BOARD_CHANNEL_ID", config.live_board_channel_id),
                      ("GAME_LEADERBOARD_CHANNEL_ID", config.game_leaderboard_channel_id),
                      ("LEADERBOARD_CHANNEL_ID", config.leaderboard_channel_id),
                      ("FACTION_CHANNEL_ID", config.faction_channel_id)):
        now[key] = str(read() or "")
    alerts = getattr(bot, "announce_channel", None)
    if alerts is not None and not now["MATCH_ALERT_CHANNEL_ID"]:
        now["MATCH_ALERT_CHANNEL_ID"] = str(alerts.id)
    now["MEMBER_ROLE_NAME"] = config.member_role_name()
    now["UNVERIFIED_ROLE_NAME"] = config.unverified_role_name()
    return now
