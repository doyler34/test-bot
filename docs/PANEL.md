# OYB Control (web panel)

A private web panel for running all three Reforger servers over RCON. Admins log
in with their own account; everything they do lands in the audit log.

It runs on the same box as the game servers, next to the bot, and only listens
on `127.0.0.1`. Caddy sits in front and gives it a domain with HTTPS.

## What's in it

- **Servers**: all three at a glance, online/offline and player counts.
- **Server page**: a live summary along the top (players on, RCON ping, server
  FPS from the `-logStats` lines, memory, uptime and running time), then an
  Overview tab (players, live feed, recent actions) and a Health tab.
- **History**: every finished game's whole log folder (console, script, error
  and crash logs) is packed into `data/log-archive/<server>/<folder>.tar.gz`
  when the server next restarts, and kept for good, even if AMP or the game
  clears its own logs. A 6 MB game log comes out around 1 MB. The History tab
  on a server page lists the last week's games, or any day you pick. Opening
  a game shows who played (times on, kills, deaths, teamkills, and IPs for
  admins), the whole feed with the same filters as the live one, admin actions
  during it, and the suspicious-activity checks run with today's settings.
  Admins and owners can download the log folder; each download is in the
  audit log. The game in progress can be opened too.
- **Live players**: refreshes every 10 seconds, with kick and ban on each row.
- **Live feed**: joins, leaves, side picks, kills and teamkills from the logs,
  messages the server sends over RCON, and admin actions, newest first,
  refreshing every 5 seconds. Filter by joins, kills, sus, admin or RCON. Every box on
  the server page has a fixed height with its own scroll and a full-screen button
  (Esc closes it).
- **Suspicious activity** (the red "Sus" rows in the live feed, kept 180 days, and posted to
  the Discord alerts channel): the patterns from past cheater investigations.
  - Explosions killing players with the kill credited to AI or "neutral or
    factionless", which never shows in the kill feed: 3 players in the same
    second, or 5 within 5 minutes.
  - Script errors (NULL pointer / INSTIGATOR_OTHER) in 20 different seconds
    within 5 minutes, like the failing player spawns in the Buford case. AI
    behaviour scripts (`SCR_AI...`) are skipped: one broken AI vehicle can throw
    70 in a second, which is a game bug, not a player.
  - One player getting 6 rifle kills in 30 seconds, 10+ kills in 15 minutes
    with 75% or more to the head, or 3 teamkills in 10 minutes.

  An explosion or error flag waits 2 minutes, then names who joined in the 10
  minutes before (leaving out reconnects, the victims and anyone who had
  already left) and any player whose own kills landed within 150 m of the
  explosions. It also saves everyone who was on the server, and a player who
  was on for incidents on any server says so ("At earlier incidents too:
  Buford (2x)"). Their player page lists every incident they were present for.
  Being present once means nothing; the same name at several is worth
  spectating. The logs never say which weapon was used, so a flag is a reason
  to look, not proof. Kills credited from over 2 km away and collision deaths
  are ignored (a crash gets credited to whoever last damaged the vehicle), and
  so are long-range explosive kills by a named player (usually a gunship).
  Every limit can be changed in panel.local.json, for example
  `"suspicion": {"same_second": 4, "headshot_share": 0.8}`. The names are
  `ai_explosions`, `ai_explosions_window`, `same_second`, `rapid_kills`,
  `rapid_window`, `headshot_kills`, `headshot_share`, `teamkills`,
  `script_errors`, `max_distance`, `nearby`, `joined_before`, `cooldown` and
  `settle`.

  To see what a log would have flagged, without touching the panel:
  `.venv/bin/python dev/scan_sus.py path/to/console.log` (add
  `--set same_second=4` to try a different limit).
- **Server controls**: restart mission, RCON shutdown, and start / stop /
  restart of the systemd service when `service` is set.
- **Memory check**: each server's memory on the dashboard and server page. A
  few minutes after a full server restart the panel notes what a clean server
  uses. After **Restart mission** it checks again once the new mission has
  loaded and warns if the old mission's memory wasn't let go, meaning it's time
  for a full **Restart server**. Needs `service` set for that server.
- **Health**: uptime over the last day and week, crashes (the server program
  ending without anyone pressing a button), recent starts and stops, and a
  24-hour memory graph. Needs `service` set for that server.
- **Discord alerts**: bans, unbans, kicks, restarts, crashes, a server going
  offline or coming back, and memory that didn't clear, posted to a staff channel.
- **Shared bans**: one list for every server. Bans go to every server at once;
  a server that's offline picks them up when it's back. Unbans work the same way.
- **IP bans**: tick "IP ban" when banning. It also bans every other account
  the panel has seen on that player's IPs (their PC, PlayStation and Xbox
  accounts, and alts), and any new account that joins from one of those IPs
  later (seen in the last 12 hours) is banned and kicked too, logged as
  "IP ban" and posted to Discord. Each gets the same length, with the reason
  marked "same IP as ...". People in one house share an IP, so leave it unticked
  when that matters. Unban each account separately.
