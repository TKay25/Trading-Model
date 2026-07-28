/**
 * TDI (Trader's Dynamic Index) Indicator Module
 * Calculates RSI, smoothed RSI, market base line, and volatility bands.
 */

class TDIIndicator {
    constructor(options = {}) {
        this.rsiPeriod = options.rsiPeriod || 13;
        this.rsiMaPeriod = options.rsiMaPeriod || 8;
        this.priceMaPeriod = options.priceMaPeriod || 34;
        this.bandMultiplier = options.bandMultiplier || 1.6185;
    }

    /**
     * Calculate TDI values from candle data.
     * @param {Array} candles - Array of {open, high, low, close, epoch}
     * @returns {Object} TDI values including rsi, rsiSmoothed, marketBase, bands
     */
    calculate(candles) {
        if (!candles || candles.length < Math.max(this.rsiPeriod, this.priceMaPeriod)) {
            return null;
        }

        const closes = candles.map(c => parseFloat(c.close));
        const highs = candles.map(c => parseFloat(c.high));
        const lows = candles.map(c => parseFloat(c.low));

        // 1. Calculate RSI
        const rsi = this._calculateRSI(closes, this.rsiPeriod);

        // 2. RSI Smoothed (MA of RSI)
        const rsiSmoothed = this._sma(rsi, this.rsiMaPeriod);

        // 3. Market Base Line (MA of price)
        const marketBase = this._sma(closes, this.priceMaPeriod);

        // 4. Volatility Bands around RSI smoothed
        const { upperBand, lowerBand } = this._calculateVolatilityBands(
            rsiSmoothed, closes, this.bandMultiplier
        );

        return {
            rsi: rsi[rsi.length - 1],
            rsiSmoothed: rsiSmoothed[rsiSmoothed.length - 1],
            marketBaseLine: marketBase[marketBase.length - 1],
            upperBand: upperBand[upperBand.length - 1],
            lowerBand: lowerBand[lowerBand.length - 1],
            fullRsi: rsi,
            fullRsiSmoothed: rsiSmoothed,
            fullMarketBase: marketBase,
            fullUpperBand: upperBand,
            fullLowerBand: lowerBand,
        };
    }

    /**
     * Calculate Relative Strength Index.
     */
    _calculateRSI(prices, period) {
        const deltas = new Array(prices.length).fill(0);
        for (let i = 1; i < prices.length; i++) {
            deltas[i] = prices[i] - prices[i - 1];
        }

        const gains = deltas.map(d => d > 0 ? d : 0);
        const losses = deltas.map(d => d < 0 ? -d : 0);

        const avgGain = new Array(prices.length).fill(0);
        const avgLoss = new Array(prices.length).fill(0);
        const rsi = new Array(prices.length).fill(50);

        // First average
        let sumGain = 0, sumLoss = 0;
        for (let i = 1; i <= period; i++) {
            sumGain += gains[i];
            sumLoss += losses[i];
        }
        avgGain[period] = sumGain / period;
        avgLoss[period] = sumLoss / period;

        // Rest using smoothed method
        for (let i = period + 1; i < prices.length; i++) {
            avgGain[i] = (avgGain[i - 1] * (period - 1) + gains[i]) / period;
            avgLoss[i] = (avgLoss[i - 1] * (period - 1) + losses[i]) / period;
        }

        for (let i = period; i < prices.length; i++) {
            if (avgLoss[i] === 0) {
                rsi[i] = 100;
            } else {
                const rs = avgGain[i] / avgLoss[i];
                rsi[i] = 100 - (100 / (1 + rs));
            }
        }

        return rsi;
    }

    /**
     * Simple Moving Average.
     */
    _sma(data, period) {
        const sma = new Array(data.length).fill(0);
        for (let i = 0; i < data.length; i++) {
            if (i < period - 1) {
                sma[i] = data[i];
            } else {
                let sum = 0;
                for (let j = i - period + 1; j <= i; j++) {
                    sum += data[j];
                }
                sma[i] = sum / period;
            }
        }
        return sma;
    }

