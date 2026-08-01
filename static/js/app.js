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
        this.chartLoading = document.getElementById('chartLoading');

        // TDI display elements
        this.tdiRsi = document.getElementById('tdiRsi');
        this.tdiRsiSmoothed = document.getElementById('tdiRsiSmoothed');
        this.tdiMarketBase = document.getElementById('tdiMarketBase');
        this.tdiProgress = document.getElementById('tdiProgress');
        this.tdiPatterns = document.getElementById('tdiPatterns');
        this.tdiZone = document.getElementById('tdiZone');

        // Decision display elements
        this.signalBody = document.getElementById('signalBody');
        this.noSignal = document.getElementById('noSignal');
        this.signalContent = document.getElementById('signalContent');
        this.decisionBadge = document.getElementById('decisionBadge');
        this.decisionSummary = document.getElementById('decisionSummary');
        this.decisionReason = document.getElementById('decisionReason');
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
    }

    /**
     * Load initial chart data on page load.
     */
    async _loadInitialData() {
        // Load real market data from Deriv first (no dummy data on startup)
        await this._loadChartData();
        this._runAnalysis();
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
                this._setChartStatus('live');
                this._runAnalysis();
            } else {
                // Server is reachable but Deriv returned no data
                console.warn('No candle data from API:', data.error || 'empty response');
                this._setChartStatus('error', data.error || 'No data returned by Deriv');
                this.candles = [];
                this.chart.clearChart();
            }
        } catch (err) {
            // Only if the server itself is unreachable do we fall back to demo data
            console.warn('Failed to fetch candle data, using demo data:', err);
            this._loadDemoData(true);
        } finally {
            this.chartLoading.classList.add('d-none');
        }
    }

    /**
     * Show a status banner above the chart.
     * mode: 'live' (hide banner) | 'demo' | 'error'
     */
    _setChartStatus(mode, message = '') {
        const banner = document.getElementById('chartStatus');
        if (!banner) return;

        if (mode === 'live') {
            banner.classList.add('d-none');
            return;
        }

        banner.classList.remove('d-none');
        if (mode === 'demo') {
            banner.className = 'chart-status demo';
            banner.innerHTML = '<i class="bi bi-exclamation-triangle-fill"></i> Showing demo data — live Deriv feed unavailable';
        } else {
            banner.className = 'chart-status error';
            banner.innerHTML = `<i class="bi bi-x-circle-fill"></i> ${message || 'Failed to load market data'}`;
        }
    }

    /**
     * Generate demo candle data for offline mode.
     */
    _loadDemoData(forceDemo = false) {
        const candles = [];
        const now = Math.floor(Date.now() / 1000);
        const interval = this._getTimeframeSeconds(this.timeframe);
        let price = 100 + Math.random() * 50;

        for (let i = 200; i > 0; i--) {
            const volatility = 0.3 + Math.random() * 0.4;
            const change = (Math.random() - 0.48) * volatility;
            const open = price;
            const close = price + change;
            const wick = Math.random() * volatility * 0.3;
            const high = Math.max(open, close) + wick;
            const low = Math.min(open, close) - wick;
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
        if (forceDemo) {
            this._setChartStatus('demo');
        }
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
        const bullishCount = patterns.filter(p => p.direction === 'bullish').length;
        const bearishCount = patterns.filter(p => p.direction === 'bearish').length;
        this.tdiPatterns.textContent = bullishCount + bearishCount;

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
                // Highlight only patterns that contributed
                const signal = data.analysis.signal;
                const recentPatterns = (data.analysis.patterns || [])
                    .filter(p => p.index >= this.candles.length - 5);
                recentPatterns.forEach(p => {
                    this.chart.highlightPattern(p.timestamp, p.type, p.direction);
                });
            } else {
                const signal = this._generateClientSignal(tdiValues, patterns, candles);
                this._displaySignal(signal, { tdi: tdiValues, patterns });
            }
        } catch (err) {
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

        if (tdi.rsi !== null) {
            this.tdiProgress.style.width = `${tdi.rsi}%`;
            if (tdi.rsi > 70) {
                this.tdiProgress.className = 'tdi-fill bearish';
                this.tdiRsi.className = 'tdi-value';
                this.tdiRsi.style.color = 'var(--red)';
                this.tdiZone.textContent = 'Overbought';
                this.tdiZone.style.color = 'var(--red)';
            } else if (tdi.rsi < 30) {
                this.tdiProgress.className = 'tdi-fill bullish';
                this.tdiRsi.className = 'tdi-value';
                this.tdiRsi.style.color = 'var(--green)';
                this.tdiZone.textContent = 'Oversold';
                this.tdiZone.style.color = 'var(--green)';
            } else {
                this.tdiProgress.className = 'tdi-fill neutral';
                this.tdiRsi.className = 'tdi-value';
                this.tdiRsi.style.color = '';
                this.tdiZone.textContent = `${tdi.rsi.toFixed(0)}`;
                this.tdiZone.style.color = '';
            }
        }
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
