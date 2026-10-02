#!/usr/bin/env bash
# Serves the public website from this box's panel as well.
#   bash deploy/live_site.sh [domain]      (default oybgaming.com, plus www.)
set -euo pipefail
DOMAIN="${1:-oybgaming.com}"
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "Run this as root."; exit 1; }

python3 - "$REPO_DIR/panel.local.json" "$DOMAIN" <<'PY'
import json, sys
path, domain = sys.argv[1], sys.argv[2]
config = json.load(open(path))
config["site_hosts"] = sorted(set(config.get("site_hosts", [])) | {domain})
json.dump(config, open(path, "w"), indent=2)
PY
if grep -qE "^$DOMAIN[ ,]" /etc/caddy/Caddyfile; then
    echo "Caddy already serves $DOMAIN"
else
    PORT="$(python3 -c "import json; print(json.load(open('$REPO_DIR/panel.local.json')).get('port', 8090))")"
    printf '\n%s, www.%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "$DOMAIN" "$DOMAIN" "$PORT" >> /etc/caddy/Caddyfile
fi
systemctl restart oyb-panel caddy
echo "Done. Once $DOMAIN points at this box, the website is live there."
