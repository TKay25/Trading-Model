/**
 * Trading Dashboard - Main Application
 * Orchestrates chart, indicators, trading controls, and API interactions.
 */

class TradingDashboardApp {
    constructor() {
        // State
        this.symbol = 'R_75';
        this.timeframe = '5m';
        this.candles = [];
        this.autoRefreshInterval = null;
        this.eventSource = null;
        this._liveTimer = null;
        this._slTpLines = [];
        this._lastDirection = 'BUY';
        this._payoutRatio = 1.82;

        // Modules
        this.chart = new TradingChart('tradingChart');
        this.tdi = new TDIIndicator();
        this.patternRecognizer = new PatternRecognizer();
        this.risk = new RiskMetrics();
        this.signalEngine = new SignalEngine();

        // DOM refs
        this.symbolSelect = document.getElementById('symbolSelect');
        this.timeframeBtns = document.querySelectorAll('.tf-btn');
        this.refreshBtn = document.getElementById('refreshChart');
        this.chartLoading = document.getElementById('chartLoading');
        this.historyBody = document.getElementById('historyBody');
        this.refreshHistoryBtn = document.getElementById('refreshHistory');
        this.positionsBody = document.getElementById('positionsBody');
        this.refreshPositionsBtn = document.getElementById('refreshPositions');

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
        this.decisionSignalRow = document.getElementById('decisionSignalRow');
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
            this._switchLiveConfig();
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
                this._switchLiveConfig();
                this._loadChartData();
                this._runAnalysis();
            });
        });

        // Refresh
        this.refreshBtn.addEventListener('click', () => {
            this._loadChartData();
            this._runAnalysis();
        });

        // Trading history refresh
        if (this.refreshHistoryBtn) {
            this.refreshHistoryBtn.addEventListener('click', () => this._loadHistory());
        }

        // Open positions refresh
        if (this.refreshPositionsBtn) {
            this.refreshPositionsBtn.addEventListener('click', () => this._loadPositions());
        }

        // $ SL/TP -> market points translation (live while typing)
        const slIn = document.getElementById('stopLoss');
        const tpIn = document.getElementById('takeProfit');
        if (slIn) slIn.addEventListener('input', () => this._updateSlTpTranslation());
        if (tpIn) tpIn.addEventListener('input', () => this._updateSlTpTranslation());
    }

    /**
     * Load initial chart data on page load.
     */
    async _loadInitialData() {
        // Load real market data from Deriv first (no dummy data on startup)
        await this._loadChartData();
        this._runAnalysis();
        // Open the live stream for real-time candle updates.
        this._connectLiveStream();
    }

    /**
     * Open the live SSE stream and redraw the chart as candles arrive.
     */
    _connectLiveStream() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        const es = new EventSource('/api/stream');
        this.eventSource = es;

        es.onmessage = (ev) => {
            try {
                const data = JSON.parse(ev.data);
                // Only apply data that matches the currently selected symbol/timeframe,
                // so a stale or mid-switch stream never shows mismatched candles.
                const matches = data &&
                    data.symbol === this.symbol &&
                    data.granularity === this._getTimeframeSeconds(this.timeframe) &&
                    Array.isArray(data.candles) && data.candles.length > 0;
                if (matches) {
                    this.candles = data.candles;
                    this.chart.setCandleData(data.candles);
                    this._setChartStatus('live');
                    this._updateLivePrice();
                    this._throttledAnalysis();
                }
            } catch (err) {
                console.warn('Bad SSE payload:', err);
            }
        };
        es.onerror = () => {
            // EventSource reconnects automatically.
        };
    }

    /**
     * Tell the backend to switch the live subscription to the current config.
     */
    async _switchLiveConfig() {
        try {
            await fetch('/api/live/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: this.symbol, timeframe: this.timeframe }),
            });
        } catch (_) {}
    }

    /**
     * Throttle analysis re-runs so live tick updates don't hammer the server.
     */
    _throttledAnalysis() {
        const now = Date.now();
        if (this._liveTimer && now - this._liveTimer < 3000) return;
        this._liveTimer = now;
        this._runAnalysis();
    }

    /**
     * Load and render the trading history from the backend.
     */
    async _loadHistory() {
        if (!this.historyBody) return;
        try {
            const resp = await fetch('/api/history');
            const data = await resp.json();
            if (data.success && Array.isArray(data.history)) {
                this._renderHistory(data.history);
            } else {
                this.historyBody.innerHTML =
                    `<tr class="history-empty"><td colspan="6">${data.error || 'No history available'}</td></tr>`;
            }
        } catch (err) {
            this.historyBody.innerHTML =
                '<tr class="history-empty"><td colspan="6">Failed to load history</td></tr>';
        }
    }

    _renderHistory(rows) {
        if (!rows || !rows.length) {
            this.historyBody.innerHTML =
                '<tr class="history-empty"><td colspan="9"><i class="bi bi-journal-x"></i>No trades yet.</td></tr>';
            return;
        }
        this.historyBody.innerHTML = rows.map(r => {
            const time = r.time ? new Date(r.time * 1000).toLocaleString() : '--';
            const symbol = r.symbol || '--';
            const type = r.contract_type || '--';
            const lot = Number(r.lot_size ?? r.stake ?? 0);
            const isOpen = r.status === 'open';
            const status = ['won', 'lost', 'open'].includes(r.status) ? r.status : 'open';
            const label = { won: 'Won', lost: 'Lost', open: 'Open' }[status] || 'Open';
            const sl = (r.stop_loss ?? 0) > 0 ? `$${(+r.stop_loss).toFixed(2)}` : '—';
            const tp = (r.take_profit ?? 0) > 0 ? `$${(+r.take_profit).toFixed(2)}` : '—';
            let profitCell;
            if (isOpen) {
                profitCell = '<span class="history-profit open">—</span>';
            } else {
                const profit = (r.profit ?? 0).toFixed(2);
                const cls = r.profit >= 0 ? 'pos' : 'neg';
                const sign = r.profit >= 0 ? '+' : '';
                profitCell = `<span class="history-profit ${cls}">${sign}$${profit}</span>`;
            }
            return `<tr class="${isOpen ? 'history-open-row' : ''}">
                <td>${time}</td>
                <td>${symbol}</td>
                <td>${type}</td>
                <td>${r.contract_id ?? '--'}</td>
                <td>${lot.toFixed(3)}</td>
                <td>${sl}</td>
                <td>${tp}</td>
                <td>${profitCell}</td>
                <td><span class="history-status ${status}">${label}</span></td>
            </tr>`;
        }).join('');
        this._updateStatsFromHistory(rows);
        // Learn the actual payout ratio from the most recent settled trade.
        const settled = rows.filter(r => r.status !== 'open');
        if (settled.length && settled[0].stake > 0 && settled[0].payout > 0) {
            this._payoutRatio = settled[0].payout / settled[0].stake;
        }
    }

    _clearHistory() {
        if (this.historyBody) {
            this.historyBody.innerHTML =
                '<tr class="history-empty"><td colspan="9"><i class="bi bi-plug"></i>Connect to your account to view trading history.</td></tr>';
        }
    }

    /**
     * Load and render open positions (portfolio).
     */
    async _loadPositions() {
        if (!this.positionsBody) return;
        try {
            const resp = await fetch('/api/positions');
            const data = await resp.json();
            if (data.success && Array.isArray(data.positions)) {
                this._renderPositions(data.positions);
            } else {
                this.positionsBody.innerHTML =
                    `<div class="positions-empty">${data.error || 'No positions available'}</div>`;
            }
        } catch (err) {
            this.positionsBody.innerHTML = '<div class="positions-empty">Failed to load positions</div>';
        }
    }

    _renderPositions(positions) {
        if (!positions || !positions.length) {
            this.positionsBody.innerHTML = '<div class="positions-empty"><i class="bi bi-briefcase"></i>No open positions.</div>';
            return;
        }
        this.positionsBody.innerHTML = positions.map(p => {
            const type = p.contract_type || '--';
            const dirClass = /DOWN|PUT|SELL/i.test(type) ? 'down' : 'up';
            const stake = (p.stake ?? 0).toFixed(2);
            const expiry = p.expiry_time ? new Date(p.expiry_time * 1000).toLocaleString() : '--';
            const lot = Number(p.lot_size ?? p.stake ?? 0);
            const sl = (p.stop_loss ?? 0) > 0 ? `$${(+p.stop_loss).toFixed(2)}` : '—';
            const tp = (p.take_profit ?? 0) > 0 ? `$${(+p.take_profit).toFixed(2)}` : '—';
            return `
            <div class="position-item" data-contract-id="${p.contract_id}">
                <div class="position-top">
                    <span class="position-symbol">${p.symbol || '--'}</span>
                    <span class="position-type ${dirClass}">${type}</span>
                    <span class="position-stake">$${stake}</span>
                </div>
                <div class="position-meta">
                    <span>Lot: ${lot.toFixed(3)}</span>
                    <span>SL: ${sl}</span>
                    <span>TP: ${tp}</span>
                    <span>Expiry: ${expiry}</span>
                </div>
                <div class="position-controls">
                    <button class="pos-close">Close</button>
                </div>
            </div>`;
        }).join('');

        const openEl = document.getElementById('statOpen');
        if (openEl) openEl.textContent = positions.length;

        this.positionsBody.querySelectorAll('.position-item').forEach(item => {
            const cid = item.dataset.contractId;
            const closeBtn = item.querySelector('.pos-close');
            closeBtn.addEventListener('click', () => this._closePosition(cid, closeBtn));
        });
    }

    async _closePosition(contractId, btn) {
        if (btn) { btn.disabled = true; btn.textContent = 'Closing...'; }
        try {
            const resp = await fetch('/api/sell', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ contract_id: contractId }),
            });
            const data = await resp.json();
            if (data.success) {
                this._notify('Position Closed', `Contract ${contractId} closed`);
                this._loadPositions();
            } else {
                this._notify('Close Failed', data.error || 'Could not close contract');
                if (btn) btn.disabled = false;
            }
        } catch (err) {
            this._notify('Close Error', String(err));
            if (btn) btn.disabled = false;
        }
    }

    _clearPositions() {
        if (this.positionsBody) {
            this.positionsBody.innerHTML =
                '<div class="positions-empty">Connect to view open positions.</div>';
        }
    }

    _notify(title, message) {
        try {
            const toastEl = document.getElementById('notificationToast');
            document.getElementById('toastTitle').textContent = title;
            document.getElementById('toastMessage').textContent = message;
            const toast = new bootstrap.Toast(toastEl, { autohide: true, delay: 4000 });
            toast.show();
        } catch (e) { /* ignore */ }
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
                this._updateLivePrice();
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
     * Update the live price readout in the chart toolbar from current candles.
     */
    /**
     * Display VaR / ES in money terms: balance x risk% (per candle).
     */
    _updateRiskDisplay(risk) {
        const balEl = document.getElementById('balanceValue');
        const balance = balEl ? parseFloat(balEl.textContent) : NaN;
        const money = (pct) => {
            if (pct === null || pct === undefined || isNaN(pct) || !isFinite(balance) || balance <= 0) return null;
            return balance * (pct / 100);
        };
        const set = (id, pct) => {
            const el = document.getElementById(id);
            const val = money(pct);
            if (el) el.textContent = val === null ? '--' : '$' + val.toFixed(2);
        };
        set('statVar95', risk.var95);
        set('statVar99', risk.var99);
        set('statEs95', risk.es95);
        set('statEs99', risk.es99);
    }

    /**
     * Recompute the dollar VaR/ES tiles after the balance changes.
     */
    _refreshRiskMoney() {
        if (this.lastRisk) this._updateRiskDisplay(this.lastRisk);
    }

    _updateLivePrice() {
        const priceEl = document.getElementById('livePrice');
        const chgEl = document.getElementById('liveChange');
        if (!priceEl || !chgEl) return;
        if (!this.candles || this.candles.length === 0) {
            priceEl.textContent = '--';
            chgEl.textContent = '';
            chgEl.className = 'live-change';
            return;
        }
        const last = parseFloat(this.candles[this.candles.length - 1].close);
        const first = parseFloat(this.candles[0].close);
        const change = first !== 0 ? ((last - first) / first) * 100 : 0;
        priceEl.textContent = last.toFixed(2);
        chgEl.textContent = `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        chgEl.className = 'live-change ' + (change >= 0 ? 'up' : 'down');
    }

    /**
     * Refresh the Win Rate + Session P/L KPI tiles from the history rows.
     */
    _updateStatsFromHistory(rows) {
        const settled = (rows || []).filter(r => r.status !== 'open');
        const won = settled.filter(r => r.status === 'won').length;
        const totalPL = (rows || []).reduce((sum, r) => sum + (r.status !== 'open' ? (r.profit ?? 0) : 0), 0);

        const wrEl = document.getElementById('statWinRate');
        if (wrEl) wrEl.textContent = settled.length ? Math.round((won / settled.length) * 100) + '%' : '--';

        const plEl = document.getElementById('statPL');
        if (plEl) {
            plEl.textContent = `${totalPL >= 0 ? '+' : ''}$${totalPL.toFixed(2)}`;
            plEl.className = 'stat-value ' + (totalPL >= 0 ? 'pos' : 'neg');
        }
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
            // Draw the TDI indicators on the chart (Market Base + RSI pane)
            this.chart.setTDI(tdiValues);
        }

        // Pattern detection
        const patterns = this.patternRecognizer.detect(candles);
        const bullishCount = patterns.filter(p => p.direction === 'bullish').length;
        const bearishCount = patterns.filter(p => p.direction === 'bearish').length;
        this.tdiPatterns.textContent = bullishCount + bearishCount;

        // Risk metrics: Value at Risk / Expected Shortfall (95% / 99%)
        const risk = this.risk.calculate(candles);
        this.lastRisk = risk;
        this._updateRiskDisplay(risk);

        // Combined signal: TDI indicator + chart patterns (M/W + formations).
        const stake = parseFloat((document.getElementById('tradeLotSize') || {}).value) || 1;
        const signal = this.signalEngine.generate(tdiValues, patterns, candles, {
            stake: stake,
            payoutRatio: this._payoutRatio,
        });
        this._displaySignal(signal, { tdi: tdiValues, patterns });

        // Highlight recent single-candle formations (M/W are drawn as shapes).
        const recentMarkers = patterns.filter(p =>
            p.index >= this.candles.length - 6 &&
            p.type !== 'double_top' && p.type !== 'double_bottom');
        recentMarkers.forEach(p => this.chart.highlightPattern(p.timestamp, p.type, p.direction));

        // Draw M (double-top) / W (double-bottom) shapes on the price chart.
        this.chart.drawMWPatterns(patterns);

        // Refresh the $ SL/TP -> market points translation.
        this._updateSlTpTranslation();
    }

    /**
     * Translate the dollar SL/TP targets into approximate market points,
     * using the measured per-candle volatility (VaR 99%) as the conversion.
     */
    _updateSlTpTranslation() {
        const txtEl = document.getElementById('slTpTranslate');
        if (!txtEl) return;
        const sl = parseFloat((document.getElementById('stopLoss') || {}).value) || 0;
        const tp = parseFloat((document.getElementById('takeProfit') || {}).value) || 0;

        if (!this.candles || this.candles.length === 0 || (!sl && !tp)) {
            txtEl.textContent = 'Set a $ SL/TP to see the equivalent market points.';
            return;
        }

        const price = parseFloat(this.candles[this.candles.length - 1].close);
        const risk = this.lastRisk || { var99: 0.5 };
        // Expected worst move per candle in points (from VaR 99%).
        const expectedPts = Math.max(1, price * ((risk.var99 || 0.5) / 100));
        // Contract $ change per point: (payout - stake) spread over the expected move.
        const perPoint = Math.max(1e-6, (this._payoutRatio - 1) / expectedPts);
        const slPts = sl ? Math.round(sl / perPoint) : 0;
        const tpPts = tp ? Math.round(tp / perPoint) : 0;

        const parts = [];
        if (slPts) parts.push(`SL ≈ ${slPts} pts`);
        if (tpPts) parts.push(`TP ≈ ${tpPts} pts`);
        txtEl.textContent = parts.length ? parts.join('  ·  ') + '  (approx)' : 'Set a $ SL/TP to see the equivalent market points.';
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
            this.signalCard.className = 'side-card';
            return;
        }

        this.noSignal.classList.add('d-none');
        this.signalContent.classList.remove('d-none');

        const action = signal.action || 'NEUTRAL';
        const isBuy = action === 'BUY';
        const isSell = action === 'SELL';
        const badgeClass = isBuy ? 'bg-success' : (isSell ? 'bg-danger' : 'bg-secondary');
        const icon = isBuy ? 'bi-arrow-up-short' : (isSell ? 'bi-arrow-down-short' : 'bi-pause-fill');
        const cardClass = isBuy ? 'buy-active' : (isSell ? 'sell-active' : '');

        this.decisionBadge.innerHTML = `
            <span class="badge ${badgeClass} signal-badge signal-active">
                <i class="bi ${icon}"></i> ${action === 'NEUTRAL' ? 'HOLD' : action}
            </span>
        `;

        // TDI verdict vs Pattern verdict breakdown.
        const sub = (label, val) => {
            const v = val || '—';
            const cls = v === 'BUY' ? 'buy' : (v === 'SELL' ? 'sell' : 'neutral');
            return `<span class="signal-sub-label">${label}</span><span class="signal-sub-badge ${cls}">${v}</span>`;
        };
        if (this.decisionSignalRow) {
            this.decisionSignalRow.innerHTML = sub('TDI', signal.tdiAction) + sub('Pattern', signal.patternAction);
        }

        this.decisionSummary.innerHTML = `
            <div class="d-flex justify-content-between">
                <span class="text-secondary">Confidence:</span>
                <span class="fw-bold">${Math.round((signal.confidence || 0) * 100)}%</span>
            </div>
            <div class="d-flex justify-content-between">
                <span class="text-secondary">Stop Loss:</span>
                <span class="fw-bold text-danger">${signal.stop_loss ? '$' + signal.stop_loss.toFixed(2) : '--'}</span>
            </div>
            <div class="d-flex justify-content-between">
                <span class="text-secondary">Take Profit:</span>
                <span class="fw-bold text-success">${signal.take_profit ? '$' + signal.take_profit.toFixed(2) : '--'}</span>
            </div>
        `;

        this.decisionReason.innerHTML = signal.reason || '';

        this.signalCard.className = `side-card ${cardClass}`.trim();

        // Signal SL/TP are $ profit targets (not price levels), so they are shown
        // in the card only — no price lines are drawn for them on the chart.
        this.chart.clearDrawings();
    }
}

// Initialize the app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new TradingDashboardApp();
});