    /**
     * Calculate volatility bands around RSI smoothed.
     */
    _calculateVolatilityBands(rsiSmoothed, prices, mult) {
        const upperBand = new Array(rsiSmoothed.length).fill(0);
        const lowerBand = new Array(rsiSmoothed.length).fill(0);

        for (let i = 0; i < rsiSmoothed.length; i++) {
            if (i < this.priceMaPeriod) {
                upperBand[i] = rsiSmoothed[i] + 10;
                lowerBand[i] = rsiSmoothed[i] - 10;
            } else {
                // Standard deviation of price changes in window
                const windowPrices = prices.slice(i - this.priceMaPeriod, i + 1);
                const changes = [];
                for (let j = 1; j < windowPrices.length; j++) {
                    changes.push(windowPrices[j] - windowPrices[j - 1]);
                }
                const mean = changes.reduce((a, b) => a + b, 0) / changes.length;
                const variance = changes.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / changes.length;
                const stdDev = Math.sqrt(variance);

                const scaledStd = stdDev * mult;
                upperBand[i] = rsiSmoothed[i] + scaledStd;
                lowerBand[i] = rsiSmoothed[i] - scaledStd;
            }
        }

        return { upperBand, lowerBand };
    }
}


/**
 * Pattern Recognizer for the frontend.
 * Detects reversal patterns on candle data.
 */
class PatternRecognizer {
    /**
     * Detect reversal patterns on candle data.
     * @param {Array} candles - Array of {open, high, low, close, epoch}
     * @returns {Array} Detected patterns
     */
    detect(candles) {
        if (!candles || candles.length < 3) return [];

        const patterns = [];

        for (let i = 2; i < candles.length; i++) {
            const c1 = candles[i - 2];
            const c2 = candles[i - 1];
            const c3 = candles[i];

            const body = Math.abs(c3.close - c3.open);
            const totalRange = c3.high - c3.low;
            if (totalRange === 0) continue;
            const bodyRatio = body / totalRange;

            // Bullish Engulfing — only when real body is significant
            if (this._isBullishEngulfing(c1, c2, c3)) {
                patterns.push({
                    index: i,
                    type: 'bullish_engulfing',
                    direction: 'bullish',
                    strength: 'strong',
                    timestamp: c3.epoch,
                });
                continue; // skip other checks on same candle
            }

            // Bearish Engulfing
            if (this._isBearishEngulfing(c1, c2, c3)) {
                patterns.push({
                    index: i,
                    type: 'bearish_engulfing',
                    direction: 'bearish',
                    strength: 'strong',
                    timestamp: c3.epoch,
                });
                continue;
            }

            // Skip tiny-range candles — not enough price action
            if (bodyRatio > 0.6) continue; // too much body, no significant wick

            const upperWick = c3.high - Math.max(c3.open, c3.close);
            const lowerWick = Math.min(c3.open, c3.close) - c3.low;
            const wickRatio = Math.max(upperWick, lowerWick) / totalRange;

            // Need a prominent wick (at least 55% of total range)
            if (wickRatio < 0.55) continue;

            // Need wick at least 3x the body
            const dominantWick = Math.max(upperWick, lowerWick);
            if (dominantWick < body * 3) continue;

            // Also check recent direction context (last 3 candles)
            const trendUp = i >= 3 && candles[i - 1].close > candles[i - 3].close;
            const trendDown = i >= 3 && candles[i - 1].close < candles[i - 3].close;

            if (lowerWick > upperWick && lowerWick > body * 3 && (trendDown || i < 3)) {
                patterns.push({
                    index: i,
                    type: 'hammer',
                    direction: 'bullish',
                    strength: 'medium',
                    timestamp: c3.epoch,
                });
            } else if (upperWick > lowerWick && upperWick > body * 3 && (trendUp || i < 3)) {
                patterns.push({
                    index: i,
                    type: 'shooting_star',
                    direction: 'bearish',
                    strength: 'medium',
                    timestamp: c3.epoch,
                });
            }
        }

        return patterns;
    }

    _isBullishEngulfing(c1, c2, c3) {
        const body2 = Math.abs(c2.close - c2.open);
        const body3 = Math.abs(c3.close - c3.open);
        return (
            body2 > 0 && body3 > 0 &&
            c2.close < c2.open &&               // C2 is bearish
            c3.close > c3.open &&               // C3 is bullish
            c3.open < c2.close &&               // Opens below prev close
            c3.close > c2.open                  // Closes above prev open
        );
    }

    _isBearishEngulfing(c1, c2, c3) {
        const body2 = Math.abs(c2.close - c2.open);
        const body3 = Math.abs(c3.close - c3.open);
        return (
            body2 > 0 && body3 > 0 &&
            c2.close > c2.open &&               // C2 is bullish
            c3.close < c3.open &&               // C3 is bearish
            c3.open > c2.close &&               // Opens above prev close
            c3.close < c2.open                  // Closes below prev open
        );
    }
}
