/**
 * Trading Dashboard - Main Application
 * Orchestrates chart, indicators, trading controls, and API interactions.
 */

class TradingDashboardApp {
    constructor() {
        // State
        this.symbol = 'R_100';
        this.timeframe = '1m';
        this.candles = [];
        this.autoRefreshInterval = null;

        // Modules
        this.chart = new TradingChart('tradingChart');
        this.tdi = new TDIIndicator();
        this.patternRecognizer = new PatternRecognizer();

        // DOM refs
        this.symbolSelect = document.getElementById('symbolSelect');
        this.timeframeBtns = document.querySelectorAll('.timeframe-btn');
        this.refreshBtn = document.getElementById('refreshChart');
        this.drawTrendlineBtn = document.getElementById('drawTrendline');
        this.drawFibBtn = document.getElementById('drawFib');
        this.drawRectBtn = document.getElementById('drawRectangle');
        this.clearDrawingsBtn = document.getElementById('clearDrawings');
        this.patternList = document.getElementById('patternList');
        this.patternCount = document.getElementById('patternCount');
        this.chartLoading = document.getElementById('chartLoading');

        // TDI display elements
        this.tdiRsi = document.getElementById('tdiRsi');
        this.tdiRsiSmoothed = document.getElementById('tdiRsiSmoothed');
        this.tdiMarketBase = document.getElementById('tdiMarketBase');
        this.tdiUpperBand = document.getElementById('tdiUpperBand');
        this.tdiLowerBand = document.getElementById('tdiLowerBand');
        this.tdiProgress = document.getElementById('tdiProgress');

        // Signal display elements
        this.signalBody = document.getElementById('signalBody');
        this.noSignal = document.getElementById('noSignal');
        this.signalContent = document.getElementById('signalContent');
        this.signalBadge = document.getElementById('signalBadge');
        this.signalDetails = document.getElementById('signalDetails');
        this.signalCard = document.getElementById('signalCard');

        this._bindEvents();
        this._loadInitialData();
    }

