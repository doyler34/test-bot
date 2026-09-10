# OYB community ranks and /rank

Use `/rank` in a Discord channel where application commands are allowed.
Approved Join OYB links resolve the caller's stable Reforger IdentityId; display
names are presentation only. Unlinked members are directed to the existing
admin-approval workflow in #join-oyb.

## Progression

| Rank | XP |
| --- | --- |
| OYB Renegade | 0 |
| OYB Recruit | 100 |
| OYB Private | 200 |
| OYB Corporal | 300 |
| OYB Sergeant | 400 |
| OYB Lieutenant | 500 |
| OYB Captain | 600 |
| OYB Major | 700 |

`rank_rules.py` is the single canonical definition. Major is the maximum rank,
but XP continues to accumulate. Progress is through the current rank: 347 XP
is Corporal, 47% toward Sergeant, with 53 XP remaining. Major has a full bar
and MAX RANK, without a next rank or remaining XP.

Total XP combines retained legacy credit, playtime XP, and **1 XP per new Discord
post** from an approved linked member. Posting rules and durable message-ID
deduplication are described in [POST_XP.md](POST_XP.md). Existing rank thresholds
remain unchanged; `XP_PER_POST` controls future post awards.

One playtime XP is earned per 600 connected seconds. Integer milliseconds retain partial
time across disconnects and restarts. All enabled servers share one balance.
Confirmed connection intervals are merged by IdentityId, so simultaneous
connections on two servers count once. Disabled servers contribute nothing new.
The same dated log timestamps on the VPS are assumed to use the same clock.
Bare console.log paths use a persisted file-date anchor; dated Reforger folders
are preferred for reliable historical alignment.

The existing parser still requires timestamped player mappings and regular
player evidence. Unknown connections, inconsistent player counts and gaps above
120 seconds are not guessed. Cursors, parser state, per-server totals and global
intervals commit together. Replays, bot restarts and repeated scans cannot award
the same interval twice. Truncated logs are refused rather than replayed.
Game restarts/wipes change match state, not cumulative XP.

## Existing XP migration

**Previously awarded test XP is retained**, as explicitly requested. Each legacy
rank_progress row becomes a wallet with:
- credit = previously earned whole test minutes × 10 XP;
- retained milliseconds = the unfinished test minute;
- a baseline against the playtime migration snapshot.

New time then earns 1 XP / 600 seconds. Capturing the baseline snapshot means
time accrued between the playtime migration and the first wallet read is kept.
Newly linked players without a legacy wallet use their recorded combined time
at the new rate. Pending/unapproved accounts receive no Discord rank.

Before migrations, SQLite's backup API writes sibling files named
`*.before-global-time-v2.sqlite3` and `*.before-rank-v2.sqlite3`. Migrations are
additive, transactional and run once; old tables and values remain intact.
The new playtime tables are `global_time`, `global_intervals`,
`global_time_meta` and `global_time_legacy`. The links database gains
`rank_wallet_v2`, `rank_roles_v2`, `rank_announced_v2` and `rank_alerts_v2`.

Historical per-server aggregates have no interval timestamps and cannot be
retroactively deduplicated. They are preserved as the migration snapshot;
overlap prevention applies to intervals recorded by the new tracker. Existing
awarded XP is never reduced to compensate. Unprocessed time before the migration
cutover is not awarded again. Keep both databases and their backups.
Missing/reset tracking data retains cached earned XP but can pause further
progression until the source catches up; restore backups instead of deleting data.

Roles are recalculated using the new thresholds, so an old test role may change
even though its XP is preserved. Only bot-managed rank assignments are removed;
notification and unrelated roles remain. Obsolete role definitions are retained.
The initial reconciliation is quiet. Later promotions mention the member in
the existing #log channel, with durable retry and send recovery.
Old promotion queues remain archived in their original tables to avoid sending
old-tier announcements under the new rank names.

## Runtime and permissions

Python 3.11+, discord.py, Pillow and resvg-py (listed in requirements.txt).
Install with `python -m pip install -r requirements.txt`.
No Cairo, external image service or runtime font downloads are needed.
Full OYB mode requires SERVERS_CONFIG and PLAYTIME_ENABLED=true, as configured
by the existing installer. No additional user IDs or tokens are required.

The bot registers a guild-only /rank command on startup. Discord authorization
must include applications.commands. Users need Use Application Commands in the
invocation channel; the bot needs Attach Files. Existing read-only information
channel policies remain in place, so a normal chat channel is suitable.
Role sync requires Manage Roles and a bot role above permissionless OYB ranks.
Promotions require View Channel, Send Messages, Embed Links and Read Message
History in #log (or the channel selected by RANK_LOG_CHANNEL_ID).
Do not enable privileged intents merely for this feature.

The command acknowledges promptly, limits requests to one per member per ten
seconds and runs at most two render jobs concurrently outside the event loop.
Only the avatar is fetched from Discord, with a five-second timeout and local
fallback. Cards are PNGs composed in memory and discarded after sending.
No per-user images are stored. Static artwork and fonts are cached.
A live Discord token is required to verify actual registration and delivery;
the automated tests use mocked Discord responses and real image rendering.

## Design and validation

Original SVG masters and font licenses live in assets/rank-card; see its README.
Edit the SVGs for backgrounds/frames/insignias, and rank_card.py for layout.
Progression does not depend on the artwork. Bundled open fonts cover Latin,
Greek and Cyrillic; unsupported glyphs degrade to the font's placeholder.
Long names shrink and then ellipsize. Invalid/missing avatars use a silhouette.

Run `python -m unittest discover -s tests`.
Generate seven development cards, including Unicode and long names:
`python dev/render_rank_previews.py --output /tmp/oyb-rank-previews`.
The generator verifies PNG dimensions and measured text bounds.
