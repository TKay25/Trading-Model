/**
 * Trading Chart Module
 * Handles the TradingView Lightweight Charts integration with
 * candlestick chart, drawing tools, and timeframe switching.
 */

// Height of the TDI pane, persisted as a FRACTION of the two panes' combined
// height rather than in pixels, so the split stays sensible when the density
// toggle or the window changes how much height is available.
const PANE_SPLIT_KEY = 'tradevue-pane-split';

class TradingChart {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.chart = null;
        this.candleSeries = null;
        this.volumeSeries = null;
        this.drawings = [];
        this.currentTool = null;
        this.data = [];
        this.symbol = 'R_75';
        this.timeframe = '5m';
        this.isDrawing = false;
        this.drawStartPoint = null;
        this.drawingLines = [];
        this.patternLines = [];

        // TDI / RSI pane
        this.rsiChart = null;
        this.rsiPaneEl = null;
        this.rsiSeries = null;
        this.rsiSignalSeries = null;
        this.rsiSmoothedSeries = null;
        this.rsiMarketBaseSeries = null;
        this.rsiUpperSeries = null;
        this.rsiLowerSeries = null;
        this.rsiMidSeries = null;
        this._tdiInited = false;
        this._fittedOnce = false;

        this.init();
    }

    init() {
        if (!this.container) return;

        const c = this._palette();
        this.chart = LightweightCharts.createChart(this.container, {
            layout: {
                background: { color: c.bg },
                textColor: c.text,
                fontSize: 12,
                fontFamily: 'Inter, -apple-system, sans-serif',
                attributionLogo: false,
            },
            grid: {
                vertLines: { color: c.grid },
                horzLines: { color: c.grid },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: {
                    color: c.cross,
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: c.crossLabel,
                },
                horzLine: {
                    color: c.cross,
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: c.crossLabel,
                },
            },
            rightPriceScale: {
                borderColor: c.border,
                scaleMargins: { top: 0.08, bottom: 0.22 },
            },
            timeScale: {
                borderColor: c.border,
                timeVisible: true,
                secondsVisible: false,
                rightOffset: 12,
                barSpacing: 8,
            },
        });

        // Candlestick series
        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#10b981',
            downColor: '#ef4444',
            borderUpColor: '#10b981',
            borderDownColor: '#ef4444',
            wickUpColor: '#34d399',
            wickDownColor: '#f87171',
            priceFormat: { type: 'price', minMove: 0.01 },
        });

        // Volume series (hidden under candles)
        this.volumeSeries = this.chart.addHistogramSeries({
            priceFormat: { type: 'volume' },
            priceScaleId: 'volume',
            scaleMargins: { top: 0.85, bottom: 0 },
        });

        this.chart.priceScale('volume').applyOptions({
            scaleMargins: { top: 0.85, bottom: 0 },
        });

        // TDI / RSI pane
        this._initRsiPane();

        // Keep both charts' time scales in sync (zoom/pan together).
        this._syncTimeScales();

        // Apply the active theme to the charts
        this.setTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');

        // Handle resize
        this._handleResize();

        // Draggable split between the price chart and the TDI pane
        this._initSplitter();
    }

    /**
     * Initialize the separate RSI (TDI) pane below the price chart.
     */
    _initRsiPane() {
        this.rsiPaneEl = document.getElementById('rsiPane');
        if (!this.rsiPaneEl) return;

        const c = this._palette();
        this.rsiChart = LightweightCharts.createChart(this.rsiPaneEl, {
            layout: {
                background: { color: c.paneBg },
                textColor: c.text,
                fontSize: 11,
                fontFamily: 'Inter, -apple-system, sans-serif',
                attributionLogo: false,
            },
            grid: {
                vertLines: { color: c.grid },
                horzLines: { color: c.grid },
            },
            rightPriceScale: { borderColor: c.border, scaleMargins: { top: 0.12, bottom: 0.12 } },
            timeScale: { borderColor: c.border, timeVisible: true, secondsVisible: false, rightOffset: 12, barSpacing: 8 },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: c.cross, width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: c.crossLabel },
                horzLine: { color: c.cross, width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: c.crossLabel },
            },
        });

        const common = { priceLineVisible: false, crosshairMarkerVisible: false };
        this.rsiSeries = this.rsiChart.addLineSeries({ color: c.tdiRsi, lineWidth: 1, lastValueVisible: true, ...common });          // RSI (13)
        this.rsiSignalSeries = this.rsiChart.addLineSeries({ color: c.tdiSignal, lineWidth: 2, lastValueVisible: true, ...common });   // Signal SMA(RSI,2)
        this.rsiSmoothedSeries = this.rsiChart.addLineSeries({ color: c.tdiSmoothed, lineWidth: 2, lastValueVisible: true, ...common }); // Smoothed SMA(RSI,7)
        this.rsiMarketBaseSeries = this.rsiChart.addLineSeries({ color: c.tdiBase, lineWidth: 2, lastValueVisible: true, ...common }); // Market Base SMA(RSI,34)
        this.rsiUpperSeries = this.rsiChart.addLineSeries({ color: 'rgba(96,165,250,0.45)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, lastValueVisible: false, ...common });
        this.rsiLowerSeries = this.rsiChart.addLineSeries({ color: 'rgba(96,165,250,0.45)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, lastValueVisible: false, ...common });
        this.rsiMidSeries = this.rsiChart.addLineSeries({ color: 'rgba(142,163,192,0.22)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, lastValueVisible: false, ...common });
    }

    /**
     * Keep the price chart and the RSI/TDI pane time scales in sync, so that
     * zooming or panning one chart automatically does the same on the other.
     */
    _syncTimeScales() {
        if (!this.chart || !this.rsiChart) return;
        const sync = (source, target) => {
            let applying = false;
            source.timeScale().subscribeVisibleLogicalRangeChange((range) => {
                if (applying || !range) return;
                applying = true;
                try {
                    target.timeScale().setVisibleLogicalRange(range);
                } catch (_) { /* ignore */ }
                applying = false;
            });
        };
        sync(this.chart, this.rsiChart);
        sync(this.rsiChart, this.chart);
    }

    /**
     * Resolve a CSS custom property from :root, falling back to a literal.
     */
    _cssVar(name, fallback) {
        try {
            const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
            return v || fallback;
        } catch (e) {
            return fallback;
        }
    }

    /**
     * The chart palette, READ FROM THE STYLESHEET.
     *
     * This used to be a second hard-coded copy of the theme colours inside the
     * JS, which meant the canvas drifted from the cards every time a colour was
     * changed in style.css (exactly what happened with the light theme). The
     * stylesheet is now the single source of truth and the fallbacks below are
     * only a safety net if a token is ever missing.
     */
    _palette() {
        const v = (n, f) => this._cssVar(n, f);
        return {
            bg: v('--chart-bg', '#0d1424'),
            paneBg: v('--chart-pane-bg', '#111a2e'),
            text: v('--chart-text', '#8ea3c0'),
            grid: v('--chart-grid', 'rgba(30,41,59,0.50)'),
            border: v('--chart-border', '#1e293b'),
            cross: v('--chart-cross', '#22d3ee'),
            crossLabel: v('--chart-cross-label', '#155e75'),
            tdiRsi: v('--tdi-rsi', '#60a5fa'),
            tdiSignal: v('--tdi-signal', '#34d399'),
            tdiSmoothed: v('--tdi-smoothed', '#f87171'),
            tdiBase: v('--tdi-base', '#fbbf24'),
        };
    }

    /**
     * Apply a theme ('dark' | 'light') to the price chart and RSI pane.
     *
     * The `theme` argument is kept for API compatibility (theme.js passes it),
     * but the colours now come from whichever theme is active in the DOM, so the
     * two can never disagree.
     */
    setTheme(theme) { // eslint-disable-line no-unused-vars
        const c = this._palette();

        if (this.chart) {
            this.chart.applyOptions({
                layout: { background: { color: c.bg }, textColor: c.text },
                grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
                rightPriceScale: { borderColor: c.border },
                timeScale: { borderColor: c.border },
                crosshair: {
                    vertLine: { color: c.cross, labelBackgroundColor: c.crossLabel },
                    horzLine: { color: c.cross, labelBackgroundColor: c.crossLabel },
                },
            });
        }
        if (this.rsiChart) {
            this.rsiChart.applyOptions({
                layout: { background: { color: c.paneBg }, textColor: c.text },
                grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
                rightPriceScale: { borderColor: c.border },
                timeScale: { borderColor: c.border },
                crosshair: {
                    vertLine: { color: c.cross, labelBackgroundColor: c.crossLabel },
                    horzLine: { color: c.cross, labelBackgroundColor: c.crossLabel },
                },
            });
        }
        // The TDI lines are part of the theme too — the legend dots in the
        // markup use the same tokens, so the two stay in sync by construction.
        if (this.rsiSeries) this.rsiSeries.applyOptions({ color: c.tdiRsi });
        if (this.rsiSignalSeries) this.rsiSignalSeries.applyOptions({ color: c.tdiSignal });
        if (this.rsiSmoothedSeries) this.rsiSmoothedSeries.applyOptions({ color: c.tdiSmoothed });
        if (this.rsiMarketBaseSeries) this.rsiMarketBaseSeries.applyOptions({ color: c.tdiBase });
    }

    /**
     * Draw the TDI indicators in the RSI pane (per TDI indicator.pdf):
     * RSI(13), Signal SMA(RSI,2), Smoothed SMA(RSI,7), Market Base SMA(RSI,34),
     * and Bollinger(RSI,34,1.619) volatility bands.
     */
    setTDI(tdi) {
        if (!tdi || !this.data || this.data.length === 0) return;
        if (!this.rsiChart) return;

        const rsiData = [], signalData = [], rsiSmData = [], mbData = [],
              upperData = [], lowerData = [], midData = [];
        for (let i = 0; i < this.data.length; i++) {
            const t = this.data[i].epoch;
            const r = tdi.fullRsi && tdi.fullRsi[i];
            const sig = tdi.fullSignal && tdi.fullSignal[i];
            const s = tdi.fullRsiSmoothed && tdi.fullRsiSmoothed[i];
            const mb = tdi.fullMarketBase && tdi.fullMarketBase[i];
            const u = tdi.fullUpperBand && tdi.fullUpperBand[i];
            const l = tdi.fullLowerBand && tdi.fullLowerBand[i];
            if (r != null && !isNaN(r)) rsiData.push({ time: t, value: r });
            if (sig != null && !isNaN(sig)) signalData.push({ time: t, value: sig });
            if (s != null && !isNaN(s)) rsiSmData.push({ time: t, value: s });
            if (mb != null && !isNaN(mb)) mbData.push({ time: t, value: mb });
            if (u != null && !isNaN(u)) upperData.push({ time: t, value: u });
            if (l != null && !isNaN(l)) lowerData.push({ time: t, value: l });
            midData.push({ time: t, value: 50 });
        }
        this.rsiSeries.setData(rsiData);
        this.rsiSignalSeries.setData(signalData);
        this.rsiSmoothedSeries.setData(rsiSmData);
        this.rsiMarketBaseSeries.setData(mbData);
        this.rsiUpperSeries.setData(upperData);
        this.rsiLowerSeries.setData(lowerData);
        this.rsiMidSeries.setData(midData);

        if (!this._tdiInited) {
            this._tdiInited = true;
            if (this.chart) this.chart.timeScale().fitContent();
            if (this.rsiChart) this.rsiChart.timeScale().fitContent();
        }
    }

    /**
     * Clear all TDI / RSI overlays.
     */
    clearTDI() {
        if (this.rsiChart) {
            this.rsiSeries.setData([]);
            this.rsiSignalSeries.setData([]);
            this.rsiSmoothedSeries.setData([]);
            this.rsiMarketBaseSeries.setData([]);
            this.rsiUpperSeries.setData([]);
            this.rsiLowerSeries.setData([]);
            this.rsiMidSeries.setData([]);
        }
        this._tdiInited = false;
    }

    /**
     * Load and display candle data.
     */
    setCandleData(candles) {
        if (!candles || candles.length === 0) return;

        this.data = candles;

        const candleData = candles.map(c => ({
            time: c.epoch,
            open: parseFloat(c.open),
            high: parseFloat(c.high),
            low: parseFloat(c.low),
            close: parseFloat(c.close),
        }));

        this.candleSeries.setData(candleData);

        // Set volume data
        const volumeData = candles.map((c, i) => {
            const isUp = parseFloat(c.close) >= parseFloat(c.open);
            return {
                time: c.epoch,
                value: parseFloat(c.volume || (c.close - c.open) * 100),
                color: isUp ? 'rgba(34, 171, 148, 0.3)' : 'rgba(242, 54, 69, 0.3)',
            };
        });
        this.volumeSeries.setData(volumeData);

        // Fit content only on the first load so live updates don't reset the
        // user's zoom. The sync keeps the RSI pane aligned automatically.
        if (!this._fittedOnce) {
            this._fittedOnce = true;
            this.chart.timeScale().fitContent();
            if (this.rsiChart) this.rsiChart.timeScale().fitContent();
        }
        document.getElementById('chartLoading').classList.add('d-none');
    }

    /**
     * Clear all candle data from the chart.
     */
    clearChart() {
        if (this.candleSeries) {
            this.candleSeries.setData([]);
        }
        if (this.volumeSeries) {
            this.volumeSeries.setData([]);
        }
        this.clearDrawings();
        this.patternLines.forEach(line => this.chart.removeSeries(line));
        this.patternLines = [];
        this.clearTDI();
        this.data = [];
        this._fittedOnce = false;
    }

    /**
     * Add a simple trendline drawing.
     */
    addTrendline(startPoint, endPoint, color = '#0d6efd') {
        const line = this.chart.addLineSeries({
            color: color,
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            crosshairMarkerVisible: false,
            lastValueVisible: false,
            priceLineVisible: false,
        });

        line.setData([
            { time: startPoint.time, value: startPoint.price },
            { time: endPoint.time, value: endPoint.price },
        ]);

        this.drawingLines.push(line);
        return line;
    }

    /**
     * Add a horizontal line (support/resistance/stop loss).
     */
    addHorizontalLine(price, color, label = '') {
        const line = this.chart.addLineSeries({
            color: color,
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            crosshairMarkerVisible: false,
            lastValueVisible: true,
            priceLineVisible: false,
            lastPriceAnimation: LightweightCharts.LastPriceAnimationMode.OnDataUpdate,
        });

        const times = this.data.map(c => c.epoch);
        if (times.length > 0) {
            line.setData([
                { time: times[0], value: price },
                { time: times[times.length - 1], value: price },
            ]);
        }

        this.drawingLines.push(line);
        return line;
    }

    /**
     * Add Fibonacci retracement levels.
     */
    addFibonacci(startPrice, endPrice, startTime, endTime) {
        const levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
        const diff = endPrice - startPrice;
        const fibColors = ['#9b59b6', '#3498db', '#2ecc71', '#f1c40f', '#e67e22', '#e74c3c', '#9b59b6'];

        levels.forEach((level, i) => {
            const price = endPrice - diff * level;
            const line = this.addHorizontalLine(
                price,
                fibColors[i % fibColors.length],
                `${((1 - level) * 100).toFixed(1)}%`
            );
        });
    }

    /**
     * Mark a candle pattern on the chart.
     *
     * Deliberately does NOT do two things it used to:
     *
     *  1. It no longer creates a PRICE LINE. It used to add one at `price: 0`
     *     with the pattern name as its title. Price 0 is far outside a 41,000
     *     index's range, so the line itself was invisible — but the AXIS LABEL
     *     still rendered, stamping "🟢 hanging_man" style text along the bottom
     *     edge of the chart once per recent pattern. That is the stray text that
     *     appeared to float below the candles next to the volume pane.
     *
     *  2. It no longer writes the full pattern name across the price action. Up
     *     to half a dozen "THREE BLACK CROWS" / "INVERTED HAMMER" labels buried
     *     the candles they were describing. The caller now passes a short code,
     *     and only the most recent formation is labelled at all.
     */
    highlightPattern(timestamp, type, direction, label) {
        const color = direction === 'bullish' ? '#22ab94' : '#f23645';
        this.candleSeries.setMarkers([
            ...(this.candleSeries.markers() || []),
            {
                time: timestamp,
                position: direction === 'bullish' ? 'belowBar' : 'aboveBar',
                color: color,
                shape: direction === 'bullish' ? 'arrowUp' : 'arrowDown',
                text: label || '',
            },
        ]);
    }

    /**
     * Draw M (double-top) and W (double-bottom) shapes on the price chart.
     * Each pattern's `points` array becomes a dashed polyline with markers
     * at the vertices and an M/W label on the first vertex.
     */
    drawMWPatterns(patterns) {
        // Clear previously drawn M/W polylines.
        this.patternLines.forEach(line => this.chart.removeSeries(line));
        this.patternLines = [];

        const chartPatternTypes = ['double_top', 'double_bottom', 'head_and_shoulders', 'inverted_head_shoulders',
            'bullish_flag', 'bearish_flag', 'bullish_pennant', 'bearish_pennant'];
        let mw = (patterns || []).filter(p =>
            chartPatternTypes.includes(p.type) && (
                (Array.isArray(p.points) && p.points.length >= 2) ||
                (Array.isArray(p.flagTop) && p.flagTop.length >= 2)
            )
        );
        if (!mw.length) return;

        // Draw patterns chronologically across the WHOLE chart so they stay
        // visible no matter where the user zooms (not just the most recent few).
        const firstTime = (p) => (p.points && p.points[0]) ? p.points[0].time : p.flagTop[0].time;
        mw.sort((a, b) => firstTime(a) - firstTime(b));
        const maxPatterns = 8;
        if (mw.length > maxPatterns) {
            const step = (mw.length - 1) / (maxPatterns - 1);
            mw = mw.map((p, i) => [i, p])
                .filter(([i]) => Math.abs(i - Math.round(i / step) * step) < 0.001)
                .map(([, p]) => p);
        }

        // Drop any old pattern markers so we don't stack duplicates. Pattern
        // markers are the only CIRCLES on the chart (highlightPattern uses
        // arrowUp/arrowDown), so filtering on shape still removes them now that
        // most of them carry no text.
        const existing = this.candleSeries.markers() || [];
        const kept = existing.filter(m => m.shape !== 'circle');
        this.candleSeries.setMarkers(kept);

        const labelMap = {
            double_top: 'M', double_bottom: 'W',
            head_and_shoulders: 'HS', inverted_head_shoulders: 'iHS',
            bullish_flag: 'FLG', bearish_flag: 'FLG',
            bullish_pennant: 'PNN', bearish_pennant: 'PNN',
        };
        const bullishTypes = ['double_bottom', 'inverted_head_shoulders', 'bullish_flag', 'bullish_pennant'];
        // Only the MOST RECENT formation carries a text label. Previously all
        // eight did, which stamped M/W/HS/iHS/FLG/PNN across the price action and
        // made the chart harder to read than the patterns it was describing.
        // `mw` is sorted oldest-first above, so the last entry is the newest.
        const labelIdx = mw.length - 1;
        mw.forEach((p, idx) => {
            const isBullish = bullishTypes.includes(p.type);
            // Faint, background-style colour so candles stay readable.
            const color = isBullish ? 'rgba(34, 171, 148, 0.30)' : 'rgba(242, 54, 69, 0.30)';
            const style = {
                color: color,
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                crosshairMarkerVisible: false,
                lastValueVisible: false,
                priceLineVisible: false,
            };

            // Main silhouette (M/W hump, H&S shoulder-head-shoulder, or pole).
            if (Array.isArray(p.points) && p.points.length >= 2) {
                const line = this.chart.addLineSeries(style);
                line.setData(p.points.map(pt => ({ time: pt.time, value: pt.price })));
                this.patternLines.push(line);
            }

            // H&S neckline (through the two valleys / peaks).
            if (Array.isArray(p.neckline) && p.neckline.length >= 2) {
                const neck = this.chart.addLineSeries({ ...style, color: isBullish ? 'rgba(34, 171, 148, 0.18)' : 'rgba(242, 54, 69, 0.18)' });
                neck.setData(p.neckline.map(pt => ({ time: pt.time, value: pt.price })));
                this.patternLines.push(neck);
            }

            // Flag / Pennant: pole + upper & lower channel lines.
            if (Array.isArray(p.flagTop) && p.flagTop.length >= 2) {
                const top = this.chart.addLineSeries({ ...style, color: isBullish ? 'rgba(34, 171, 148, 0.45)' : 'rgba(242, 54, 69, 0.45)' });
                top.setData(p.flagTop.map(pt => ({ time: pt.time, value: pt.price })));
                this.patternLines.push(top);
            }
            if (Array.isArray(p.flagBottom) && p.flagBottom.length >= 2) {
                const bottom = this.chart.addLineSeries({ ...style, color: isBullish ? 'rgba(34, 171, 148, 0.45)' : 'rgba(242, 54, 69, 0.45)' });
                bottom.setData(p.flagBottom.map(pt => ({ time: pt.time, value: pt.price })));
                this.patternLines.push(bottom);
            }
            if (Array.isArray(p.pole) && p.pole.length >= 2) {
                const pole = this.chart.addLineSeries({ ...style, color: isBullish ? 'rgba(34, 171, 148, 0.55)' : 'rgba(242, 54, 69, 0.55)' });
                pole.setData(p.pole.map(pt => ({ time: pt.time, value: pt.price })));
                this.patternLines.push(pole);
            }

            // Subtle label at the first vertex only (no bold arrows), and only
            // for the newest pattern.
            const first = (Array.isArray(p.points) && p.points.length) ? p.points[0] : (p.flagTop ? p.flagTop[0] : null);
            if (!first) return;
            this.candleSeries.setMarkers([
                ...(this.candleSeries.markers() || []),
                {
                    time: first.time,
                    position: isBullish ? 'belowBar' : 'aboveBar',
                    color: isBullish ? 'rgba(34, 171, 148, 0.55)' : 'rgba(242, 54, 69, 0.55)',
                    shape: 'circle',
                    text: idx === labelIdx ? (labelMap[p.type] || 'M') : '',
                },
            ]);
        });
    }

    /**
     * Clear all custom drawings.
     */
    clearDrawings() {
        this.drawingLines.forEach(line => this.chart.removeSeries(line));
        this.drawingLines = [];
        this.drawings = [];
        this.candleSeries.setMarkers([]);
        this._clearPriceLines();
    }

    _clearPriceLines() {
        // PriceLines can't be easily cleared, so we just remap
    }

    /**
     * Set active drawing tool.
     */
    setDrawingTool(tool) {
        this.currentTool = tool;
        this.isDrawing = false;
        this.drawStartPoint = null;

        const container = this.container;
        if (tool) {
            container.style.cursor = 'crosshair';
            this._enableDrawingMode();
        } else {
            container.style.cursor = 'default';
            this._disableDrawingMode();
        }
    }

    _enableDrawingMode() {
        this.container.addEventListener('click', this._drawClickHandler);
    }

    _disableDrawingMode() {
        this.container.removeEventListener('click', this._drawClickHandler);
    }

    _drawClickHandler = (e) => {
        // Get time/price from mouse position
        const rect = this.container.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const timeScale = this.chart.timeScale();
        const logical = timeScale.coordinateToLogical(x);
        const price = this.candleSeries.coordinateToPrice(y);

        if (logical === null || price === null) return;

        const time = timeScale.logicalToCoordinate(logical);
        const timeValue = this.chart.timeScale().coordinateToTime(x);

        if (!this.isDrawing) {
            // First click - start point
            this.isDrawing = true;
            this.drawStartPoint = { time: timeValue, price: price };
        } else {
            // Second click - end point
            if (this.currentTool === 'trendline') {
                this.addTrendline(this.drawStartPoint, { time: timeValue, price: price });
            } else if (this.currentTool === 'fib') {
                this.addFibonacci(
                    this.drawStartPoint.price, price,
                    this.drawStartPoint.time, timeValue
                );
            }
            this.isDrawing = false;
            this.drawStartPoint = null;
        }
    };

    /**
     * Update timeframe and symbol.
     */
    updateConfig(symbol, timeframe) {
        this.symbol = symbol;
        this.timeframe = timeframe;
        // A new symbol/timeframe should re-fit the view on its first render.
        this._fittedOnce = false;
    }

    /**
     * Size both charts to their containers.
     *
     * Uses the documented `chart.resize(w, h, forceRepaint)` rather than
     * `applyOptions({ width, height })`. applyOptions only RECORDS the requested
     * size and treats a repeated identical request as a no-op, so after a
     * density change the canvas kept its old height — measured: still rendered
     * at 442px inside a 360px pane 600ms later, i.e. visibly overflowing and
     * clipped. resize() with forceRepaint applies immediately and reliably.
     */
    _applySize() {
        if (this.chart) {
            const w = this.container.clientWidth;
            const h = this.container.clientHeight;
            if (typeof this.chart.resize === 'function') this.chart.resize(w, h, true);
            else this.chart.applyOptions({ width: w, height: h });
        }
        if (this.rsiChart && this.rsiPaneEl) {
            const w = this.rsiPaneEl.clientWidth;
            const h = this.rsiPaneEl.clientHeight;
            if (typeof this.rsiChart.resize === 'function') this.rsiChart.resize(w, h, true);
            else this.rsiChart.applyOptions({ width: w, height: h });
        }
    }

    /**
     * Resize handler: keep both charts sized to their containers.
     */
    _handleResize() {
        const panel = this.container.parentElement ? this.container.parentElement.parentElement : this.container;
        const observer = new ResizeObserver(() => this._applySize());
        observer.observe(panel);
        // Also watch the two panes themselves. Dragging the splitter changes
        // THEIR boxes but not the panel's, so observing only the panel would
        // miss it and the canvases would keep their old heights.
        if (this.container.parentElement) observer.observe(this.container.parentElement);
        if (this.rsiPaneEl) observer.observe(this.rsiPaneEl);
        this._resizeObserver = observer;
        this._applySize();
    }

    /* ======================================================================
       RESIZABLE SPLIT: price chart <-> TDI pane
       ----------------------------------------------------------------------
       Only the PANE's height is ever written. #chartContainer is flex:1 1 auto,
       so the chart silently absorbs the difference and the combined height is
       unchanged by construction - there is no arithmetic to get wrong and no
       way for the two panes to fight each other.
       ====================================================================== */

    /** Total height shared by the two panes (excludes the fixed pane title). */
    _splitTotal() {
        const cc = (this.container && this.container.parentElement) || null;
        return (cc ? cc.clientHeight : 0) + (this.rsiPaneEl ? this.rsiPaneEl.clientHeight : 0);
    }

    /**
     * Clamp bounds for the pane, read from CSS `min-height` rather than
     * hard-coded, so the density variants keep owning the real limits.
     */
    _splitLimits(total) {
        const cc = (this.container && this.container.parentElement) || null;
        const minChart = (cc && parseFloat(getComputedStyle(cc).minHeight)) || 140;
        const minPane = (this.rsiPaneEl && parseFloat(getComputedStyle(this.rsiPaneEl).minHeight)) || 40;
        // The 2px is slack for sub-pixel rounding: the clamp exists to stop the
        // pane from pushing the grid row taller, so sitting exactly on the
        // boundary is not good enough.
        return { minPane: minPane, maxPane: Math.max(minPane, total - minChart - 2) };
    }

    /** Apply a pane height (px), leaving the shortfall to the chart. */
    _setSplitPx(px, total) {
        if (!this.rsiPaneEl) return;
        const lim = this._splitLimits(total || this._splitTotal());
        const h = Math.round(Math.min(Math.max(px, lim.minPane), lim.maxPane));
        if (h === this._splitPx) return;
        this._splitPx = h;
        this.rsiPaneEl.style.height = h + 'px';
        if (this.splitterEl) this.splitterEl.setAttribute('aria-valuenow', String(h));
        this._applySize();
        // One coalesced trailing pass. A TIMER, not requestAnimationFrame:
        // rAF is paused in a background tab (measured previously) and a drag
        // can outlive the tab losing focus.
        if (!this._splitKick) {
            this._splitKick = true;
            setTimeout(() => { this._splitKick = false; this._applySize(); }, 80);
        }
    }

    /** Re-apply the saved fraction (no-op when the user never set one). */
    _applySplit() {
        if (!this.rsiPaneEl) return;
        let saved = NaN;
        try { saved = parseFloat(localStorage.getItem(PANE_SPLIT_KEY)); } catch (e) { saved = NaN; }
        if (!isFinite(saved) || saved <= 0 || saved >= 1) return;
        const total = this._splitTotal();
        if (total < 80) return;           // not laid out yet; a later pass will do it
        this._splitPx = null;             // force the write
        this._setSplitPx(saved * total, total);
    }

    /** Persist the current split as a fraction of the available height. */
    _saveSplit() {
        const total = this._splitTotal();
        if (!total || !this.rsiPaneEl) return;
        try {
            localStorage.setItem(PANE_SPLIT_KEY, (this.rsiPaneEl.clientHeight / total).toFixed(4));
        } catch (e) { /* private mode / quota - the split just will not persist */ }
    }

    /** Drop the custom split and hand the sizing back to CSS. */
    resetSplit() {
        try { localStorage.removeItem(PANE_SPLIT_KEY); } catch (e) { /* ignore */ }
        if (this.rsiPaneEl) this.rsiPaneEl.style.height = '';
        this._splitPx = null;
        this._applySize();
        setTimeout(() => this._applySize(), 60);
    }

    _initSplitter() {
        const el = document.getElementById('paneSplitter');
        if (!el || !this.rsiPaneEl || !this.container || !this.container.parentElement) return;
        this.splitterEl = el;

        let dragging = false, startY = 0, startPane = 0, total = 0;

        const onMove = (e) => {
            if (!dragging) return;
            e.preventDefault();
            // The handle sits BETWEEN the two panes, so the pane below it moves
            // OPPOSITE to the pointer: dragging UP makes the pane taller, hence
            // MINUS the delta. (Getting this backwards is easy - measured: a
            // 90px upward drag shrank the pane 74 -> 40.)
            this._setSplitPx(startPane - (e.clientY - startY), total);
        };

        const onEnd = () => {
            if (!dragging) return;
            dragging = false;
            document.body.classList.remove('panes-resizing');
            // The move/end listeners live on WINDOW while a drag is in progress
            // (see pointerdown), so they must come off here or every later
            // mouse move would keep resizing.
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onEnd);
            window.removeEventListener('pointercancel', onEnd);
            window.removeEventListener('blur', onEnd);
            this._saveSplit();
        };

        el.addEventListener('pointerdown', (e) => {
            if (e.button) return;
            e.preventDefault();
            dragging = true;
            startY = e.clientY;
            startPane = this.rsiPaneEl.clientHeight;
            total = this._splitTotal();          // frozen for the whole drag
            this._splitPx = startPane;
            document.body.classList.add('panes-resizing');
            // WINDOW, not the handle: as soon as the pointer moves it is nowhere
            // near this 7px element, and setPointerCapture proved undependable
            // here - measured, the drag itself worked but pointerup never
            // reached the handle, so the split was never saved and the page was
            // left stuck showing the resizing cursor.
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onEnd);
            window.addEventListener('pointercancel', onEnd);
            // A drag interrupted by the window losing focus would otherwise
            // leave `panes-resizing` on the body forever.
            window.addEventListener('blur', onEnd);
        });

        // Double-click restores the density's own proportions.
        el.addEventListener('dblclick', () => this.resetSplit());

        // Keyboard: the handle is focusable, so arrows nudge the split.
        el.addEventListener('keydown', (e) => {
            if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
            e.preventDefault();
            const step = e.shiftKey ? 40 : 8;
            this._splitPx = this.rsiPaneEl.clientHeight;
            this._setSplitPx(this.rsiPaneEl.clientHeight + (e.key === 'ArrowDown' ? step : -step));
            this._saveSplit();
        });

        // Re-apply the fraction whenever the layout's height changes (density
        // switch, window resize, sidebar content growing). Safe to observe the
        // panel: its height is set by the grid row, not by its own children, so
        // reacting to it cannot feed back into it.
        const panel = this.container.parentElement.parentElement;
        if (panel && typeof ResizeObserver !== 'undefined') {
            this._splitObserver = new ResizeObserver(() => this._applySplit());
            this._splitObserver.observe(panel);
        }
        this._applySplit();
    }

    /**
     * Force the charts to re-measure their containers (e.g. after a density
     * change alters the pane heights). Safe to call at any time.
     */
    resize() {
        this._applySize();
        // One extra pass next frame is free insurance against a layout that has
        // not been flushed when this is called synchronously.
        requestAnimationFrame(() => this._applySize());
    }

    /**
     * Clean up.
     */
    destroy() {
        this.clearDrawings();
        if (this.chart) {
            this.chart.remove();
            this.chart = null;
        }
    }
}
