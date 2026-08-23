/**
 * Trading Chart Module
 * Handles the TradingView Lightweight Charts integration with
 * candlestick chart, drawing tools, and timeframe switching.
 */

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

        this.chart = LightweightCharts.createChart(this.container, {
            layout: {
                background: { color: '#0d1424' },
                textColor: '#8ea3c0',
                fontSize: 12,
                fontFamily: 'Inter, -apple-system, sans-serif',
                attributionLogo: false,
            },
            grid: {
                vertLines: { color: 'rgba(30,41,59,0.5)' },
                horzLines: { color: 'rgba(30,41,59,0.5)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: {
                    color: '#22d3ee',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: '#155e75',
                },
                horzLine: {
                    color: '#22d3ee',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: '#155e75',
                },
            },
            rightPriceScale: {
                borderColor: '#1e293b',
                scaleMargins: { top: 0.08, bottom: 0.22 },
            },
            timeScale: {
                borderColor: '#1e293b',
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
    }

    /**
     * Initialize the separate RSI (TDI) pane below the price chart.
     */
    _initRsiPane() {
        this.rsiPaneEl = document.getElementById('rsiPane');
        if (!this.rsiPaneEl) return;

        this.rsiChart = LightweightCharts.createChart(this.rsiPaneEl, {
            layout: {
                background: { color: '#111a2e' },
                textColor: '#8ea3c0',
                fontSize: 11,
                fontFamily: 'Inter, -apple-system, sans-serif',
                attributionLogo: false,
            },
            grid: {
                vertLines: { color: 'rgba(30,41,59,0.5)' },
                horzLines: { color: 'rgba(30,41,59,0.5)' },
            },
            rightPriceScale: { borderColor: '#1e293b', scaleMargins: { top: 0.12, bottom: 0.12 } },
            timeScale: { borderColor: '#1e293b', timeVisible: true, secondsVisible: false, rightOffset: 12, barSpacing: 8 },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: '#22d3ee', width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#155e75' },
                horzLine: { color: '#22d3ee', width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#155e75' },
            },
        });

        const common = { priceLineVisible: false, crosshairMarkerVisible: false };
        this.rsiSeries = this.rsiChart.addLineSeries({ color: '#60a5fa', lineWidth: 1, lastValueVisible: true, ...common });          // RSI (13)
        this.rsiSignalSeries = this.rsiChart.addLineSeries({ color: '#34d399', lineWidth: 2, lastValueVisible: true, ...common });   // Signal SMA(RSI,2)
        this.rsiSmoothedSeries = this.rsiChart.addLineSeries({ color: '#f87171', lineWidth: 2, lastValueVisible: true, ...common }); // Smoothed SMA(RSI,7)
        this.rsiMarketBaseSeries = this.rsiChart.addLineSeries({ color: '#fbbf24', lineWidth: 2, lastValueVisible: true, ...common }); // Market Base SMA(RSI,34)
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
     * Apply a theme ('dark' | 'light') to the price chart and RSI pane.
     */
    setTheme(theme) {
        const dark = theme !== 'light';
        const c = dark ? {
            bg: '#0d1424', paneBg: '#111a2e', text: '#8ea3c0',
            grid: 'rgba(30,41,59,0.5)', border: '#1e293b',
            cross: '#22d3ee', crossLabel: '#155e75',
        } : {
            bg: '#ffffff', paneBg: '#f4f7fb', text: '#5b6b80',
            grid: 'rgba(227,232,239,0.7)', border: '#e3e8ef',
            cross: '#0ea5e9', crossLabel: '#0c4a6e',
        };

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
     * Highlight a pattern on the chart with a marker.
     */
    highlightPattern(timestamp, type, direction) {
        const color = direction === 'bullish' ? '#22ab94' : '#f23645';
        const emoji = direction === 'bullish' ? '🟢' : '🔴';

        this.candleSeries.createPriceLine({
            price: 0,
            color: color,
            lineWidth: 0,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: `${emoji} ${type}`,
        });

        this.candleSeries.setMarkers([
            ...(this.candleSeries.markers() || []),
            {
                time: timestamp,
                position: direction === 'bullish' ? 'belowBar' : 'aboveBar',
                color: color,
                shape: direction === 'bullish' ? 'arrowUp' : 'arrowDown',
                text: type.replace(/_/g, ' ').toUpperCase(),
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

        // Drop any old M/W/H&S/flag markers so we don't stack duplicates.
        const existing = this.candleSeries.markers() || [];
        const kept = existing.filter(m => !['M', 'W', 'HS', 'iHS', 'FLG', 'PNN'].includes(m.text));
        this.candleSeries.setMarkers(kept);

        const labelMap = {
            double_top: 'M', double_bottom: 'W',
            head_and_shoulders: 'HS', inverted_head_shoulders: 'iHS',
            bullish_flag: 'FLG', bearish_flag: 'FLG',
            bullish_pennant: 'PNN', bearish_pennant: 'PNN',
        };
        const bullishTypes = ['double_bottom', 'inverted_head_shoulders', 'bullish_flag', 'bullish_pennant'];
        mw.forEach(p => {
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

            // Subtle label at the first vertex only (no bold arrows).
            const first = (Array.isArray(p.points) && p.points.length) ? p.points[0] : (p.flagTop ? p.flagTop[0] : null);
            if (!first) return;
            this.candleSeries.setMarkers([
                ...(this.candleSeries.markers() || []),
                {
                    time: first.time,
                    position: isBullish ? 'belowBar' : 'aboveBar',
                    color: isBullish ? 'rgba(34, 171, 148, 0.55)' : 'rgba(242, 54, 69, 0.55)',
                    shape: 'circle',
                    text: labelMap[p.type] || 'M',
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
     * Resize handler.
     */
    _handleResize() {
        const panel = this.container.parentElement ? this.container.parentElement.parentElement : this.container;
        const applySize = () => {
            if (this.chart) {
                this.chart.applyOptions({
                    width: this.container.clientWidth,
                    height: this.container.clientHeight,
                });
            }
            if (this.rsiChart && this.rsiPaneEl) {
                this.rsiChart.applyOptions({
                    width: this.rsiPaneEl.clientWidth,
                    height: this.rsiPaneEl.clientHeight,
                });
            }
        };
        const observer = new ResizeObserver(applySize);
        observer.observe(panel);
        applySize();
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
