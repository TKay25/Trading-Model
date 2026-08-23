"""Trading strategy and analysis service."""
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:
    """OHLC candle data."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class TradeSignal:
    """Represents a trading signal."""
    symbol: str
    action: str  # 'BUY' or 'SELL'
    confidence: float  # 0.0 to 1.0
    timestamp: datetime
    reason: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class TDICalculator:
    """Trader's Dynamic Index (TDI) indicator calculator.

    TDI consists of:
    1. RSI (Relative Strength Index)
    2. RSI Smoothed (Moving Average of RSI)
    3. Market Base Line (Moving Average of price)
    4. Volatility Bands (Standard Deviation of RSI smoothed)
    """

    def __init__(self, rsi_period: int = 13, rsi_ma_period: int = 8,
                 price_ma_period: int = 34, band_multiplier: float = 1.6185):
        self.rsi_period = rsi_period
        self.rsi_ma_period = rsi_ma_period
        self.price_ma_period = price_ma_period
        self.band_multiplier = band_multiplier

    def calculate(self, candles: List[Candle]) -> Dict:
        """Calculate TDI values from candle data."""
        closes = np.array([c.close for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])

        # 1. Calculate RSI
        rsi = self._calculate_rsi(closes, self.rsi_period)

        # 2. RSI Smoothed (Moving Average of RSI)
        rsi_smoothed = self._sma(rsi, self.rsi_ma_period)

        # 3. Market Base Line (Simple Moving Average of price)
        market_base = self._sma(closes, self.price_ma_period)

        # 4. Volatility Bands around RSI smoothed
        upper_band, lower_band = self._calculate_volatility_bands(
            rsi_smoothed, closes, self.band_multiplier
        )

        return {
            "rsi": rsi[-1] if len(rsi) > 0 else None,
            "rsi_smoothed": rsi_smoothed[-1] if len(rsi_smoothed) > 0 else None,
            "market_base_line": market_base[-1] if len(market_base) > 0 else None,
            "upper_band": upper_band[-1] if len(upper_band) > 0 else None,
            "lower_band": lower_band[-1] if len(lower_band) > 0 else None,
            "full_rsi": rsi.tolist() if len(rsi) > 0 else [],
            "full_rsi_smoothed": rsi_smoothed.tolist() if len(rsi_smoothed) > 0 else [],
            "full_market_base": market_base.tolist() if len(market_base) > 0 else [],
            "full_upper_band": upper_band.tolist() if len(upper_band) > 0 else [],
            "full_lower_band": lower_band.tolist() if len(lower_band) > 0 else [],
        }

    def _calculate_rsi(self, prices: np.ndarray, period: int) -> np.ndarray:
        """Calculate Relative Strength Index."""
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.zeros_like(prices)
        avg_loss = np.zeros_like(prices)

        avg_gain[period] = np.mean(gains[:period])
        avg_loss[period] = np.mean(losses[:period])

        for i in range(period + 1, len(prices)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

        rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss != 0)
        rsi = 100 - (100 / (1 + rs))
        rsi[:period] = 50  # Default for initial values

        return rsi

    def _sma(self, data: np.ndarray, period: int) -> np.ndarray:
        """Simple Moving Average."""
        sma = np.zeros_like(data)
        cumsum = np.cumsum(data)
        sma[period - 1:] = (cumsum[period - 1:] - np.concatenate([[0], cumsum[:-period]])) / period
        sma[:period - 1] = data[:period - 1]
        return sma

    def _calculate_volatility_bands(self, rsi_smoothed: np.ndarray,
                                    prices: np.ndarray, mult: float) -> Tuple[np.ndarray, np.ndarray]:
        """Calculate volatility bands around the RSI smoothed line."""
        # Standard deviation of price changes scaled to RSI level
        price_changes = np.diff(prices, prepend=prices[0])
        std_dev = np.zeros_like(rsi_smoothed)

        for i in range(1, len(rsi_smoothed)):
            window = max(0, i - self.price_ma_period)
            std_dev[i] = np.std(price_changes[window:i + 1])

        # Scale std dev to RSI range (0-100)
        scaled_std = std_dev * mult
        upper_band = rsi_smoothed + scaled_std
        lower_band = rsi_smoothed - scaled_std

        return upper_band, lower_band


class PatternRecognizer:
    """Recognizes market reversal patterns on candle data."""

    @staticmethod
    def detect_reversal_patterns(candles: List[Candle]) -> List[Dict]:
        """Detect common reversal patterns with strict criteria to avoid false positives."""
        patterns = []
        if len(candles) < 4:
            return patterns

        for i in range(2, len(candles)):
            c1, c2, c3 = candles[i - 2], candles[i - 1], candles[i]

            body = abs(c3.close - c3.open)
            total_range = c3.high - c3.low
            if total_range == 0:
                continue
            body_ratio = body / total_range

            upper_wick = c3.high - max(c3.open, c3.close)
            lower_wick = min(c3.open, c3.close) - c3.low

            # --- Engulfing ---
            # Bullish Engulfing
            body2 = abs(c2.close - c2.open)
            body3 = abs(c3.close - c3.open)
            if (body2 > 0 and body3 > 0 and
                c2.close < c2.open and
                c3.close > c3.open and
                c3.open < c2.close and
                c3.close > c2.open):
                patterns.append({
                    "index": i, "type": "bullish_engulfing",
                    "direction": "bullish", "strength": "strong",
                    "timestamp": c3.timestamp
                })
                continue

            # Bearish Engulfing
            if (body2 > 0 and body3 > 0 and
                c2.close > c2.open and
                c3.close < c3.open and
                c3.open > c2.close and
                c3.close < c2.open):
                patterns.append({
                    "index": i, "type": "bearish_engulfing",
                    "direction": "bearish", "strength": "strong",
                    "timestamp": c3.timestamp
                })
                continue

            # --- Reversal bars (hammer / shooting star) ---
            # Skip candles where body dominates (>60% of range)
            if body_ratio > 0.6:
                continue

            dominant_wick = max(upper_wick, lower_wick)
            wick_ratio = dominant_wick / total_range

            # Need wick >= 55% of total range AND >= 3x body
            if wick_ratio < 0.55 or dominant_wick < body * 3:
                continue

            # Trend context: hammer should follow a downtrend, star an uptrend
            trend_down = i >= 3 and candles[i - 1].close < candles[i - 3].close
            trend_up = i >= 3 and candles[i - 1].close > candles[i - 3].close

            if lower_wick > upper_wick and (trend_down or i < 3):
                patterns.append({
                    "index": i, "type": "hammer",
                    "direction": "bullish", "strength": "medium",
                    "timestamp": c3.timestamp
                })
            elif upper_wick > lower_wick and (trend_up or i < 3):
                patterns.append({
                    "index": i, "type": "shooting_star",
                    "direction": "bearish", "strength": "medium",
                    "timestamp": c3.timestamp
                })

        # M (double-top) / W (double-bottom) reversal patterns
        patterns.extend(_detect_double_patterns(candles))

        # Continuation patterns: Bullish/Bearish Flags and Pennants
        patterns.extend(_detect_continuation_patterns(candles))

        return patterns


def _detect_double_patterns(candles: List[Candle]) -> List[Dict]:
    """Detect M (double-top) and W (double-bottom) reversal patterns.

    A double top (M) = two near-equal peaks with a valley between, confirmed by
    a close below the neckline. A double bottom (W) is the mirror image.
    """
    patterns = []
    n = len(candles)
    if n < 16:
        return patterns

    w = 3  # swing window (bars each side)
    highs = []  # (index, price)
    lows = []
    for i in range(w, n - w):
        is_high = all(candles[j].high <= candles[i].high for j in range(i - w, i + w + 1))
        is_low = all(candles[j].low >= candles[i].low for j in range(i - w, i + w + 1))
        if is_high:
            highs.append((i, candles[i].high))
        if is_low:
            lows.append((i, candles[i].low))

    tol = 0.01  # 1% tolerance for matching peak/valley heights

    def _record(type_, direction, c, strength):
        """Keep only the strongest pattern per (type, confirmation bar)."""
        for i, existing in enumerate(patterns):
            if existing["type"] == type_ and existing["index"] == c:
                if strength > patterns[i].get("_strength", 0):
                    patterns[i]["_strength"] = strength
                return
        patterns.append({
            "index": c, "type": type_, "direction": direction,
            "strength": "strong", "timestamp": candles[c].timestamp,
            "_strength": strength,
        })

    # Double Top (M)
    for ai, (i1, p1) in enumerate(highs):
        for i2, p2 in highs[ai + 1:]:
            if i2 - i1 < 6:
                continue
            if abs(p2 - p1) > p1 * tol:
                continue
            neck = None
            for li, lp in lows:
                if i1 < li < i2 and (neck is None or lp < neck[1]):
                    neck = (li, lp)
            if neck is None:
                continue
            for c in range(i2 + 1, n):
                if candles[c].close < neck[1]:
                    _record("double_top", "bearish", c, p1 - neck[1])
                    break

    # Double Bottom (W)
    for ai, (i1, p1) in enumerate(lows):
        for i2, p2 in lows[ai + 1:]:
            if i2 - i1 < 6:
                continue
            if abs(p2 - p1) > p1 * tol:
                continue
            neck = None
            for hi, hp in highs:
                if i1 < hi < i2 and (neck is None or hp > neck[1]):
                    neck = (hi, hp)
            if neck is None:
                continue
            for c in range(i2 + 1, n):
                if candles[c].close > neck[1]:
                    _record("double_bottom", "bullish", c, neck[1] - p1)
                    break

    # Head & Shoulders (bearish): left shoulder, higher head, right shoulder
    # near-equal to the left; neckline through the two valleys; close below.
    for ai, (i1, p1) in enumerate(highs):
        for bi in range(ai + 1, len(highs)):
            i2, p2 = highs[bi]
            if i2 - i1 < 3:
                continue
            if p2 <= p1:  # head must be higher than left shoulder
                continue
            for ci in range(bi + 1, len(highs)):
                i3, p3 = highs[ci]
                if i3 - i2 < 3:
                    continue
                if i3 - i1 < 10:  # meaningful span
                    continue
                if p3 >= p2:  # head must stay the highest
                    continue
                if abs(p3 - p1) > p1 * tol * 1.5:  # shoulders ~equal
                    continue
                if p2 < max(p1, p3) * (1 + 0.0015):  # head protrudes above shoulders
                    continue
                n1 = None
                for li, lp in lows:
                    if i1 < li < i2 and (n1 is None or lp < n1[1]):
                        n1 = (li, lp)
                n2 = None
                for li, lp in lows:
                    if i2 < li < i3 and (n2 is None or lp < n2[1]):
                        n2 = (li, lp)
                if n1 is None or n2 is None:
                    continue
                t1 = candles[n1[0]].timestamp
                t2 = candles[n2[0]].timestamp
                span = (t2 - t1) or 1
                for c in range(i3 + 1, n):
                    t = candles[c].timestamp
                    neck = n1[1] + (n2[1] - n1[1]) * ((t - t1) / span)
                    if candles[c].close < neck:
                        _record("head_and_shoulders", "bearish", c,
                                max(p1, p3) - min(n1[1], n2[1]))
                        break

    # Inverted Head & Shoulders (bullish): mirror image on the lows.
    for ai, (i1, p1) in enumerate(lows):
        for bi in range(ai + 1, len(lows)):
            i2, p2 = lows[bi]
            if i2 - i1 < 3:
                continue
            if p2 >= p1:  # head must be lower than left shoulder
                continue
            for ci in range(bi + 1, len(lows)):
                i3, p3 = lows[ci]
                if i3 - i2 < 3:
                    continue
                if i3 - i1 < 10:  # meaningful span
                    continue
                if p3 <= p2:  # head must stay the lowest
                    continue
                if abs(p3 - p1) > p1 * tol * 1.5:  # shoulders ~equal
                    continue
                if p2 > min(p1, p3) * (1 - 0.0015):  # head protrudes below shoulders
                    continue
                n1 = None
                for hi, hp in highs:
                    if i1 < hi < i2 and (n1 is None or hp > n1[1]):
                        n1 = (hi, hp)
                n2 = None
                for hi, hp in highs:
                    if i2 < hi < i3 and (n2 is None or hp > n2[1]):
                        n2 = (hi, hp)
                if n1 is None or n2 is None:
                    continue
                t1 = candles[n1[0]].timestamp
                t2 = candles[n2[0]].timestamp
                span = (t2 - t1) or 1
                for c in range(i3 + 1, n):
                    t = candles[c].timestamp
                    neck = n1[1] + (n2[1] - n1[1]) * ((t - t1) / span)
                    if candles[c].close > neck:
                        _record("inverted_head_shoulders", "bullish", c,
                                max(n1[1], n2[1]) - min(p1, p3))
                        break

    # Strip the internal strength marker before returning.
    for p in patterns:
        p.pop("_strength", None)

    return patterns[-8:]


def _detect_continuation_patterns(candles: List[Candle]) -> List[Dict]:
    """Detect Bullish/Bearish Flags and Pennants (continuation patterns).

    A flag = a strong "pole" move (rocket up / waterfall down) followed by a
    TIGHT channel (rectangle) sloping against the trend, then a breakout beyond
    the channel line. A pennant = the same pole but the resting period is a
    small converging (symmetrical) triangle instead of a rectangle.
    """
    patterns = []
    n = len(candles)
    if n < 30:
        return patterns

    MIN_POLE, MAX_POLE = 4, 14
    MIN_FLAG, MAX_FLAG = 4, 14
    POLE_PCT = 0.004    # min pole range (0.4% of price)
    TIGHT_PCT = 0.007   # max flag range (0.7% of price)
    TIGHT_FACTOR = 0.7  # flag range < 70% of pole range

    # Dedup: strongest flag/pennant per (type, breakout bar).
    best = {}

    def _add(type_, direction, i, strength):
        key = (type_, i)
        if key in best and strength <= best[key].get("_strength", 0):
            return
        best[key] = {
            "index": i, "type": type_, "direction": direction,
            "strength": "medium", "timestamp": candles[i].timestamp,
            "_strength": strength,
        }

    for i in range(MIN_FLAG + MIN_POLE, n):
        for flag_len in range(MIN_FLAG, MAX_FLAG + 1):
            f_start = i - flag_len
            f_end = i - 1
            for pole_len in range(MIN_POLE, MAX_POLE + 1):
                p_start = f_start - pole_len
                if p_start < 0:
                    continue
                p_end = f_start - 1

                price0 = candles[p_start].open
                if price0 <= 0:
                    continue

                pole_high = max(c.high for c in candles[p_start:p_end + 1])
                pole_low = min(c.low for c in candles[p_start:p_end + 1])
                pole_range = pole_high - pole_low
                if pole_range / price0 < POLE_PCT:
                    continue
                pole_up = candles[p_end].close > candles[p_start].open
                pole_dn = candles[p_end].close < candles[p_start].open

                f0, f1 = candles[f_start], candles[f_end]
                flag_high = max(c.high for c in candles[f_start:f_end + 1])
                flag_low = min(c.low for c in candles[f_start:f_end + 1])
                flag_range = flag_high - flag_low
                if flag_range / price0 > TIGHT_PCT:
                    continue
                if flag_range > pole_range * TIGHT_FACTOR:
                    continue

                hi_slope = f0.high - f1.high  # upper line (positive = descends)
                lo_slope = f0.low - f1.low    # lower line
                close = candles[i].close

                if pole_up and flag_high < pole_high and hi_slope >= 0 and close > flag_high:
                    _add("bullish_pennant" if lo_slope < 0 else "bullish_flag", "bullish", i, pole_range)
                    continue
                if pole_dn and flag_low > pole_low and lo_slope <= 0 and close < flag_low:
                    _add("bearish_pennant" if hi_slope > 0 else "bearish_flag", "bearish", i, pole_range)

    for p in best.values():
        p.pop("_strength", None)
    return list(best.values())


class TradingService:
    """Main trading strategy service combining TDI and pattern recognition."""

    def __init__(self, symbol: str = "R_75"):
        self.symbol = symbol
        self.tdi = TDICalculator()
        self.pattern_recognizer = PatternRecognizer()
        self.position_open = False
        self.current_position = None
        self.stop_loss = None
        self.take_profit = None

    def analyze(self, candles: List[Candle]) -> Dict:
        """Perform full market analysis using TDI and patterns."""
        if len(candles) < 50:
            return {"error": "Not enough data. Need at least 50 candles."}

        tdi_values = self.tdi.calculate(candles)
        patterns = self.pattern_recognizer.detect_reversal_patterns(candles)
        signal = self._generate_signal(tdi_values, patterns, candles)

        return {
            "symbol": self.symbol,
            "tdi": tdi_values,
            "patterns": patterns,
            "signal": signal,
            "current_price": candles[-1].close if candles else None,
            "candle_count": len(candles),
        }

    def _generate_signal(self, tdi: Dict, patterns: List[Dict],
                         candles: List[Candle]) -> Optional[TradeSignal]:
        """Generate trading signal based on TDI + pattern confluence."""
        if tdi.get("rsi") is None:
            return None

        rsi = tdi["rsi"]
        rsi_smoothed = tdi.get("rsi_smoothed", 50)
        price = candles[-1].close
        market_base = tdi.get("market_base_line", price)
        upper_band = tdi.get("upper_band", 70)
        lower_band = tdi.get("lower_band", 30)

        # Check for recent patterns (last 5 candles)
        recent_patterns = [p for p in patterns
                          if p["index"] >= len(candles) - 5]

        bullish_patterns = [p for p in recent_patterns if p["direction"] == "bullish"]
        bearish_patterns = [p for p in recent_patterns if p["direction"] == "bearish"]

        # TDI Buy Signal: RSI crossing above smoothed line from below 50
        # + price above Market Base Line + bullish pattern
        if (rsi > rsi_smoothed and rsi < 70 and
                price > market_base and bullish_patterns):
            confidence = min(0.5 + len(bullish_patterns) * 0.15, 0.95)
            stop_loss = self._calculate_stop_loss(candles, "BUY")
            take_profit = price + (price - stop_loss) * 1.5  # 1:1.5 risk/reward

            return TradeSignal(
                symbol=self.symbol,
                action="BUY",
                confidence=confidence,
                timestamp=datetime.utcnow(),
                reason=f"TDI bullish + {len(bullish_patterns)} reversal pattern(s)",
                stop_loss=stop_loss,
                take_profit=take_profit
            )

        # TDI Sell Signal: RSI crossing below smoothed line from above 50
        # + price below Market Base Line + bearish pattern
        if (rsi < rsi_smoothed and rsi > 30 and
                price < market_base and bearish_patterns):
            confidence = min(0.5 + len(bearish_patterns) * 0.15, 0.95)
            stop_loss = self._calculate_stop_loss(candles, "SELL")
            take_profit = price - (stop_loss - price) * 1.5

            return TradeSignal(
                symbol=self.symbol,
                action="SELL",
                confidence=confidence,
                timestamp=datetime.utcnow(),
                reason=f"TDI bearish + {len(bearish_patterns)} reversal pattern(s)",
                stop_loss=stop_loss,
                take_profit=take_profit
            )

        # Overbought / Oversold with pattern confirmation
        if rsi > 80 and bearish_patterns:
            stop_loss = self._calculate_stop_loss(candles, "SELL")
            return TradeSignal(
                symbol=self.symbol,
                action="SELL",
                confidence=0.6,
                timestamp=datetime.utcnow(),
                reason=f"RSI overbought ({rsi:.1f}) + bearish pattern",
                stop_loss=stop_loss,
                take_profit=price - (stop_loss - price) * 1.5
            )

        if rsi < 20 and bullish_patterns:
            stop_loss = self._calculate_stop_loss(candles, "BUY")
            return TradeSignal(
                symbol=self.symbol,
                action="BUY",
                confidence=0.6,
                timestamp=datetime.utcnow(),
                reason=f"RSI oversold ({rsi:.1f}) + bullish pattern",
                stop_loss=stop_loss,
                take_profit=price + (price - stop_loss) * 1.5
            )

        return None

    def _calculate_stop_loss(self, candles: List[Candle], direction: str) -> float:
        """Calculate stop loss level based on recent price action."""
        recent = candles[-20:] if len(candles) >= 20 else candles
        if direction == "BUY":
            # Place stop loss below recent low with a buffer
            recent_low = min(c.low for c in recent)
            atr = self._calculate_atr(recent)
            return recent_low - atr * 0.5
        else:
            # Place stop loss above recent high with a buffer
            recent_high = max(c.high for c in recent)
            atr = self._calculate_atr(recent)
            return recent_high + atr * 0.5

    def _calculate_atr(self, candles: List[Candle], period: int = 14) -> float:
        """Calculate Average True Range for volatility-based stops."""
        if len(candles) < 2:
            return 0

        true_ranges = []
        for i in range(1, len(candles)):
            high_low = candles[i].high - candles[i].low
            high_close = abs(candles[i].high - candles[i - 1].close)
            low_close = abs(candles[i].low - candles[i - 1].close)
            true_ranges.append(max(high_low, high_close, low_close))

        return float(np.mean(true_ranges[-period:]))
