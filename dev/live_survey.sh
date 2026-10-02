#!/usr/bin/env bash
# Read-only look at a box running AMP and the OYB bot, so the panel can be set
# up there without guessing. Changes nothing. Passwords, tokens and keys are
# never printed: only whether they are set.
#   bash live_survey.sh [panel upload link]   (as root, or a user that can sudo)
# Everything goes to the screen and to /tmp/oyb-survey.txt, and to the panel
# if an upload link (made on a server's History tab) is given.

OUT=/tmp/oyb-survey.txt
echo "OYB box report: looking around, this takes about 30 seconds..."
exec > >(tee "$OUT") 2>&1
SECRET='pass|token|secret|key|webhook'

section() { printf '\n=== %s ===\n' "$1"; }
run() { timeout 20 "$@" 2>&1 | head -n "${LINES_MAX:-60}"; }

section "machine"
date -u; whoami; id
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
uname -r; nproc; free -h | head -2; uptime
df -h / /home 2>/dev/null
timedatectl 2>/dev/null | grep -i "time zone"
echo "public ip: $(timeout 5 curl -s https://api.ipify.org || echo unknown)"

section "tools"
for tool in python3 git sqlite3 nginx caddy certbot ufw systemctl sudo curl; do
  printf '%-10s %s\n' "$tool" "$(command -v $tool || echo missing)"
done
python3 --version 2>&1
python3 -c "import venv, sqlite3; print('venv ok, sqlite', sqlite3.sqlite_version)" 2>&1

section "listening ports"
LINES_MAX=80 run ss -ltnup

section "firewall"
run ufw status verbose

section "services"
systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -Ei 'oyb|arma|amp|bot|panel|nginx|caddy' | head -40
crontab -l 2>/dev/null | grep -v '^#' | head -20

section "amp instances"
for who in amp root; do
  if id "$who" >/dev/null 2>&1; then
    echo "--- ampinstmgr as $who"
    LINES_MAX=80 run sudo -n -u "$who" ampinstmgr -l
  fi
done
ls -ld /home/amp /home/amp/.ampdata /home/amp/.ampdata/instances 2>&1
ls -l /home/amp/.ampdata/instances 2>&1 | head -30

section "reforger log folders"
find /home /root /opt /srv /var/lib -maxdepth 9 -type d -name 'logs_20*' 2>/dev/null \
  | sed 's#/logs_20[^/]*$##' | sort | uniq -c | while read -r count root; do
  newest=$(ls -1d "$root"/logs_20* 2>/dev/null | sort | tail -1)
  echo "--- $root"
  echo "games: $count   size: $(du -sh "$root" 2>/dev/null | cut -f1)"
  echo "owner/perms: $(stat -c '%U:%G %A' "$root")   newest: $(basename "$newest")"
  ls -1d "$root"/logs_20* | sort | tail -3 | while read -r game; do
    echo "  $(basename "$game"): $(ls "$game" | tr '\n' ' ')"
  done
  if [ -f "$newest/console.log" ]; then
    log="$newest/console.log"
    echo "  newest console.log: $(du -h "$log" | cut -f1), $(wc -l < "$log") lines"
    echo "  kill lines: $(grep -c 'KILL ' "$log")   identities: $(grep -c 'IdentityId=' "$log")   joins with IP: $(grep -c "connected'" "$log")"
    echo "  first line: $(head -1 "$log" | cut -c1-120)"
    echo "  last line:  $(tail -1 "$log" | cut -c1-120)"
  fi
done

section "reforger server configs"
find /home /root /opt /srv -maxdepth 9 -type f -name '*.json' -path '*Configs*' 2>/dev/null | head -10 | while read -r cfg; do
  echo "--- $cfg ($(stat -c '%U:%G %A' "$cfg"))"
  python3 - "$cfg" "$SECRET" <<'PY'
import json, re, sys
path, secret = sys.argv[1], re.compile(sys.argv[2], re.I)
try:
    data = json.load(open(path))
except Exception as exc:
    sys.exit(f"  could not read: {exc}")
def show(value, key=""):
    if secret.search(key):
        return "(set)" if value else "(empty)"
    if isinstance(value, dict):
        return {k: show(v, k) for k, v in value.items() if k not in ("mods",)}
    if isinstance(value, list):
        return f"[{len(value)} items]"
    return value
top = {k: show(data.get(k), k) for k in ("bindAddress", "bindPort", "publicAddress", "publicPort", "a2s", "rcon")}
game = data.get("game", {})
top["game"] = {k: show(game.get(k), k) for k in ("name", "scenarioId", "maxPlayers", "visible", "password", "passwordAdmin")}
top["game"]["admins"] = f"{len(game.get('admins', []))} ids"
top["game"]["mods"] = f"{len(game.get('mods', []))} mods"
print(json.dumps(top, indent=1))
PY
done

section "bot checkout"
for dir in /root/Arma-bot /home/*/Arma-bot; do
  [ -d "$dir" ] || continue
  echo "--- $dir ($(stat -c '%U:%G' "$dir"))"
  git -C "$dir" log --oneline -1 2>&1
  git -C "$dir" status -sb 2>&1 | head -5
  git -C "$dir" remote -v 2>&1 | head -1 | sed -E 's#//[^@/]*@#//#'
  if [ -f "$dir/.env" ]; then
    echo ".env:"
    grep -v '^\s*#' "$dir/.env" | grep '=' | while IFS='=' read -r key value; do
      if echo "$key" | grep -qiE "$SECRET"; then
        echo "  $key=$( [ -n "$value" ] && echo '(set)' || echo '(empty)')"
      else
        echo "  $key=$value"
      fi
    done
  fi
  echo "data:"
  ls -la "$dir/data" 2>&1 | head -30
done

section "oyb helper"
command -v oyb && sed -n '1,60p' "$(command -v oyb)" | grep -viE "$SECRET"
LINES_MAX=20 run oyb status

section "bot log (last 80 lines)"
LINES_MAX=400 run oyb logs | tail -80 | grep -viE 'token='

echo
echo "Saved to $OUT"
if [ -n "$1" ]; then
  sleep 1
  curl -sS --max-time 30 --data-binary "@$OUT" -H "Content-Type: text/plain" "$1?kind=survey" \
    || echo "Couldn't send it to the panel; the report is still in $OUT"
fi
