# Production bot setup

On a Debian/Ubuntu VPS with systemd and Python 3.11+, clone this branch and run:

```bash
git clone https://github.com/doyler34/test-bot.git
cd test-bot
bash deploy/setup.sh
```

For an existing checkout, pull `main` first, then run the same setup command.
Setup installs Python dependencies only. It never installs Reforger, changes a
game config, opens game ports, restarts a game service or writes to game logs.
The older combined test-server installer is retired; existing game templates are
retained for reference. This installer requires existing Reforger console logs.

Prompts: hidden Discord token (reuse on rerun), guild ID, active server count
(1–3), log selection/manual fallback and display name per active server, then a
summary and confirmation before saving. Discovery examines accessible running
Reforger process profiles and bounded searches under /home, /root, /opt, /srv and
/var/lib. It requires dated logs_*/console.log folders and engine evidence.
If detection misses a custom location, select Manual and enter its logs directory.
Start the game server once if it has not produced logs yet.

The installer reuses an existing reforger-timer service account and checkout;
new installs use the invoking sudo user (or root if run directly as root).
All log, config and database checks run as that account. If access fails, grant
that account appropriate access to the bot checkout and read access to the log
directories, then retry. Setup never changes Reforger ownership or permissions.

It writes .env and servers.local.json (or preserves an existing configured file
location), keeps exactly three entries, enables full OYB mode and playtime, and
disables voice connections/A2S without prompting for their configuration.
Custom state database paths, settings and rules are retained. New active servers
reuse Vanilla defaults; edit settings later if the server differs. No rank or XP
thresholds are changed: XP remains combined across servers at the current test pace.

Config replacement requires the final confirmation. Previous config copies are
stored privately under .oyb-backups; token files are mode 600 and gitignored.
SQLite databases are never replaced, cleared or imported by setup. Keep backups
of the existing data directory. A failed setup retains data and reports failure;
configuration backups support recovery if startup fails after an approved save.

Existing unit drop-ins are preserved. They must not override User, WorkingDirectory
or runtime configuration inconsistently with the checkout. Setup runs application
config validation and the full unit suite before saving, verifies the service
unit, enables it at boot, then waits up to 90 seconds for the bot's current-run
channel, rank and playtime startup logs before reporting completion. This confirms
startup, not an actual in-game match or future Discord delivery.

The bot still needs Discord channel/message/role permissions and its role above
OYB rank roles. Promotion messages use an existing #log text channel; use
RANK_LOG_CHANNEL_ID only if a specific channel is needed. That ID is optional.

```bash
oyb status
oyb logs
oyb restart
oyb setup
```

Rerun `oyb setup` to enable Server 2/3 and choose distinct local log directories.
Updating/restarting the bot never resets XP or starts a new game.
