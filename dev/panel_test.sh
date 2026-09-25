#!/usr/bin/env bash
# Puts a small Reforger test server and OYB Control on this box.
#   bash panel_test.sh [branch]   set up / update and start (branch defaults to main)
#   bash panel_test.sh stop       stop both
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 1; }

GAME=/root/reforger-test
PANEL=/root/oyb-panel
SOURCE=/root/test-bot
BRANCH="${1:-main}"

if [ "${1:-}" = stop ]; then
    systemctl stop oyb-panel reforger-test 2>/dev/null || true
    systemctl disable -q oyb-panel 2>/dev/null || true
    echo "Stopped the test server and the panel."
    exit 0
fi

echo "== Installing packages"
apt-get update -qq
apt-get install -y -qq lib32gcc-s1 python3-venv curl git openssl >/dev/null

echo "== Downloading the Reforger server (first time takes a while)"
if [ ! -x /root/steamcmd/steamcmd.sh ]; then
    mkdir -p /root/steamcmd
    curl -sL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar -xz -C /root/steamcmd
fi
/root/steamcmd/steamcmd.sh +force_install_dir "$GAME" +login anonymous +app_update 1874900 validate +quit >/dev/null
[ -x "$GAME/ArmaReforgerServer" ] || { echo "The server download failed. Run this again."; exit 1; }

[ -f "$GAME/.rcon_pass" ] || openssl rand -hex 12 > "$GAME/.rcon_pass"
RCON_PASS="$(cat "$GAME/.rcon_pass")"
cat > "$GAME/test.json" <<EOF
{
  "bindAddress": "0.0.0.0",
  "bindPort": 2001,
  "publicPort": 2001,
  "a2s": {"address": "0.0.0.0", "port": 17777},
  "rcon": {"address": "127.0.0.1", "port": 19999, "password": "$RCON_PASS", "permission": "admin", "maxClients": 4},
  "game": {
    "name": "OYB Panel Test",
    "password": "oybtest",
    "passwordAdmin": "$(openssl rand -hex 8)",
    "scenarioId": "{C41618FD18E9D714}Missions/23_Campaign_Arland.conf",
    "maxPlayers": 8,
    "visible": true,
    "crossPlatform": true,
    "gameProperties": {"battlEye": true}
  }
}
EOF

cat > /etc/systemd/system/reforger-test.service <<EOF
[Unit]
Description=Reforger test server for OYB Control
After=network-online.target

[Service]
WorkingDirectory=$GAME
ExecStart=$GAME/ArmaReforgerServer -config $GAME/test.json -profile $GAME/profile -maxFPS 30
Restart=always
RestartSec=10
EOF
systemctl daemon-reload
systemctl restart reforger-test

echo "== Getting the panel"
git -C "$SOURCE" fetch -q origin "$BRANCH"
if [ -d "$PANEL" ]; then
    git -C "$PANEL" pull -q --ff-only origin "$BRANCH"
else
    git -C "$SOURCE" worktree prune
    git -C "$SOURCE" worktree add -q -B panel-test "$PANEL" FETCH_HEAD
fi
cat > "$PANEL/panel.local.json" <<EOF
{
  "listen": "0.0.0.0",
  "port": 8080,
  "cookie_secure": false,
  "servers": [
    {"id": "server-1", "name": "OYB Panel Test", "rcon_port": 19999, "rcon_password": "$RCON_PASS", "service": "reforger-test"}
  ]
}
EOF
chmod 600 "$PANEL/panel.local.json"

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 8080/tcp >/dev/null
    ufw allow 2001/udp >/dev/null
    ufw allow 17777/udp >/dev/null
fi

echo "== Installing the panel"
bash "$PANEL/deploy/panel_setup.sh"

echo "== Waiting for the game server to load (up to 5 minutes)"
READY=false
for _ in $(seq 1 60); do
    if ss -Hlun 'sport = :19999' | grep -q .; then READY=true; break; fi
    sleep 5
done

IP="$(hostname -I | awk '{print $1}')"
echo
if [ "$READY" = true ]; then
    echo "Game server is up."
else
    echo "Game server is still loading. The panel will connect by itself once it's up."
    echo "Check it with: journalctl -u reforger-test -n 40"
fi
echo
echo "Panel:        http://$IP:8080"
echo "Game server:  search 'OYB Panel Test' in the server browser, password oybtest"
echo "Stop it all:  bash /root/panel_test.sh stop"
echo
echo "If the page won't open, allow TCP 8080 and UDP 2001 + 17777 in the Hostinger firewall (hPanel)."
