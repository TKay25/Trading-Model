#!/usr/bin/env bash
# ============================================================================
# BotTraderX5 — InterServer VPS one-shot bootstrap (Ubuntu 24.04 recommended)
#
# Sets up the server, clones your repo from GitHub, installs deps, creates the
# .env you edit once, and installs the systemd service so the bot runs 24/7
# and auto-starts on reboot. After this, updates are: git pull + restart
# (or push-to-deploy via the included GitHub Actions workflow).
#
# Usage:
#   sudo bash deploy/setup_interserver.sh https://github.com/YOU/REPO.git
# ============================================================================
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR="${APP_DIR:-/opt/bot}"
APP_USER="${APP_USER:-bot}"
SERVICE="bot-traderx5"

if [[ -z "$REPO_URL" ]]; then
  echo "ERROR: pass your repo URL, e.g.:"
  echo "  sudo bash deploy/setup_interserver.sh https://github.com/you/bot-traderx5.git"
  exit 1
fi
if [[ "$(id -u)" != "0" ]]; then
  echo "ERROR: run with sudo."
  exit 1
fi

echo "==> 1/7 Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip curl ca-certificates
# Ubuntu 24.04 ships Python 3.12 (the version this app is tested on).
python3 --version

echo "==> 2/7 Creating app user + dir"
id -u "$APP_USER" &>/dev/null || useradd -m -s /bin/bash "$APP_USER"
mkdir -p "$APP_DIR"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> 3/7 Cloning repo from GitHub"
if [[ -z "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
else
  echo "    $APP_DIR is not empty — assuming it is already cloned; skipping clone."
fi

echo "==> 4/7 Python venv + dependencies"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> 5/7 .env (secrets)"
if [[ ! -f "$APP_DIR/.env" ]]; then
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
cat <<'EOF'
    Edit /opt/bot/.env and set these (never leave a value blank — delete the line):
      SECRET_KEY=<long random string>
      DERIV_APP_ID=<your new-API app id>
      DERIV_API_TOKEN=<your PAT token, starts with pat_>
      DERIV_ACCOUNT_TYPE=demo        <-- keep "demo" until you want live money
      DEFAULT_SYMBOL=R_75
      DEFAULT_TIMEFRAME=5m
    Example:  sudo -u bot nano /opt/bot/.env
EOF
read -r -p "    Press Enter once you have saved .env ..."

echo "==> 6/7 Installing systemd service"
cp "$APP_DIR/deploy/bot-traderx5.service" "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE"

echo "==> 7/7 Verifying"
sleep 3
systemctl --no-pager --full status "$SERVICE" || true
echo
echo "DONE."
echo "  Live logs:      sudo journalctl -u $SERVICE -f"
echo "  Restart:        sudo systemctl restart $SERVICE"
echo "  Bot status API: curl -s http://127.0.0.1:8000/api/auto/status"
echo "  Dashboard:      http://$(hostname -I | awk '{print $1}'):8000"
echo
echo "IMPORTANT: run the bot on ONE machine only (this VPS OR your localhost,"
echo "never both on the same Deriv account)."
