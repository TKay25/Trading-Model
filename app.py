"""Flask Trading Dashboard - Main Application."""
import asyncio
import json
import logging
from flask import Flask, render_template, request, jsonify, session
from config import Config
from deriv_api import DerivAPI
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


def init_services():
    """Initialize the trading service."""
    global trading_service
    trading_service = TradingService(symbol=Config.DEFAULT_SYMBOL)


def _deriv_call(coro_factory, token=None):
    """Open a fresh Deriv WebSocket connection, run coro_factory(api), and clean up.

    Each request gets its own event loop and connection so that a slow or
    failed request can never poison the next one.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    api = DerivAPI(
        app_id=Config.DERIV_APP_ID,
        api_token=token if token is not None else Config.DERIV_API_TOKEN
    )
    try:
        if not loop.run_until_complete(api.connect()):
            raise ConnectionError("Could not connect to the Deriv WebSocket API")
        return loop.run_until_complete(coro_factory(api))
    finally:
        try:
            loop.run_until_complete(api.close())
        except Exception:
            pass
        loop.close()


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

        result = _deriv_call(_connect, token=token)
        if result is None:
            return jsonify({
                "success": False,
                "error": "Authentication failed. Check your API token."
            }), 401
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Connection error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
    """Open a position on Deriv Volatility Indices."""
    data = request.get_json()
    symbol = data.get("symbol", Config.DEFAULT_SYMBOL)
    lot_size = data.get("lot_size", 0.10)
    direction = data.get("direction", "BUY")  # BUY (CALL) or SELL (PUT)
    stop_loss = data.get("stop_loss", 0)
    take_profit = data.get("take_profit", 0)

    if not Config.DERIV_API_TOKEN:
        return jsonify({
            "success": False,
            "error": "No Deriv API token configured. Set DERIV_API_TOKEN in .env"
        }), 400

    # Map lot_size to stake amount and direction to contract type
    amount = lot_size  # lot size maps directly to stake for volatility indices
    contract_type = "CALL" if direction == "BUY" else "PUT"

    async def _trade(api):
        return await api.buy_contract(symbol, amount, contract_type, 1, "t")

    try:
        result = _deriv_call(_trade)

        if "buy" in result:
            return jsonify({
                "success": True,
                "contract_id": result["buy"]["contract_id"],
                "transaction_id": result["buy"].get("transaction_id"),
                "price": result["buy"].get("price"),
                "balance_after": result["buy"].get("balance_after"),
            })
        else:
            return jsonify({"success": False, "error": str(result)}), 400

    except Exception as e:
        logger.error(f"Error placing trade: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/sell", methods=["POST"])
def sell_contract():
    """Sell/close an existing contract."""
    data = request.get_json()
    contract_id = data.get("contract_id")

    if not contract_id:
        return jsonify({"success": False, "error": "contract_id required"}), 400

    async def _sell(api):
        return await api.sell_contract(contract_id)

    try:
        result = _deriv_call(_sell)
        return jsonify({"success": True, "result": result})

    except Exception as e:
        logger.error(f"Error selling contract: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/balance")
def get_balance():
    """Get account balance."""
    if not Config.DERIV_API_TOKEN:
        return jsonify({"success": False, "error": "No API token configured"}), 400

    async def _balance(api):
        return await api.get_balance()

    try:
        result = _deriv_call(_balance)
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
        result = _deriv_call(_portfolio)
        return jsonify({"success": True, "portfolio": result})

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}")
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
