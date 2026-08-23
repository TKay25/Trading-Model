"""Persistent Deriv WebSocket live stream (background thread).

Maintains a single, persistent WebSocket connection to Deriv's public market-data
endpoint and streams live candles for the active symbol/timeframe. The rest of the
app reads the in-memory candle buffer (no per-request connections for market data)
and can block on ``wait_for_update`` to receive SSE-style push notifications.
"""
import asyncio
import json
import logging
import threading

import websockets

logger = logging.getLogger(__name__)

PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"


class LiveDerivStream:
    """Persistent Deriv public WebSocket that streams live candles."""

    def __init__(self):
        self._symbol = "R_75"
        self._granularity = 300
        self._candles = []          # [{epoch, open, high, low, close}]
        self._version = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._ws = None
        self._thread = None
        self._running = False
        self._sub_id = None         # current Deriv subscription id (same thread only)

    # ---- lifecycle ----------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="deriv-live")
        self._thread.start()
        logger.info("Live Deriv stream started (symbol=%s)", self._symbol)

    def stop(self):
        self._running = False
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=3)

    # ---- control / read -------------------------------------------------
    def set_config(self, symbol, granularity):
        with self._lock:
            if (symbol, granularity) != (self._symbol, self._granularity):
                self._symbol = symbol
                self._granularity = granularity
                # notify_all must be called while holding the lock — do NOT wrap
                # in `with self._cond:` here (that re-acquires the same lock and
                # deadlocks, since self._lock is a non-reentrant Lock).
                self._cond.notify_all()

    def get_state(self):
        with self._lock:
            return {
                "symbol": self._symbol,
                "granularity": self._granularity,
                "version": self._version,
                "candles": list(self._candles),
            }

    def wait_for_update(self, version, timeout=30.0):
        """Block until the candle buffer version exceeds `version`, or timeout."""
        with self._cond:
            if self._version > version:
                return True
            self._cond.wait(timeout)
            return self._version > version

    # ---- background loop -------------------------------------------------
    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._stream_loop())
        except Exception:
            logger.exception("Live Deriv stream loop crashed")
        finally:
            loop.close()
            logger.info("Live Deriv stream stopped")

    async def _stream_loop(self):
        subscribed = None  # (symbol, granularity) currently subscribed
        while self._running:
            if self._ws is None:
                try:
                    self._ws = await asyncio.wait_for(
                        websockets.connect(PUBLIC_WS_URL, ping_interval=20, ping_timeout=20),
                        timeout=20,
                    )
                    logger.info("Live stream connected to Deriv (public)")
                except Exception as e:
                    logger.error(f"Live stream connect failed: {e!r}")
                    await asyncio.sleep(3)
                    continue

            with self._lock:
                want = (self._symbol, self._granularity)

            if subscribed != want:
                try:
                    # Drop the previous subscription if we know its id.
                    if self._sub_id:
                        await self._ws.send(json.dumps({"forget": self._sub_id}))
                    await self._ws.send(json.dumps({
                        "ticks_history": want[0],
                        "adjust_start_time": 1,
                        "count": 200,
                        "end": "latest",
                        "style": "candles",
                        "granularity": want[1],
                        "subscribe": 1,
                        "req_id": 1,
                    }))
                    subscribed = want
                    self._sub_id = None
                    with self._lock:
                        self._candles = []
                        self._version += 1
                        self._cond.notify_all()
                    logger.info("Live stream subscribed to %s @ %ss", want[0], want[1])
                except Exception as e:
                    logger.error(f"Live stream subscribe failed: {e!r}")
                    await self._close_ws()
                    subscribed = None
                    continue

            try:
                message = await asyncio.wait_for(self._ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue  # no new message; re-check config / connection
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Live stream connection closed; reconnecting")
                await self._close_ws()
                subscribed = None
                continue
            except Exception as e:
                logger.error(f"Live stream recv error: {e!r}")
                await self._close_ws()
                subscribed = None
                continue

            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            # Track the subscription id so we can forget it on switch.
            sub = (data.get("subscription") or {}).get("id")
            if sub and subscribed is not None:
                self._sub_id = sub

            # Only apply data that belongs to the current subscription.
            if "candles" in data:
                echo = data.get("echo_req") or {}
                if (echo.get("ticks_history"), echo.get("granularity")) == subscribed:
                    self._replace_candles(data.get("candles", []))
            elif data.get("msg_type") == "ohlc":
                ohlc = data.get("ohlc") or {}
                if (ohlc.get("symbol"), ohlc.get("granularity")) == subscribed:
                    self._apply_ohlc(ohlc)

        await self._close_ws()

    async def _close_ws(self):
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    # ---- buffer updates ---------------------------------------------------
    def _replace_candles(self, candles):
        normalized = []
        for c in candles or []:
            try:
                normalized.append({
                    "epoch": int(c["epoch"]),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        with self._lock:
            self._candles = normalized
            self._version += 1
            self._cond.notify_all()

    def _apply_ohlc(self, ohlc):
        if not ohlc:
            return
        try:
            open_time = int(ohlc["open_time"])
            candle = {
                "epoch": open_time,
                "open": float(ohlc["open"]),
                "high": float(ohlc["high"]),
                "low": float(ohlc["low"]),
                "close": float(ohlc["close"]),
            }
        except (KeyError, TypeError, ValueError):
            return
        with self._lock:
            if self._candles and self._candles[-1]["epoch"] == open_time:
                last = self._candles[-1]
                last["high"] = max(last["high"], candle["high"])
                last["low"] = min(last["low"], candle["low"])
                last["close"] = candle["close"]
            else:
                for i, c in enumerate(self._candles):
                    if c["epoch"] == open_time:
                        self._candles[i] = candle
                        break
                else:
                    self._candles.append(candle)
            self._version += 1
            self._cond.notify_all()
