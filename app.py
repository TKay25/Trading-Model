"""Flask Trading Dashboard - Main Application."""
import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, session, Response
from config import Config
from deriv_api import DerivAPI, SharedDerivConnection
from live_balance_stream import LiveBalanceStream
from live_stream import LiveDerivStream
from position_monitor import PositionMonitor
from trading_service import (
    TradingService, Candle, PatternRecognizer, TDICalculator
)
from auto_trader import AutoTrader

# ---------------------------------------------------------------------------
# idna codec shim (Render crash fix, 2026-08-24)
# Python 3.14 REMOVED the stdlib "idna" codec. Werkzeug binds the Host header
# via server_name.encode("idna"); on a Python build without the codec EVERY
# request dies with "LookupError: unknown encoding: idna" (this crashed the
# Render deployment). Re-register the codec at import time so the app works
# even with an older Werkzeug on a Python 3.14 build that lacks it. Render
# hostnames are ASCII, so an ASCII-passthrough codec is functionally correct.
# ---------------------------------------------------------------------------
import codecs as _codecs
try:
    _codecs.lookup("idna")
except LookupError:
    try:
        import encodings.idna  # noqa: F401  # <3.14: re-registers the stdlib codec
        _codecs.lookup("idna")
    except (LookupError, ImportError):
        def _idna_encode(text, errors="strict"):
            if isinstance(text, str):
                text = text.encode("ascii", errors)
            return bytes(text), len(text)

        def _idna_decode(data, errors="strict"):
            if isinstance(data, bytes):
                data = data.decode("ascii", errors)
            return str(data), len(data)

        def _idna_search(name):
            if name == "idna":
                return _codecs.CodecInfo(
                    name="idna",
                    encode=_idna_encode,
                    decode=_idna_decode,
                    incrementalencoder=None,
                    incrementaldecoder=None,
                    streamreader=None,
                    streamwriter=None,
                )
            return None

        _codecs.register(_idna_search)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

# Global service instances
trading_service: TradingService = None
live_stream: LiveDerivStream = None
balance_stream: LiveBalanceStream = None
position_monitor: PositionMonitor = None
_shared_conn: SharedDerivConnection = None
auto_trader: AutoTrader = None

# In-app ledger of trades placed through this app (contract_id -> metadata).
# Deriv's profit_table/portfolio omit symbol, lot size and SL/TP, so we keep
# them here to enrich the history and positions views. Persisted to a small
# JSON file so the enrichment survives server restarts.
trade_ledger = {}
# Keep more history enriched (Instrument/Type/Timeframe/Signal). 500 was too
# small once auto-trading ran for a while; 5000 keeps ~10x more trades without
# making the JSON ledger (or each save) unwieldy.
MAX_LEDGER = 5000
LEDGER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_ledger.json")


def _load_ledger():
    global trade_ledger
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # JSON object keys are always strings, but Deriv's
                    # profit_table/portfolio return contract_id as an INT and
                    # /api/history & /api/positions look up the ledger by that
                    # int. Normalize keys back to int so enrichment still works
                    # after a restart (otherwise every settled row shows "--").
                    trade_ledger = {
                        int(k): v for k, v in data.items() if str(k).lstrip("-").isdigit()
                    }
        except Exception:
            trade_ledger = {}


def _save_ledger():
    try:
        with open(LEDGER_FILE, "w", encoding="utf-8") as f:
            json.dump(trade_ledger, f)
    except Exception as e:
        logger.warning(f"Failed to save trade ledger: {e}")


def _record_trade_ledger(contract_id, symbol, contract_type, lot_size, multiplier,
                         stop_loss, take_profit, timeframe=None, signal_strength=None):
    """Record a trade in the in-app ledger (history/positions enrichment).

    Deriv's profit_table/portfolio omit symbol, lot size, multiplier and SL/TP,
    so we keep them here. Used by both /api/trade and the server-side AutoTrader.
    `timeframe` is the signal timeframe the AutoTrader opened on (used by the
    same-timeframe reversal exit). `signal_strength` is the reversal strength (%)
    the AutoTrader saw when it opened the trade (0-100); None for manual trades.
    """
    trade_ledger[int(contract_id)] = {
        "symbol": symbol,
        "contract_type": contract_type,
        "lot_size": lot_size,
        "multiplier": multiplier,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "timeframe": timeframe,
        "signal_strength": signal_strength,
    }
    if len(trade_ledger) > MAX_LEDGER:
        for _cid in list(trade_ledger)[: len(trade_ledger) - MAX_LEDGER]:
            trade_ledger.pop(_cid, None)
    _save_ledger()


def _num(v):
    """Best-effort float conversion; None for missing/non-numeric."""
    try:
        return float(v) if v is not None and str(v) != "" else None
    except (TypeError, ValueError):
        return None


