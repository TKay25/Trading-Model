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
        const push = (i, type, direction, strength) => {
            patterns.push({ index: i, type, direction, strength, timestamp: candles[i].epoch });
        };

        for (let i = 0; i < candles.length; i++) {
            const c = candles[i];
            const p = candles[i - 1];
            const q = candles[i - 2];

            const body = Math.abs(c.close - c.open);
            const range = c.high - c.low;
            if (range === 0) continue;
            const bodyRatio = body / range;
            const upper = c.high - Math.max(c.open, c.close);
            const lower = Math.min(c.open, c.close) - c.low;
            const isBull = c.close > c.open;
            const isBear = c.close < c.open;

            // Previous-candle derived values (shared by 2- and 3-candle patterns).
            const pb = p ? Math.abs(p.close - p.open) : 0;
            const pBull = p ? p.close > p.open : false;
            const pBear = p ? p.close < p.open : false;
            const qb = q ? Math.abs(q.close - q.open) : 0;
            const qBull = q ? q.close > q.open : false;
            const qBear = q ? q.close < q.open : false;

            // ---------- Single-candle formations ----------

            // Marubozu: full body, negligible wicks
            if (body > 0 && upper < body * 0.1 && lower < body * 0.1) {
                push(i, isBull ? 'bullish_marubozu' : 'bearish_marubozu', isBull ? 'bullish' : 'bearish', 'strong');
                continue;
            }

            // Doji family: tiny body
            if (body <= range * 0.1) {
                if (lower >= range * 0.5) push(i, 'dragonfly_doji', 'bullish', 'medium');
                else if (upper >= range * 0.5) push(i, 'gravestone_doji', 'bearish', 'medium');
                else push(i, 'doji', 'neutral', 'weak');
                continue;
            }

            // Spinning top: small body, wicks on both sides
            if (bodyRatio <= 0.3 && upper > body * 1.5 && lower > body * 1.5) {
                push(i, 'spinning_top', 'neutral', 'weak');
                continue;
            }

            // Hammer / Hanging Man: small body near top, long lower wick
            if (bodyRatio <= 0.4 && lower >= body * 2 && lower > upper) {
                const prior = (p ? (p.close > p.open ? 1 : -1) : 0) + (q ? (q.close > q.open ? 1 : -1) : 0);
                const uptrend = prior >= 1;
                push(i, uptrend ? 'hanging_man' : 'hammer', uptrend ? 'bearish' : 'bullish', 'medium');
                continue;
            }

            // Shooting Star / Inverted Hammer: small body near bottom, long upper wick
            if (bodyRatio <= 0.4 && upper >= body * 2 && upper > lower) {
                const prior = (p ? (p.close > p.open ? 1 : -1) : 0) + (q ? (q.close > q.open ? 1 : -1) : 0);
                const uptrend = prior >= 1;
                push(i, uptrend ? 'shooting_star' : 'inverted_hammer', uptrend ? 'bearish' : 'bullish', 'medium');
                continue;
            }

            // ---------- Two-candle formations ----------
            if (p) {
                // Bullish Engulfing
                if (pBear && isBull && pb > 0 && body > pb && c.open <= p.close && c.close >= p.open) {
                    push(i, 'bullish_engulfing', 'bullish', 'strong');
                    continue;
                }
                // Bearish Engulfing
                if (pBull && isBear && pb > 0 && body > pb && c.open >= p.close && c.close <= p.open) {
                    push(i, 'bearish_engulfing', 'bearish', 'strong');
                    continue;
                }
                // Bullish Harami: small bullish body inside prev bearish body
                if (pBear && isBull && pb > 0 && body > 0 && body < pb && c.high <= p.high && c.low >= p.low) {
                    push(i, 'bullish_harami', 'bullish', 'medium');
                    continue;
                }
                // Bearish Harami
                if (pBull && isBear && pb > 0 && body > 0 && body < pb && c.high <= p.high && c.low >= p.low) {
                    push(i, 'bearish_harami', 'bearish', 'medium');
                    continue;
                }
                // Piercing Line
                if (pBear && isBull && pb > 0 && c.open <= p.low && c.close > p.close + pb / 2) {
                    push(i, 'piercing_line', 'bullish', 'medium');
                    continue;
                }
                // Dark Cloud Cover
                if (pBull && isBear && pb > 0 && c.open >= p.high && c.close < p.close - pb / 2) {
                    push(i, 'dark_cloud_cover', 'bearish', 'medium');
                    continue;
                }
                // Tweezer Bottom / Top
                if (pBear && isBull && Math.abs(c.low - p.low) <= range * 0.1) {
                    push(i, 'tweezer_bottom', 'bullish', 'medium');
                    continue;
                }
                if (pBull && isBear && Math.abs(c.high - p.high) <= range * 0.1) {
                    push(i, 'tweezer_top', 'bearish', 'medium');
                    continue;
                }
            }

            // ---------- Three-candle formations ----------
            if (p && q) {
                // Morning Star: big bear, small middle, big bull closing above
                if (qBear && isBull && qb > 0 && body > pb && p.open < q.close && c.close > q.open) {
                    push(i, 'morning_star', 'bullish', 'strong');
                    continue;
                }
                // Evening Star
                if (qBull && isBear && qb > 0 && body > pb && p.open > q.close && c.close < q.open) {
                    push(i, 'evening_star', 'bearish', 'strong');
                    continue;
                }
                // Three White Soldiers
                if (qBull && pBull && isBull && q.close > q.open && p.close > p.open && c.close > c.open) {
                    push(i, 'three_white_soldiers', 'bullish', 'strong');
                    continue;
                }
                // Three Black Crows
                if (qBear && pBear && isBear && q.close < q.open && p.close < p.open && c.close < c.open) {
                    push(i, 'three_black_crows', 'bearish', 'strong');
                    continue;
                }
            }
        }

        // M (double-top) / W (double-bottom) reversal patterns
        patterns.push(...this._detectDoublePatterns(candles));

        // Continuation patterns: Bullish/Bearish Flags and Pennants.
        patterns.push(...this._detectContinuationPatterns(candles));

        return patterns;
    }

    /**
     * Detect continuation patterns — Bullish/Bearish Flags and Pennants.
     *
     * A flag = a strong "pole" move (rocket up / waterfall down) followed by a
     * TIGHT channel (rectangle) sloping against the trend, then a breakout beyond
     * the channel line. A pennant is the same idea but the resting period is a
     * small converging (symmetrical) triangle instead of a rectangle.
     *
     * Returned patterns carry `pole`, `flagTop` and `flagBottom` polylines so the
     * chart can draw the pole + channel, and `index` = the breakout bar.
     */
    _detectContinuationPatterns(candles) {
        const patterns = [];
        const n = candles.length;
        if (n < 30) return patterns;

        const MIN_POLE = 4, MAX_POLE = 14;   // pole length (bars)
        const MIN_FLAG = 4, MAX_FLAG = 14;   // flag/rest length (bars)
        const POLE_PCT = 0.004;              // min pole range (0.4% of price)
        const TIGHT_PCT = 0.007;             // max flag range (0.7% of price)
        const TIGHT_FACTOR = 0.7;            // flag range < 70% of pole range

        // Dedup: strongest flag/pennant per (type, breakout bar).
        const best = new Map();
        const add = (p, strength) => {
            const key = `${p.type}:${p.index}`;
            const existing = best.get(key);
            if (!existing || strength > existing._strength) {
                p._strength = strength;
                best.set(key, p);
            }
        };

        for (let i = MIN_FLAG + MIN_POLE; i < n; i++) {
            for (let flagLen = MIN_FLAG; flagLen <= MAX_FLAG; flagLen++) {
                const fStart = i - flagLen;   // first bar of the flag
                const fEnd = i - 1;           // last bar of the flag
                for (let poleLen = MIN_POLE; poleLen <= MAX_POLE; poleLen++) {
                    const pStart = fStart - poleLen;
                    if (pStart < 0) continue;
                    const pEnd = fStart - 1;  // last bar of the pole

                    const price0 = candles[pStart].open;
                    if (price0 <= 0) continue;

                    // --- Pole: strong directional move ---
                    let poleHigh = -Infinity, poleLow = Infinity;
                    for (let k = pStart; k <= pEnd; k++) {
                        if (candles[k].high > poleHigh) poleHigh = candles[k].high;
                        if (candles[k].low < poleLow) poleLow = candles[k].low;
                    }
                    const poleRange = poleHigh - poleLow;
                    if (poleRange / price0 < POLE_PCT) continue;
                    const poleUp = candles[pEnd].close > candles[pStart].open;
                    const poleDn = candles[pEnd].close < candles[pStart].open;

                    // --- Flag: tight consolidation after the pole ---
                    const f0 = candles[fStart], f1 = candles[fEnd];
                    let flagHigh = -Infinity, flagLow = Infinity;
                    for (let k = fStart; k <= fEnd; k++) {
                        if (candles[k].high > flagHigh) flagHigh = candles[k].high;
                        if (candles[k].low < flagLow) flagLow = candles[k].low;
                    }
                    const flagRange = flagHigh - flagLow;
                    if (flagRange / price0 > TIGHT_PCT) continue;       // must be tight
                    if (flagRange > poleRange * TIGHT_FACTOR) continue; // tight vs pole

                    // Channel line slopes (positive = line descends).
                    const hiSlope = f0.high - f1.high; // upper line
                    const loSlope = f0.low - f1.low;   // lower line
                    const close = candles[i].close;    // breakout bar

                    // BULLISH continuation: pole up, channel must not rise on top,
                    // breakout above the flag's top line.
                    if (poleUp && flagHigh < poleHigh && hiSlope >= 0 && close > flagHigh) {
                        const isPennant = loSlope < 0; // lows rising => converging triangle
                        add({
                            index: i,
                            type: isPennant ? 'bullish_pennant' : 'bullish_flag',
                            direction: 'bullish',
                            strength: 'medium',
                            timestamp: candles[i].epoch,
                            pole: [
                                { time: candles[pStart].epoch, price: poleLow },
                                { time: candles[pEnd].epoch, price: poleHigh },
                            ],
                            flagTop: [
                                { time: f0.epoch, price: f0.high },
                                { time: f1.epoch, price: f1.high },
                            ],
                            flagBottom: [
                                { time: f0.epoch, price: f0.low },
                                { time: f1.epoch, price: f1.low },
                            ],
                        }, poleRange);
                        continue;
                    }

                    // BEARISH continuation: pole down, channel must not fall on bottom,
                    // breakout below the flag's bottom line.
                    if (poleDn && flagLow > poleLow && loSlope <= 0 && close < flagLow) {
                        const isPennant = hiSlope > 0; // highs falling => converging triangle
                        add({
                            index: i,
                            type: isPennant ? 'bearish_pennant' : 'bearish_flag',
                            direction: 'bearish',
                            strength: 'medium',
                            timestamp: candles[i].epoch,
                            pole: [
                                { time: candles[pStart].epoch, price: poleHigh },
                                { time: candles[pEnd].epoch, price: poleLow },
                            ],
                            flagTop: [
                                { time: f0.epoch, price: f0.high },
                                { time: f1.epoch, price: f1.high },
                            ],
                            flagBottom: [
                                { time: f0.epoch, price: f0.low },
                                { time: f1.epoch, price: f1.low },
                            ],
                        }, poleRange);
                    }
                }
            }
        }

        return Array.from(best.values());
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
            // Dedup key: H&S patterns anchor on the HEAD so multiple right-shoulder
            // interpretations of the same head collapse into the single strongest.
            const key = p._dedupKey || `${p.type}:${p.index}`;
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

        // Head & Shoulders (bearish): left shoulder, a HIGHER head, and a right
        // shoulder near-equal to the left. Neckline runs through the two valleys
        // (N1 between LS-H, N2 between H-RS); a close below it confirms.
        for (let a = 0; a < highs.length; a++) {
            const LS = highs[a];
            for (let b = a + 1; b < highs.length; b++) {
                const H = highs[b];
                if (H.index - LS.index < 3) continue;
                if (H.price <= LS.price) continue; // head must be higher than left shoulder
                for (let c = b + 1; c < highs.length; c++) {
                    const RS = highs[c];
                    if (RS.index - H.index < 3) continue;
                    if (RS.index - LS.index < 10) continue; // meaningful span
                    if (RS.price >= H.price) continue; // head must stay the highest
                    if (Math.abs(RS.price - LS.price) > LS.price * tol * 1.5) continue; // shoulders ~equal
                    if (H.price < Math.max(LS.price, RS.price) * (1 + 0.0015)) continue; // head protrudes above shoulders
                    let N1 = null, N2 = null;
                    for (const L of lows) {
                        if (L.index > LS.index && L.index < H.index && (!N1 || L.price < N1.price)) N1 = L;
                        if (L.index > H.index && L.index < RS.index && (!N2 || L.price < N2.price)) N2 = L;
                    }
                    if (!N1 || !N2) continue;
                    const neckVal = (t) => {
                        const span = (N2.time - N1.time) || 1;
                        return N1.price + (N2.price - N1.price) * ((t - N1.time) / span);
                    };
                    for (let k = RS.index + 1; k < n; k++) {
                        if (candles[k].close < neckVal(candles[k].epoch)) {
                            add({
                                index: k,
                                type: 'head_and_shoulders',
                                direction: 'bearish',
                                strength: 'strong',
                                timestamp: candles[k].epoch,
                                _dedupKey: 'head_and_shoulders:' + H.time,
                                points: [
                                    { time: LS.time, price: LS.price },
                                    { time: H.time, price: H.price },
                                    { time: RS.time, price: RS.price },
                                ],
                                neckline: [
                                    { time: N1.time, price: N1.price },
                                    { time: N2.time, price: N2.price },
                                ],
                            }, (Math.max(LS.price, RS.price) - Math.min(N1.price, N2.price)));
                            break;
                        }
                    }
                }
            }
        }

        // Inverted Head & Shoulders (bullish): mirror image on the lows — left
        // shoulder, a LOWER head, right shoulder near-equal to the left. Neckline
        // through the two peaks; a close above it confirms.
        for (let a = 0; a < lows.length; a++) {
            const LS = lows[a];
            for (let b = a + 1; b < lows.length; b++) {
                const H = lows[b];
                if (H.index - LS.index < 3) continue;
                if (H.price >= LS.price) continue; // head must be lower than left shoulder
                for (let c = b + 1; c < lows.length; c++) {
                    const RS = lows[c];
                    if (RS.index - H.index < 3) continue;
                    if (RS.index - LS.index < 10) continue; // meaningful span
                    if (RS.price <= H.price) continue; // head must stay the lowest
                    if (Math.abs(RS.price - LS.price) > LS.price * tol * 1.5) continue; // shoulders ~equal
                    if (H.price > Math.min(LS.price, RS.price) * (1 - 0.0015)) continue; // head protrudes below shoulders
                    let N1 = null, N2 = null;
                    for (const Hh of highs) {
                        if (Hh.index > LS.index && Hh.index < H.index && (!N1 || Hh.price > N1.price)) N1 = Hh;
                        if (Hh.index > H.index && Hh.index < RS.index && (!N2 || Hh.price > N2.price)) N2 = Hh;
                    }
                    if (!N1 || !N2) continue;
                    const neckVal = (t) => {
                        const span = (N2.time - N1.time) || 1;
                        return N1.price + (N2.price - N1.price) * ((t - N1.time) / span);
                    };
                    for (let k = RS.index + 1; k < n; k++) {
                        if (candles[k].close > neckVal(candles[k].epoch)) {
                            add({
                                index: k,
                                type: 'inverted_head_shoulders',
                                direction: 'bullish',
                                strength: 'strong',
                                timestamp: candles[k].epoch,
                                _dedupKey: 'inverted_head_shoulders:' + H.time,
                                points: [
                                    { time: LS.time, price: LS.price },
                                    { time: H.time, price: H.price },
                                    { time: RS.time, price: RS.price },
                                ],
                                neckline: [
                                    { time: N1.time, price: N1.price },
                                    { time: N2.time, price: N2.price },
                                ],
                            }, (Math.max(N1.price, N2.price) - Math.min(LS.price, RS.price)));
                            break;
                        }
                    }
                }
            }
        }

        // Keep only the most recent handful to avoid clutter.
        return Array.from(best.values()).slice(-8);
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

    /**
     * Compute VaR / ES from ACTUAL trade results (settled profit/loss values in $).
     * Unlike calculate() (price volatility), this measures YOUR trading risk from
     * your real outcomes. Returned values are $ loss magnitudes PER TRADE.
     * Needs at least 5 settled trades to be meaningful (else all nulls).
     */
    calculateFromTrades(profits) {
        const out = { var95: null, var99: null, es95: null, es99: null, count: 0, period: 'trade' };
        const vals = (profits || []).filter(v => typeof v === 'number' && !isNaN(v));
        if (vals.length < 5) return out;
        const sorted = [...vals].sort((a, b) => a - b); // worst (most negative) first
        const n = sorted.length;
        out.count = n;
        const percentile = (p) => sorted[Math.min(n - 1, Math.max(0, Math.floor(p * n)))];
        const tailMean = (p) => {
            const idx = Math.min(n - 1, Math.max(0, Math.floor(p * n)));
            let sum = 0;
            for (let i = 0; i <= idx; i++) sum += sorted[i];
            return sum / (idx + 1);
        };
        // Profit/loss values are negative for losses; flip to a positive loss magnitude.
        out.var95 = -percentile(0.05);
        out.var99 = -percentile(0.01);
        out.es95 = -tailMean(0.05);
        out.es99 = -tailMean(0.01);
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
        const patternAction = this._patternAction(patterns, n, { mw: true });  // M/W (double top/bottom) only
        const candleAction = this._patternAction(patterns, n, { mw: false }); // candlestick formations only
        const overall = this._combine(tdiAction, patternAction, candleAction);

        // REVERSAL-only verdict (TDI/Bollinger/RSI bounce + M/W/H&S/candle).
        const reversal = this._reversalSignal(tdi, patterns, candles);

        // Suggested $ SL/TP for MULTIPLIER positions, scaled to the lot size
        // (stake): stop-loss at -50% of the stake, take-profit at +100% (1:2).
        const stake = (opts.stake && opts.stake > 0) ? opts.stake : 1;
        const suggestedSl = stake * 0.5;
        const suggestedTp = stake * 1.0;
        const directional = reversal === 'BUY' || reversal === 'SELL' ||
            overall.action === 'BUY' || overall.action === 'SELL';
        // Strength follows whichever signal is actually driving the trade.
        const strengthAction = (reversal === 'BUY' || reversal === 'SELL') ? reversal : overall.action;

        return {
            action: overall.action,
            reversal: reversal,
            tdiAction: tdiAction,
            patternAction: patternAction,
            candleAction: candleAction,
            confidence: overall.confidence,
            conflict: !!overall.conflict,
            reason: this._reason(tdiAction, patternAction, candleAction, patterns, n),
            stop_loss: directional ? Math.round(suggestedSl * 100) / 100 : null,
            take_profit: directional ? Math.round(suggestedTp * 100) / 100 : null,
            strength: this._strength(strengthAction, tdi, candles),
        };
    }

    /**
     * Signal strength 0..1 — how applicable the signal STILL is right now.
     *
     * Decays as recent candles move against the signal and drops to 0 once the
     * TDI green/red alignment flips against it. HOLD always = 0.
     */
    _strength(action, tdi, candles) {
        if (action !== 'BUY' && action !== 'SELL') return 0;
        const n = candles ? candles.length : 0;
        if (n < 5) return 0;

        // Hard gate: if the TDI green/red lines flipped against the signal, it's dead.
        let tdiOk = true;
        if (tdi && Array.isArray(tdi.fullSignal) && Array.isArray(tdi.fullRsiSmoothed)) {
            const g = tdi.fullSignal[n - 1];
            const r = tdi.fullRsiSmoothed[n - 1];
            if (g != null && r != null) tdiOk = action === 'BUY' ? g > r : g < r;
        }
        if (!tdiOk) return 0;

        // Recent momentum: how many of the last 6 candles agree with the signal.
        let agree = 0;
        const W = 6;
        for (let k = 1; k <= W; k++) {
            const c = candles[n - k];
            if (!c) continue;
            const bull = parseFloat(c.close) > parseFloat(c.open);
            if ((action === 'BUY' && bull) || (action === 'SELL' && !bull)) agree++;
        }
        const momentum = agree / W;

        // Freshness: consecutive recent bars still supporting the signal.
        let consecutive = 0;
        for (let k = 1; k <= n; k++) {
            const c = candles[n - k];
            if (!c) break;
            const bull = parseFloat(c.close) > parseFloat(c.open);
            const agrees = (action === 'BUY' && bull) || (action === 'SELL' && !bull);
            if (agrees) consecutive++; else break;
        }
        const freshness = Math.min(1, consecutive / 3);

        const s = (0.4 + 0.6 * momentum) * (0.3 + 0.7 * freshness);
        return Math.round(Math.max(0, Math.min(1, s)) * 100) / 100;
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
     * REVERSAL detector #1 — TDI + Bollinger bands + RSI bounce.
     *
     * A reversal is confirmed when RSI has been pushed beyond one of the blue
     * Bollinger bands (Bollinger(RSI,34,1.619)) and has now reclaimed it,
     * ideally on a green/red flip:
     *   - Buy : RSI was at/below the LOWER band (or <30), now back above it.
     *   - Sell: RSI was at/above the UPPER band (or >70), now back below it.
     */
    _tdiReversal(tdi) {
        if (!tdi || !Array.isArray(tdi.fullRsi) || tdi.fullRsi.length < 40) return 'NEUTRAL';
        const n = tdi.fullRsi.length;
        const last = (a, k = 0) => a[Math.max(0, n - 1 - k)];

        const rsi = tdi.fullRsi;
        const green = tdi.fullSignal;
        const red = tdi.fullRsiSmoothed;
        const up = tdi.fullUpperBand;
        const lo = tdi.fullLowerBand;

        // RSI recently beyond a band (the "extreme" it reverses from).
        let wasBelow = false, wasAbove = false;
        for (let k = 1; k <= 6; k++) {
            if (last(rsi, k) < last(lo, k) || last(rsi, k) < 30) wasBelow = true;
            if (last(rsi, k) > last(up, k) || last(rsi, k) > 70) wasAbove = true;
        }
        const rsiNow = last(rsi);
        const reclaimedLow = rsiNow > last(lo);   // back inside/above the lower band
        const reclaimedHigh = rsiNow < last(up);  // back inside/below the upper band

        // Green/Red flip in the last few bars (TDI momentum turn).
        let buyFlip = false, sellFlip = false;
        for (let k = 1; k <= 3; k++) {
            const gp = last(green, k), rp = last(red, k);
            const gc = last(green, k - 1), rc = last(red, k - 1);
            if (gc > rc && gp <= rp) buyFlip = true;
            if (gc < rc && gp >= rp) sellFlip = true;
        }

        if (wasBelow && reclaimedLow && (buyFlip || rsiNow >= 45)) return 'BUY';
        if (wasAbove && reclaimedHigh && (sellFlip || rsiNow <= 55)) return 'SELL';
        return 'NEUTRAL';
    }

    /**
     * REVERSAL detector #2 — reversal chart patterns only (M/W, head &
     * shoulders, and candlestick reversals). Continuation/momentum patterns
     * (marubozu, three soldiers/crows, flags/pennants) are excluded.
     */
    _reversalPatterns(patterns, candleCount) {
        const w = {
            // bullish reversal patterns
            hammer: 1, inverted_hammer: 1, dragonfly_doji: 1,
            bullish_engulfing: 2, bullish_harami: 1, piercing_line: 1,
            tweezer_bottom: 1, morning_star: 2,
            double_bottom: 2, inverted_head_shoulders: 2,
            // bearish reversal patterns
            hanging_man: 1, shooting_star: 1, gravestone_doji: 1,
            bearish_engulfing: 2, bearish_harami: 1, dark_cloud_cover: 1,
            tweezer_top: 1, evening_star: 2,
            double_top: 2, head_and_shoulders: 2,
        };
        const recent = (patterns || []).filter(p => p.index >= candleCount - 12);
        let bull = 0, bear = 0;
        recent.forEach(p => {
            const weight = w[p.type];
            if (!weight) return;                  // not a reversal pattern
            if (p.direction === 'bullish') bull += weight;
            else if (p.direction === 'bearish') bear += weight;
        });
        if (bull > bear) return 'BUY';
        if (bear > bull) return 'SELL';
        return 'NEUTRAL';
    }

    /**
     * Combined REVERSAL verdict — what auto-trades now trigger on.
     *
     * A tradeable reversal needs BOTH signals to agree: the TDI/Bollinger/RSI
     * bounce AND a reversal chart pattern (M/W, H&S or candlestick) in the
     * same direction. Anything else = NEUTRAL (no reversal trade).
     */
    _reversalSignal(tdi, patterns, candles) {
        const tdiRev = this._tdiReversal(tdi);
        const patRev = this._reversalPatterns(patterns, candles ? candles.length : 0);
        if (tdiRev === 'BUY' && patRev === 'BUY') return 'BUY';
        if (tdiRev === 'SELL' && patRev === 'SELL') return 'SELL';
        return 'NEUTRAL';
    }

    /**
     * Verdict from a pattern group: opts.mw=true counts M/W (double top/bottom)
     * only; opts.mw=false counts candlestick formations only. Weighted bullish
     * vs bearish in the recent window.
     */
    _patternAction(patterns, candleCount, opts = {}) {
        const onlyMw = !!opts.mw;
        const recent = (patterns || []).filter(p => p.index >= candleCount - 10);
        let bull = 0, bear = 0;
        recent.forEach(p => {
            const isMw = p.type === 'double_top' || p.type === 'double_bottom' ||
                p.type === 'head_and_shoulders' || p.type === 'inverted_head_shoulders' ||
                p.type === 'bullish_flag' || p.type === 'bearish_flag' ||
                p.type === 'bullish_pennant' || p.type === 'bearish_pennant';
            if (onlyMw && !isMw) return;
            if (!onlyMw && isMw) return;
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
     * Overall decision (ALL-ALIGNED rule).
     *
     * BUY/SELL is ONLY issued when TDI, the M/W pattern, AND the candlestick
     * verdict all point the same direction. Anything else = HOLD.
     */
    _combine(tdi, pattern, candle) {
        if (tdi === 'BUY' && pattern === 'BUY' && candle === 'BUY') return { action: 'BUY', confidence: 0.95 };
        if (tdi === 'SELL' && pattern === 'SELL' && candle === 'SELL') return { action: 'SELL', confidence: 0.95 };
        return { action: 'NEUTRAL', confidence: 0.2 };
    }

    /**
     * Human-readable reasoning.
     */
    _reason(tdiAction, patternAction, candleAction, patterns, candleCount) {
        if (tdiAction === 'NEUTRAL' || patternAction === 'NEUTRAL' || candleAction === 'NEUTRAL') {
            return 'Not all signals aligned (TDI / M-W / candlestick) — HOLD.';
        }
        const tdiTxt = tdiAction === 'BUY' ? 'TDI bullish' : 'TDI bearish';
        const patTxt = patternAction === 'BUY' ? 'M/W bullish' : 'M/W bearish';
        const canTxt = candleAction === 'BUY' ? 'candles bullish' : 'candles bearish';
        const recent = (patterns || []).filter(p => p.index >= candleCount - 10);
        const mw = recent.filter(p => ['double_top', 'double_bottom', 'head_and_shoulders', 'inverted_head_shoulders',
            'bullish_flag', 'bearish_flag', 'bullish_pennant', 'bearish_pennant'].includes(p.type));
        const labels = { double_top: 'M', double_bottom: 'W', head_and_shoulders: 'H&S', inverted_head_shoulders: 'iH&S',
            bullish_flag: 'Flag', bearish_flag: 'Flag', bullish_pennant: 'Penn', bearish_pennant: 'Penn' };
        const mwTxt = mw.length ? ` (${mw.map(p => labels[p.type]).join('/')})` : '';
        return `${tdiTxt} / ${patTxt}${mwTxt} / ${canTxt} — all aligned`;
    }
}
