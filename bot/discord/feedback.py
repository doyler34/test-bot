"""Feedback from members. A Give feedback button opens a short form; what they
write goes to a staff channel, where staff can reply to them by DM or mark it
done, which thanks them."""
import logging
import os
import time
from datetime import datetime, timezone

import discord

from bot.discord import welcome_doc
from bot.discord.interactions import say
from bot.discord.onboarding import by_name

LOG = logging.getLogger("reforger.feedback")
PREFIX = "oyb:fb:"
COOLDOWN = 300
OPEN, DONE = 0xD9A441, 0x3BA55D
# The Feedback page as published from OYB Control.
SETTINGS: dict = {}
_sent = {}


def settings():
    return {**welcome_doc.default_feedback(), **SETTINGS}


def staff_channel(guild):
    channel_id = os.getenv("FEEDBACK_CHANNEL_ID", "").strip()
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    return channel if isinstance(channel, discord.TextChannel) else None


def wait_left(member_id, now=None):
    return max(0, int(_sent.get(member_id, 0) + COOLDOWN - (now or time.time())))


class FeedbackForm(discord.ui.Modal, title="Give feedback"):
    """The topic is picked from the list set on the Feedback page, or typed when there's no list."""

    def __init__(self, bot, topics=()):
        super().__init__()
        self.bot = bot
        if topics:
            self.topic = discord.ui.Select(placeholder="Pick one",
                                           options=[discord.SelectOption(label=t) for t in topics[:25]])
        else:
            self.topic = discord.ui.TextInput(max_length=100, placeholder="A server, a rule, the Discord, an admin...")
        self.add_item(discord.ui.Label(text="What's it about?", component=self.topic))
        self.text = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=1500)
        self.add_item(discord.ui.Label(text="Your feedback", component=self.text))

    def chosen(self):
        if isinstance(self.topic, discord.ui.Select):
            return self.topic.values[0] if self.topic.values else ""
        return str(self.topic)

    async def on_submit(self, interaction):
        await say(interaction, await submit(interaction.guild, interaction.user, self.chosen(), str(self.text)))


class ReplyForm(discord.ui.Modal, title="Reply by DM"):
    text = discord.ui.TextInput(label="Your reply", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, member_id, message):
        super().__init__()
        self.member_id, self.message = member_id, message

    async def on_submit(self, interaction):
        await say(interaction, await reply(interaction, self.member_id, self.message, str(self.text)))


async def open_form(bot, interaction):
    if not settings()["on"]:
        await say(interaction, "Feedback is closed right now.")
    elif staff_channel(interaction.guild) is None:
        LOG.warning("Someone pressed Give feedback but no feedback channel is picked (Discord → Feedback)")
        await say(interaction, "Feedback isn't set up yet. Give an admin a shout.")
    elif wait_left(interaction.user.id):
        await say(interaction, f"You've just sent some. You can send more in {wait_left(interaction.user.id) // 60 + 1} min.")
    else:
        await interaction.response.send_modal(FeedbackForm(bot, settings()["topics"]))


def staff_view(member_id):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Reply by DM", emoji="✉️", style=discord.ButtonStyle.primary,
                                    custom_id=f"{PREFIX}reply:{member_id}"))
    view.add_item(discord.ui.Button(label="Done", emoji="✅", style=discord.ButtonStyle.success,
                                    custom_id=f"{PREFIX}done:{member_id}"))
    return view


async def submit(guild, member, topic, text):
    channel = staff_channel(guild)
    if channel is None:
        return "Feedback isn't set up yet. Give an admin a shout."
    found = settings()
    embed = discord.Embed(title=f"💬 {topic.strip() or 'Feedback'}"[:256], description=text.strip()[:4000],
                          colour=OPEN, timestamp=datetime.now(timezone.utc))
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.add_field(name="From", value=f"{member.mention} ({member})", inline=False)
    embed.set_footer(text="Feedback")
    role = by_name(guild, found["ping_role"]) if found["ping_role"] else None
    try:
        await channel.send(role.mention if role else None, embed=embed, view=staff_view(member.id),
                           allowed_mentions=discord.AllowedMentions(roles=[role] if role else False,
                                                                    everyone=False, users=False))
    except discord.HTTPException as exc:
        LOG.warning("Couldn't post feedback in #%s: %s", channel.name, exc.text or exc.status)
        return "That didn't go through. Try again in a bit."
    _sent[member.id] = time.time()
    LOG.info("Feedback from %s: %s", member.display_name, topic)
    return found["thanks"]


def topic_of(message):
    title = message.embeds[0].title if message.embeds else ""
    return (title or "").removeprefix("💬 ").strip() or "your feedback"


async def dm(guild, member_id, **kwargs):
    member = guild.get_member(member_id)
    if member is None:
        return False
    try:
        await member.send(**kwargs)
        return True
    except discord.HTTPException:
        return False


async def reply(interaction, member_id, message, text):
    embed = discord.Embed(title=f"Reply about: {topic_of(message)}"[:256], description=text.strip(), colour=OPEN)
    embed.set_footer(text=f"From the {interaction.guild.name} staff")
    sent = await dm(interaction.guild, member_id, embed=embed)
    note = message.embeds[0] if message.embeds else discord.Embed()
    if len(note.fields) < 24:
        name = f"Reply from {interaction.user.display_name}" + ("" if sent else " (not delivered: DMs closed)")
        note.add_field(name=name[:256], value=text.strip()[:1024], inline=False)
        try:
            await message.edit(embed=note)
        except discord.HTTPException:
            pass
    return "Sent to them by DM." if sent else "Couldn't DM them (they've left, or their DMs are closed)."


async def mark_done(interaction, member_id):
    message = interaction.message
    thanks = settings()["done_dm"].replace("{topic}", topic_of(message)).strip()
    sent = await dm(interaction.guild, member_id, content=thanks) if thanks else False
    note = message.embeds[0] if message.embeds else discord.Embed()
    note.colour = DONE
    if len(note.fields) < 25:
        how = " and thanked them by DM" if sent else ""
        note.add_field(name="Done", value=f"By {interaction.user.mention}{how}, <t:{int(time.time())}:R>", inline=False)
    await interaction.response.edit_message(embed=note, view=None)


async def press(bot, interaction):
    kind, _, member = (interaction.data or {}).get("custom_id", "")[len(PREFIX):].partition(":")
    if not member.isdigit():
        await say(interaction, "That button is broken.")
    elif kind == "reply":
        await interaction.response.send_modal(ReplyForm(int(member), interaction.message))
    elif kind == "done":
        await mark_done(interaction, int(member))
    else:
        await say(interaction, "That button is broken.")
