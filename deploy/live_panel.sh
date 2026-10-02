#!/usr/bin/env bash
# Sets OYB Control up on a box whose Arma servers run under AMP, next to
# whatever else manages them. Reads the RCON details from AMP, leaves starting
# and stopping to AMP, and puts the panel behind HTTPS with Caddy.
#   bash deploy/live_panel.sh [domain]      (default panel.oybgaming.com)
set -euo pipefail
DOMAIN="${1:-panel.oybgaming.com}"
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "Run this as root."; exit 1; }

echo "== Reading the servers from AMP"
python3 "$REPO_DIR/deploy/amp_panel_config.py"

echo "== Installing the panel"
bash "$REPO_DIR/deploy/panel_setup.sh"

echo "== HTTPS for $DOMAIN"
command -v caddy >/dev/null || { apt-get update -qq && apt-get install -y -qq caddy; }
if grep -q "$DOMAIN" /etc/caddy/Caddyfile 2>/dev/null; then
    echo "Caddy already serves $DOMAIN"
elif grep -qvE '^\s*(#|$)' /etc/caddy/Caddyfile 2>/dev/null && ! grep -q ':80 {' /etc/caddy/Caddyfile; then
    printf '\n%s {\n\treverse_proxy 127.0.0.1:8090\n}\n' "$DOMAIN" >> /etc/caddy/Caddyfile
else
    printf '%s {\n\treverse_proxy 127.0.0.1:8090\n}\n' "$DOMAIN" > /etc/caddy/Caddyfile
fi
systemctl enable -q caddy
systemctl restart caddy
echo
echo "Done. Once $DOMAIN points at this box, open https://$DOMAIN and log in."
