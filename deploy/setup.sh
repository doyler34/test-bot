#!/usr/bin/env bash
# Bot-only setup alongside existing Reforger servers (Debian/Ubuntu, Python 3.11+).
set +x
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"
if [ "$(id -u)" -ne 0 ]; then
    exec sudo bash "$SCRIPT_DIR/setup.sh"
fi
die() { printf 'Setup failed: %s\n' "$*" >&2; exit 1; }
[ -t 0 ] || die "Run setup in an interactive VPS terminal. Token input must be hidden."
command -v systemctl >/dev/null || die "systemd is required."
[ -d /run/systemd/system ] || die "systemd is not running on this machine."
command -v apt-get >/dev/null || die "Use Debian/Ubuntu with Python 3.11 or newer."
SERVICE=reforger-timer.service
LOAD="$(systemctl show "$SERVICE" -p LoadState --value)"
if [ "$LOAD" = loaded ]; then
    RUN_USER="$(systemctl show "$SERVICE" -p User --value)"
    RUN_USER="${RUN_USER:-root}"
    OLD_DIR="$(systemctl show "$SERVICE" -p WorkingDirectory --value)"
    [ "$(realpath -m -- "$OLD_DIR")" = "$REPO_DIR" ] || die "Existing bot service uses $OLD_DIR. Run setup from that checkout to preserve its state."
else
    RUN_USER="${SUDO_USER:-root}"
fi
id "$RUN_USER" >/dev/null || die "Existing service user does not exist."
as_bot() { runuser -u "$RUN_USER" -- "$@"; }
as_bot test -w "$REPO_DIR" || die "Service user $RUN_USER needs access to the bot checkout. Fix ownership, then retry."
printf 'Bot service account: %s\n' "$RUN_USER"
printf 'Installing bot Python dependencies...\n'
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "Python 3.11+ is required; upgrade the VPS Python before continuing.")'
[ ! -L "$REPO_DIR/.venv" ] || die "Use a local .venv directory, not a symlink."
as_bot python3 -m venv "$REPO_DIR/.venv"
PY="$REPO_DIR/.venv/bin/python"
as_bot "$PY" -m pip install -r "$REPO_DIR/requirements.txt"
as_bot "$PY" -m pip check
cd -- "$REPO_DIR"
as_bot "$PY" "$SCRIPT_DIR/setup_config.py"
STAGE="$(mktemp -d)"
trap 'rm -f -- "$STAGE/reforger-timer.service" "$STAGE/oyb"; rmdir -- "$STAGE"' EXIT
"$PY" "$SCRIPT_DIR/setup_config.py" --render-unit "$REPO_DIR" "$RUN_USER" > "$STAGE/reforger-timer.service"
systemd-analyze verify "$STAGE/reforger-timer.service"
if [ -f /etc/systemd/system/reforger-timer.service ]; then
    cp -p /etc/systemd/system/reforger-timer.service "$REPO_DIR/.oyb-service-backup-$(date +%s)"
fi
install -m 644 "$STAGE/reforger-timer.service" /etc/systemd/system/reforger-timer.service
"$PY" "$SCRIPT_DIR/setup_config.py" --render-cli "$REPO_DIR" > "$STAGE/oyb"
if [ -e /usr/local/bin/oyb ] && ! grep -q 'OYB management helper' /usr/local/bin/oyb; then
    die "/usr/local/bin/oyb already belongs to another program; resolve the name before rerunning."
fi
install -m 755 "$STAGE/oyb" /usr/local/bin/oyb
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
printf 'Waiting for Discord channels, ranks and playtime tracking to initialise...\n'
READY=false
for attempt in $(seq 1 45); do
    sleep 2
    INVOCATION="$(systemctl show "$SERVICE" -p InvocationID --value)"
    if systemctl is-active --quiet "$SERVICE" && [ -n "$INVOCATION" ]; then
        JOURNAL="$(journalctl "_SYSTEMD_INVOCATION_ID=$INVOCATION" --no-pager -o cat)"
        if printf '%s' "$JOURNAL" | grep -q 'Ready: three read-only channels' &&
           printf '%s' "$JOURNAL" | grep -q 'Test ranks ready:' &&
           printf '%s' "$JOURNAL" | grep -q 'Playtime test tracker started'; then
            READY=true
            break
        fi
    fi
done
[ "$READY" = true ] || die "Bot did not complete startup within 90 seconds. Run oyb logs to check Discord access, token and role permissions. Saved configuration and state were retained."
"$PY" "$SCRIPT_DIR/setup_config.py" --summary
