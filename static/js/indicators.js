/**
 * TDI (Trader's Dynamic Index) Indicator Module
 * Calculates RSI, smoothed RSI, market base line, and volatility bands.
 */

class TDIIndicator {
    /**
     * Trader's Dynamic Index (per TDI indicator.pdf):
     *   - RSI(13) on Close
     *   - Signal line        = SMA(RSI, 2)                 (green)
     *   - RSI Smoothed       = SMA(RSI, 7)                 (red)
     *   - Market Base line   = SMA(RSI, 34)                (yellow)
     *   - Volatility Bands   = Bollinger(RSI, 34, 1.619)   (blue)
     */
    constructor(options = {}) {
        this.rsiPeriod = options.rsiPeriod || 13;
        this.signalPeriod = options.signalPeriod || 2;
        this.smoothedPeriod = options.smoothedPeriod || 7;
        this.bbPeriod = options.bbPeriod || 34;
        this.bbDeviation = options.bbDeviation || 1.619;
    }

    /**
     * Calculate TDI values from candle data.
     * @param {Array} candles - Array of {open, high, low, close, epoch}
     * @returns {Object} TDI values including rsi, signal, rsiSmoothed, marketBase, bands
     */
    calculate(candles) {
        if (!candles || candles.length < this.rsiPeriod) {
            return null;
        }

        const closes = candles.map(c => parseFloat(c.close));

        // 1. RSI(13)
        const rsi = this._calculateRSI(closes, this.rsiPeriod);

        // 2. Signal line = SMA(RSI, 2)
        const signal = this._sma(rsi, this.signalPeriod);

        // 3. RSI Smoothed = SMA(RSI, 7)
        const rsiSmoothed = this._sma(rsi, this.smoothedPeriod);

        // 4. Market Base line = SMA(RSI, 34)
        const marketBase = this._sma(rsi, this.bbPeriod);

        // 5. Volatility Bands = Bollinger(RSI, 34, 1.619)
        const { upperBand, lowerBand } = this._calculateBollingerBands(
            rsi, this.bbPeriod, this.bbDeviation
        );

        const last = (arr) => arr[arr.length - 1];
        return {
            rsi: last(rsi),
            signal: last(signal),
            rsiSmoothed: last(rsiSmoothed),
            marketBaseLine: last(marketBase),
            upperBand: last(upperBand),
            lowerBand: last(lowerBand),
            fullRsi: rsi,
            fullSignal: signal,
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
     * Calculate Bollinger Bands on the given data series.
     */
    _calculateBollingerBands(data, period, deviation) {
        const upper = new Array(data.length).fill(0);
        const lower = new Array(data.length).fill(0);
        const mid = this._sma(data, period);
        for (let i = 0; i < data.length; i++) {
            if (i < period - 1) {
                upper[i] = mid[i] + deviation * 10;
                lower[i] = mid[i] - deviation * 10;
            } else {
                let sum = 0;
                for (let j = i - period + 1; j <= i; j++) {
                    const d = data[j] - mid[i];
                    sum += d * d;
                }
                const std = Math.sqrt(sum / period);
                upper[i] = mid[i] + deviation * std;
                lower[i] = mid[i] - deviation * std;
            }
        }
        return { upperBand: upper, lowerBand: lower };
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

        // M (double-top) / W (double-bottom) reversal patterns
        patterns.push(...this._detectDoublePatterns(candles));

        return patterns;
    }

    /**
     * Detect M (double-top) and W (double-bottom) reversal patterns.
     * Returns patterns with a `points` array [{time, price}] so the chart can
     * draw the M/W shape.
     */
    _detectDoublePatterns(candles) {
        const patterns = [];
        const n = candles.length;
        if (n < 16) return patterns;

        // Swing highs / swing lows (local extrema over `w` bars each side)
        const w = 3;
        const highs = [];
        const lows = [];
        for (let i = w; i < n - w; i++) {
            let isHigh = true, isLow = true;
            for (let j = i - w; j <= i + w; j++) {
                if (candles[j].high > candles[i].high) isHigh = false;
                if (candles[j].low < candles[i].low) isLow = false;
            }
            if (isHigh) highs.push({ index: i, price: candles[i].high, time: candles[i].epoch });
            if (isLow) lows.push({ index: i, price: candles[i].low, time: candles[i].epoch });
        }

        const tol = 0.01; // 1% tolerance for matching peak/valley heights
        // Keep the strongest single pattern per (type, confirmation bar) so
        // overlapping swing pairs don't stack identical M/W shapes on top of each other.
        const best = new Map();
        const add = (p, strength) => {
            const key = `${p.type}:${p.index}`;
            const existing = best.get(key);
            if (!existing || strength > existing._strength) {
                p._strength = strength;
                best.set(key, p);
            }
        };

        // M — Double Top: two near-equal peaks with a valley between,
        // then a close below the neckline confirms the pattern.
        for (let a = 0; a < highs.length; a++) {
            const H1 = highs[a];
            for (let b = a + 1; b < highs.length; b++) {
                const H2 = highs[b];
                if (H2.index - H1.index < 6) continue;
                if (Math.abs(H2.price - H1.price) > H1.price * tol) continue;
                let neck = null;
                for (const L of lows) {
                    if (L.index > H1.index && L.index < H2.index && (!neck || L.price < neck.price)) neck = L;
                }
                if (!neck) continue;
                for (let c = H2.index + 1; c < n; c++) {
                    if (candles[c].close < neck.price) {
                        add({
                            index: c,
                            type: 'double_top',
                            direction: 'bearish',
                            strength: 'strong',
                            timestamp: candles[c].epoch,
                            points: [
                                { time: H1.time, price: H1.price },
                                { time: neck.time, price: neck.price },
                                { time: H2.time, price: H2.price },
                                { time: candles[c].epoch, price: candles[c].close },
                            ],
                        }, H1.price - neck.price);
                        break;
                    }
                }
            }
        }

        // W — Double Bottom: two near-equal valleys with a peak between,
        // then a close above the neckline confirms the pattern.
        for (let a = 0; a < lows.length; a++) {
            const L1 = lows[a];
            for (let b = a + 1; b < lows.length; b++) {
                const L2 = lows[b];
                if (L2.index - L1.index < 6) continue;
                if (Math.abs(L2.price - L1.price) > L1.price * tol) continue;
                let neck = null;
                for (const H of highs) {
                    if (H.index > L1.index && H.index < L2.index && (!neck || H.price > neck.price)) neck = H;
                }
                if (!neck) continue;
                for (let c = L2.index + 1; c < n; c++) {
                    if (candles[c].close > neck.price) {
                        add({
                            index: c,
                            type: 'double_bottom',
                            direction: 'bullish',
                            strength: 'strong',
                            timestamp: candles[c].epoch,
                            points: [
                                { time: L1.time, price: L1.price },
                                { time: neck.time, price: neck.price },
                                { time: L2.time, price: L2.price },
                                { time: candles[c].epoch, price: candles[c].close },
                            ],
                        }, neck.price - L1.price);
                        break;
                    }
                }
            }
        }

        // Keep only the most recent handful to avoid clutter.
        return Array.from(best.values()).slice(-6);
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


/**
 * Risk Metrics: Value at Risk (VaR) and Expected Shortfall (ES) using the
 * historical simulation method at 95% and 99% confidence levels.
 */
class RiskMetrics {
    /**
     * Compute VaR / ES from OHLC candles.
     * @param {Array} candles - Array of {open, high, low, close, epoch}
     * @returns {Object} { var95, var99, es95, es99, count, period }
     */
    calculate(candles) {
        const out = { var95: null, var99: null, es95: null, es99: null, count: 0, period: 'candle' };
        if (!candles || candles.length < 30) return out;

        // Simple (arithmetic) returns from consecutive closes.
        const returns = [];
        for (let i = 1; i < candles.length; i++) {
            const prev = parseFloat(candles[i - 1].close);
            const cur = parseFloat(candles[i].close);
            if (prev > 0) returns.push((cur - prev) / prev);
        }
        if (returns.length < 30) return out;

        returns.sort((a, b) => a - b);
        const n = returns.length;
        out.count = n;

        const percentile = (p) => returns[Math.min(n - 1, Math.floor(p * n))];
        const tailMean = (p) => {
            const idx = Math.min(n - 1, Math.floor(p * n));
            let sum = 0;
            for (let i = 0; i <= idx; i++) sum += returns[i];
            return sum / (idx + 1);
        };

        out.var95 = -percentile(0.05) * 100;
        out.var99 = -percentile(0.01) * 100;
        out.es95 = -tailMean(0.05) * 100;
        out.es99 = -tailMean(0.01) * 100;
        return out;
    }
}


/**
 * Combined Signal Engine: scores the TDI indicator and the chart patterns
 * (M/W + candlestick formations) separately, then derives an overall
 * BUY / SELL / HOLD decision with a confidence score.
 */
class SignalEngine {
    /**
     * @param {Object} tdi     Output of TDIIndicator.calculate()
     * @param {Array}  patterns Output of PatternRecognizer.detect()
     * @param {Array}  candles  OHLC candles
     * @returns {Object} { action, tdiAction, patternAction, confidence, reason, stop_loss, take_profit }
     */
    generate(tdi, patterns, candles, opts = {}) {
        const n = candles ? candles.length : 0;
        const tdiAction = this._tdiAction(tdi);
        const patternAction = this._patternAction(patterns, n);
        const overall = this._combine(tdiAction, patternAction);

        // Suggested $ SL/TP, scaled to the lot size (stake) with a 1:2 risk/reward.
        const stake = (opts.stake && opts.stake > 0) ? opts.stake : 1;
        const payoutRatio = (opts.payoutRatio && opts.payoutRatio > 1) ? opts.payoutRatio : 1.82;
        const maxProfit = stake * (payoutRatio - 1);
        const suggestedSl = stake * 0.5;
        const suggestedTp = Math.min(suggestedSl * 2, maxProfit);
        const isDirectional = overall.action === 'BUY' || overall.action === 'SELL';

        return {
            action: overall.action,
            tdiAction: tdiAction,
            patternAction: patternAction,
            confidence: overall.confidence,
            conflict: !!overall.conflict,
            reason: this._reason(tdiAction, patternAction, patterns, n),
            stop_loss: isDirectional ? Math.round(suggestedSl * 100) / 100 : null,
            take_profit: isDirectional ? Math.round(suggestedTp * 100) / 100 : null,
        };
    }

    /**
     * TDI verdict per "Traders Dynamic Index Ultimate Guide".
     *
     * Components: Green = SMA(RSI,2), Red = SMA(RSI,7), Yellow = SMA(RSI,34),
     * Blue = Bollinger(RSI,34,1.619) bands.
     *
     * Signal rules:
     *  1) Macro trend bias — Yellow > 50 => only buy setups, < 50 => only sell.
     *  2) Volatility extreme — Green breaks outside the Blue bands (or 68/32).
     *  3) Trigger — Green crosses back over the Red line: buy from an oversold
     *     floor, sell from an overbought ceiling.
     *  4) Filter — skip when the Yellow (market base) line is flat.
     */
    _tdiAction(tdi) {
        if (!tdi || !Array.isArray(tdi.fullSignal) || tdi.fullSignal.length < 40) return 'NEUTRAL';
        const n = tdi.fullSignal.length;
        const last = (a, k = 0) => a[Math.max(0, n - 1 - k)];

        const green = tdi.fullSignal;        // green line: SMA(RSI,2)
        const red = tdi.fullRsiSmoothed;     // red line: SMA(RSI,7)
        const yellow = tdi.fullMarketBase;   // yellow line: SMA(RSI,34)
        const up = tdi.fullUpperBand;        // blue bands (upper)
        const lo = tdi.fullLowerBand;        // blue bands (lower)

        const g = last(green);
        const y = last(yellow);

        // Step 1 — macro trend bias (Yellow vs 50)
        const trendBuy = y > 50;
        const trendSell = y < 50;

        // Step 4 — flat market base filter (compare ~5 bars ago)
        const yPrev = last(yellow, 5);
        const flat = Math.abs(y - yPrev) < 0.5;

        // Step 2 — volatility extremes: Green beyond the bands (or 68/32)
        let oversold = false, overbought = false;
        for (let k = 0; k < 6; k++) {
            const gg = last(green, k), uu = last(up, k), ll = last(lo, k);
            if (gg < ll || gg < 32) oversold = true;
            if (gg > uu || gg > 68) overbought = true;
        }

        // Step 3 — Green/Red crossover in the last few bars
        let buyCross = false, sellCross = false;
        for (let k = 1; k <= 3; k++) {
            const gPrev = last(green, k), rPrev = last(red, k);
            const gCur = last(green, k - 1), rCur = last(red, k - 1);
            if (gCur > rCur && gPrev <= rPrev) buyCross = true;
            if (gCur < rCur && gPrev >= rPrev) sellCross = true;
        }

        // Trigger: crossover from the extreme floor/ceiling, with trend, not flat.
        if (buyCross && oversold && trendBuy && !flat) return 'BUY';
        if (sellCross && overbought && trendSell && !flat) return 'SELL';

        // Fallback lean: stay with the macro trend when momentum (green vs red)
        // agrees — still respects the guide's "trade only with the bias" rule.
        if (trendBuy && !flat && g > last(red) && g > 50) return 'BUY';
        if (trendSell && !flat && g < last(red) && g < 50) return 'SELL';

        return 'NEUTRAL';
    }

    /**
     * Pattern verdict: weighted bullish vs bearish formations in the recent window.
     */
    _patternAction(patterns, candleCount) {
        const recent = (patterns || []).filter(p => p.index >= candleCount - 10);
        let bull = 0, bear = 0;
        recent.forEach(p => {
            const w = p.strength === 'strong' ? 2 : 1;
            if (p.direction === 'bullish') bull += w;
            else if (p.direction === 'bearish') bear += w;
        });
        if (bull === 0 && bear === 0) return 'NEUTRAL';
        if (bull > bear) return 'BUY';
        if (bear > bull) return 'SELL';
        return 'NEUTRAL';
    }

    /**
     * Overall decision from the two verdicts (STRICT rule).
     *
     * A neutral TDI always means HOLD — no directional call is made unless the
     * TDI itself has a verdict. The pattern side only adds/removes conviction.
     */
    _combine(tdi, pattern) {
        if (tdi === 'NEUTRAL') return { action: 'NEUTRAL', confidence: 0.2, tdiNeutral: true };
        if (pattern === 'NEUTRAL') {
            // TDI has direction, patterns are silent -> follow TDI at reduced confidence.
            return tdi === 'BUY'
                ? { action: 'BUY', confidence: 0.6 }
                : { action: 'SELL', confidence: 0.6 };
        }
        if ((tdi === 'BUY' && pattern === 'SELL') || (tdi === 'SELL' && pattern === 'BUY')) {
            return { action: 'NEUTRAL', confidence: 0.25, conflict: true };
        }
        if (tdi === 'BUY' && pattern === 'BUY') return { action: 'BUY', confidence: 0.9 };
        if (tdi === 'SELL' && pattern === 'SELL') return { action: 'SELL', confidence: 0.9 };
        return { action: 'NEUTRAL', confidence: 0 };
    }

    /**
     * Human-readable reasoning.
     */
    _reason(tdiAction, patternAction, patterns, candleCount) {
        if (tdiAction === 'NEUTRAL') {
            return 'TDI neutral — no directional call (HOLD). Wait for a TDI signal.';
        }
        const tdiTxt = { BUY: 'TDI bullish', SELL: 'TDI bearish' }[tdiAction] || 'TDI neutral';
        const patTxt = { BUY: 'patterns bullish', SELL: 'patterns bearish', NEUTRAL: 'patterns neutral' }[patternAction] || 'patterns neutral';
        const recent = (patterns || []).filter(p => p.index >= candleCount - 10);
        const mw = recent.filter(p => p.type === 'double_top' || p.type === 'double_bottom');
        const others = recent.length - mw.length;
        const mwTxt = mw.length ? ` (${mw.map(p => p.type === 'double_bottom' ? 'W' : 'M').join('/')})` : '';
        const otherTxt = others > 0 ? ` +${others} formation(s)` : '';
        return `${tdiTxt} / ${patTxt}${mwTxt}${otherTxt}`;
    }
}
