# Reforger → Discord session timer

Turns a Discord voice channel (e.g. **SERVER TIME**) into a live match timer for an
**unmodded** Arma Reforger dedicated server. When a match starts, the bot updates the
channel's status with match uptime; when it ends, crashes, or restarts, it clears the
status. By default it never joins voice, so it does not appear in the channel's
participant list — no Reforger mod required.

## How it works

The bot watches the server's stock `console.log` and reacts to vanilla log lines
(regexes ported from [ReforgerJS](https://github.com/ZSU-GG-Reforger/ReforgerJS)):

| Signal | Log line | Action |
| --- | --- | --- |
| Session start | `SCR_BaseGameMode::OnGameStateChanged = GAME` | set status (optionally join VC) |
| Session end | `SCR_BaseGameMode::OnGameStateChanged = POSTGAME` | clear status (leave VC if enabled) |
| Liveness | `FPS: .., Mem: .. kB, Player: ..,` | feed the staleness watchdog |
| Restart/crash | a new `logs_*` session folder appears, or heartbeat goes stale | end session |

`SCR_BaseGameMode` cycles `GAME → POSTGAME → GAME` **within the same process**, so a
Conflict match can restart without the server process restarting — and the timer resets
correctly each time.

### What the timer represents

**Conflict match / scenario-session uptime** — time since the most recent `= GAME`
transition. Not merely process uptime; it resets on match end, server restart/crash, or a
stale log stream.

### Voice visibility and the timer

The default `JOIN_VOICE_CHANNEL=false` updates the native **Voice Channel Status**
(e.g. `🟢 LIVE · 02h 14m`) about once a minute without joining voice. The bot
does not appear under the voice channel, but remains a member of the Discord server.

Discord has no supported way to hide a connected voice participant. Setting a bot's
presence to Invisible only changes its online status; it does not hide it in voice.

Set `JOIN_VOICE_CHANNEL=true` to restore voice joining/leaving and connection timers
for members running [AllCallTimers](https://github.com/Max-Herbold/AllCallTimersDiscordPlugin)
(Vencord/BetterDiscord). In this mode the bot is visible in voice, muted and deafened.
Without a voice connection, there is no bot connection timer for that plugin to show.
Vanilla Discord has no native, everyone-visible per-second voice timer.

### Three game servers

This program monitors **one game server per running instance**. For three game servers,
run three separate instances with their own `.env` files, log paths, voice-channel IDs,
and optional A2S addresses. They can share a host if it can read all three sets of logs.
The supplied installer creates one timer service; it does not provision three instances.

With `JOIN_VOICE_CHANNEL=true`, three simultaneous voice channels in the **same Discord
server** require three separate bot accounts/tokens: a bot has one voice connection per
Discord server. Status-only mode avoids that voice limit, but this code still only
configures one game server/channel per instance. Supporting all three in one bot process
would require multi-server configuration and monitoring.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then fill in the values
```

Bot requirements in the Discord Developer Portal / server:
- Default status-only mode: grant **View Channel**, **Set Voice Channel Status**, and
  **Manage Channels** on the target voice channel. Discord requires Manage Channels
  to [set status while disconnected](https://docs.discord.com/developers/resources/channel#set-voice-channel-status).
- Optional voice mode: grant **View Channel**, **Connect**, and **Set Voice Channel Status**.
- Existing installations: grant **Manage Channels** before restarting with this update,
  or set `JOIN_VOICE_CHANNEL=true` to keep the original behaviour.
- No privileged gateway intents are required.

## Run

```bash
python main.py
```

## Deploy on a VPS

For a turnkey, one-command install on a fresh Debian/Ubuntu VPS (Reforger test server +
bot + systemd services), see **[DEPLOY.md](DEPLOY.md)**.

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | yes | Bot token |
| `GUILD_ID` | yes | Server ID |
| `VOICE_CHANNEL_ID` | yes | Voice channel to display the timer in |
| `JOIN_VOICE_CHANNEL` | no (false) | Join voice for connection timers; makes the bot visible in voice |
| `REFORGER_LOG_DIR` | yes | Readable path to `profile/logs` (or a single session folder) |
| `SESSION_STALE_SECONDS` | no (120) | End session if no heartbeat for this long |
| `STATUS_REFRESH_SECONDS` | no (60) | Voice-status refresh cadence |
| `A2S_HOST` / `A2S_PORT` | no | A2S liveness fallback when logs go stale |
| `LOG_LEVEL` | no (INFO) | Logging verbosity |

No database is used — session state is in-memory only.

## Deployment note (remote + shipped logs)

The bot only needs to *read* `console.log`. Point `REFORGER_LOG_DIR` at wherever your
log-shipping (SFTP sync, shared mount, syslog) delivers the server logs. Match start/end
is only as timely as the shipped logs; if shipping stalls, the watchdog ends the session
(the optional A2S check prevents false ends when the server is actually still up).

## Tests

```bash
python tests/test_parser.py     # or: pytest
```

## Limitations

- Per-second ticking for plugin-less users isn't possible natively; they see the
  ~1-minute Voice Channel Status text instead.
- `= GAME` / `= POSTGAME` come from `SCR_BaseGameMode` (Conflict and most stock modes). A
  scenario using a different game-mode class may not emit them — verify against your
  scenario's `console.log`.
- In optional voice mode, discord.py holds a silent idle voice connection and
  auto-reconnects. Default status-only mode does not open a voice connection.
