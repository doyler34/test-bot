# OYB Control (web panel)

A private web panel for running all three Reforger servers over RCON. Admins log
in with their own account; everything they do lands in the audit log.

It runs on the same box as the game servers, next to the bot, and only listens
on `127.0.0.1`. Caddy sits in front and gives it a domain with HTTPS.

## What's in it

- **Servers**: all three at a glance, online/offline and player counts.
- **Live players**: refreshes every 10 seconds, with kick and ban on each row.
- **Server controls**: restart mission, RCON shutdown, and start / stop /
  restart of the systemd service when `service` is set.
- **Memory check**: each server's memory on the dashboard and server page. A
  few minutes after a full server restart the panel notes what a clean server
  uses. After **Restart mission** it checks again once the new mission has
  loaded and warns if the old mission's memory wasn't let go, meaning it's time
  for a full **Restart server**. Needs `service` set for that server.
- **Shared bans**: one list for every server. Bans go to every server at once;
  a server that's offline picks them up when it's back. Unbans work the same way.
- **Players**: everyone the panel has seen, searchable by name, old name or
  identity ID, with first/last seen, names used, ban history and admin notes.
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
