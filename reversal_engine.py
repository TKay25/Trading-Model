"""Server-side reversal signal engine.

A faithful Python port of the frontend `SignalEngine` (static/js/indicators.js)
so the server-side auto-trader uses the EXACT same strategy the dashboard
displays: TDI (per TDI indicator.pdf) + Bollinger/RSI bounce + reversal chart
patterns (M/W, H&S, candlesticks), combined into a BUY/SELL/NEUTRAL reversal
verdict, with a 0..1 strength that can be confirmed on 1m candles.

Candles are passed as `Candle` objects (trading_service.Candle) — the same
objects PatternRecognizer consumes.
"""
import numpy as np

from trading_service import Candle  # noqa: F401  (documented input type)


class ReversalEngine:
    """TDI + reversal confluence + strength (mirrors the frontend engine)."""

    # reversal-only pattern weights (continuation/momentum types excluded)
    REVERSAL_WEIGHTS = {
        # bullish reversal patterns
        "hammer": 1, "inverted_hammer": 1, "dragonfly_doji": 1,
        "bullish_engulfing": 2, "bullish_harami": 1, "piercing_line": 1,
        "tweezer_bottom": 1, "morning_star": 2,
        "double_bottom": 2, "inverted_head_shoulders": 2,
        # bearish reversal patterns
        "hanging_man": 1, "shooting_star": 1, "gravestone_doji": 1,
        "bearish_engulfing": 2, "bearish_harami": 1, "dark_cloud_cover": 1,
        "tweezer_top": 1, "evening_star": 2,
        "double_top": 2, "head_and_shoulders": 2,
    }

    def __init__(self, rsi_period=13, signal_period=2, smoothed_period=7,
                 bb_period=34, bb_deviation=1.619):
        self.rsi_period = rsi_period
        self.signal_period = signal_period
        self.smoothed_period = smoothed_period
        self.bb_period = bb_period
        self.bb_deviation = bb_deviation

    # ------------------------------------------------------------- TDI
    def tdi(self, candles):
        """Frontend-style TDI: RSI(13), SMA(RSI,2)=signal, SMA(RSI,7)=smoothed,
        SMA(RSI,34)=market base, Bollinger(RSI,34,1.619) bands."""
        closes = np.array([float(c.close) for c in candles], dtype=float)
        rsi = self._rsi(closes, self.rsi_period)
        signal = self._sma(rsi, self.signal_period)
        smoothed = self._sma(rsi, self.smoothed_period)
        market_base = self._sma(rsi, self.bb_period)
        upper, lower = self._bollinger(rsi, self.bb_period, self.bb_deviation)
        return {
            "full_rsi": rsi.tolist(),
            "full_signal": signal.tolist(),
            "full_rsi_smoothed": smoothed.tolist(),
            "full_market_base": market_base.tolist(),
            "full_upper_band": upper.tolist(),
            "full_lower_band": lower.tolist(),
        }

    def _rsi(self, prices, period):
        # exact port of frontend TDIIndicator._calculateRSI (Wilder smoothing)
        n = len(prices)
        deltas = np.zeros(n)
        deltas[1:] = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.zeros(n)
        avg_loss = np.zeros(n)
        rsi = np.full(n, 50.0)
        if n <= period:
            return rsi
        avg_gain[period] = float(gains[1:period + 1].mean())
        avg_loss[period] = float(losses[1:period + 1].mean())
        for i in range(period + 1, n):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period
        for i in range(period, n):
            if avg_loss[i] == 0:
                rsi[i] = 100.0
            else:
                rsi[i] = 100 - (100 / (1 + avg_gain[i] / avg_loss[i]))
        return rsi

    def _sma(self, data, period):
        # frontend TDIIndicator._sma
        n = len(data)
        if period <= 1:
            return data.astype(float).copy()
        out = np.zeros(n)
        csum = np.cumsum(np.insert(data, 0, 0.0))
        out[period - 1:] = (csum[period:] - csum[:n - period + 1]) / period
        out[:period - 1] = data[:period - 1]
        return out

    def _bollinger(self, data, period, deviation):
        # frontend TDIIndicator._calculateBollingerBands
        mid = self._sma(data, period)
        n = len(data)
        upper = np.zeros(n)
        lower = np.zeros(n)
        for i in range(n):
            if i < period - 1:
                upper[i] = mid[i] + deviation * 10
                lower[i] = mid[i] - deviation * 10
            else:
                seg = data[i - period + 1:i + 1]
                std = float(np.sqrt(np.mean((seg - mid[i]) ** 2)))
                upper[i] = mid[i] + deviation * std
                lower[i] = mid[i] - deviation * std
        return upper, lower

    # ------------------------------------------------------ reversal verdict
    def reversal(self, tdi, patterns, candles):
        """Combined BUY/SELL/NEUTRAL — needs TDI bounce AND a reversal pattern
        in the SAME direction (mirrors frontend _reversalSignal)."""
        tdi_rev = self._tdi_reversal(tdi)
        pat_rev = self._reversal_patterns(patterns, len(candles))
        if tdi_rev == "BUY" and pat_rev == "BUY":
            return "BUY"
        if tdi_rev == "SELL" and pat_rev == "SELL":
            return "SELL"
        return "NEUTRAL"

    def _tdi_reversal(self, tdi):
        # port of frontend SignalEngine._tdiReversal
        rsi = tdi.get("full_rsi") or []
        n = len(rsi)
        if n < 40:
            return "NEUTRAL"

        def last(a, k=0):
            return a[max(0, n - 1 - k)]

        green = tdi.get("full_signal") or []
        red = tdi.get("full_rsi_smoothed") or []
        up = tdi.get("full_upper_band") or []
        lo = tdi.get("full_lower_band") or []

        was_below = was_above = False
        for k in range(1, 7):
            if last(rsi, k) < last(lo, k) or last(rsi, k) < 30:
                was_below = True
            if last(rsi, k) > last(up, k) or last(rsi, k) > 70:
                was_above = True

        rsi_now = last(rsi)
        reclaimed_low = rsi_now > last(lo)
        reclaimed_high = rsi_now < last(up)

        buy_flip = sell_flip = False
        for k in range(1, 4):
            gp, rp = last(green, k), last(red, k)
            gc, rc = last(green, k - 1), last(red, k - 1)
            if gc > rc and gp <= rp:
                buy_flip = True
            if gc < rc and gp >= rp:
                sell_flip = True

        if was_below and reclaimed_low and (buy_flip or rsi_now >= 45):
            return "BUY"
        if was_above and reclaimed_high and (sell_flip or rsi_now <= 55):
            return "SELL"
        return "NEUTRAL"

    def _reversal_patterns(self, patterns, candle_count):
        # port of frontend SignalEngine._reversalPatterns (reversal-only, recent 12)
        bull = bear = 0
        for p in patterns or []:
            if p.get("index", 0) < candle_count - 12:
                continue
            weight = self.REVERSAL_WEIGHTS.get(p.get("type"))
            if not weight:
                continue
            if p.get("direction") == "bullish":
                bull += weight
            elif p.get("direction") == "bearish":
                bear += weight
        if bull > bear:
            return "BUY"
        if bear > bull:
            return "SELL"
        return "NEUTRAL"

    # ------------------------------------------------------------- strength
    def strength(self, action, tdi, candles, confirm_candles=None):
        """0..1 — how applicable the signal STILL is. TDI gate on the signal
        timeframe; momentum/freshness on the 1m confirm candles when given
        (port of frontend _strength(action, tdi, candles, confirmCandles))."""
        if action not in ("BUY", "SELL"):
            return 0.0
        n = len(candles)
        if n < 5:
            return 0.0

        # TDI hard gate on the SIGNAL timeframe (green/red flip against -> 0)
        g = None
        r = None
        fs = tdi.get("full_signal") if tdi else None
        fr = tdi.get("full_rsi_smoothed") if tdi else None
        if fs and fr:
            g = fs[n - 1]
            r = fr[n - 1]
        tdi_ok = True
        if g is not None and r is not None:
            tdi_ok = g > r if action == "BUY" else g < r
        if not tdi_ok:
            return 0.0

        cc = confirm_candles if (confirm_candles and len(confirm_candles)) else candles
        m = len(cc)
        if m < 5:
            return 0.0

        agree = 0
        W = 6
        for k in range(1, W + 1):
            c = cc[m - k]
            if (action == "BUY" and c.close > c.open) or \
               (action == "SELL" and c.close < c.open):
                agree += 1
        momentum = agree / W

        consecutive = 0
        for k in range(1, m + 1):
            c = cc[m - k]
            agrees = (action == "BUY" and c.close > c.open) or \
                     (action == "SELL" and c.close < c.open)
            if agrees:
                consecutive += 1
            else:
                break
        freshness = min(1.0, consecutive / 3.0)

        s = (0.4 + 0.6 * momentum) * (0.3 + 0.7 * freshness)
        return round(max(0.0, min(1.0, s)) * 100) / 100