def init_services():
    """Initialize services: trading service, live market stream, live balance stream.

    Idempotent: the stream singletons are created+started only once, even
    though init_services() may be invoked multiple times (module import,
    create_app(), and the __main__ block).
    """
    global trading_service, live_stream, balance_stream, position_monitor, auto_trader
    trading_service = TradingService(symbol=Config.DEFAULT_SYMBOL)
    if live_stream is None:
        live_stream = LiveDerivStream()
        live_stream.set_config(
            Config.DEFAULT_SYMBOL,
            Config.TIMEFRAMES.get(Config.DEFAULT_TIMEFRAME, 300)
        )
        live_stream.start()
    if balance_stream is None:
        balance_stream = LiveBalanceStream()
        balance_stream.start()
    if position_monitor is None:
        position_monitor = PositionMonitor()
        position_monitor.start()
    _get_shared_conn()  # start the persistent connection thread
    if auto_trader is None:
        auto_trader = AutoTrader(
            resolve_multiplier=lambda sym, req: _nearest_multiplier(
                req, _get_valid_multipliers(sym) or []),
            record_trade=_record_trade_ledger,
            position_monitor=position_monitor,
            ledger=trade_ledger,
            symbols=Config.VOLATILITY_INDICES.keys(),
            timeframes=Config.TIMEFRAMES,
            app_id=Config.DERIV_APP_ID,
            account_type=Config.DERIV_ACCOUNT_TYPE,
            token=Config.DERIV_API_TOKEN,
        )
        auto_trader.start()


_shared_conn_lock = threading.Lock()


def _get_shared_conn():
    global _shared_conn
    if _shared_conn is None:
        with _shared_conn_lock:
            if _shared_conn is None:
                _shared_conn = SharedDerivConnection(
                    app_id=Config.DERIV_APP_ID,
                    account_type=Config.DERIV_ACCOUNT_TYPE,
                )
                _shared_conn.start()
    return _shared_conn


_public_conn: SharedDerivConnection = None
_public_conn_lock = threading.Lock()


def _get_public_conn():
    """A dedicated PUBLIC Deriv connection for heavy market-data fetches
    (chart candles + the multi-market scanner).

    Market data needs no auth/OTP, and putting it on its own connection means a
    70-request scanner burst can never queue behind account operations on the
    shared connection — which is what caused the "Deriv request timed out after
    45s" 500s on page refresh from Render's datacenter.
    """
    global _public_conn
    if _public_conn is None:
        with _public_conn_lock:
            if _public_conn is None:
                _public_conn = SharedDerivConnection(
                    app_id=Config.DERIV_APP_ID,
                    account_type=Config.DERIV_ACCOUNT_TYPE,
                    public=True,
                )
                _public_conn.start()
    return _public_conn


