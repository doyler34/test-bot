"""When OYB Control picks a new channel for something the bot keeps posted,
take it down from the old channel and put it up in the new one."""
import logging

import discord

LOG = logging.getLogger("reforger.channel_moves")


async def delete_ours(bot, channel, marker):
    """Delete the bot's own message with this footer from that channel, if it's there."""
    if not isinstance(channel, discord.TextChannel):
        return
    async for message in channel.history(limit=50):
        if message.author.id == bot.user.id and any(e.footer and e.footer.text == marker for e in message.embeds):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            return


async def move_factions(bot, guild):
    from bot.config import faction_channel_id
    from bot.discord.factions import PICKER_MARKER, prepare_faction_picker
    from bot.discord.notification_roles import PANEL_MARKER, prepare_notifications
    target = guild.get_channel(faction_channel_id() or 0)
    if not isinstance(target, discord.TextChannel):
        LOG.warning("The faction channel %s is not a text channel I can see", faction_channel_id())
        return
    old = getattr(bot, "notification_channel", None)
    if old is not None and old.id != target.id:
        await delete_ours(bot, old, PICKER_MARKER)
        await delete_ours(bot, old, PANEL_MARKER)
    await prepare_faction_picker(bot, guild, target)
    await prepare_notifications(bot, guild, target)


async def move(bot, guild, key):
    if key == "SERVERS_CHANNEL_ID":
        await bot.prepare_servers(guild)
    elif key == "FACTION_CHANNEL_ID":
        await move_factions(bot, guild)
    elif key == "MATCH_ALERT_CHANNEL_ID":
        await bot.prepare_announcement_channel(guild)
    elif key == "LINK_REVIEW_CHANNEL_ID":
        from bot.discord.link_review import prepare_review_channel
        await prepare_review_channel(bot, guild)
    elif key == "LEADERBOARD_CHANNEL_ID":
        # It redraws, in the new channel, on its next pass.
        bot.leaderboard_display.reconcile_at = 0
