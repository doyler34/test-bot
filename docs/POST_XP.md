# Discord posts + playtime XP

Approved linked members earn **1 XP per new Discord message** in the configured
OYB guild, added to existing playtime XP (1 per 600 connected seconds) and any
retained legacy balance. `/rank`, link status, rank roles and promotion messages
all use this same combined balance. Existing thresholds and combat stats are
unchanged. The permanent kills/deaths leaderboard remains a combat table.

Eligible posts include ordinary messages, replies and attachment/sticker posts
in channels or threads visible to the bot. No post cooldown is applied at this
test rate. Bots, webhooks, DMs, other guilds, system messages and slash-command
responses are excluded. Message edits and reactions do not award XP. Deleting a
post does not remove already awarded XP. Messages created before link approval
do not earn XP, including old events replayed after approval.

Counting starts after deployment and covers MESSAGE_CREATE events the bot receives.
There is no history import or guarantee for posts sent while the bot is offline.
No message text or attachment contents are read or saved. The default guild message
intent is sufficient; Message Content privileged intent is not required. The bot
must be able to view the channel/thread to receive its posts.

The existing account-links SQLite database gains `discord_post_events` (unique
Discord message ID, guild, author ID, linked identity, awarded XP, creation time)
and `discord_post_totals` (cached guild/member balance). A backup is created before
the additive migration. Awarding the ledger entry and total is one transaction,
so replayed events, retries and bot restarts cannot credit a message twice. No
playtime rows or legacy balances are overwritten by message awarding.

`XP_PER_POST = 1` in `bot/ranks/rank_rules.py` sets the current rate. Future rate changes
apply to new awards; already awarded post XP remains intact. Playtime has its
separate existing `SECONDS_PER_XP` setting.

The normal 15-second rank loop applies any resulting promotion and the existing
announcement queue posts congratulations in #log. Each post causes no Discord
API call. Transient database errors get at most three attempts, waiting 1 and 3
seconds; exhausted attempts log the unrecorded message ID. This is not an offline
message recovery queue. Keep the existing account-links database across updates.

After updating/restarting `reforger-timer`, use an approved linked account:
run `/rank`, send one normal message, then run `/rank` after its command cooldown.
The total should rise by 1 plus any newly earned playtime XP. Check rank roles
after about 15 seconds if that post crosses a threshold. Test edits/bot messages
to confirm they add nothing. Live Discord delivery must be checked after deployment.
