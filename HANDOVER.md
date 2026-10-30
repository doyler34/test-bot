# Handover: read this before doing anything (updated 30 October 2026)

Read the top of CLAUDE.md first: it says how the owner wants you to work. The
short version: **you never touch anything live, you never push to Gazlagom,
you never store tokens or install things, you only do what was asked, and you
keep it short.** Breaking those rules is what went wrong on 30 October (below).

Status words: CONFIRMED = the owner saw it working; AWAITING TEST = in `main`,
not tested by the owner; OPEN = not solved.

## The owner

- Works from a phone. One step at a time, one single-line command per step,
  plain words. Long answers lose them.
- Runs every command on live machines themselves. You give the command.
- Gets angry, with reason, at guessing presented as fact, at changes they
  didn't ask for, and at things done to live systems without asking.

## Where things are

- **GitHub `doyler34/test-bot`, branch `main`:** all the code. Work happens
  here (cloud sessions push to `main`; the VPS session can push too, with the
  owner's token).
- **VPS `srv1669502` (bos2.hostingervps.com):**
  - `/root/test-bot`: a checkout of `main`, used to feed the Gazlagom sync and
    by the Claude session on the VPS. Not live. Its old branch setting was
    broken; update it with `git -C /root/test-bot pull --ff-only origin main`.
    The branch `vps-notes` there keeps some old test notes; nothing needs them.
  - `/root/Arma-bot-upload.StfK1D`: the Gazlagom mirror upload folder. Only
    the owner runs the sync (CLAUDE.md has the one-line command).
  - `/root/oyb-panel`: the **live panel** (service `oyb-panel`). On branch
    `panel-test`, no upstream. Owner updates it with
    `cd /root/oyb-panel && git pull --ff-only origin main && systemctl restart oyb-panel`.
  - Remote Control runs in tmux session `claude`
    (`cd /root/test-bot && claude remote-control --spawn=same-dir`).
  - Hostinger's browser console and SSH didn't let the owner in on 30 Oct
    while their phone's VPN was on; it worked once the VPN was off (probably
    the cause, not proven).
- **Friend's box:** the live bot in `~/Arma-bot` (data in `~/Arma-bot/data`).
  Owner updates it with `cd ~/Arma-bot && git pull && oyb restart`.
- **Live game server:** AMP instance `ArmaReforger01`, not on the VPS. Its
  logs are read through the panel's command box.
- **Owner's Windows PC:** Workbench and the game. Only the owner can compile,
  test in game and publish the mods. Building-menu lines are in the owner's
  own game log: `Documents\My Games\ArmaReforger\logs\<newest>\console.log`.

## What happened on 30 October (so it doesn't happen again)

- The VPS session, without being asked: updated the live panel's code in
  `/root/oyb-panel` from `8cf5906` to `ecc8fa2` (381 commits at once, months
  of panel work going live together), tried to restart the panel (the owner
  stopped it), saved the owner's GitHub token in plain text on the VPS, and
  changed `.gitattributes` after asking "want me to?" without waiting.
- Rollback point for the live panel if the new version is broken (the owner
  runs it): `cd /root/oyb-panel && git reset --hard 8cf5906 && systemctl restart oyb-panel`.
  The old code works with the database as the new code left it (WAL mode and
  extra indexes are harmless to it).
- The token: `git -C /root/test-bot config --unset credential.helper` and
  deleting the credentials file removes it.

## Panel work (all in `main`, AWAITING TEST on the live panel)

- 29–30 Oct, cloud session: speed and clean-up. WAL database, one write per
  player list, new indexes, the live feed reads only the newest rows, hidden
  page parts don't refresh, Investigate and Unusual read in another thread,
  AMP login kept 5 minutes (start/stop/restart still log in fresh), public
  site reads server files at most every 10 s. Hourly clean-up: server events
  180 days, staff alerts 90 days, saved game logs 180 days (game stays
  listed). The audit log is kept forever. Security items were left alone on
  purpose (the owner said so).
- 30 Oct, VPS session: side tags before names in the live feed (`bdbf3d1`,
  fixed in `d38ca40` after the tags sat over the sidebar), New joiners card
  on the Live tab (`4a86dee`).
- Tests: `python -m unittest discover -s tests` from the repo root (needs the
  packages in `requirements.txt`; the VPS's system Python doesn't have
  aiohttp, so don't run them there without asking).

## Mods (owner compiles and tests; nothing here is CONFIRMED in game unless it says so)

### OYB Core vehicle limiter (`mod/OYB_Core`)

- Build marker `vehicles-2026-10-28f`; the server log's first OYB line names
  the running build. `28f` was handed to the owner to publish.
- CONFIRMED from logs: the vehicle list (~347 rows) and limits work; the
  broken BMP2 prefab from a mod is skipped instead of crashing the building
  menu.
- OPEN, heli price bug: with a building-menu vehicle priced on the panel, the
  menu cards show no price; resetting the prices brings them back. The price
  code doesn't write to the prefab or its UI info (checked line by line). Next
  step: the `OYB_CORE price …` lines from the owner's game log with one heli
  priced and the heli pad opened. Fallback: depot-only prices.

### OYB Arsenal (`mod/OYB_Arsenal`, see MILESTONES.md)

- Leftover bar across the screen: fixed and CONFIRMED in Workbench; the later
  extra clean-up (`24863c2`/`8e546cb`) is AWAITING TEST.
- Milestone 1 (bad settings file refused, switched-off arsenals left alone):
  AWAITING TEST. Milestone 2 waits for that.
- Medical tent and ambulance left medical-only; kits with OYB items can be
  saved: in `main`.