def _public_deriv_call(coro_factory, timeout=45):
    """Run a market-data coroutine over the dedicated PUBLIC connection."""
    try:
        return _get_public_conn().call(coro_factory, token="", timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        logger.error("Deriv public request timed out after %ss", timeout)
        raise TimeoutError(f"Deriv public request timed out after {timeout}s")


def _session_token():
    """Safe read of the per-session Deriv token (None outside a request context)."""
    try:
        return session.get("deriv_api_token")
    except RuntimeError:
        return None


def _deriv_call(coro_factory, token=None, authenticated=False, timeout=45):
    """Run coro_factory(api) over a SINGLE shared authenticated connection.

    Previously every API call opened its own Deriv WebSocket (connect -> request
    -> close), churning dozens of connections that Deriv throttled / timed out
    from Render's datacenter. Now all routes multiplex over one persistent
    connection: DerivAPI._send_request already routes concurrent requests by
    req_id, and this wraps the call in a hard timeout so no request can hang a
    gunicorn worker (returns a clean TimeoutError instead).

    Token priority (same as before): explicit token > this session's connected
    token > DERIV_API_TOKEN (.env).
    """
    effective_token = token or _session_token() or Config.DERIV_API_TOKEN
    try:
        return _get_shared_conn().call(coro_factory, token=effective_token, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        logger.error("Deriv request timed out after %ss", timeout)
        raise TimeoutError(f"Deriv request timed out after {timeout}s")


@app.after_request
def add_no_cache_headers(response):
    """Prevent browsers from serving stale HTML/JS/CSS between restarts.

    Without this, a browser may cache an old chart.js/indicators.js/index.html
    and keep showing a broken page (no indicator lines, missing history)
    even after the server code is updated.
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# Per-symbol valid multiplier cache (fetched from Deriv's contracts_for).
# Valid multipliers differ per underlying: R_75/R_100 accept 50/100/200/300/500
# but R_50 accepts 80/200/400/600/800 and R_10 accepts 400/1000/2000/3000/4000.
_VALID_MULTIPLIERS = {}

# Known-valid multiplier sets per underlying (discovered via contracts_for).
# Used as an instant fallback so /api/multipliers + auto-trades never block or
# fail when the live contracts_for lookup is slow/throttled (e.g. on Render).
_DEFAULT_MULTIPLIERS = {
    "R_10": [400, 1000, 2000, 3000, 4000],
    "R_25": [160, 400, 800, 1200, 1600],
    "R_50": [80, 200, 400, 600, 800],
    "R_75": [50, 100, 200, 300, 500],
    "R_100": [40, 100, 200, 300, 400],
    "1HZ10V": [400, 1000, 2000, 3000, 4000],
    "1HZ25V": [160, 400, 800, 1200, 1600],
    "1HZ50V": [80, 200, 400, 600, 800],
    "1HZ75V": [50, 100, 200, 300, 500],
    "1HZ100V": [40, 100, 200, 300, 400],
}


def _get_valid_multipliers(symbol):
    """Return the multiplier values Deriv accepts for `symbol` (cached)."""
    if symbol in _VALID_MULTIPLIERS:
        return _VALID_MULTIPLIERS[symbol]
    try:
        async def _fetch(api):
            return await api.get_valid_multipliers(symbol)
        # contracts_for is public market data — use the PUBLIC connection so a
        # cache miss never contends with account requests on the shared conn.
        values = _public_deriv_call(_fetch, timeout=15)
        if isinstance(values, list) and values:
            _VALID_MULTIPLIERS[symbol] = values
            return values
    except Exception as e:
        logger.warning("Failed to fetch valid multipliers for %s: %s", symbol, e)
    # Fall back to the known set so the frontend dropdown + auto-trades always
    # have a valid multiplier, even when Deriv's contracts_for is slow/throttled.
    default = _DEFAULT_MULTIPLIERS.get(symbol, [50, 100, 200, 300, 500])
    _VALID_MULTIPLIERS[symbol] = default
    return default


def _warm_multiplier_cache():
    """Pre-fetch valid multipliers for every scanner symbol in the background.

    Runs once per process on startup so the first /api/multipliers call (and the
    first trade on each symbol) is served instantly from the cache.
    """
    # Keep the startup burst small (2 at a time) — Deriv throttles rapid
    # reconnects, and Render's datacenter connects are slower than local ones.
    symbols = list(getattr(Config, "VOLATILITY_INDICES", []) or [])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(_get_valid_multipliers, symbols))
    logger.info("Multiplier cache warmed for %d symbols", len(symbols))


def _nearest_multiplier(requested, allowed):
    """Pick the allowed multiplier closest to `requested` (ties -> smaller)."""
    if not allowed:
        return requested
    if requested in allowed:
        return requested
    return min(allowed, key=lambda v: (abs(v - requested), v))


# Load any previously-recorded trades so history enrichment survives restarts.
_load_ledger()

# Initialize on startup (needed for gunicorn/production)
init_services()

# Warm the per-symbol multiplier cache in the background so the dropdown and
# auto-trades never wait on a slow first lookup. Daemon thread: harmless if
# the process is torn down mid-warm.
threading.Thread(target=_warm_multiplier_cache, daemon=True).start()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the main dashboard."""
    return render_template(
        "index.html",
        symbols=Config.VOLATILITY_INDICES,
        timeframes=list(Config.TIMEFRAMES.keys()),
        default_symbol=Config.DEFAULT_SYMBOL,
        default_timeframe=Config.DEFAULT_TIMEFRAME,
        connected=bool(Config.DERIV_API_TOKEN)
    )


@app.route("/api/config")
def api_config():
    """Return app configuration for the frontend."""
    return jsonify({
        "symbols": Config.VOLATILITY_INDICES,
        "timeframes": list(Config.TIMEFRAMES.keys()),
        "default_symbol": Config.DEFAULT_SYMBOL,
        "default_timeframe": Config.DEFAULT_TIMEFRAME,
        "has_token": bool(Config.DERIV_API_TOKEN),
    })


@app.route("/api/multipliers")
def api_multipliers():
    """Return the multiplier values Deriv accepts for a given symbol."""
    symbol = request.args.get("symbol") or Config.DEFAULT_SYMBOL
    values = _get_valid_multipliers(symbol)
    return jsonify({"symbol": symbol, "multipliers": values or []})


@app.route("/api/live/config", methods=["POST"])
def live_config():
    """Switch the persistent live stream to a new symbol/timeframe."""
    data = request.get_json() or {}
    symbol = data.get("symbol")
    timeframe = data.get("timeframe")
    granularity = Config.TIMEFRAMES.get(
        timeframe, Config.TIMEFRAMES.get(Config.DEFAULT_TIMEFRAME, 300)
    )
    if not symbol:
        return jsonify({"success": False, "error": "symbol required"}), 400
    live_stream.set_config(symbol, granularity)
    return jsonify({"success": True, "symbol": symbol, "granularity": granularity})


@app.route("/api/stream")
def stream():
    """Server-Sent Events: push live candle updates to the browser."""
    def generate():
        version = live_stream.get_state()["version"]
        yield "retry: 2000\n\n"
        while True:
            updated = live_stream.wait_for_update(version, timeout=25)
            state = live_stream.get_state()
            version = state["version"]
            if updated:
                payload = json.dumps({
                    "symbol": state["symbol"],
                    "granularity": state["granularity"],
                    "version": version,
                    "candles": state["candles"],
                })
                yield f"data: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/balance/stream")
def balance_stream_route():
    """Server-Sent Events: push live balance updates as trades settle."""
    def generate():
        version = balance_stream.get_state()["version"]
        yield "retry: 2000\n\n"
        while True:
            updated = balance_stream.wait_for_update(version, timeout=25)
            state = balance_stream.get_state()
            version = state["version"]
            if updated:
                payload = json.dumps({"version": version, "balance": state["balance"]})
                yield f"data: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/connect", methods=["POST"])
def connect():
    """Connect to Deriv with the provided API token (or the one from .env)."""
    data = request.get_json() or {}
    token = data.get("api_token", "").strip() or Config.DERIV_API_TOKEN

    if not token:
        return jsonify({
            "success": False,
            "error": "No API token. Set DERIV_API_TOKEN in .env or provide one."
        }), 400

    try:
        async def _connect(api):
            if not api._authenticated:
                return None  # authentication failed
            # Fetch balance to confirm the account is live
            balance_data = await api.get_balance()
            balance = balance_data.get("balance", {})
            return {
                "loginid": balance.get("loginid", "unknown"),
                "balance": float(balance.get("balance", 0)),
                "currency": balance.get("currency", "USD"),
            }

        result = _deriv_call(_connect, token=token, authenticated=True)
        if result is None:
            return jsonify({
                "success": False,
                "error": "Authentication failed. Check your API token."
            }), 401

        # Remember the working token for this browser session so that
        # trade/balance/portfolio calls keep using it (even if .env is stale).
        session["deriv_api_token"] = token
        # Start live balance streaming for this account.
        balance_stream.activate(Config.DERIV_APP_ID, token, Config.DERIV_ACCOUNT_TYPE)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Connection error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/disconnect", methods=["POST"])
