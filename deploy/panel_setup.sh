#!/usr/bin/env bash
# Installs or updates the OYB Control web panel as the oyb-panel service.
set +x
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"
if [ "$(id -u)" -ne 0 ]; then
    exec sudo bash "$SCRIPT_DIR/panel_setup.sh"
fi
die() { printf 'Panel setup failed: %s\n' "$*" >&2; exit 1; }
command -v systemctl >/dev/null || die "systemd is required."
SERVICE=oyb-panel.service
BOT=reforger-timer.service
if [ "$(systemctl show "$SERVICE" -p LoadState --value)" = loaded ]; then
    RUN_USER="$(systemctl show "$SERVICE" -p User --value)"
elif [ "$(systemctl show "$BOT" -p LoadState --value)" = loaded ]; then
    RUN_USER="$(systemctl show "$BOT" -p User --value)"
else
    RUN_USER="${SUDO_USER:-root}"
fi
RUN_USER="${RUN_USER:-root}"
id "$RUN_USER" >/dev/null || die "Service user $RUN_USER does not exist."
as_panel() { runuser -u "$RUN_USER" -- "$@"; }
as_panel test -w "$REPO_DIR" || die "$RUN_USER needs write access to $REPO_DIR."
printf 'Panel service account: %s\n' "$RUN_USER"

PY="$REPO_DIR/.venv/bin/python"
[ -x "$PY" ] || as_panel python3 -m venv "$REPO_DIR/.venv"
as_panel "$PY" -m pip install -q -r "$REPO_DIR/requirements.txt"

CONFIG="$REPO_DIR/panel.local.json"
if [ ! -f "$CONFIG" ]; then
    as_panel cp "$REPO_DIR/panel.example.json" "$CONFIG"
    as_panel chmod 600 "$CONFIG"
    printf '\nCreated %s.\nPut each server'"'"'s RCON port and password in it (see docs/PANEL.md), then run this again.\n' "$CONFIG"
    exit 0
fi
cd -- "$REPO_DIR"
as_panel "$PY" -c 'from panel.config import load_config; load_config()' || die "fix panel.local.json and run this again."

SERVICES="$(as_panel "$PY" -c 'from panel.config import load_config; print(" ".join(s.service for s in load_config().servers if s.service))')"
SUDOERS=/etc/sudoers.d/oyb-panel
if [ "$RUN_USER" != root ] && [ -n "$SERVICES" ]; then
    SYSTEMCTL="$(command -v systemctl)"
    STAGE_SUDO="$(mktemp)"
    for unit in $SERVICES; do
        for action in start stop restart; do
            printf '%s ALL=(root) NOPASSWD: %s %s %s\n' "$RUN_USER" "$SYSTEMCTL" "$action" "$unit"
        done
    done > "$STAGE_SUDO"
    visudo -cf "$STAGE_SUDO" >/dev/null || { rm -f "$STAGE_SUDO"; die "could not build the sudo rule for server controls."; }
    install -m 440 "$STAGE_SUDO" "$SUDOERS"
    rm -f "$STAGE_SUDO"
    printf 'Start / Stop / Restart allowed for: %s\n' "$SERVICES"
elif [ -f "$SUDOERS" ] && [ -z "$SERVICES" ]; then
    rm -f "$SUDOERS"
fi

STAGE="$(mktemp -d)"
trap 'rm -f -- "$STAGE/$SERVICE"; rmdir -- "$STAGE"' EXIT
"$PY" "$SCRIPT_DIR/setup_config.py" --render-panel-unit "$REPO_DIR" "$RUN_USER" > "$STAGE/$SERVICE"
systemd-analyze verify "$STAGE/$SERVICE"
install -m 644 "$STAGE/$SERVICE" "/etc/systemd/system/$SERVICE"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || die "the panel did not start. Check: journalctl -u oyb-panel -n 40"

OWNERS="$(as_panel "$PY" -c 'from panel.config import load_config; from panel.db import PanelDB; print(PanelDB(load_config().database).owner_count())')"
if [ "$OWNERS" = 0 ]; then
    printf '\nNo owner account yet. Pick a username and password for yourself.\n'
    read -r -p 'Username: ' OWNER
    as_panel "$PY" -m panel adduser "$OWNER" --role owner
fi
PORT="$(as_panel "$PY" -c 'from panel.config import load_config; print(load_config().port)')"
printf '\nOYB Control is running on 127.0.0.1:%s.\nPoint your domain at it with Caddy (docs/PANEL.md) so admins can log in over HTTPS.\n' "$PORT"
