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
        this.symbol = 'R_100';
        this.timeframe = '1m';
        this.isDrawing = false;
        this.drawStartPoint = null;
        this.drawingLines = [];

        this.init();
    }

    init() {
        if (!this.container) return;

        this.chart = LightweightCharts.createChart(this.container, {
            layout: {
                background: { color: '#ffffff' },
                textColor: '#6b7280',
                fontSize: 12,
                fontFamily: 'Inter, -apple-system, sans-serif',
            },
            grid: {
                vertLines: { color: '#f0f2f5' },
                horzLines: { color: '#f0f2f5' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: {
                    color: '#2962ff',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: '#2962ff',
                },
                horzLine: {
                    color: '#2962ff',
                    width: 1,
                    style: LightweightCharts.LineStyle.Dashed,
                    labelBackgroundColor: '#2962ff',
                },
            },
            rightPriceScale: {
                borderColor: '#e4e7ec',
                scaleMargins: { top: 0.1, bottom: 0.3 },
            },
            timeScale: {
                borderColor: '#e4e7ec',
                timeVisible: true,
                secondsVisible: false,
                rightOffset: 12,
                barSpacing: 8,
            },
        });

        // Candlestick series
        this.candleSeries = this.chart.addCandlestickSeries({
            upColor: '#00c853',
            downColor: '#ff1744',
            borderUpColor: '#00c853',
            borderDownColor: '#ff1744',
            wickUpColor: '#00c853',
            wickDownColor: '#ff1744',
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

        // Handle resize
        this._handleResize();
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

        // Fit content
        this.chart.timeScale().fitContent();
        document.getElementById('chartLoading').classList.add('d-none');
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
    }

    /**
     * Resize handler.
     */
    _handleResize() {
        const observer = new ResizeObserver(() => {
            if (this.chart) {
                this.chart.applyOptions({
                    width: this.container.clientWidth,
                    height: 500,
                });
            }
        });
        observer.observe(this.container.parentElement);
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
