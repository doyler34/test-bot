# OYB Reforger bot

A Discord bot for the OYB Arma Reforger community. It watches the local server
logs on the VPS and turns them into Discord: live server status, playtime ranks,
combat stats and match pings — no game mods or RCON needed.

One bot handles up to three Reforger servers on the same box.

## What it does

- **Live server status** — a SERVER STATS category shows each server (Classic /
  3x Everon / Arland) with a live match timer, plus Playing ArmA and Users in VC.
- **Ranks & XP** — 1 XP per 10 minutes played (combined across servers) and 1 XP
  per Discord post once your account is linked. `/rank` shows your card.
- **Combat leaderboard** — kills/deaths from the vanilla kill log, in a pinned
  `/leaderboard`. `/stats` for a single player.
- **Match alerts** — pings when a match goes live in the announcements channel.
- **Account linking** — `#join-oyb` lets players link their Discord to their
  in-game name; admins approve from a private staff channel.

Game restarts and map wipes don't reset anyone's XP or stats.

## Setup

On the Debian/Ubuntu VPS (systemd, Python 3.11+):

```bash
git clone https://github.com/doyler34/test-bot.git
cd test-bot
bash deploy/setup.sh
```

Setup asks for the bot token, guild ID, how many servers are active, and where
each server's logs live. It only installs the bot and its Python deps — it never
touches the game install, configs or databases. See [deploy/README.md](deploy/README.md)
for details.

Manage it with:

```bash
oyb status
oyb logs
oyb restart
oyb setup
```

The bot needs Manage Channels / Manage Roles / Send Messages / Embed Links, and
its role has to sit above the rank roles it creates.

## Config & data

`setup.sh` writes a gitignored `.env` and `servers.local.json`. Keep the SQLite
files under `data/` across updates — they hold XP, combat totals, playtime and
account links, and setup never overwrites them. Set `MATCH_ALERT_CHANNEL_ID`,
`ADMIN_ROLE_ID`, etc. in `.env` to override defaults (see `.env.example`).

More detail: [RANKS.md](RANKS.md) · [STATS.md](STATS.md) · [LEADERBOARD.md](LEADERBOARD.md)
· [NOTIFICATIONS.md](NOTIFICATIONS.md) · [PLAYTIME.md](PLAYTIME.md) · [JOIN_OYB.md](JOIN_OYB.md)

## Tests

```bash
.venv/bin/python -m unittest discover -s tests
```

Setup runs the full suite before saving config.