def disconnect():
    """Forget the connected token for this session."""
    session.pop("deriv_api_token", None)
    balance_stream.deactivate()
    return jsonify({"success": True})


# Small cache of the last good candle set per (symbol, granularity) so the chart
# degrades gracefully when Deriv throttles (e.g. two instances on one account)
# instead of 500ing the page.
_CANDLE_CACHE = {}
_CANDLE_CACHE_MAX_AGE = 300  # seconds


@app.route("/api/candles", methods=["POST"])
def get_candles():
    """Fetch historical candle data from Deriv."""
    data = request.get_json()
    symbol = data.get("symbol", Config.DEFAULT_SYMBOL)
    granularity = Config.TIMEFRAMES.get(
        data.get("timeframe", Config.DEFAULT_TIMEFRAME), 60
    )
    count = data.get("count", 100)
    cache_key = (symbol, granularity)

    async def _fetch(api):
        return await api.get_candles(symbol, granularity, count)

    try:
        # Candles are public market data — run on the dedicated PUBLIC connection
        # so chart loads never contend with account requests.
        result = _public_deriv_call(_fetch)

        if "candles" in result:
            candles = result["candles"]
            _CANDLE_CACHE[cache_key] = (time.time(), candles)
            return jsonify({"success": True, "candles": candles})
        else:
            return jsonify({"success": False, "error": str(result)}), 400

    except Exception as e:
        # Deriv slow/throttled: serve the last good candles instead of 500ing.
        cached = _CANDLE_CACHE.get(cache_key)
        if cached and (time.time() - cached[0]) < _CANDLE_CACHE_MAX_AGE:
            logger.warning("Serving cached candles for %s (gran=%s): %s", symbol, granularity, e)
            return jsonify({"success": True, "candles": cached[1], "cached": True})
        logger.error(f"Error fetching candles: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/scanner", methods=["POST"])