- **Kick on ban**: a banned account that is connected is kicked straight away,
  and any banned account still on a server is kicked on the next check.
- **Players**: everyone the panel or the bot has seen, searchable by name, old
  name or identity ID. Each player shows time played, kills, deaths, K/D,
  teamkills and recent games from the bot's logs, their linked Discord account,
  first/last seen, names used, ban history and admin notes. The panel only reads
  the bot's data and never changes it.
- **Connections** (admins and owners only): every IP a player has connected
  from, their BattlEye GUID, and every other account that used the same IP, so
  alts and ban evaders stand out. The live player list flags players with alts,
  in red when one of them is banned. Search the Players page by IP. Read from
  each server's console logs: `log_dir` in panel.local.json, or the bot's own
  `servers.local.json` when the panel runs from the bot's folder. Records not
  seen for 180 days are deleted.
- **Console**: raw RCON commands for anything the buttons don't cover.
- **Audit log**: logins, kicks, bans, restarts, console commands, account changes.
- **Admins**: create accounts, change roles, reset passwords, disable accounts.

| Role | Can |
|---|---|
| Moderator | see everything, kick, add notes |
| Admin | also ban / unban, server controls, audit log |
| Owner | also the raw console and admin accounts |

## 1. Turn on RCON in each server

Add an `rcon` block to each server's `server.json`, with its own port and
password (no spaces, at least 3 characters), then restart that server:

```json
"rcon": {
  "address": "127.0.0.1",
  "port": 19999,
  "password": "long-random-password",
  "permission": "admin",
  "maxClients": 4
}
```

`127.0.0.1` keeps RCON off the internet. Use 19999, 19998 and 19997 for the
three servers. BattleMetrics keeps working through the public `a2s` block.

## 2. Install the panel

On the box that runs the bot:

```
cd ~/Arma-bot && git pull && bash deploy/panel_setup.sh
```

The first run creates `panel.local.json` and stops. Put each server's RCON port
and password in it (`nano panel.local.json`), then run the same command again.
Set `service` to a server's systemd unit (e.g. `reforger-server`) to get
Start / Stop / Restart buttons; setup gives the panel permission to control
just those units.

The second run starts the `oyb-panel` service and asks you to make your owner
account.

## 3. Give it a domain

Point a DNS A record (e.g. `panel.yourdomain.com`) at the box, open ports 80
and 443, then:

```
sudo apt install -y caddy
```

```
printf 'panel.yourdomain.com {\n\treverse_proxy 127.0.0.1:8080\n}\n' | sudo tee /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

That replaces the whole Caddyfile, so skip it if Caddy is already serving
something else on the box and add the block by hand instead. Caddy fetches the
HTTPS certificate itself. Open `https://panel.yourdomain.com` and log in.

## Discord alerts

In Discord: the staff channel → Edit Channel → Integrations → Webhooks → New
Webhook → Copy Webhook URL. Put it in `panel.local.json`:

```
"discord_webhook": "https://discord.com/api/webhooks/..."
```

then restart the panel. Leave it empty to turn alerts off.

## Banned roles in Discord

While a player is banned in the panel, the bot gives their linked Discord
account a role naming the length (`Banned · 1 hour`, `Banned · 1 day`,
`Banned · 7 days`, `Banned · 30 days` or `Banned · Permanent`) and removes it
when the ban ends or is lifted. It checks once a minute. The bot makes the
roles itself the first time it needs them; they carry no permissions.

It reads the panel's `data/panel.sqlite3`, which is where it already is when
the panel runs from the bot's folder. Otherwise set `PANEL_DB` in the bot's
`.env` to the panel's database. Players who haven't linked their Discord get
no role.

## Adding admins

Admins → New admin. The panel shows a temporary password once; send it to them.
They pick their own password the first time they log in.

Locked out? On the box:

```
cd ~/Arma-bot && .venv/bin/python -m panel passwd YOURNAME
```

## Day to day

```
sudo systemctl status oyb-panel
```

```
sudo journalctl -u oyb-panel -n 60 -f
```

```
cd ~/Arma-bot && git pull && sudo systemctl restart oyb-panel
```

The panel's accounts, bans, notes and audit log live in `data/panel.sqlite3`.
Back it up with the rest of `data/`.

## If the player list looks wrong

The panel reads the reply to `#players`. If a server words it differently, the
server page shows the raw reply instead of a table. Send that reply to whoever
maintains the panel. The commands the panel sends can be changed per server in
`panel.local.json` under `commands` without touching the code.

## Trying it without a game server

```
.venv/bin/python dev/fake_rcon.py --port 19999 --password test
```

Point a server in `panel.local.json` at that port and password, set
`"cookie_secure": false` so the login works over plain http, and run
`.venv/bin/python -m panel serve`.
