"""Award linked members XP for new Discord posts without reading message content."""
import asyncio
import logging
import sqlite3

import discord

LOG = logging.getLogger('reforger.message_xp')


async def award_message(bot, message):
    if (message.guild is None or message.guild.id != bot.config.guild_id or
            message.author.bot or message.webhook_id is not None or
            message.type not in (discord.MessageType.default, discord.MessageType.reply)):
        return
    # Bounded retries only; the transaction and message ID make retries idempotent.
    for attempt, delay in enumerate((0, 1, 3)):
        if delay:
            await asyncio.sleep(delay)
        try:
            awarded = bot.rank_sync.wallet.award_post(message.guild.id, message.author.id,
                                                      message.id, message.created_at.timestamp())
            if awarded:
                LOG.debug('Awarded %s post XP to Discord %s', awarded, message.author.id)
            return  # The existing 15-second rank loop applies roles and announcements.
        except sqlite3.Error:
            LOG.warning('Post XP database unavailable for message %s (attempt %s/3)',
                        message.id, attempt + 1, exc_info=True)
        except ValueError:
            LOG.exception('Post XP identity mismatch for Discord %s; admin review required', message.author.id)
            return
    LOG.error('Post XP not recorded for message %s after three attempts', message.id)
