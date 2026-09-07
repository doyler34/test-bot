# Three server notification channels

This optional mode uses one Discord bot for three read-only text channels.
It does not join voice or refresh a voice status every minute.

- **server-1-vanilla:** enabled; watches the existing REFORGER_LOG_DIR.
  Includes the supplied OYB in-game rules, condensed without changing their meaning.
  Only vanilla/unmodded is confirmed; unspecified settings are labelled accordingly.
- **server-2** and **server-3:** coming-soon information channels. Disabled game
  monitors mean no fake match announcements.

Each channel has one permanent settings/rules message. A detected GAME transition
posts a new match announcement with a relative start timestamp. The announcement
is deleted 30 minutes after it was posted, normally within the worker's five-second
check interval. Discord rate limits and downtime can delay deletion.

Members opt in or out with the **Toggle match notifications** button on each
information card. The bot creates Server One, Server Two and Server Three roles
with no permissions. Announcements mention only the matching role; no everyone,
here or user mentions are allowed. Confirmation is private to the clicking member.
Buttons are restored on startup. Delivery still depends on each member's Discord
and device settings, including suppression of role mentions.

The bot's highest role must be above the notification roles. The roles are
mentionable so the bot can ping them without broad Mention Everyone permission.
Other members may also mention those roles wherever they can send messages.

For an existing installation, run `.venv/bin/python update_server_rules.py`
after pulling this update and before restarting the bot. This backs up the local
JSON and changes only Server 1's rules to the shorter version.

## Enable on the existing VPS

Grant the bot **Manage Channels**, **Manage Roles** (channel permission overwrites),
**View Channel**, **Send Messages**, **Embed Links** and **Read Message History**.
Do not give Administrator just for this feature. Administrators and the server
owner can still write because Discord allows them to bypass channel overwrites.

Then run:

```bash
cd /root/test-bot && git pull --ff-only origin claude/full-repo-wipe-2ib85o && { test -f servers.local.json || cp servers.example.json servers.local.json; } && sed -i '/^[[:space:]]*SERVERS_CONFIG[[:space:]]*=/d' .env && printf '\nSERVERS_CONFIG=servers.local.json\n' >> .env && systemctl restart reforger-timer
```

The game server stays running. The three channels are created when the bot connects.
Existing unrelated channels are not repurposed, even if their names match.

## Add the future game servers

Edit servers.local.json. Set each future server's enabled value to true, add its
real readable log_dir, settings and rules, then restart reforger-timer. Each active
server needs its own log directory. Optional a2s_host and a2s_port must be supplied
together. Keep each id unchanged: it identifies the server's channel and announcements.

The JSON contains exactly three entries. Settings allow up to 1024 characters and
rules up to 4096. Keep local customisations in servers.local.json so updates do not
overwrite them.

## Restart behaviour

If PLAYTIME_ENABLED=true, each enabled server also runs the experimental
playtime tracker using its JSON server id. The existing server-1 totals carry
over when using the bundled configuration. Use the report command with
`--server server-2` or `--server server-3` for future servers. See PLAYTIME.md.

data/notifications.sqlite3 records channel IDs, information-message IDs, match
identities, announcement IDs and deletion deadlines. Keep this file when updating
or restarting; it is not committed to Git. Only run one instance against this
database and Discord setup.

An existing match less than 30 minutes old can be announced on first setup.
Already-recorded matches are not announced again after a bot restart, even if
their announcements have been deleted. Older recovered matches are not announced.
An unsent announcement older than 30 minutes is discarded rather than sent late.

The bot also checks its own channel history after an interrupted send to recover
a message accepted before its ID could be saved. Pending deletions resume after a
restart, including announcements whose deadlines passed while the bot was offline.
It never purges the channel or schedules deletion of the information message.

To return to the legacy voice-status timer, remove SERVERS_CONFIG from .env and
restart. First allow existing announcements to expire in notification mode: legacy
mode does not run its deletion worker. Created text channels and rules remain.

## Verification

```bash
journalctl -u reforger-timer -n 30 --no-pager
```

Look for “Ready: three read-only channels; 1 active game monitors”, followed by
“Discord accepted match announcement” on a new match and “Deleted expired
announcement” after the deadline.
