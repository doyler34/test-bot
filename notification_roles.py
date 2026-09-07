"""Persistent self-service notification roles; no gameplay permissions."""
import discord
import time

NAMES = {"server-1": "Server One", "server-2": "Server Two", "server-3": "Server Three"}


def safe_role(role, guild):
    return (role is not None and not role.is_default() and not role.managed
            and role.permissions.value == 0 and guild.me is not None
            and guild.me.top_role > role)


async def prepare_role(bot, guild, server):
    bot.store.db.execute("CREATE TABLE IF NOT EXISTS notification_roles (server TEXT PRIMARY KEY, role INTEGER NOT NULL)")
    bot.store.db.commit()
    row = bot.store.db.execute("SELECT role FROM notification_roles WHERE server=?", (server.id,)).fetchone()
    role = guild.get_role(row[0]) if row else None
    if role is None:
        name = NAMES.get(server.id, server.name + " Notifications")
        matches = [r for r in guild.roles if r.name == name]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple notification roles named {name}; resolve duplicates")
        role = matches[0] if matches else await guild.create_role(
            name=name, permissions=discord.Permissions.none(), mentionable=True,
            reason="OYB opt-in match notifications")
    if not safe_role(role, guild):
        raise RuntimeError("Notification role must have no permissions and be below the bot role")
    if not role.mentionable:
        role = await role.edit(mentionable=True, reason="Allow opt-in match role mentions")
    with bot.store.db:
        bot.store.db.execute("INSERT OR REPLACE INTO notification_roles VALUES (?,?)", (server.id, role.id))
    bot.roles_by_server[server.id] = role
    return role


class NotificationView(discord.ui.View):
    def __init__(self, bot, server_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.server_id = server_id
        button = discord.ui.Button(label="Toggle match notifications", style=discord.ButtonStyle.primary,
                                   custom_id=f"oyb:match-notifications:{server_id}")
        button.callback = self.toggle
        self.add_item(button)
        check = discord.ui.Button(label="Check match time", style=discord.ButtonStyle.secondary,
                                  custom_id=f"oyb:match-time:{server_id}")
        check.callback = self.check_time
        self.add_item(check)

    async def check_time(self, interaction):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message("Use this button in the OYB server.", ephemeral=True)
            return
        started = self.bot.match_times.get(self.server_id)
        if started is None:
            text = "No live match is currently detected for this server."
        else:
            seconds = max(0, int(time.monotonic() - started[1]))
            hours, rest = divmod(seconds, 3600)
            minutes, seconds = divmod(rest, 60)
            elapsed = f"{hours}h {minutes:02d}m {seconds:02d}s" if hours else f"{minutes}m {seconds:02d}s"
            text = f"🟢 **Match time: {elapsed}**\nAs of this check. Started <t:{int(started[0])}:t>."
        await interaction.response.send_message(text, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    async def toggle(self, interaction):
        if interaction.guild_id != self.bot.config.guild_id:
            await interaction.response.send_message("Use this button in the OYB server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        # Serialize clicks and read fresh member roles so repeated clicks toggle
        # predictably without needing the privileged members gateway intent.
        async with self.bot.notification_role_lock:
            guild = interaction.guild
            role = self.bot.roles_by_server.get(self.server_id)
            role = guild.get_role(role.id) if role is not None else None
            if not safe_role(role, guild):
                await interaction.followup.send("An admin needs to put the bot role above the notification role and check its permissions.", ephemeral=True)
                return
            try:
                member = await guild.fetch_member(interaction.user.id)
                subscribed = any(r.id == role.id for r in member.roles)
                if subscribed:
                    await member.remove_roles(role, reason="Member opted out of match alerts")
                else:
                    await member.add_roles(role, reason="Member opted in to match alerts")
                state = "off" if subscribed else "on"
                await interaction.followup.send(f"Match notifications for {role.name}: **{state}**.",
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                await interaction.followup.send("Could not update your notification role. Please try again; an admin may need to check Manage Roles and the bot's role position.", ephemeral=True)
