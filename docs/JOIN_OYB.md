# Join OYB account linking

Notification mode now provisions a read-only `join-oyb` channel automatically.
It contains one permanent card with Link Reforger account, My link status, and
Admin: review requests buttons. Existing game-server channels and timers remain.

Members first join a tracked OYB game server, then submit their exact in-game
name in the private form. The bot resolves the name to a game IdentityId in
PLAYTIME_DB. Unknown or ambiguous names cannot be submitted automatically;
the member must resolve the name with an admin and reconnect as necessary.
Changing a name after linking does not change the saved identity association.

Admins with Manage Server (or Administrator) click Admin: review requests. The
private list shows up to 25 pending requests, oldest first; reopen it to see
more after processing them. Select a request to see the Discord account, name
and stable game identity. Confirm ownership with the player in-game before
approving. This is manual verification: a matching name alone is not proof.
Members use My link status to see approval or rejection; the bot sends no DMs.

Links are one-to-one per Discord guild. Duplicate claims cannot replace an
existing link. Submitting a new request invalidates that member's old pending
request; stale review buttons cannot approve it. Approval and request state are
committed together. Account transfers/unlinking require a future admin workflow;
this release never silently replaces links.

State is stored in `account_links.sqlite3` beside NOTIFICATION_STATE_DB by
default (normally `data/account_links.sqlite3`). ACCOUNT_LINKS_DB overrides it.
Back up this database alongside the playtime database. Bot restarts and game
save wipes do not remove links or tracked time. The game logs and saves are never
written by the linking workflow.

This release links accounts for future XP ranks. It does not award XP, change
Discord rank roles or grant gameplay benefits. Existing playtime remains in
PLAYTIME_DB keyed by game identity, so it can be associated with an approved
Discord account without moving/resetting totals.

Deploy by pulling the branch and restarting reforger-timer. No extra Python
packages or privileged gateway intents are required. Test with your own player:
submit, check pending status, approve as admin, and check linked status again.
