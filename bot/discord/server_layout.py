"""Conservative migration from three bot information channels to one."""
import logging
import discord
from bot.discord.category_timer import matches_category

LOG = logging.getLogger("reforger.layout")


async def remove_timer_categories(bot, guild):
    """Delete the retired per-server timer categories (SERVER ONE/TWO/THREE).

    Any channels inside them are moved out (ungrouped) first, never deleted, so
    an operator's own voice channels survive. The live match counter now lives on
    the SERVER STATS channels instead.
    """
    try:
        channels = await guild.fetch_channels()
    except discord.HTTPException:
        LOG.exception("Retiring server categories deferred; check Manage Channels")
        return
    categories = [c for c in channels if isinstance(c, discord.CategoryChannel)
                  and any(matches_category(c, server) for server in bot.config.servers)]
    for category in categories:
        for channel in [c for c in channels if getattr(c, "category_id", None) == category.id]:
            try:
                await channel.edit(category=None, sync_permissions=False,
                                   reason="OYB SERVER STATS replaces per-server categories")
            except discord.HTTPException:
                LOG.warning("Could not ungroup channel %s from a retired category", channel.id)
        try:
            await category.delete(reason="Replaced by SERVER STATS live channels")
            LOG.info("Removed retired server category %s", category.name)
        except discord.HTTPException:
            LOG.warning("Could not delete retired category %s; check Manage Channels", category.id)


async def cleanup_legacy_layout(bot, guild):
    try:
        channels = await guild.fetch_channels()
        shared = {c.id for c in bot.channels_by_server.values()}
        for server in bot.config.servers:
            topic = f"OYB • {server.id} • Settings, rules and match notifications"
            info_marker = f"OYB • {server.id.replace('-', ' ').title()} • In-game rules"
            for channel in channels:
                if not isinstance(channel, discord.TextChannel) or channel.id in shared or channel.topic != topic:
                    continue
                # Retain a channel until its durable alert queue finishes expiry.
                if bot.store.db.execute("SELECT 1 FROM announcements WHERE channel=? AND done=0", (channel.id,)).fetchone():
                    continue
                if channel.threads:
                    continue
                archived = [t async for t in channel.archived_threads(limit=1)]
                if archived:
                    continue
                safe = True
                async for message in channel.history(limit=None):
                    owned = message.author.id == bot.user.id and not message.attachments and any(
                        e.footer.text == info_marker or (e.footer.text == "OYB • Match notifications"
                        and e.title == f"🟢 {server.name} — match started") for e in message.embeds)
                    if not owned:
                        safe = False
                        break
                if safe:
                    await channel.delete(reason="OYB information migrated to shared servers channel")
                else:
                    LOG.warning("Keeping legacy channel %s because it contains other messages", channel.id)
        # Fetch again so recently deleted text channels don't make empty categories look occupied.
        channels = await guild.fetch_channels()
        occupied = {getattr(c, "category_id", None) for c in channels}
        keep = {c.id for c in bot.category_timers.channels.values()}
        for category in channels:
            if category.id in keep or category.id in occupied:
                continue
            if any(matches_category(category, s) for s in bot.config.servers):
                await category.delete(reason="Remove empty duplicate OYB server category")
    except discord.HTTPException:
        LOG.exception("Legacy layout cleanup deferred; check Manage Channels and Read Message History")
