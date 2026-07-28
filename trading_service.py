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
        """Detect common reversal patterns."""
        patterns = []
        if len(candles) < 3:
            return patterns

        for i in range(2, len(candles)):
            c1, c2, c3 = candles[i - 2], candles[i - 1], candles[i]

            # Bullish Engulfing
            if (c2.close < c2.open and  # Bearish candle
                c3.close > c3.open and  # Bullish candle
                c3.open < c2.close and  # Opens below prev close
                c3.close > c2.open):    # Closes above prev open
                patterns.append({
                    "index": i,
                    "type": "bullish_engulfing",
                    "direction": "bullish",
                    "strength": "strong",
                    "timestamp": c3.timestamp
                })

            # Bearish Engulfing
            if (c2.close > c2.open and  # Bullish candle
                c3.close < c3.open and  # Bearish candle
                c3.open > c2.close and  # Opens above prev close
                c3.close < c2.open):    # Closes below prev open
                patterns.append({
                    "index": i,
                    "type": "bearish_engulfing",
                    "direction": "bearish",
                    "strength": "strong",
                    "timestamp": c3.timestamp
                })

            # Doji (indecision - potential reversal)
            body = abs(c3.close - c3.open)
            range_candle = c3.high - c3.low
            if range_candle > 0 and body / range_candle < 0.1:
                upper_wick = c3.high - max(c3.open, c3.close)
                lower_wick = min(c3.open, c3.close) - c3.low
                if upper_wick > 2 * body and lower_wick < body:
                    patterns.append({
                        "index": i,
                        "type": "shooting_star",
                        "direction": "bearish",
                        "strength": "medium",
                        "timestamp": c3.timestamp
                    })
                elif lower_wick > 2 * body and upper_wick < body:
                    patterns.append({
                        "index": i,
                        "type": "hammer",
                        "direction": "bullish",
                        "strength": "medium",
                        "timestamp": c3.timestamp
                    })

            # Pin Bar / Rejection
            body = abs(c3.close - c3.open)
            total_range = c3.high - c3.low
            if total_range > 0 and body / total_range < 0.3:
                upper_wick = c3.high - max(c3.open, c3.close)
                lower_wick = min(c3.open, c3.close) - c3.low
                # Long lower wick (bullish rejection)
                if lower_wick > 2 * body and lower_wick > upper_wick:
                    patterns.append({
                        "index": i,
                        "type": "pin_bar_bullish",
                        "direction": "bullish",
                        "strength": "medium",
                        "timestamp": c3.timestamp
                    })
                # Long upper wick (bearish rejection)
                if upper_wick > 2 * body and upper_wick > lower_wick:
                    patterns.append({
                        "index": i,
                        "type": "pin_bar_bearish",
                        "direction": "bearish",
                        "strength": "medium",
                        "timestamp": c3.timestamp
                    })

        return patterns


class TradingService:
    """Main trading strategy service combining TDI and pattern recognition."""

    def __init__(self, symbol: str = "R_100"):
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
