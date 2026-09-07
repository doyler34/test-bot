# OYB test ranks

Approved links start at OYB Recruit (0 XP). Pending/rejected requests get no rank.
The bot automatically creates permissionless rank roles and awards 10 XP per
completed tracked minute, summed across the playtime database's servers.

| Rank | XP |
| --- | --- |
| OYB Recruit | 0 |
| OYB Private | 10 |
| OYB Corporal | 20 |
| OYB Sergeant | 30 |
| OYB Lieutenant | 40 |
| OYB Captain | 50 |
| OYB Major | 60 |
| OYB Colonel | 70 |

These deliberately fast thresholds are for testing. XP continues above Colonel.
Enable PLAYTIME_ENABLED=true and use notification mode (SERVERS_CONFIG).
Give the bot Manage Roles and put its highest role above all OYB rank roles.
Existing matching rank roles must have no permissions; setup rejects unsafe roles.
Rank roles are displayed separately in the member list; nicknames are not changed.

Existing approved members also start at zero on first activation. Their earlier
tracked time is preserved but excluded using a baseline saved in ACCOUNT_LINKS_DB.
The baseline waits for the tracker to finish importing historical logs. Missing
player data defers the baseline and keeps Recruit. XP follows recorded connected
time, not a wall-clock timer, so log cadence can delay a promotion. Offline members
earn no extra XP merely because the bot is running.

Keep both the account-links and playtime SQLite files across updates and game
wipes. Match restarts/wipes do not reset links or ranks. Bot restarts resume earned
XP and retry role changes. Only managed rank roles are replaced on promotion;
notification and other roles are preserved. Role changes are checked every 15s
and reconciled every five minutes even when the rank is unchanged.

If the playtime database is manually deleted/reset, existing XP is retained but
further progression pauses until recorded totals exceed the saved baseline plus
earned time; restore the database backup rather than resetting tracking files.