    _bindEvents() {
        // Symbol change
        this.symbolSelect.addEventListener('change', (e) => {
            this.symbol = e.target.value;
            tradingControls.setSymbol(this.symbol);
            this.chart.updateConfig(this.symbol, this.timeframe);
            this._loadChartData();
            this._runAnalysis();
        });

        // Timeframe change
        this.timeframeBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.timeframeBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.timeframe = btn.dataset.timeframe;
                this.chart.updateConfig(this.symbol, this.timeframe);
                this._loadChartData();
                this._runAnalysis();
            });
        });

        // Refresh
        this.refreshBtn.addEventListener('click', () => {
            this._loadChartData();
            this._runAnalysis();
        });

        // Drawing tools
        this.drawTrendlineBtn.addEventListener('click', () => {
            this._toggleDrawingTool('trendline', this.drawTrendlineBtn);
        });

        this.drawFibBtn.addEventListener('click', () => {
            this._toggleDrawingTool('fib', this.drawFibBtn);
        });

        this.clearDrawingsBtn.addEventListener('click', () => {
            this.chart.clearDrawings();
            document.querySelectorAll('.drawing-tool-active').forEach(el => {
                el.classList.remove('drawing-tool-active');
            });
            this.chart.setDrawingTool(null);
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.chart.setDrawingTool(null);
                document.querySelectorAll('.drawing-tool-active').forEach(el => {
                    el.classList.remove('drawing-tool-active');
                });
            }
        });
    }

    _toggleDrawingTool(tool, button) {
        if (button.classList.contains('drawing-tool-active')) {
            button.classList.remove('drawing-tool-active');
            this.chart.setDrawingTool(null);
        } else {
            // Deactivate other tools
            document.querySelectorAll('.drawing-tool-active').forEach(el => {
                el.classList.remove('drawing-tool-active');
            });
            button.classList.add('drawing-tool-active');
            this.chart.setDrawingTool(tool);
        }
    }

    /**
     * Load initial chart data on page load.
     */
    async _loadInitialData() {
        // Use demo/simulated data if no Deriv connection
        this._loadDemoData();
        this._runAnalysis();

        // Try to load real data
        this._loadChartData();
    }

    /**
     * Load candle data from the backend.
     */
    async _loadChartData() {
        this.chartLoading.classList.remove('d-none');

        try {
            const response = await fetch('/api/candles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: this.symbol,
                    timeframe: this.timeframe,
                    count: 200,
                }),
            });

            const data = await response.json();

            if (data.success && data.candles && data.candles.length > 0) {
                this.candles = data.candles;
                this.chart.setCandleData(data.candles);
                this._runAnalysis();
            } else {
                // Fall back to demo data
                console.warn('No candle data from API, using demo data');
                this._loadDemoData();
            }
        } catch (err) {
            console.warn('Failed to fetch candle data:', err);
            this._loadDemoData();
        } finally {
            this.chartLoading.classList.add('d-none');
        }
    }

    /**
     * Generate demo candle data for offline mode.
     */
    _loadDemoData() {
        const candles = [];
        const now = Math.floor(Date.now() / 1000);
        const interval = this._getTimeframeSeconds(this.timeframe);
        let price = 100 + Math.random() * 50;

        for (let i = 200; i > 0; i--) {
            const volatility = 0.5 + Math.random() * 0.8;
            const change = (Math.random() - 0.48) * volatility;
            const open = price;
            const close = price + change;
            const high = Math.max(open, close) + Math.random() * volatility * 0.5;
            const low = Math.min(open, close) - Math.random() * volatility * 0.5;
            const volume = Math.random() * 1000 + 100;

            candles.push({
                epoch: now - i * interval,
                open: open.toFixed(2),
                high: high.toFixed(2),
                low: low.toFixed(2),
                close: close.toFixed(2),
                volume: volume.toFixed(0),
            });

            price = close;
        }

        this.candles = candles;
        this.chart.setCandleData(candles);
    }

    _getTimeframeSeconds(tf) {
        const map = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 };
        return map[tf] || 60;
    }

    /**
     * Run TDI analysis and pattern detection.
     */
    async _runAnalysis() {
        if (this.candles.length === 0) return;

        // Client-side TDI calculation
        const candles = this.candles.map(c => ({
            open: parseFloat(c.open),
            high: parseFloat(c.high),
            low: parseFloat(c.low),
            close: parseFloat(c.close),
            epoch: c.epoch,
        }));

        const tdiValues = this.tdi.calculate(candles);
        if (tdiValues) {
            this._updateTDIDisplay(tdiValues);
        }

        // Pattern detection
        const patterns = this.patternRecognizer.detect(candles);
        this._displayPatterns(patterns);

        // Try server-side analysis for signals
        try {
            const response = await fetch('/api/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: this.symbol,
                    timeframe: this.timeframe,
                    count: 200,
                }),
            });

            const data = await response.json();
            if (data.success && data.analysis) {
                this._displaySignal(data.analysis.signal, data.analysis);

                // Highlight patterns on chart
                if (data.analysis.patterns) {
                    data.analysis.patterns.forEach(p => {
                        this.chart.highlightPattern(p.timestamp, p.type, p.direction);
                    });
                }
            } else {
                // Client-side signal generation
                const signal = this._generateClientSignal(tdiValues, patterns, candles);
                this._displaySignal(signal, { tdi: tdiValues, patterns });
            }
        } catch (err) {
            // Fallback to client-side signal
            const signal = this._generateClientSignal(tdiValues, patterns, candles);
            this._displaySignal(signal, { tdi: tdiValues, patterns });
        }
    }

    /**
     * Update the TDI indicator display panel.
     */
    _updateTDIDisplay(tdi) {
        this.tdiRsi.textContent = tdi.rsi ? tdi.rsi.toFixed(2) : '--';
        this.tdiRsiSmoothed.textContent = tdi.rsiSmoothed ? tdi.rsiSmoothed.toFixed(2) : '--';
        this.tdiMarketBase.textContent = tdi.marketBaseLine ? tdi.marketBaseLine.toFixed(2) : '--';
        this.tdiUpperBand.textContent = tdi.upperBand ? tdi.upperBand.toFixed(2) : '--';
        this.tdiLowerBand.textContent = tdi.lowerBand ? tdi.lowerBand.toFixed(2) : '--';

        // Color code RSI
        if (tdi.rsi !== null) {
            this.tdiProgress.style.width = `${tdi.rsi}%`;
            if (tdi.rsi > 70) {
                this.tdiProgress.className = 'progress-bar bg-danger';
                this.tdiRsi.className = 'fw-bold text-danger';
            } else if (tdi.rsi < 30) {
                this.tdiProgress.className = 'progress-bar bg-success';
                this.tdiRsi.className = 'fw-bold text-success';
            } else {
                this.tdiProgress.className = 'progress-bar bg-info';
                this.tdiRsi.className = 'fw-bold text-info';
            }
        }
    }

    /**
     * Display detected patterns in the panel.
     */
    _displayPatterns(patterns) {
        this.patternCount.textContent = `${patterns.length} patterns`;

        if (patterns.length === 0) {
            this.patternList.innerHTML = `
                <div class="col-12 text-center text-secondary">
                    <small>No reversal patterns detected</small>
                </div>
            `;
            return;
        }

        let html = '';
        patterns.forEach((p, i) => {
            const badgeClass = p.direction === 'bullish' ? 'bg-success' : 'bg-danger';
            const icon = p.direction === 'bullish' ? 'bi-arrow-up' : 'bi-arrow-down';
            html += `
                <div class="col-md-4 col-sm-6">
                    <div class="border border-secondary rounded p-2 small">
                        <span class="badge ${badgeClass} me-1">
                            <i class="bi ${icon}"></i>
                        </span>
                        <span>${p.type.replace(/_/g, ' ').toUpperCase()}</span>
                        <span class="badge bg-secondary ms-1">${p.strength}</span>
                    </div>
                </div>
            `;
        });

        this.patternList.innerHTML = html;
    }

    /**
     * Display trading signal.
     */
    _displaySignal(signal, analysis) {
        if (!signal) {
            this.noSignal.classList.remove('d-none');
            this.signalContent.classList.add('d-none');
            this.signalCard.className = 'card bg-dark border-secondary mb-3';
            return;
        }

        this.noSignal.classList.add('d-none');
        this.signalContent.classList.remove('d-none');

        const isBuy = signal.action === 'BUY';
        const badgeClass = isBuy ? 'bg-success' : 'bg-danger';
        const icon = isBuy ? 'bi-arrow-up-short' : 'bi-arrow-down-short';
        const cardClass = isBuy ? 'buy-signal' : 'sell-signal';

        this.signalBadge.innerHTML = `
            <span class="badge ${badgeClass} signal-badge signal-active">
                <i class="bi ${icon}"></i> ${signal.action}
            </span>
        `;

        this.signalDetails.innerHTML = `
            <div class="mt-2">
                <div class="d-flex justify-content-between">
                    <span class="text-secondary">Confidence:</span>
                    <span class="fw-bold">${(signal.confidence * 100).toFixed(0)}%</span>
                </div>
                <div class="d-flex justify-content-between">
                    <span class="text-secondary">Stop Loss:</span>
                    <span class="fw-bold text-danger">$${signal.stop_loss ? signal.stop_loss.toFixed(2) : '--'}</span>
                </div>
                <div class="d-flex justify-content-between">
                    <span class="text-secondary">Take Profit:</span>
                    <span class="fw-bold text-success">$${signal.take_profit ? signal.take_profit.toFixed(2) : '--'}</span>
                </div>
                <hr class="my-1">
                <small class="text-secondary">${signal.reason || ''}</small>
            </div>
        `;

        this.signalCard.className = `card bg-dark border-secondary mb-3 ${cardClass}`;

        // Set stop loss / take profit on trading controls
        tradingControls.setSignalLevels(signal);

        // Draw lines on chart for SL/TP
        this.chart.clearDrawings();
        if (signal.stop_loss) {
            const color = isBuy ? '#dc3545' : '#22ab94';
            this.chart.addHorizontalLine(signal.stop_loss, color, 'SL');
        }
        if (signal.take_profit) {
            const color = isBuy ? '#22ab94' : '#dc3545';
            this.chart.addHorizontalLine(signal.take_profit, color, 'TP');
        }
    }

    /**
     * Client-side signal generation fallback.
     */
    _generateClientSignal(tdi, patterns, candles) {
        if (!tdi || !tdi.rsi || !patterns || patterns.length === 0) return null;

        const lastPrice = candles[candles.length - 1].close;
        const recentPatterns = patterns.filter(p => p.index >= candles.length - 5);

        const bullishPatterns = recentPatterns.filter(p => p.direction === 'bullish');
        const bearishPatterns = recentPatterns.filter(p => p.direction === 'bearish');

        // Buy signal: RSI crossing up + bullish patterns
        if (tdi.rsi > tdi.rsiSmoothed && tdi.rsi < 70 && bullishPatterns.length > 0) {
            const confidence = Math.min(0.5 + bullishPatterns.length * 0.15, 0.95);
            const stopLoss = lastPrice * 0.99;
            return {
                action: 'BUY',
                confidence: confidence,
                reason: `TDI bullish + ${bullishPatterns.length} pattern(s)`,
                stop_loss: stopLoss,
                take_profit: lastPrice + (lastPrice - stopLoss) * 1.5,
            };
        }

        // Sell signal: RSI crossing down + bearish patterns
        if (tdi.rsi < tdi.rsiSmoothed && tdi.rsi > 30 && bearishPatterns.length > 0) {
            const confidence = Math.min(0.5 + bearishPatterns.length * 0.15, 0.95);
            const stopLoss = lastPrice * 1.01;
            return {
                action: 'SELL',
                confidence: confidence,
                reason: `TDI bearish + ${bearishPatterns.length} pattern(s)`,
                stop_loss: stopLoss,
                take_profit: lastPrice - (stopLoss - lastPrice) * 1.5,
            };
        }

        return null;
    }
}

// Initialize the app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new TradingDashboardApp();
});
