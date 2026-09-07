"""Durable promotion announcements for the existing Discord log channel."""
from datetime import datetime, timezone
import logging
import os
import time

import discord
from rank_sync import RANKS

LOG = logging.getLogger("reforger.ranks")


class RankAnnouncements:
    def __init__(self, bot):
        self.bot, self.db = bot, bot.account_links.db
        # Seed existing progress only when this feature is first installed.
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='rank_announced'").fetchone()
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS rank_announced (
                guild INTEGER, member INTEGER, tier INTEGER, PRIMARY KEY(guild,member));
            CREATE TABLE IF NOT EXISTS rank_alerts (
                guild INTEGER, member INTEGER, tier INTEGER, xp INTEGER,
                queued REAL, channel INTEGER, message INTEGER,
                PRIMARY KEY(guild,member,tier));
        ''')
        if not exists:
            with self.db:
                self.db.execute('''INSERT INTO rank_announced
                    SELECT guild,member,MIN(CAST(seconds / 60 AS INTEGER),?) FROM rank_progress''',
                    (len(RANKS)-1,))

    def record(self, guild, member, tier, xp):
        """Called only after the desired role was successfully reconciled."""
        with self.db:
            row = self.db.execute("SELECT tier FROM rank_announced WHERE guild=? AND member=?",
                                  (guild, member)).fetchone()
            previous = row[0] if row else 0
            if tier > previous:
                self.db.execute("INSERT OR IGNORE INTO rank_alerts VALUES (?,?,?,?,?,NULL,NULL)",
                                (guild, member, tier, xp, time.time()))
            self.db.execute("INSERT OR REPLACE INTO rank_announced VALUES (?,?,?)",
                            (guild, member, max(previous, tier)))

    def channel(self, guild, saved):
        configured = os.getenv("RANK_LOG_CHANNEL_ID", "").strip()
        if saved or configured:
            channel = guild.get_channel(saved or int(configured))
        else:
            matches = [c for c in guild.text_channels if c.name == "log"]
            if len(matches) != 1:
                raise RuntimeError("Need one #log text channel, or set RANK_LOG_CHANNEL_ID")
            channel = matches[0]
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
            raise RuntimeError("Rank log channel must be a text channel in this Discord server")
        return channel

    async def flush(self, guild):
        rows = self.db.execute("SELECT member,tier,xp,queued,channel FROM rank_alerts WHERE guild=? AND message IS NULL ORDER BY queued",
                               (guild.id,)).fetchall()
        for member, tier, xp, queued, saved in rows:
            try:
                channel = self.channel(guild, saved)
                with self.db:
                    self.db.execute("UPDATE rank_alerts SET channel=? WHERE guild=? AND member=? AND tier=?",
                                    (channel.id, guild.id, member, tier))
                marker = f"OYB • Promotion • {guild.id}:{member}:{tier}"
                sent = None
                # Recover a send accepted by Discord just before a crash/timeout.
                async for message in channel.history(limit=None, after=datetime.fromtimestamp(queued-5, timezone.utc)):
                    if message.author.id == self.bot.user.id and any(e.footer.text == marker for e in message.embeds):
                        sent = message
                        break
                if sent is None:
                    embed = discord.Embed(title="🎉 OYB rank up!", colour=0x2ECC71,
                        description=f"You've reached **{RANKS[tier]}**!\n**{xp} XP** earned playing on OYB. Keep it up!")
                    embed.set_footer(text=marker)
                    sent = await channel.send(content=f"🎉 Congratulations <@{member}>!", embed=embed,
                        allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                            users=[discord.Object(id=member)], replied_user=False))
                with self.db:
                    self.db.execute("UPDATE rank_alerts SET message=? WHERE guild=? AND member=? AND tier=?",
                                    (sent.id, guild.id, member, tier))
            except Exception:
                LOG.exception("Promotion announcement pending; check #log permissions (View Channel, Send Messages, Embed Links, Read Message History)")