def scanner():
    """Fetch candle history for MULTIPLE symbols AND timeframes over ONE connection.

    The frontend runs the same TDI / M-W / candlestick signal engine on each
    symbol+timeframe locally and alerts the user when a strong (all-aligned)
    BUY/SELL appears on a market that isn't currently on screen.
    """
    data = request.get_json() or {}
    symbol = data.get("symbol", Config.DEFAULT_SYMBOL)
    tf_list = data.get("timeframes") or list(Config.TIMEFRAMES.keys())
    try:
        count = int(data.get("count", 100))
    except (TypeError, ValueError):
        count = 100

    valid = [tf for tf in tf_list if tf in Config.TIMEFRAMES]
    if not valid:
        return jsonify({"success": False, "error": "no valid timeframes"}), 400

    known = list(Config.VOLATILITY_INDICES.keys())
    syms = [s for s in (data.get("symbols") or [symbol]) if s in known] or [Config.DEFAULT_SYMBOL]

    async def _scan(api):
        # Fire all symbol x timeframe fetches but cap concurrency so Deriv's
        # ticks_history rate limit isn't blown by the burst (that caused
        # RateLimit 400s that broke the chart on refresh).
        sem = asyncio.Semaphore(4)

        async def _one(s, tf):
            async with sem:
                return await api.get_candles(s, Config.TIMEFRAMES[tf], count)

        keys = [(s, tf) for s in syms for tf in valid]
        outs = await asyncio.gather(*(_one(s, tf) for (s, tf) in keys),
                                    return_exceptions=True)
        results = []
        for (s, tf), res in zip(keys, outs):
            if isinstance(res, Exception):
                results.append({"symbol": s, "timeframe": tf, "candles": [], "error": str(res)})
            elif isinstance(res, dict) and "candles" in res:
                results.append({"symbol": s, "timeframe": tf, "candles": res["candles"], "error": None})
            else:
                results.append({"symbol": s, "timeframe": tf, "candles": [], "error": str(res)})
        return results

    try:
        # The scanner is display-only now (the server-side AutoTrader trades), so
        # give it a generous timeout — from Render's datacenter a 70-request scan
        # can be slow. It runs on a dedicated PUBLIC connection so it never
        # queues behind account requests on the shared connection.
        results = _public_deriv_call(_scan, timeout=90)
        return jsonify({"success": True, "symbol": symbol, "symbols": syms, "results": results})
    except Exception as e:
        logger.error(f"Error in scanner: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """Run TDI analysis and pattern recognition on candle data."""
    data = request.get_json()
    symbol = data.get("symbol", Config.DEFAULT_SYMBOL)
    granularity = Config.TIMEFRAMES.get(
        data.get("timeframe", Config.DEFAULT_TIMEFRAME), 60
    )
    count = data.get("count", 100)

    async def _fetch(api):
        return await api.get_candles(symbol, granularity, count)

    try:
        result = _deriv_call(_fetch)

        if "candles" not in result:
            return jsonify({"success": False, "error": "No candle data"}), 400

        raw_candles = result["candles"]

        # Convert to Candle objects
        candles = [
            Candle(
                timestamp=c["epoch"],
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"])
            )
            for c in raw_candles
        ]

        # Run analysis
        service = TradingService(symbol=symbol)
        analysis = service.analyze(candles)

        return jsonify({"success": True, "analysis": _serialize_analysis(analysis)})

    except Exception as e:
        logger.error(f"Error analyzing: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _serialize_analysis(analysis: dict) -> dict:
    """Convert analysis result to JSON-serializable dict."""
    result = {
        "symbol": analysis.get("symbol"),
        "current_price": analysis.get("current_price"),
        "candle_count": analysis.get("candle_count"),
        "patterns": analysis.get("patterns", []),
        "tdi": analysis.get("tdi", {}),
    }

    signal = analysis.get("signal")
    if signal:
        result["signal"] = {
            "action": signal.action,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "timestamp": signal.timestamp.isoformat(),
        }
    else:
        result["signal"] = None

    # Remove numpy values (already converted in TDI calculator)
    tdi = result.get("tdi", {})
    for key in list(tdi.keys()):
        if isinstance(tdi[key], (list,)):
            tdi[key] = [float(v) if v is not None else None for v in tdi[key]]
        elif tdi[key] is not None:
            try:
                tdi[key] = float(tdi[key])
            except (TypeError, ValueError):
                pass

    return result


@app.route("/api/trade", methods=["POST"])
def place_trade():
    """Open a traditional binary position (CALL/PUT) with optional SL/TP."""
    data = request.get_json() or {}
    symbol = data.get("symbol", Config.DEFAULT_SYMBOL)
    lot_size = data.get("lot_size", 0.10)
    direction = data.get("direction", "BUY")  # BUY (MULTUP) or SELL (MULTDOWN)
    stop_loss = data.get("stop_loss", 0) or 0
    take_profit = data.get("take_profit", 0) or 0
    break_even = bool(data.get("break_even", False))
    trail = bool(data.get("trail", False))
    multiplier = int(data.get("multiplier", 100) or 100)

    if not Config.DERIV_API_TOKEN:
        return jsonify({
            "success": False,
            "error": "No Deriv API token configured. Set DERIV_API_TOKEN in .env"
        }), 400

    amount = lot_size  # lot size maps directly to the multiplier stake
    contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

    # Different underlyings accept different multiplier values (e.g. R_50 wants
    # 80/200/400/600/800, R_75 wants 50/100/200/300/500). Resolve the requested
    # one to the nearest valid value for this symbol so auto-trades across the
    # scanner never fail with ContractBuyValidationError.
    allowed = _get_valid_multipliers(symbol)
    multiplier_used = _nearest_multiplier(multiplier, allowed or [])

    async def _trade(api):
        # Open-ended MULTIPLIER position: no fixed expiry — it stays open until
        # the SL/TP auto-close monitor acts or the user closes it manually.
        return await api.buy_multiplier(symbol, amount, direction, multiplier_used)

    try:
        result = _deriv_call(_trade, authenticated=True)

        if "buy" in result:
            contract_id = result["buy"]["contract_id"]
            if stop_loss > 0 or take_profit > 0:
                position_monitor.track(contract_id, symbol, stop_loss, take_profit,
                                       break_even=break_even, trail=trail)
            # Record in the in-app ledger so history/positions can show the
            # instrument, lot size and SL/TP (Deriv omits these fields).
            _record_trade_ledger(contract_id, symbol, contract_type, lot_size,
                                 multiplier_used, stop_loss, take_profit)
            return jsonify({
                "success": True,
                "contract_id": contract_id,
                "transaction_id": result["buy"].get("transaction_id"),
                "price": result["buy"].get("buy_price"),
                "balance_after": result["buy"].get("balance_after"),
                "multiplier": multiplier_used,
            })
        else:
            # Extract a friendly message from the Deriv error envelope, e.g.
            # "Please enter a stake amount that's at least 0.35." (InvalidMinStake).
            details = result.get("details", {}) if isinstance(result, dict) else {}
            err = details.get("error", {}) if isinstance(details, dict) else {}
            message = err.get("message") if isinstance(err, dict) else None
            code = err.get("code") if isinstance(err, dict) else None
            logger.warning("Deriv rejected trade (code=%s): %s — symbol=%s lot=%s dir=%s mult=%s",
                           code, message, symbol, lot_size, direction, multiplier)
            return jsonify({"success": False, "error": message or str(result)}), 400

    except Exception as e:
        logger.error(f"Error placing trade: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sell", methods=["POST"])
def sell_contract():
    """Sell/close an existing contract."""
    data = request.get_json() or {}
    contract_id = data.get("contract_id")

    if not contract_id:
        return jsonify({"success": False, "error": "contract_id required"}), 400

    async def _sell(api):
        return await api.sell_contract(contract_id)

    try:
        result = _deriv_call(_sell, authenticated=True)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"].get("message", str(result))}), 400
        position_monitor.untrack(contract_id)
        return jsonify({"success": True, "result": result})

    except Exception as e:
        logger.error(f"Error selling contract: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/close_all", methods=["POST"])
def close_all_positions():
    """Sell/close EVERY open position in one request (concurrently)."""
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    # Pause the server-side AutoTrader FIRST so it can't re-open positions while
    # (or right after) we close everything — otherwise "Close All" looks broken
    # because the bot re-opens trades within seconds.
    auto_trader_paused = False
    if auto_trader is not None:
        auto_trader.update_config(enabled=False)
        auto_trader_paused = True

    async def _close_all(api):
        port = await api.get_portfolio()
        pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
        contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
        cids = [c.get("contract_id") for c in contracts if c.get("contract_id")]
        # Sell every contract CONCURRENTLY over the one connection (req_id
        # multiplexing) — far faster than 40 sequential round-trips.
        outs = await asyncio.gather(
            *(api.sell_contract(cid) for cid in cids), return_exceptions=True)
        closed, failed = [], []
        for cid, o in zip(cids, outs):
            if isinstance(o, Exception):
                failed.append((cid, str(o)))
            elif isinstance(o, dict) and "sell" in o and "error" not in o:
                closed.append(cid)
            else:
                # Deriv returned an error WITHOUT raising — must not count as closed
                msg = ""
                if isinstance(o, dict):
                    e = o.get("error") or {}
                    msg = e.get("message") if isinstance(e, dict) else str(o)
                failed.append((cid, msg or str(o)))
        if failed:
            logger.warning("close_all: %d/%d sells failed (sample %s)",
                           len(failed), len(cids), failed[:5])
        return closed, failed

    try:
        closed, failed = _deriv_call(_close_all, authenticated=True, timeout=60)
        position_monitor.untrack_all()
        return jsonify({"success": True, "closed": len(closed), "failed": len(failed),
                        "auto_trader_paused": auto_trader_paused})
    except Exception as e:
        logger.error(f"Error closing all positions: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/close_symbol", methods=["POST"])
def close_symbol_positions():
    """Sell/close every open position for one symbol. Used by the technical
    reversal exit: when TDI + M/W + candlestick signals flip AGAINST the open
    trade, the frontend closes that symbol's positions even before SL/TP."""
    data = request.get_json() or {}
    symbol = (data.get("symbol") or "").strip()
    if not symbol:
        return jsonify({"success": False, "error": "symbol required"}), 400
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _close_sym(api):
        port = await api.get_portfolio()
        pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
        contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
        closed = []
        for c in contracts:
            cid = c.get("contract_id")
            if not cid or (c.get("underlying_symbol") or "") != symbol:
                continue
            try:
                await api.sell_contract(cid)
                closed.append(cid)
            except Exception:
                continue
        return closed

    try:
        closed = _deriv_call(_close_sym, authenticated=True)
        for cid in closed:
            position_monitor.untrack(cid)
        return jsonify({"success": True, "closed": len(closed), "contract_ids": closed})
    except Exception as e:
        logger.error(f"Error closing symbol {symbol}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/auto/config", methods=["POST"])
def auto_config():
    """Update the SERVER-side auto-trader config (pushed from the dashboard)."""
    if auto_trader is None:
        return jsonify({"success": False, "error": "auto-trader not initialized"}), 500
    data = request.get_json() or {}
    cfg = auto_trader.update_config(**data)
    return jsonify({"success": True, "config": cfg})


@app.route("/api/auto/status")
def auto_status():
    """Server-side auto-trader status (config + last cycle activity)."""
    if auto_trader is None:
        return jsonify({"success": False, "error": "auto-trader not initialized"}), 500
    return jsonify({"success": True, **auto_trader.get_status()})


@app.route("/api/positions")
def get_positions():
    """Get current open positions (portfolio) normalized for the UI, enriched
    with LIVE unrealized P/L.

    The new-API portfolio response does NOT include a current-profit field, so
    each open contract's live P/L is fetched via `proposal_open_contract` (the
    same call the SL/TP auto-close monitor uses successfully).
    """
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _portfolio(api):
        port = await api.get_portfolio()
        pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
        contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
        # Fetch each open contract's live P/L CONCURRENTLY (bounded by a
        # semaphore). Sequential per-contract calls took ~50s with many open
        # positions — that lag made page refreshes feel broken on Render.
        sem = asyncio.Semaphore(10)

        async def _pl(c):
            async with sem:
                cid = c.get("contract_id")
                if not cid:
                    return
                try:
                    poc = await api._send_request({"proposal_open_contract": 1, "contract_id": int(cid)})
                    poc_data = (poc or {}).get("proposal_open_contract") or {}
                    if isinstance(poc_data, dict):
                        c["_profit"] = _num(poc_data.get("profit"))
                        c["_sellable"] = poc_data.get("is_valid_to_sell")
                except Exception:
                    pass

        await asyncio.gather(*(_pl(c) for c in contracts), return_exceptions=True)
        return contracts

    try:
        contracts = _deriv_call(_portfolio, authenticated=True)
        cutoff = session.get("history_cutoff") or 0
        positions = []
        for c in contracts:
            try:
                if cutoff and (c.get("purchase_time") or 0) < cutoff:
                    continue
                buy_price = float(c.get("buy_price", 0))
                payout = float(c.get("payout", 0))
                cid = c.get("contract_id")
                info = trade_ledger.get(cid, {})
                positions.append({
                    "contract_id": cid,
                    "contract_type": c.get("contract_type") or info.get("contract_type"),
                    "symbol": c.get("underlying_symbol") or info.get("symbol"),
                    "stake": buy_price,
                    "lot_size": info.get("lot_size", buy_price),
                    "stop_loss": info.get("stop_loss", 0),
                    "take_profit": info.get("take_profit", 0),
                    "payout": payout,
                    "profit": c.get("_profit"),
                    "is_valid_to_sell": c.get("_sellable", c.get("is_valid_to_sell")),
                    "purchase_time": c.get("purchase_time"),
                    "expiry_time": c.get("expiry_time"),
                    "shortcode": c.get("shortcode"),
                    "limit_order": c.get("limit_order"),
                })
            except (TypeError, ValueError):
                continue
        positions.sort(key=lambda p: p["purchase_time"] or 0, reverse=True)
        return jsonify({"success": True, "positions": positions})

    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# SL/TP are enforced by the background PositionMonitor (binary contracts have
# no native SL/TP on Deriv), so there is no /api/update_contract route.


@app.route("/api/balance")
def get_balance():
    """Get account balance."""
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _balance(api):
        return await api.get_balance()

    try:
        result = _deriv_call(_balance, authenticated=True)
        return jsonify({"success": True, "balance": result})

    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/portfolio")
def get_portfolio():
    """Get current open positions."""
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _portfolio(api):
        return await api.get_portfolio()

    try:
        result = _deriv_call(_portfolio, authenticated=True)
        return jsonify({"success": True, "portfolio": result})

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _build_history(pt_payload, port_payload, cutoff=0):
    """Build normalized trading-history rows from raw Deriv responses.

    Both the profit_table (settled) and portfolio (open) payloads are enriched
    with symbol, lot size, SL/TP, timeframe and signal strength from the in-app
    trade ledger, because Deriv's profit_table/portfolio do not carry those
    fields. Used by /api/history and /api/history/export (full CSV download).
    """
    txns = pt_payload.get("transactions", []) if isinstance(pt_payload, dict) else []
    contracts = port_payload.get("contracts", []) if isinstance(port_payload, dict) else []

    history = []
    open_ids = set()

    # 1) Live (open) trades first, so open positions appear immediately.
    for c in contracts:
        try:
            if cutoff and (c.get("purchase_time") or 0) < cutoff:
                continue
            cid = c.get("contract_id")
            if cid is not None:
                open_ids.add(cid)
            info = trade_ledger.get(cid, {})
            buy_price = float(c.get("buy_price", 0))
            history.append({
                "time": c.get("purchase_time"),
                "contract_id": cid,
                "symbol": info.get("symbol") or c.get("underlying_symbol"),
                "contract_type": info.get("contract_type") or c.get("contract_type"),
                "lot_size": info.get("lot_size", buy_price),
                "stop_loss": info.get("stop_loss", 0),
                "take_profit": info.get("take_profit", 0),
                "timeframe": info.get("timeframe"),
                "signal_strength": info.get("signal_strength"),
                "stake": buy_price,
                "payout": float(c.get("payout", 0)),
                "profit": None,  # unrealized for open positions
                "status": "open",
            })
        except (TypeError, ValueError):
            continue

    # 2) Settled trades from profit_table (skip any still-open duplicates).
    for t in txns:
        try:
            if cutoff and (t.get("purchase_time") or 0) < cutoff:
                continue
            cid = t.get("contract_id")
            if cid in open_ids:
                continue
            info = trade_ledger.get(cid, {})
            buy_price = float(t.get("buy_price", 0))
            payout = float(t.get("payout", 0))
            sell_price = float(t.get("sell_price", 0))
            profit = round(sell_price - buy_price, 2)
            if not t.get("sell_time"):
                status = "open"
            elif profit >= 0:
                status = "won"
            else:
                status = "lost"
            history.append({
                "time": t.get("purchase_time"),
                "contract_id": cid,
                "transaction_id": t.get("transaction_id"),
                "symbol": info.get("symbol"),
                "contract_type": info.get("contract_type"),
                "lot_size": info.get("lot_size", buy_price),
                "stop_loss": info.get("stop_loss", 0),
                "take_profit": info.get("take_profit", 0),
                "timeframe": info.get("timeframe"),
                "signal_strength": info.get("signal_strength"),
                "stake": buy_price,
                "payout": payout,
                "sell_price": sell_price,
                "profit": profit,
                "status": status,
            })
        except (TypeError, ValueError):
            continue

    history.sort(key=lambda h: h["time"] or 0, reverse=True)
    return history


@app.route("/api/history")
def get_history():
    """Get trading history: live (open) positions + settled contracts.

    Both are enriched with symbol, lot size, SL/TP, timeframe and signal
    strength from the in-app trade ledger, because Deriv's profit_table/
    portfolio do not carry those fields.
    """
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _history(api):
        # One connection: settled transactions + open positions (live trades).
        pt = await api.get_profit_table()
        port = await api.get_portfolio()
        return {"profit_table": pt, "portfolio": port}

    try:
        result = _deriv_call(_history, authenticated=True)
        # result wraps the two raw Deriv responses; each raw response carries
        # the actual payload under its own key (profit_table / portfolio).
        pt = result.get("profit_table", {}) if isinstance(result, dict) else {}
        pt_payload = pt.get("profit_table", {}) if isinstance(pt, dict) else {}
        port = result.get("portfolio", {}) if isinstance(result, dict) else {}
        port_payload = port.get("portfolio", {}) if isinstance(port, dict) else {}
        history = _build_history(pt_payload, port_payload, session.get("history_cutoff") or 0)
        return jsonify({"success": True, "history": history})

    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


async def _fetch_full_history(api, page_size=100, max_pages=100):
    """Page through ALL of Deriv's profit_table transactions + open positions.

    Returns (pt_payload, port_payload) ready for _build_history(). Deriv only
    returns a recent slice per call, so we loop with limit/offset.
    """
    offset = 0
    all_txns = []
    while True:
        pt = await api.get_profit_table(limit=page_size, offset=offset)
        pt_payload = pt.get("profit_table", {}) if isinstance(pt, dict) else {}
        txns = pt_payload.get("transactions", []) if isinstance(pt_payload, dict) else []
        all_txns.extend(txns)
        if len(txns) < page_size:
            break
        offset += page_size
        if offset > page_size * max_pages:  # safety cap
            break
    port = await api.get_portfolio()
    port_payload = port.get("portfolio", {}) if isinstance(port, dict) else {}
    return {"transactions": all_txns}, port_payload


@app.route("/api/history/export")
def export_history():
    """Full trading history for the CSV download: ALL settled transactions
    (paged through Deriv's profit_table, which otherwise returns only a recent
    slice) plus open positions, enriched with timeframe and signal strength."""
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _fetch(api):
        return await _fetch_full_history(api)

    try:
        pt_payload, port_payload = _deriv_call(_fetch, authenticated=True, timeout=120)
        history = _build_history(pt_payload, port_payload, session.get("history_cutoff") or 0)
        return jsonify({"success": True, "history": history})
    except Exception as e:
        logger.error(f"Error fetching full history for export: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _signal_bucket(strength):
    """Bucket a 0-100 signal strength into a display range like '50-59%'."""
    if strength is None:
        return None
    try:
        s = float(strength)
    except (TypeError, ValueError):
        return None
    if s < 0:
        s = 0
    b = int(s // 10)
    if b >= 9:
        return "90-100%"
    return f"{b * 10}-{b * 10 + 9}%"


def _stat(items):
    wins = sum(1 for r in items if r.get("status") == "won")
    total = len(items)
    net = round(sum(float(r.get("profit") or 0) for r in items), 2)
    return {
        "wins": wins,
        "losses": total - wins,
        "total": total,
        "win_rate": round(100.0 * wins / total, 1) if total else 0.0,
        "net_profit": net,
        "avg_profit": round(net / total, 2) if total else 0.0,
    }


def _group_stats(groups):
    out = []
    for key, items in groups.items():
        row = _stat(items)
        row["key"] = key
        out.append(row)
    out.sort(key=lambda g: (-g["win_rate"], -g["total"]))
    return out


def _compute_analysis(settled):
    by_instrument = {}
    by_timeframe = {}
    by_signal = {}
    combos = {}
    for r in settled:
        sym = r.get("symbol")
        tf = r.get("timeframe")
        sig = _signal_bucket(r.get("signal_strength"))
        if sym:
            by_instrument.setdefault(sym, []).append(r)
        by_timeframe.setdefault(tf or "N/A", []).append(r)
        if sig:
            by_signal.setdefault(sig, []).append(r)
        if sym and tf and sig:
            combos.setdefault((sym, tf, sig), []).append(r)

    best_setups = []
    for (sym, tf, sig), items in combos.items():
        row = _stat(items)
        row.update({"symbol": sym, "timeframe": tf, "signal_range": sig})
        best_setups.append(row)
    # Only setups with a meaningful sample, ranked by win rate.
    best_setups = [s for s in best_setups if s["total"] >= 3]
    best_setups.sort(key=lambda s: (-s["win_rate"], -s["total"]))

    total = len(settled)
    wins = sum(1 for r in settled if r.get("status") == "won")
    net = round(sum(float(r.get("profit") or 0) for r in settled), 2)
    return {
        "settled": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(100.0 * wins / total, 1) if total else 0.0,
        "net_profit": net,
        "by_instrument": _group_stats(by_instrument),
        "by_timeframe": _group_stats(by_timeframe),
        "by_signal_range": _group_stats(by_signal),
        "best_setups": best_setups[:15],
    }


@app.route("/api/analysis")
def get_analysis():
    """Win/loss analytics by instrument, timeframe and signal-strength range.

    Uses ALL settled history (paged) so the user can see which setups win most
    and sharpen the model (e.g. raise min_strength to the profitable range).
    """
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _fetch(api):
        return await _fetch_full_history(api)

    try:
        pt_payload, port_payload = _deriv_call(_fetch, authenticated=True, timeout=120)
        rows = _build_history(pt_payload, port_payload, session.get("history_cutoff") or 0)
        settled = [r for r in rows if r.get("status") in ("won", "lost")]
        analysis = _compute_analysis(settled)
        return jsonify({"success": True, **analysis})
    except Exception as e:
        logger.error(f"Error computing analysis: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/clear", methods=["POST"])
def clear_trades():
    """Clear the in-app trade ledger and start history fresh from now.

    The trade ledger (symbol/lot/SL/TP enrichment) is wiped, and a per-session
    "history cutoff" timestamp is stored so /api/history and /api/positions only
    return trades placed AFTER the reset — a true fresh start.
    """
    global trade_ledger
    trade_ledger = {}
    if os.path.exists(LEDGER_FILE):
        try:
            os.remove(LEDGER_FILE)
        except Exception:
            pass
    cutoff = int(time.time())
    session["history_cutoff"] = cutoff
    logger.info("Trades cleared; history cutoff set to %s", cutoff)
    return jsonify({"success": True, "cutoff": cutoff})


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"success": False, "error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app():
    """Create and configure the Flask application."""
    init_services()
    return app


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    init_services()
    app.run(debug=debug, host="0.0.0.0", port=port)
