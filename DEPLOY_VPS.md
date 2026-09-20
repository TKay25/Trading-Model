# Deploy BotTraderX5 to a 24/7 Linux VPS (InterServer or any VPS)

This bot is **designed to run headless**: the AutoTrader, all indicators/patterns
(TDI, M/W, H&S, candlesticks), the hard SL/TP exits and the reversal exits run
**server-side** in the Flask process. The dashboard is only a
monitor/control panel. So as long as the process is up, it trades 24/7 **with no
browser open**.

Two supported routes — pick one:

| Route | Use when | Artifacts |
|-------|----------|-----------|
| **A. systemd + venv** (recommended) | You want the simplest, most transparent setup | `deploy/bot-traderx5.service` |
| **B. Docker Compose** | You prefer containers / easy teardown | `Dockerfile`, `docker-compose.yml`, `.dockerignore` |

> **InterServer specifically:** their **shared hosting will NOT work** (this needs a
> long-running Python process + persistent WebSockets). Use their **Linux VPS**
> (Ubuntu 22.04/24.04). Any cheap Linux VPS works the same.

---

## 💡 "Can I just link it via GitHub?"

Depends what you mean:

- **A bare VPS has no "Connect GitHub" button** (that's a PaaS thing). "Linking"
  GitHub on a VPS = `git clone` once + `git pull` for updates.
- **One-command setup:** SSH into the VPS and run
  ```bash
  sudo bash deploy/setup_interserver.sh https://github.com/YOU/REPO.git
  ```
  It installs everything, asks you to fill `.env` once, and starts the bot under
  systemd. (`deploy/setup_interserver.sh`)
- **Push-to-deploy (like a PaaS):** use the included GitHub Actions workflow
  (`.github/workflows/deploy-vps.yml`) — every `git push` to `main` SSHes into
  the VPS, pulls, reinstalls deps, and restarts the bot. Setup + secrets are
  documented at the top of that file.
- **True click-button "connect repo":** skip the VPS and use **Render** — this repo
  already has `render.yaml`, so you just connect the GitHub repo in Render's
  dashboard (use a **paid** always-on plan so it never sleeps).

---

## 0. Prereqs on the VPS

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3.12 python3.12-venv python3-pip curl
```

Create a dedicated user (Route A) and the app dir:

```bash
sudo useradd -m -s /bin/bash bot || true
sudo mkdir -p /opt/bot
sudo chown bot:bot /opt/bot
```

## 1. Get the code + secrets

```bash
cd /opt/bot
sudo -u bot git clone <YOUR-REPO-URL> .
sudo -u bot cp .env.example .env
sudo -u bot nano .env        # fill in the real values
```

`.env` must contain at least:

```
SECRET_KEY=<long-random-string>
DERIV_APP_ID=<your new-API app id>
DERIV_API_TOKEN=<your PAT, starts pat_>
DERIV_ACCOUNT_TYPE=demo      # <-- keep "demo" unless you want live money!
DEFAULT_SYMBOL=R_75
DEFAULT_TIMEFRAME=5m
```

> ⚠️ `config.py` reads `.env` with `os.getenv(...) or default`, and **empty values
> bypass the defaults** — never leave a value blank, always delete the line instead.

---

## Route A — systemd + venv (recommended)

```bash
cd /opt/bot
sudo -u bot python3.12 -m venv .venv
sudo -u bot .venv/bin/pip install --upgrade pip
sudo -u bot .venv/bin/pip install -r requirements.txt
```

Install + start the service:

```bash
sudo cp deploy/bot-traderx5.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-traderx5
sudo systemctl status bot-traderx5 --no-pager
```

Check the logs — you should see it connect to Deriv and start the AutoTrader:

```bash
sudo journalctl -u bot-traderx5 -f
```

**Quick health check** (dashboard responds):

```bash
curl -s http://127.0.0.1:8000/api/auto/status
# -> {"config": {... "profit_target":0, "stop_loss_pct":0.2, "take_profit_pct":5.0, ...}, ...}
```

---

## Route B — Docker Compose

Install Docker + compose plugin, then:

```bash
cd /opt/bot
cp .env.example .env && nano .env      # fill real values
docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

The repo folder is bind-mounted at `/app`, so `auto_trader_config.json` and
`trade_ledger.json` persist on the host and survive rebuilds. Healthcheck hits
`/api/config` every 30s.

---

## 2. Firewall

If the dashboard should be reachable from outside:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp        # direct HTTP (dev)
# or only 80/443 if you add the reverse proxy below
sudo ufw enable
```

Now open `http://<YOUR_VPS_IP>:8000` in a browser and click **Connect** (or it
auto-connects from `.env`). The bot is already trading regardless of whether you
keep that tab open.

---

## 3. (Optional but recommended) HTTPS + domain with Caddy

Caddy gives you automatic HTTPS **and** proxies WebSockets correctly.

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
bot.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```

Point a DNS A record at the VPS IP, then `sudo systemctl reload caddy`. Caddy
handles the TLS cert and the WebSocket `Upgrade` headers for `/api/stream` and
`/api/balance/stream` automatically.

---

## 4. Operations

```bash
# systemd (Route A)
sudo systemctl status bot-traderx5
sudo journalctl -u bot-traderx5 -f
sudo systemctl restart bot-traderx5

# Docker (Route B)
docker compose ps
docker compose logs -f bot
docker compose restart bot
```

**Update to a new version of the code:**

```bash
cd /opt/bot
sudo -u bot git pull                      # (systemd) or just git pull (docker)
sudo systemctl restart bot-traderx5       # Route A
# docker compose up -d --build            # Route B
```

Config that lives in the repo and survives restarts:
- `auto_trader_config.json` — bot toggles, `min_strength`, stake, multiplier,
  `stop_loss_pct`/`take_profit_pct` (hard exits, fractions of the stake),
  `profit_target` (0 = off), `blocked_symbols`/`blocked_timeframes`
- `trade_ledger.json` — history enrichment (symbol/lot/SL/TP)
- `trade_paths.json` — trade-path dataset (MAE/MFE excursions per position)
- `.env` — credentials

---

## 5. ⚠️ Rules & warnings (read these)

1. **ONE instance only.** Never run the VPS bot **and** the localhost bot at the
   same time on the same Deriv account — you'll get two bots racing and
   double-trading (this is also why the start command is a **single** gunicorn
   worker; don't raise `--workers`).
2. **Demo first.** `.env` ships with `DERIV_ACCOUNT_TYPE=demo`. A 24/7 box with a
   **real** account is unattended, real-money trading. Before going real: add or
   tighten the hard exits (`stop_loss_pct`/`take_profit_pct`), raise
   `min_strength`, restrict symbols/timeframes with the dashboard chips, and
   supervise it for a while first.
3. **Deriv connectivity.** The app needs outbound `wss/https` to
   `api.derivws.com`. From a US/EU datacenter this is normally fine — and it
   avoids the corporate-network blocks/503s you may see at work.
4. **Deriv ToS.** Deriv permits API/automated trading on personal accounts, but
   confirm their current acceptable-use policy before running a persistent bot.
5. **Uptime is the bot's uptime.** The AutoTrader lives inside the web process —
   keep the service healthy (`Restart=always` handles crashes/reboots).

---

## Files in this deploy package

```
Dockerfile                     # python:3.12-slim + gunicorn (single gthread worker)
.dockerignore                  # keeps .venv/.git/artifacts out of the build
docker-compose.yml             # single container, repo bind-mounted for persistence
deploy/bot-traderx5.service    # systemd unit (Route A)
DEPLOY_VPS.md                  # this guide
```
