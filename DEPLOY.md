# Deploy on a test VPS (from scratch)

This sets up a **throwaway 2-player test server + the bot together** on one Debian/Ubuntu
VPS, so you can confirm the timer works before pointing the bot at your real
128-player server. Designed to be run with a single paste — it prompts you for the three
Discord values and installs everything else automatically.

## One command

SSH into the VPS and paste:

```bash
cd ~ && git clone -b claude/full-repo-wipe-2ib85o https://github.com/doyler34/test-bot.git \
  && cd test-bot && bash deploy/setup.sh
```

It will:

1. Install SteamCMD + the Arma Reforger dedicated server (app `1874900`, anonymous — no game
   purchase needed) into `~/reforger`.
2. Write a minimal 2-player **Conflict Everon** config (BattlEye on, no mods).
3. Create the bot's Python environment.
4. **Prompt you** for `DISCORD_BOT_TOKEN`, `GUILD_ID`, `VOICE_CHANNEL_ID` and write `.env`
   (it fills in the log path and A2S automatically, and `chmod 600`s the file).
5. Install and start two `systemd` services: `reforger-server` and `reforger-timer`, both set
   to auto-restart and start on boot.

The game server takes a minute or two to boot; once the scenario reaches the `GAME` state the
bot joins the voice channel and sets its status.

## Getting the Discord values

- **Token:** [Developer Portal](https://discord.com/developers/applications) → your app →
  **Bot** → *Reset Token* → copy.
- **Invite the bot** with **Connect** + **Set Voice Channel Status** (+ **View Channel**):
  OAuth2 → URL Generator → scope `bot`, tick those permissions, open the URL.
- **GUILD_ID / VOICE_CHANNEL_ID:** enable Discord *Settings → Advanced → Developer Mode*,
  then right-click the server / the voice channel → **Copy ID**.

## Check it's working

```bash
sudo systemctl status reforger-server reforger-timer
journalctl -u reforger-server -f     # watch the game server boot
journalctl -u reforger-timer  -f     # bot: look for "Session START detected"
ls ~/reforger/profile/logs/          # a logs_* folder with console.log should appear
```

## Firewall

Open **UDP 2001** (game) and **UDP 17777** (query). The script does this automatically if
`ufw` is active; if your provider has a separate security group/firewall, add them there too.

## Change scenario / player count

Re-run with overrides (the script keeps an existing config unless you delete it first):

```bash
rm ~/reforger/configs/server.json
MAX_PLAYERS=8 SERVER_NAME="My Test" bash ~/test-bot/deploy/setup.sh
```

Or edit `~/reforger/configs/server.json` directly, then `sudo systemctl restart reforger-server`.

## Point the bot at your real 128-player server later

You don't need the game server on that box — only the bot, reading the real server's logs:

- If the bot runs **on the same machine** as the real server, set `REFORGER_LOG_DIR` in
  `.env` to that server's `profile/logs` and run only `reforger-timer`.
- If the bot runs **elsewhere**, ship the real server's logs to a readable path (sshfs /
  rsync / syslog) and point `REFORGER_LOG_DIR` there. See the README's deployment note.

Then: `sudo systemctl disable --now reforger-server` (stop the test game server) and keep
`reforger-timer` running.

## Uninstall

```bash
sudo systemctl disable --now reforger-server reforger-timer
sudo rm /etc/systemd/system/reforger-server.service /etc/systemd/system/reforger-timer.service
sudo systemctl daemon-reload
rm -rf ~/reforger ~/test-bot
```
