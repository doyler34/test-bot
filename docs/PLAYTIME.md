# Experimental playtime test

Set `PLAYTIME_ENABLED=true` in `.env` and restart the existing timer service.
The tracker runs alongside the timer without needing RCON or new packages.
`PLAYTIME_DB` defaults to `data/playtime.sqlite3`; `PLAYTIME_SERVER_ID` defaults
to `server-1`. Back up the database to preserve totals. Keep it out of Git.

Run `.venv/bin/python -m bot.tracking.playtime_tracker report` from the bot directory for
saved totals. Reporting only reads the database; it does not start a second
tracker. No Discord accounts, roles, ranks, or XP are changed in this test.

On first activation, the current console.log is imported. Older server runs
are excluded. After activation, later log folders are processed, including
ones created during bot downtime, as long as those files are still present.
The parser requires the HH:MM:SS.mmm timestamps used by the existing timer.
It uses the observed Updating player identity mapping, RPL disconnect, and
FPS player count records. It stores account IDs, latest names, connected time,
and log progress, but no player IP addresses.

Time is credited between consecutive player events/heartbeats at most 120
seconds apart. Missing heartbeat gaps and mismatched counts clear uncertain
connections until another explicit identity mapping appears; this can
undercount. Idle time counts. Offline time is not estimated from wall clocks.
Truncated or rewritten files fail closed to avoid replaying awards; a new
server log folder can be tracked normally. Missing/deleted logs cannot be
recovered. Matching counts alone cannot detect every missed disconnect.
These are test totals, not a production XP ledger.

Live acceptance test:

1. Enable the tracker, join/spawn, play two minutes, then run the report.
2. Leave for one minute; the total should stop growing after buffered logs flush.
3. Rejoin for one minute; the same account should gain more time.
4. Restart only reforger-timer; totals must survive and not suddenly double.
5. When convenient, restart the game and rejoin; the same account must continue.

Check `journalctl -u reforger-timer --since '5 minutes ago' --no-pager` for
`reforger.playtime` messages. No mappings in the report means the live log
format needs checking before using totals for ranks.

Disable with `PLAYTIME_ENABLED=false` and restart the timer. The saved database
remains available. The standalone `watch` command is for development; do not
run it alongside the integrated tracker.
