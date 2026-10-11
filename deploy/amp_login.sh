#!/usr/bin/env bash
# Gives the panel an AMP login so its In-game admins page can read and change
# each server's admin list. Asks for the login, checks it works, saves it in
# panel.local.json and restarts the panel. Run again to change it.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
[ -f panel.local.json ] || { echo "No panel.local.json in $ROOT. Set the panel up first." >&2; exit 1; }

read -r -p "AMP address [http://127.0.0.1:8080]: " AMP_URL
read -r -p "AMP username: " AMP_USER
read -r -s -p "AMP password: " AMP_PASS
echo
export AMP_URL="${AMP_URL:-http://127.0.0.1:8080}" AMP_USER AMP_PASS

"$PY" - <<'PY'
import asyncio, json, os, sys
from pathlib import Path
from panel.amp import Amp, AmpError, instance_name
from panel.config import load_config

amp = Amp(os.environ["AMP_URL"], os.environ["AMP_USER"], os.environ["AMP_PASS"])
try:
    found = asyncio.run(amp.instances())
except AmpError as exc:
    sys.exit(f"Not saved: {exc}")
print("AMP login works. Servers it can see: " + (", ".join(sorted(found)) or "none"))
for server in load_config().servers:
    name = server.amp_instance or instance_name(server.log_dir)
    print(f"  {server.name}: {'matched to ' + name if name in found else 'NOT matched (AMP instance ' + (name or 'unknown') + ')'}")
path = Path("panel.local.json")
config = json.loads(path.read_text())
config["amp"] = {"url": os.environ["AMP_URL"], "username": os.environ["AMP_USER"], "password": os.environ["AMP_PASS"]}
path.write_text(json.dumps(config, indent=2) + "\n")
path.chmod(0o600)
print("Saved.")
PY

systemctl restart oyb-panel 2>/dev/null && echo "Panel restarted. Open In-game admins in the panel's menu." \
    || echo "Restart the panel to pick it up: systemctl restart oyb-panel"
