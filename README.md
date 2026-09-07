# OYB Discord / Reforger bot

Run one bot alongside up to three existing Reforger servers on the same VPS.
It reads local game logs for match monitoring, categories, notifications and
playtime. Approved account links connect that playtime to Discord rank roles.
XP combines tracked time across all servers for the same Reforger IdentityId.

## Production setup

On the actual Debian/Ubuntu VPS (systemd, Python 3.11+), from this branch's checkout:

```bash
bash deploy/setup.sh
```

Setup asks for a hidden Discord token, guild ID, active server count, local log
selection and display names. It detects log candidates and offers manual entry.
The installer configures the full OYB mode with three server definitions; unused
servers stay disabled / Coming Soon. It installs only the bot and its Python
dependencies, preserving game files, bot databases and existing settings.

See [the setup guide](deploy/README.md) for requirements, reruns and troubleshooting.

```bash
oyb status
oyb logs
oyb restart
oyb setup
```

The existing systemd service name remains `reforger-timer`. No voice-channel ID,
voice connection, A2S configuration or remote agent is needed for this setup.
The bot needs Discord permissions to manage its channels, messages and roles;
its own role must be above its notification/rank roles.

## Configuration and state

The installer writes a private, gitignored `.env` and `servers.local.json`.
`SERVERS_CONFIG` selects full OYB mode and `PLAYTIME_ENABLED=true` enables tracking.
Active server entries use distinct local log directories containing Reforger
`logs_*/console.log` files. Existing custom settings/rules and database locations
are reused. Reruns show a summary and require confirmation before replacing config.

Preserve `data/notifications.sqlite3`, `data/playtime.sqlite3` and
`data/account_links.sqlite3` (or your configured equivalents) across updates.
Setup backs up replaced config, but never clears or replaces these databases.

Ranks retain their current testing pace: 10 XP per recorded minute, one rank per
10 XP. See [RANKS.md](RANKS.md). Promotions mention the member in the existing
`#log` text channel. Optional `RANK_LOG_CHANNEL_ID` selects a specific log channel.

## Match monitoring

Match state follows Reforger's `GAME` and `POSTGAME` log events, FPS heartbeats
and log rotation. Categories show approximate time, updated roughly every five
minutes. The information card contains the match-start timestamp. A bot restart
recovers the current logged match; a real new game starts a new match timer.
Game restarts and wipes do not clear accumulated player XP.

Match alerts mention the opt-in server notification role and expire 30 minutes
after match start. Historical expired matches do not generate fresh alerts.
Scenarios must emit the expected game-state/player lines for accurate tracking.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

The installer runs this full suite before saving configuration. VPS/systemd and
live Discord integration still require checks on the deployment machine.

The older single-server voice timer remains available in code when
`SERVERS_CONFIG` is absent, but production setup always selects full OYB mode.
