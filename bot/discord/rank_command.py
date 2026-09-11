"""Guild /rank command; identity resolution and rendering remain separate."""
import asyncio
from contextlib import closing
from io import BytesIO
import logging

import discord
from discord import app_commands
from bot.ranks.rank_card import render_card

LOG = logging.getLogger("reforger.rank_command")


class RankCommand:
    def __init__(self, bot):
        self.bot = bot
        self.slots = asyncio.Semaphore(2)
        self.tree = app_commands.CommandTree(bot)
        self.guild = discord.Object(id=bot.config.guild_id)
        self.command = app_commands.Command(name="rank", description="Show your OYB Reforger rank card", callback=self.show)
        app_commands.checks.cooldown(1, 10, key=lambda i: (i.guild_id, i.user.id))(self.command)
        self.command.error(self.error)
        self.tree.add_command(self.command, guild=self.guild)

    async def register(self):
        # Retry transient registration failures without taking tracking offline.
        for delay in (0, 30, 120, 300):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self.tree.sync(guild=self.guild)
                LOG.info("OYB /rank command ready")
                if self.tree.get_command('stats', guild=self.guild):
                    LOG.info("OYB /stats command ready")
                return
            except discord.HTTPException:
                LOG.exception("Could not register /rank; check applications.commands authorization")

    async def error(self, interaction, error):
        message = (f"Try /rank again in {error.retry_after:.0f} seconds."
                   if isinstance(error, app_commands.CommandOnCooldown)
                   else "Your rank card could not be loaded. Please try again shortly.")
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def show(self, interaction: discord.Interaction):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message("Use /rank in the OYB Discord server.", ephemeral=True)
            return
        identity = self.bot.account_links.lookup(interaction.guild_id, interaction.user.id)
        if not identity:
            await interaction.response.send_message(
                "Open **#join-oyb**, press **Link Reforger account**, and submit your in-game name. "
                "An admin must approve your link before you can use /rank.", ephemeral=True)
            return
        if not interaction.app_permissions.attach_files:
            await interaction.response.send_message("The bot needs **Attach Files** permission in this channel to show your card.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            async with self.slots:
                xp = self.bot.rank_sync.progress(interaction.user.id, identity)
                avatar = None
                try:
                    async with asyncio.timeout(5):
                        avatar = await interaction.user.display_avatar.replace(format="png", size=256).read()
                except (discord.HTTPException, OSError, TimeoutError):
                    LOG.debug("Avatar unavailable; using local portrait")
                data = await asyncio.to_thread(render_card, interaction.user.display_name, xp, avatar)
                with BytesIO(data) as buffer:
                    with closing(discord.File(buffer, filename="oyb-rank.png")) as attachment:
                        await interaction.followup.send(file=attachment, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            LOG.exception("Rank card failed for Discord %s", interaction.user.id)
            await interaction.followup.send("Your rank card could not be loaded. Please try again shortly.", ephemeral=True)
