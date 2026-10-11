#!/usr/bin/env bash
# Turns on automatic updates: every 10 minutes the panel and bot pull anything
# new from GitHub and restart if they need to. Run once; run again after moving
# the checkout. Undo with: systemctl disable --now oyb-update.timer
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ "$(id -u)" -ne 0 ]; then
    exec sudo bash "$SCRIPT_DIR/auto_update_setup.sh"
fi
command -v systemctl > /dev/null || { echo "systemd is required." >&2; exit 1; }

cat > /etc/systemd/system/oyb-update.service <<EOF
[Unit]
Description=Update OYB Control and the OYB bot from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $SCRIPT_DIR/auto_update.sh
EOF

cat > /etc/systemd/system/oyb-update.timer <<'EOF'
[Unit]
Description=Check for OYB updates every 10 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=10min
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
EOF

systemd-analyze verify /etc/systemd/system/oyb-update.service /etc/systemd/system/oyb-update.timer
systemctl daemon-reload
systemctl enable --now oyb-update.timer
touch /var/log/oyb-update.log
echo "Automatic updates are on: every 10 minutes. See what it's done with: tail -n 20 /var/log/oyb-update.log"
