# Permanent OYB leaderboard

The existing kills/deaths leaderboard now lives in one pinned message in
`===OYB-LeaderBoard===`. Discord can normalize the channel name to lowercase;
discovery is case-insensitive and also uses the saved ID and channel topic marker.
The table still contains position, approved Reforger name, player kills and deaths,
15 players per page. Names, totals, sort order and eligible approved links use the
original `standings()` query; there is no second player database or query.
XP calculations, rank thresholds, rank names and player statistics are unchanged.
This combat leaderboard has no XP column, so XP-only changes cause no message edit.

## State and startup

The existing notification SQLite database (`config.state_path`) gains a small
`leaderboard_display` table: `guild` primary key, `channel`, `message`, `page`,
`retry_at`. It holds display state only. Existing notification and player tables
are not rewritten. No Discord-generated ID is hardcoded.

The manager registers a `timeout=None` View with stable custom IDs in setup_hook.
One background task starts under the existing on_ready boot guard and is cancelled
with the bot's other jobs on shutdown. Reconnects do not create extra tasks.
Startup immediately discovers the channel using a fresh Discord channel inventory,
checks its overwrites, fetches the stored message, and renders the current data.
If the message is gone, channel history is searched for this bot's marked
leaderboards, preferring a pinned one before an unpinned orphan. Only when none
exists is a new message sent. Its ID is saved before pinning and cleanup. This also
recovers a successful send whose response or subsequent state write was interrupted.

The normal model is one running instance of this bot per guild. Do not run two
independent installations simultaneously against the same Discord channel.

## Shared pages and refreshes

Any member who can view the permanent message can click Previous or Next; there
is no owner restriction. The centre disabled button shows `Page N / M`. Navigation
acknowledges the interaction immediately, persists the selected page, and edits
the same message. The first/last boundaries are disabled. Automatic refreshes keep
the selected page, clamping it when the list shrinks. The controls remain registered
after restarts; they do not use expiring slash-command webhook tokens.

A local database check runs every 15 seconds. Changes to combat totals, approved
links or displayed names start a 30-second coalescing window. Further changes are
combined into that refresh instead of postponing it indefinitely. Every five
minutes, a separate reconciliation deadline checks channel permissions, message
existence, pinning and duplicate cleanup even when the data is unchanged. Each
refresh compares rendered embed content and button state to the actual Discord
message. Identical output is not edited. Off-page changes therefore do not edit
the current page unless the page count or visible output changes.

Database failures leave the current message intact and schedule a retry. A missing
channel or message is rediscovered/recreated on the next reconciliation or earlier
data-driven refresh. The selected page and retry deadline survive restart.

## Rate limits and failures

A shared lock serializes automatic refreshes and button edits. Rapid page clicks
are limited to one accepted change per two seconds; clicks while another refresh
is active receive a private busy response and do not queue more edits.
discord.py handles normal Discord rate-limit buckets and waits for retry-after.
If a 429, RateLimited, HTTP error or database failure escapes, the manager logs it
and schedules a bounded exponential backoff of 30 seconds to 15 minutes. A larger
Discord retry-after is always honoured, including exception retry_after, response
headers and JSON error text when available. Retry deadlines are persisted where
the state database is available. There is no immediate retry loop.

## Permissions and safe cleanup

Normal members can view/history-read and click buttons. @everyone and existing
ordinary role/member overwrites deny sending, threads, thread replies, reactions,
external stickers/apps, voice messages, polls and application commands. Explicit
ordinary role grants are neutralized so they cannot bypass the @everyone deny.
Existing moderator/admin overwrites are retained; roles with administrative or
moderation permissions are treated as staff. Administrator access still bypasses
channel overwrites. This is not intended to restrict administrators.

Give the bot **Manage Channels** and **Manage Roles** to create/configure the
channel and overwrites, plus **View Channel**, **Read Message History**, **Send
Messages**, **Embed Links**, **Manage Messages**, and **Pin Messages**. The latter
is a distinct permission in current Discord. No Administrator grant is required.
discord.py >=2.7 is required for current pin permissions and endpoints.

Cleanup only targets this bot's recognizable leaderboard messages in the dedicated
channel and this bot's pin notices referencing those messages. Human/admin
messages and unrelated bot messages are preserved. Cleanup is bounded to 20
messages per pass; large legacy backlogs finish across reconciliation passes.
Existing reactions on the managed message are cleared. No unrelated channels,
public slash-command messages elsewhere, or existing threads are purged.

References: [Discord pin permission](https://docs.discord.com/developers/resources/message#pin-message),
[Discord retry-after handling](https://docs.discord.com/developers/topics/rate-limits).

## Update and verify on the VPS

```bash
cd /root/test-bot &&
git pull --ff-only origin claude/full-repo-wipe-2ib85o &&
.venv/bin/pip install -r requirements.txt &&
sudo systemctl restart reforger-timer
sudo journalctl -u reforger-timer --since "2 minutes ago" --no-pager
```

No game-server restart or wipe is needed. Expected logs include persistent controls
registered, channel/message created or recovered, and leaderboard refreshed.
Unchanged checks are debug-level to avoid routine log noise.

1. Confirm one pinned leaderboard in the dedicated channel. Test buttons using a
   regular member account; sending messages/threads should be denied.
2. With more than 15 recorded linked players, select page 2. Restart only the bot
   and confirm the same message ID/page and working controls.
3. Record a supported combat event. Allow ingestion plus the debounce window
   (usually under one minute without a backlog/rate limit) and verify the totals.
4. Check that unchanged reconciliation cycles do not create/edit messages.
5. On a test guild, delete the managed message and allow up to five minutes for
   recovery. Do not delete the bot's data to test message recovery.

Local automated checks use real SQLite state and simulated Discord responses.
They cover startup, restart, normalized channel reuse, pinning, safe cleanup,
public buttons, page retention/clamping, coalescing, unchanged output, deleted
channels/messages, state-write interruptions, database outages and 429 retry
deadlines. They also verify the real combat query refreshes without modifying XP
or statistics. Live VPS permissions and Discord delivery must be verified after
deployment; local tests are not a live Discord integration test.
