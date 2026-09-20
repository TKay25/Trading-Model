# BotTraderX5 — Deriv auto-trading dashboard + server-side bot
#
# Single-worker gthread is CRITICAL: the app runs background singletons
# (live stream, balance stream, SL/TP position monitor, AutoTrader) in the
# Flask process. More than one worker = duplicated bots racing to trade the
# same account. Threads handle the long-lived SSE streams + concurrent calls.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    FLASK_DEBUG=0

WORKDIR /app

# Dependencies first (better layer caching). Requirements are pinned to the
# verified combo (Werkzeug 3.1.8 avoids the Python 3.14 idna codec crash).
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code. NOTE: for persistence we recommend bind-mounting this repo dir
# (see docker-compose.yml) so auto_trader_config.json and trade_ledger.json
# live on the host and survive container rebuilds.
COPY . .

# Runs as root by default so a host bind-mount keeps write access to the
# config/ledger files. Single-process web service, so this is acceptable.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/config', timeout=5)"

CMD ["gunicorn", "app:app", \
     "--workers", "1", "--threads", "16", "--worker-class", "gthread", \
     "--timeout", "120", "--graceful-timeout", "30", "--keep-alive", "5", \
     "--bind", "0.0.0.0:8000"]
