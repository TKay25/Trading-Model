"""Flask Trading Dashboard - Main Application."""
import asyncio
import json
import logging
import os
from flask import Flask, render_template, request, jsonify, session, Response
from config import Config
from deriv_api import DerivAPI
from live_balance_stream import LiveBalanceStream
from live_stream import LiveDerivStream
from position_monitor import PositionMonitor
from trading_service import (
    TradingService, Candle, PatternRecognizer, TDICalculator
)

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

# In-app ledger of trades placed through this app (contract_id -> metadata).
# Deriv's profit_table/portfolio omit symbol, lot size and SL/TP, so we keep
# them here to enrich the history and positions views. Persisted to a small
# JSON file so the enrichment survives server restarts.
trade_ledger = {}
MAX_LEDGER = 500
LEDGER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_ledger.json")


def _load_ledger():
    global trade_ledger
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    trade_ledger = data
        except Exception:
            trade_ledger = {}


def _save_ledger():
    try:
        with open(LEDGER_FILE, "w", encoding="utf-8") as f:
            json.dump(trade_ledger, f)
    except Exception as e:
        logger.warning(f"Failed to save trade ledger: {e}")


def init_services():
    """Initialize services: trading service, live market stream, live balance stream.

    Idempotent: the stream singletons are created+started only once, even
    though init_services() may be invoked multiple times (module import,
    create_app(), and the __main__ block).
    """
    global trading_service, live_stream, balance_stream, position_monitor
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


def _deriv_call(coro_factory, token=None, authenticated=False):
    """Open a fresh Deriv WebSocket connection, run coro_factory(api), and clean up.

    Each request gets its own event loop and connection so that a slow or
    failed request can never poison the next one. When authenticated=True, the
    token priority is: explicit token > this session's connected token >
    DERIV_API_TOKEN (.env), and the new-API OTP flow is used. Public endpoints
    (candles/analyze) connect to the public WS (no token, no OTP).
    """
    effective_token = ""
    if authenticated:
        effective_token = token or session.get("deriv_api_token") or Config.DERIV_API_TOKEN
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    api = DerivAPI(
        app_id=Config.DERIV_APP_ID,
        api_token=effective_token,
        account_type=Config.DERIV_ACCOUNT_TYPE,
    )
    try:
        # connect() raises on failure (with the full error already logged)
        loop.run_until_complete(api.connect(authenticated=authenticated))
        return loop.run_until_complete(coro_factory(api))
    finally:
        try:
            loop.run_until_complete(api.close())
        except Exception:
            pass
        loop.close()


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


# Load any previously-recorded trades so history enrichment survives restarts.
_load_ledger()

# Initialize on startup (needed for gunicorn/production)
init_services()

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


@app.route("/api/candles", methods=["POST"])
def get_candles():
    """Fetch historical candle data from Deriv."""
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

        if "candles" in result:
            candles = result["candles"]
            return jsonify({"success": True, "candles": candles})
        else:
            return jsonify({"success": False, "error": str(result)}), 400

    except Exception as e:
        logger.error(f"Error fetching candles: {e}")
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
    direction = data.get("direction", "BUY")  # BUY (CALL) or SELL (PUT)
    stop_loss = data.get("stop_loss", 0) or 0
    take_profit = data.get("take_profit", 0) or 0

    if not Config.DERIV_API_TOKEN:
        return jsonify({
            "success": False,
            "error": "No Deriv API token configured. Set DERIV_API_TOKEN in .env"
        }), 400

    amount = lot_size  # lot size maps directly to stake for volatility indices
    contract_type = "CALL" if direction == "BUY" else "PUT"

    async def _trade(api):
        # 1-minute contract gives the SL/TP auto-close monitor time to act.
        return await api.buy_contract(symbol, amount, contract_type, 1, "m")

    try:
        result = _deriv_call(_trade, authenticated=True)

        if "buy" in result:
            contract_id = result["buy"]["contract_id"]
            if stop_loss > 0 or take_profit > 0:
                position_monitor.track(contract_id, symbol, stop_loss, take_profit)
            # Record in the in-app ledger so history/positions can show the
            # instrument, lot size and SL/TP (Deriv omits these fields).
            trade_ledger[contract_id] = {
                "symbol": symbol,
                "contract_type": contract_type,
                "lot_size": lot_size,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }
            if len(trade_ledger) > MAX_LEDGER:
                for _cid in list(trade_ledger)[: len(trade_ledger) - MAX_LEDGER]:
                    trade_ledger.pop(_cid, None)
            _save_ledger()
            return jsonify({
                "success": True,
                "contract_id": contract_id,
                "transaction_id": result["buy"].get("transaction_id"),
                "price": result["buy"].get("buy_price"),
                "balance_after": result["buy"].get("balance_after"),
            })
        else:
            # Extract a friendly message from the Deriv error envelope, e.g.
            # "Please enter a stake amount that's at least 0.35." (InvalidMinStake).
            details = result.get("details", {}) if isinstance(result, dict) else {}
            err = details.get("error", {}) if isinstance(details, dict) else {}
            message = err.get("message") if isinstance(err, dict) else None
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


@app.route("/api/positions")
def get_positions():
    """Get current open positions (portfolio) normalized for the UI."""
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _portfolio(api):
        return await api.get_portfolio()

    try:
        result = _deriv_call(_portfolio, authenticated=True)
        pt = result.get("portfolio", {}) if isinstance(result, dict) else {}
        contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
        positions = []
        for c in contracts:
            try:
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


@app.route("/api/history")
def get_history():
    """Get trading history: live (open) positions + settled contracts.

    Both are enriched with symbol, lot size and SL/TP from the in-app trade
    ledger, because Deriv's profit_table/portfolio do not carry those fields.
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
        txns = pt_payload.get("transactions", []) if isinstance(pt_payload, dict) else []
        port = result.get("portfolio", {}) if isinstance(result, dict) else {}
        port_payload = port.get("portfolio", {}) if isinstance(port, dict) else {}
        contracts = port_payload.get("contracts", []) if isinstance(port_payload, dict) else []

        history = []
        open_ids = set()

        # 1) Live (open) trades first, so open positions appear immediately.
        for c in contracts:
            try:
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
                    "stake": buy_price,
                    "payout": payout,
                    "sell_price": sell_price,
                    "profit": profit,
                    "status": status,
                })
            except (TypeError, ValueError):
                continue

        history.sort(key=lambda h: h["time"] or 0, reverse=True)
        return jsonify({"success": True, "history": history})

    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
