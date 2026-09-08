# OYB /stats — vanilla combat logs

`/stats` shows the invoking member's linked Reforger combat record.
`/stats user:@Member` looks up another approved member. The existing Join OYB
workflow and stable UUID are reused; display names and temporary playerID values
are never database keys. No new player database or gameplay mod is introduced.

## Permanent leaderboard

The bot creates or adopts `===OYB-LeaderBoard===` on startup (Discord may display
its normalized lowercase spelling). One pinned message shows position, approved
Reforger name, player kills and deaths. The original query and table renderer are
reused: 15 players per page, kills descending, deaths ascending, Discord ID for
stable ties. Approved linked players with recorded combat data are included.
No XP column or new XP behavior is added; XP-only changes cannot change this
combat table. All combat totals still combine the available server logs.

Everyone can use the shared Previous / Page indicator / Next buttons. Controls
have stable custom IDs and no expiry, and are registered once during setup.
The selected page survives restart and refresh, clamping to the final page if
players disappear. `/leaderboard` now responds privately with a channel link;
it never creates another public leaderboard. See [LEADERBOARD.md](LEADERBOARD.md)
for storage, recovery, permissions, rate limiting and deployment checks.

## Confirmed source and limits

The supplied VPS output contains timestamped vanilla kill records in:
`/root/reforger/profile/logs/logs_2026-09-07_14-44-37/console.log`.
They describe individual deaths, not cumulative snapshots. Two supplied records
identify a human victim killed by enemy AI. These produce two deaths, no human
kill credit, and no AI-kill claim.

The format is implemented by SCR_BaseGameMode.LogKillEvent. Its source rejects
victims without a player ID. Therefore **AI Kills displays Unavailable**. No
shots, accuracy, vehicles destroyed or AI-victim totals are inferred. Supporting
those requires a separately verified source of telemetry, potentially an addon.
This implementation does not claim to meet that part of the full stats request.

References checked during implementation:
- [Bohemia's API: LogKillEvent](https://community.bistudio.com/wikidata/external-data/arma-reforger/ArmaReforgerScriptAPIPublic/interfaceSCR__BaseGameMode.html)
- [Game source mirror: SCR_BaseGameMode](https://arexplorer.zeroy.com/_s_c_r___base_game_mode_8c_source.html)
- [Startup parameters](https://community.bistudio.com/wiki/Arma_Reforger%3AStartup_Parameters)

`-logStats 30000` already exists in the supplied service configuration; it emits
performance statistics, not these combat events. **No startup argument, profile,
service, Docker definition, game config or backend storage setting was changed.**
The bot follows each enabled server's existing log_dir automatically.

## Counting rules

- A recognised event with a valid human victim UUID adds one death.
- ENEMY with a distinct human killer UUID adds one player kill.
- TK with a distinct human killer UUID adds one teamkill, never an enemy kill.
- Suicide, AI killers and other incident types do not add player kills.
- Other recognised human death relations still count deaths, including GM events.
- Warning-level kill records use the same parser; unrelated warnings are ignored.
- Unknown or ambiguous event formats are skipped and logged, not guessed.

K/D is derived at display time: kills/deaths, or kills when deaths is zero,
always two decimal places. A linked player with no combat events sees an explicit
no-data message. Other fields may be zero only after a combat event identifies
that player. Values describe the available imported logs, not guaranteed lifetime
totals or complete engine telemetry. Lost/deleted logs cannot be reconstructed.

## Persistence and duplicate prevention

The existing ACCOUNT_LINKS_DB gains combat_events, combat_totals and
combat_sources. The migration uses the existing SQLite backup helper first,
creating a sibling `.before-combat-v1.sqlite3` backup. DDL runs in one transaction
and the migration is idempotent. Existing links, XP and rank tables are untouched.
No kills or deaths affect XP; the 1 XP / 600 seconds curve stays unchanged.

The event ledger is unique by server and a hash of dated millisecond timestamp,
victim UUID, killer UUID and incident relation. It deliberately excludes names,
log formatting and byte positions so duplicate entries and same-day copied logs
do not add kills again. Identical event keys are conservatively counted once.
Different servers are distinct event sources; public totals sum by player UUID.

The cursor, ledger entries and totals commit together. Restart/reprocessing and
duplicate appended lines cannot recount committed events. New server runs have
new dated folders; cumulative totals do not reset. Incomplete trailing lines wait
until completed. A changed cursor anchor or truncation pauses that source rather
than guessing a new timestamp epoch. Keep normal dated folders; moving old logs
into folders with invented dates is unsupported because lines lack their own date.

All available dated console logs are imported, oldest first, in bounded passes.
The worker polls every 15 seconds, processes at most eight advancing files per
enabled server per pass and at most 4 MiB per file. File parsing and database
writes use a worker thread with its own connection to the same database. Shutdown
waits for that worker to finish so a database connection cannot outlive its work.
An oversized source line pauses its file and logs an error. Backlogs need several
polls to finish; imports include any available history, not only the current match.

## Discord and verification

/stats shares the existing /rank command tree and startup sync. It uses an embed,
not a PNG, with Player Kills, Deaths, K/D, AI Kills and Teamkills. Only the AI field
is explicitly unavailable. No XP, rank, playtime or leaderboard is included.
Users need Use Application Commands; the bot needs Embed Links in that channel.
Use a normal chat channel; existing read-only information-channel policies remain.

Run `python -m unittest discover -s tests` for regression verification. Fixtures
cover repeated reads, duplicate records, added events, rotations, bot restarts,
renames, separate servers, rollback, partial writes, truncation, midnight,
teamkills, suicides, AI killers, no data, account links, K/D and unchanged XP.
Snapshot-delta tests are not applicable: no snapshot source is being ingested.

Local replay of the two supplied real records is evidence of parsing and database
deduplication, **not** a live VPS/Discord integration test. After deployment check
the journal for `OYB /stats command ready` and `Recorded ... new combat events`,
then invoke /stats. Restarting only the bot and checking again verifies live
checkpoint recovery. Do not restart or wipe the game to deploy this feature.
