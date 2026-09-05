#!/usr/bin/env bash
#
# One-command setup for a test VPS (Debian/Ubuntu):
#   * installs SteamCMD + the Arma Reforger dedicated server (app 1874900)
#   * writes a minimal 2-player Conflict Everon config
#   * installs the Discord timer bot (venv)
#   * prompts for the 3 Discord values and writes .env
#   * installs + starts both as systemd services
#
# Usage:  bash deploy/setup.sh
#
# Overridable via environment:
#   INSTALL_DIR   (default: $HOME/reforger)     where the game server installs
#   MAX_PLAYERS   (default: 2)
#   SERVER_NAME   (default: "Bot Test Server")
#   SCENARIO      (default: Conflict Everon)

set -euo pipefail

# --- resolve paths / privileges ----------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
RUN_USER="$(id -un)"
INSTALL_DIR="${INSTALL_DIR:-$HOME/reforger}"
MAX_PLAYERS="${MAX_PLAYERS:-2}"
SERVER_NAME="${SERVER_NAME:-Bot Test Server}"
SCENARIO="${SCENARIO:-{ECC61978EDCC2B5A}Missions/23_Campaign.conf}"
REFORGER_APPID=1874900

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

log() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v apt-get >/dev/null 2>&1 || die "This installer supports Debian/Ubuntu (apt) only."

# --- 1. system packages ------------------------------------------------------
log "Installing system packages (SteamCMD, Python, git)..."
$SUDO dpkg --add-architecture i386
$SUDO apt-get update -y
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common
$SUDO add-apt-repository -y multiverse
$SUDO apt-get update -y
# Pre-accept the Steam license so the install is non-interactive.
echo steam steam/question select "I AGREE" | $SUDO debconf-set-selections
echo steam steam/license note '' | $SUDO debconf-set-selections
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    steamcmd python3-venv python3-pip git curl ca-certificates

STEAMCMD="$(command -v steamcmd || true)"
[ -n "$STEAMCMD" ] || die "steamcmd not found after install."

# --- 2. Reforger dedicated server -------------------------------------------
log "Installing/updating Arma Reforger dedicated server into $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# Run twice: SteamCMD often self-updates on first run and exits.
"$STEAMCMD" +force_install_dir "$INSTALL_DIR" +login anonymous \
    +app_update "$REFORGER_APPID" validate +quit || \
"$STEAMCMD" +force_install_dir "$INSTALL_DIR" +login anonymous \
    +app_update "$REFORGER_APPID" validate +quit

[ -x "$INSTALL_DIR/ArmaReforgerServer" ] || \
    die "ArmaReforgerServer binary missing in $INSTALL_DIR (SteamCMD download failed?)."

# --- 3. server config --------------------------------------------------------
mkdir -p "$INSTALL_DIR/configs" "$INSTALL_DIR/profile"
CONFIG="$INSTALL_DIR/configs/server.json"
if [ ! -f "$CONFIG" ]; then
    log "Writing server config ($MAX_PLAYERS players, Conflict Everon)..."
    PUBLIC_ADDRESS="$(curl -s --max-time 5 ifconfig.me || true)"
    ADMIN_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 12)"
    sed -e "s|@PUBLIC_ADDRESS@|${PUBLIC_ADDRESS}|g" \
        -e "s|@SERVER_NAME@|${SERVER_NAME}|g" \
        -e "s|@ADMIN_PASSWORD@|${ADMIN_PASSWORD}|g" \
        -e "s|@SCENARIO@|${SCENARIO}|g" \
        -e "s|@MAX_PLAYERS@|${MAX_PLAYERS}|g" \
        "$SCRIPT_DIR/server.json.tpl" > "$CONFIG"
    echo "    Admin password: $ADMIN_PASSWORD   (saved in $CONFIG)"
else
    log "Keeping existing server config at $CONFIG"
fi

# --- 4. bot venv -------------------------------------------------------------
log "Setting up the bot Python environment..."
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$REPO_DIR/.venv/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

# --- 5. .env (prompt for Discord values) ------------------------------------
ENV_FILE="$REPO_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    log "Discord configuration (paste each value, press Enter):"
    read -rp "  DISCORD_BOT_TOKEN: " DISCORD_BOT_TOKEN
    read -rp "  GUILD_ID (server ID): " GUILD_ID
    read -rp "  VOICE_CHANNEL_ID: " VOICE_CHANNEL_ID
    umask 177
    cat > "$ENV_FILE" <<EOF
DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN}
GUILD_ID=${GUILD_ID}
VOICE_CHANNEL_ID=${VOICE_CHANNEL_ID}
REFORGER_LOG_DIR=${INSTALL_DIR}/profile/logs
SESSION_STALE_SECONDS=120
STATUS_REFRESH_SECONDS=60
A2S_HOST=127.0.0.1
A2S_PORT=17777
EOF
    umask 022
    chmod 600 "$ENV_FILE"
    echo "    Wrote $ENV_FILE (permissions 600)."
else
    log "Keeping existing $ENV_FILE"
fi

# --- 6. systemd services -----------------------------------------------------
render_unit() {
    sed -e "s|@USER@|${RUN_USER}|g" \
        -e "s|@INSTALL_DIR@|${INSTALL_DIR}|g" \
        -e "s|@REPO_DIR@|${REPO_DIR}|g" "$1"
}
log "Installing systemd services..."
render_unit "$SCRIPT_DIR/reforger-server.service.tpl" | \
    $SUDO tee /etc/systemd/system/reforger-server.service >/dev/null
render_unit "$SCRIPT_DIR/reforger-timer.service.tpl" | \
    $SUDO tee /etc/systemd/system/reforger-timer.service >/dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable --now reforger-server.service
$SUDO systemctl enable --now reforger-timer.service

# --- 7. firewall reminder ----------------------------------------------------
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status | grep -q "Status: active"; then
    log "Opening game ports in ufw..."
    $SUDO ufw allow 2001/udp || true
    $SUDO ufw allow 17777/udp || true
fi

# --- done --------------------------------------------------------------------
cat <<EOF

$(log "Done.")
Reforger server : $INSTALL_DIR   (2001/udp game, 17777/udp query)
Bot repo        : $REPO_DIR
Logs watched by bot: $INSTALL_DIR/profile/logs

Check status:
  sudo systemctl status reforger-server reforger-timer
Follow logs:
  journalctl -u reforger-server -f
  journalctl -u reforger-timer  -f

If this VPS has a firewall/security group outside ufw, allow UDP 2001 and 17777.
The Reforger server takes a minute or two to boot; the bot joins the VC once the
match reaches the GAME state.
EOF
