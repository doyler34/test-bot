# Reforger → Discord session timer

Turns a Discord voice channel (e.g. **SERVER TIME**) into a live match timer for an
**unmodded** Arma Reforger dedicated server. When a match starts, a bot joins the voice
channel; when it ends, crashes, or restarts, the bot leaves. The voice session therefore
mirrors match uptime — no Reforger mod required.

## How it works

The bot watches the server's stock `console.log` and reacts to vanilla log lines
(regexes ported from [ReforgerJS](https://github.com/ZSU-GG-Reforger/ReforgerJS)):

| Signal | Log line | Action |
| --- | --- | --- |
| Session start | `SCR_BaseGameMode::OnGameStateChanged = GAME` | join VC, set status |
| Session end | `SCR_BaseGameMode::OnGameStateChanged = POSTGAME` | clear status, leave VC |
| Liveness | `FPS: .., Mem: .. kB, Player: ..,` | feed the staleness watchdog |
| Restart/crash | a new `logs_*` session folder appears, or heartbeat goes stale | end session |

`SCR_BaseGameMode` cycles `GAME → POSTGAME → GAME` **within the same process**, so a
Conflict match can restart without the server process restarting — and the timer resets
correctly each time.

### What the timer represents

**Conflict match / scenario-session uptime** — time since the most recent `= GAME`
transition. Not merely process uptime; it resets on match end, server restart/crash, or a
stale log stream.

### About the Discord timer (important)

Vanilla Discord has **no** native, everyone-visible voice timer. The connection timer is
per-user and only the connected user sees their own duration. So:

- The bot **joining/leaving** the VC is the reliable on/off signal, and it drives the
  per-second timer shown by the [AllCallTimers](https://github.com/Max-Herbold/AllCallTimersDiscordPlugin)
  client plugin (Vencord/BetterDiscord) for members who run it.
- For everyone else (no plugin), the bot also sets the channel's native **Voice Channel
  Status** text (e.g. `🟢 Match live · 2h14m`), refreshed about once a minute. This is
  visible to all members with no plugin and no channel renaming.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then fill in the values
```

Bot requirements in the Discord Developer Portal / server:
- Invite with the **Connect** and **Set Voice Channel Status** permissions on the target
  voice channel (plus **View Channel**).
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
| `VOICE_CHANNEL_ID` | yes | Voice channel to sit in |
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
- discord.py is used (over discord.js) to keep this a single lightweight Python service; it
  holds a silent idle voice connection and auto-reconnects.
