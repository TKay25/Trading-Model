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

        // Multi-timeframe / multi-market scanner state
        this._scannerTimeframes = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];
        this._scannerTimer = null;
        this._scannerBusy = false;
        this._scannerNotified = {};   // `${symbol}:${tf}:${action}` -> true (avoid repeat alerts)

        // Analytics / trading helpers
        this._historyRows = [];
        this._autoTradeNotified = {};    // `auto:${symbol}:${tf}:${action}` -> last trade ts
        this._autoTradeTimeframes = ['5m', '15m', '30m'];   // ONLY auto-trade these timeframes
        this._scannerSignals = [];
        this._tradeRisk = null;          // VaR/ES from ACTUAL trade results
        this._audioCtx = null;

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

        // Decision display elements
        this.signalBody = document.getElementById('signalBody');
        this.noSignal = document.getElementById('noSignal');
        this.signalContent = document.getElementById('signalContent');
        this.decisionBadge = document.getElementById('decisionBadge');
        this.decisionSignalRow = document.getElementById('decisionSignalRow');
        this.decisionSummary = document.getElementById('decisionSummary');
        this.decisionReason = document.getElementById('decisionReason');
        this.signalCard = document.getElementById('signalCard');

        // TF Scanner DOM refs
        this.tfScannerList = document.getElementById('tfScannerList');
        this.scannerStatus = document.getElementById('scannerStatus');
        this.scannerUpdated = document.getElementById('scannerUpdated');
        this.scannerSymbol = document.getElementById('scannerSymbol');
        this.scannerMarkets = document.getElementById('scannerMarkets');
        this.scannerMarketsCount = document.getElementById('scannerMarketsCount');

        // Quick Trade helpers
        this.riskPct = document.getElementById('riskPct');
        this.riskHint = document.getElementById('riskHint');
        this.autoTradeToggle = document.getElementById('autoTradeToggle');
        this.autoTradeStrength = document.getElementById('autoTradeStrength');
        this.autoStrengthVal = document.getElementById('autoStrengthVal');
        this.autoTradePaper = document.getElementById('autoTradePaper');
        this.autoModeBadge = document.getElementById('autoModeBadge');

        // Performance / analytics
        this.equityCanvas = document.getElementById('equityCanvas');
        this.runBacktest = document.getElementById('runBacktest');
        this.backtestBox = document.getElementById('backtestBox');
        this.exportCsv = document.getElementById('exportCsv');
        this.resetHistory = document.getElementById('resetHistory');

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
        const closeAllBtn = document.getElementById('closeAllPositions');
        if (closeAllBtn) closeAllBtn.addEventListener('click', () => this._closeAllPositions());

        // $ SL/TP -> market points translation (live while typing)
        const slIn = document.getElementById('stopLoss');
        const tpIn = document.getElementById('takeProfit');
        if (slIn) slIn.addEventListener('input', () => this._updateSlTpTranslation());
        if (tpIn) tpIn.addEventListener('input', () => this._updateSlTpTranslation());

        // Scanner symbol scope (market view vs single-symbol TF breakdown).
        if (this.scannerSymbol) {
            this.scannerSymbol.addEventListener('change', () => this._runScanner());
        }
        if (this.scannerMarkets) {
            this.scannerMarkets.addEventListener('click', () => {
                if (this.scannerSymbol) this.scannerSymbol.value = '__ALL__';
                this._runScanner();
            });
        }

        // Risk-based lot sizing.
        if (this.riskPct) this.riskPct.addEventListener('input', () => this._suggestLot());

        // Auto-trade min-strength label.
        if (this.autoTradeStrength) {
            this.autoTradeStrength.addEventListener('input', () => {
                if (this.autoStrengthVal) this.autoStrengthVal.textContent = this.autoTradeStrength.value;
            });
        }

        // Auto-trade LIVE vs PAPER indicator.
        if (this.autoTradePaper) this.autoTradePaper.addEventListener('change', () => this._updateAutoMode());
        this._updateAutoMode();

        // Backtest + CSV export + reset.
        if (this.runBacktest) this.runBacktest.addEventListener('click', () => this._runBacktest());
        if (this.exportCsv) this.exportCsv.addEventListener('click', () => this._exportCsv());
        if (this.resetHistory) this.resetHistory.addEventListener('click', () => this._resetHistory());
    }

    _updateAutoMode() {
        const paper = !!(this.autoTradePaper && this.autoTradePaper.checked);
        if (this.autoModeBadge) {
            this.autoModeBadge.textContent = paper ? 'PAPER' : 'LIVE';
            this.autoModeBadge.className = 'auto-mode-badge ' + (paper ? 'paper' : 'live');
        }
        const box = document.querySelector('.auto-trade-box');
        if (box) box.classList.toggle('live', !paper);
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
        // Scan every timeframe for signals that appear off-screen.
        this._startScanner();
    }

    // ------------------------------------------------------------------
    // Multi-timeframe signal scanner
    // ------------------------------------------------------------------

    /**
     * Start periodic scanning of ALL timeframes so the user is informed of a
     * strong (all-aligned) signal even when it fires on a timeframe that is
     * not the one currently open on screen.
     */
    _startScanner() {
        if (this._scannerTimer) clearInterval(this._scannerTimer);
        this._runScanner();
        this._scannerTimer = setInterval(() => this._runScanner(), 40000);
    }

    _stopScanner() {
        if (this._scannerTimer) {
            clearInterval(this._scannerTimer);
            this._scannerTimer = null;
        }
    }

    _scannerSymbols() {
        if (!this.scannerSymbol) return [this.symbol];
        const opts = Array.from(this.scannerSymbol.options).map(o => o.value).filter(v => v !== '__ALL__');
        return opts.length ? opts : [this.symbol];
    }

    async _runScanner() {
        if (this._scannerBusy || !this.tfScannerList) return;
        this._scannerBusy = true;
        if (this.scannerStatus) this.scannerStatus.innerHTML = '<span class="status-dot pulse"></span> Scanning…';
        try {
            const resp = await fetch('/api/scanner', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: this.symbol,
                    symbols: this._scannerSymbols(),
                    timeframes: this._scannerTimeframes,
                    count: 100,
                }),
            });
            const data = await resp.json();
            if (data.success && Array.isArray(data.results)) {
                this._renderScanner(data.results);
                if (this.scannerStatus) this.scannerStatus.innerHTML = '<span class="status-dot ok"></span> Live';
                if (this.scannerUpdated) {
                    this.scannerUpdated.textContent = 'updated ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                }
            }
        } catch (err) {
            console.warn('Scanner fetch failed:', err);
            if (this.scannerStatus) this.scannerStatus.innerHTML = '<span class="status-dot err"></span> Offline';
        } finally {
            this._scannerBusy = false;
        }
    }

    _renderScanner(results) {
        const activeSym = this.symbol;
        const activeTf = this.timeframe;
        const scope = this.scannerSymbol ? this.scannerSymbol.value : '__ALL__';

        // Analyse every symbol+timeframe with the SAME signal engine as the chart.
        const analyzed = [];
        const currentActions = {};   // `${symbol}:${tf}` -> action (stale-alert cleanup)
        results.forEach(r => {
            if (!r.candles || !r.candles.length) return;
            const candles = r.candles.map(c => ({ open: parseFloat(c.open), high: parseFloat(c.high), low: parseFloat(c.low), close: parseFloat(c.close), epoch: c.epoch }));
            const tdi = this.tdi.calculate(candles);
            if (!tdi) return;
            const patterns = this.patternRecognizer.detect(candles);
            const stake = parseFloat((document.getElementById('tradeLotSize') || {}).value) || 1;
            const signal = this.signalEngine.generate(tdi, patterns, candles, { stake, payoutRatio: this._payoutRatio });
            const key = `${r.symbol}:${r.timeframe}`;
            // Alerts/stale-cleanup now track the REVERSAL verdict (tradeable signal).
            currentActions[key] = signal.reversal || 'NEUTRAL';
            analyzed.push({ symbol: r.symbol, timeframe: r.timeframe, candles, tdi, signal });
        });

        // Prune alert keys whose signal has since disappeared.
        for (const key of Object.keys(this._scannerNotified)) {
            const parts = key.split(':');
            const sym = parts[0], tf = parts[1], act = parts[2];
            if (currentActions[`${sym}:${tf}`] !== act) delete this._scannerNotified[key];
        }

        // Alert ONLY on confirmed REVERSALS not on the active (symbol,timeframe) —
        // matches the reversal-only auto-trade (no more pings for untradeable
        // all-aligned signals).
        let marketCount = 0;
        analyzed.forEach(a => {
            const rev = a.signal.reversal;
            if ((rev === 'BUY' || rev === 'SELL') && !(a.symbol === activeSym && a.timeframe === activeTf)) {
                marketCount++;
                this._checkScannerAlert(a.symbol, a.timeframe, a.signal);
            }
        });

        // Markets badge in the scanner header.
        if (this.scannerMarketsCount) this.scannerMarketsCount.textContent = marketCount;
        if (this.scannerMarkets) this.scannerMarkets.classList.toggle('d-none', marketCount === 0);

        // Render the list based on the scope dropdown.
        let html = '';
        if (scope === '__ALL__') {
            const strong = analyzed.filter(a => a.signal.action === 'BUY' || a.signal.action === 'SELL');
            if (!strong.length) {
                html = '<div class="tf-scan-empty"><i class="bi bi-check-circle"></i>No strong signals across markets.</div>';
            } else {
                html = strong.map(a => {
                    const act = a.signal.action;
                    const isBuy = act === 'BUY';
                    const isActive = a.symbol === activeSym && a.timeframe === activeTf;
                    const pct = Math.round((a.signal.strength || 0) * 100);
                    const cls = isBuy ? 'buy' : 'sell';
                    return `<div class="tf-scan-row ${cls}${isActive ? ' active' : ''}" data-symbol="${a.symbol}" data-tf="${a.timeframe}">
                        <span class="tf-scan-name">${a.symbol}</span>
                        <span class="tf-scan-tf">${a.timeframe}</span>
                        <span class="tf-scan-verdict ${cls}">${act}</span>
                        <span class="tf-scan-strength ${cls}">${pct}%</span>
                    </div>`;
                }).join('');
            }
        } else {
            const symData = analyzed.filter(a => a.symbol === scope);
            if (!symData.length) {
                html = '<div class="tf-scan-empty"><i class="bi bi-radar"></i>No data.</div>';
            } else {
                html = symData.map(a => {
                    const act = a.signal.action || 'NEUTRAL';
                    const isActive = scope === activeSym && a.timeframe === activeTf;
                    const isBuy = act === 'BUY', isSell = act === 'SELL';
                    const verdictCls = isBuy ? 'buy' : (isSell ? 'sell' : 'neutral');
                    const label = isBuy ? 'BUY' : (isSell ? 'SELL' : 'HOLD');
                    const pct = Math.round((a.signal.strength || 0) * 100);
                    const sub = (n, v) => {
                        const cls = v === 'BUY' ? 'buy' : (v === 'SELL' ? 'sell' : 'neutral');
                        const arrow = v === 'BUY' ? '▲' : (v === 'SELL' ? '▼' : '·');
                        return `<span class="tf-scan-sub ${cls}">${n}${arrow}</span>`;
                    };
                    const subs = sub('T', a.signal.tdiAction) + sub('W', a.signal.patternAction) + sub('C', a.signal.candleAction);
                    const rowCls = (isBuy || isSell) ? ` ${verdictCls}` : '';
                    return `<div class="tf-scan-row${rowCls}${isActive ? ' active' : ''}" data-symbol="${a.symbol}" data-tf="${a.timeframe}">
                        <span class="tf-scan-name">${a.timeframe}${isActive ? '<i class="bi bi-broadcast tf-scan-active" title="on screen"></i>' : ''}</span>
                        <span class="tf-scan-verdict ${verdictCls}">${label}</span>
                        <span class="tf-scan-subs">${subs}</span>
                        <span class="tf-scan-strength ${pct > 0 ? verdictCls : ''}">${isBuy || isSell ? pct + '%' : '—'}</span>
                    </div>`;
                }).join('');
            }
        }
        this.tfScannerList.innerHTML = html;

        const card = document.getElementById('scannerCard');
        if (card) card.classList.toggle('has-signal', marketCount > 0);

        // Auto-trade any strong all-aligned signal across ALL scanned markets.
        this._scannerSignals = analyzed;
        this._maybeAutoTradeScanner();
    }

    /**
     * Alert ONCE per new strong signal on a market that isn't on screen:
     * toast + sound + desktop notification. Re-arms when the signal fades.
     */
    _checkScannerAlert(symbol, tf, signal) {
        // Only reversal verdicts are alerted now (they're what auto-trade fires on).
        const action = (signal.reversal === 'BUY' || signal.reversal === 'SELL')
            ? signal.reversal
            : signal.action;
        const key = `${symbol}:${tf}:${action}`;
        if (this._scannerNotified[key]) return;
        this._scannerNotified[key] = true;

        const pct = Math.round((signal.strength || 0) * 100);
        this._notify(
            `${symbol} ${tf} ${action} REVERSAL`,
            `${symbol} · ${tf} — ${action} reversal (TDI + Bollinger/RSI + M/W/H&S/candles). Strength ${pct}%.`
        );
        this._playAlertSound(action === 'BUY');
        this._desktopNotify(`${symbol} ${tf} ${action} reversal`, `${symbol} ${tf} — ${action} reversal confirmed. Strength ${pct}%.`);

        const row = this.tfScannerList.querySelector(`.tf-scan-row[data-symbol="${symbol}"][data-tf="${tf}"]`);
        if (row) {
            row.classList.remove('flash');
            void row.offsetWidth;
            row.classList.add('flash');
        }
    }

    /**
     * Short two-tone beep (Web Audio — no asset needed). Up tone for BUY,
     * lower tone for SELL.
     */
    _playAlertSound(up = true) {
        try {
            const ctx = this._audioCtx || (this._audioCtx = new (window.AudioContext || window.webkitAudioContext)());
            if (ctx.state === 'suspended') ctx.resume();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(up ? 880 : 520, ctx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(up ? 1320 : 392, ctx.currentTime + 0.35);
            gain.gain.setValueAtTime(0.001, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.45);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(ctx.currentTime);
            osc.stop(ctx.currentTime + 0.5);
        } catch (_) { /* ignore */ }
    }

    /**
     * Desktop (browser) notification — fires even when the tab isn't focused.
     */
    _desktopNotify(title, body) {
        try {
            if (!('Notification' in window)) return;
            if (Notification.permission === 'granted') {
                new Notification(title, { body: body });
            } else if (Notification.permission !== 'denied') {
                Notification.requestPermission().then(p => {
                    if (p === 'granted') new Notification(title, { body: body });
                });
            }
        } catch (_) { /* ignore */ }
    }

    /**
     * Auto-trade rule: TDI aligned AND at least ONE other signal (M/W pattern or
     * candlestick) aligned the same way. (The displayed Signal card keeps the
     * stricter all-aligned rule; this is just the trade trigger.)
     * @returns 'BUY' | 'SELL' | null
     */
    _twoOfThreeAction(signal) {
        if (!signal) return null;
        const tdi = signal.tdiAction, pat = signal.patternAction, can = signal.candleAction;
        if (tdi === 'BUY' && (pat === 'BUY' || can === 'BUY')) return 'BUY';
        if (tdi === 'SELL' && (pat === 'SELL' || can === 'SELL')) return 'SELL';
        return null;
    }

    /**
     * Auto-trade the OPEN chart signal — ONLY on a confirmed REVERSAL
     * (TDI/Bollinger/RSI bounce agreed with an M/W, H&S or candlestick
     * reversal pattern).
     */
    _maybeAutoTrade(signal, tdi, candles) {
        const action = this._reversalTradeAction(signal);
        if (!action) return;
        const strength = Math.round((this.signalEngine._strength(action, tdi, candles) || 0) * 100);
        // Pass the signal's suggested $ SL/TP so the auto-trade is protected.
        this._autoTradeSignal(this.symbol, this.timeframe, action, strength, {
            stopLoss: signal.stop_loss,
            takeProfit: signal.take_profit,
        });
    }

    /**
     * Auto-trade ANY confirmed REVERSAL found by the TF scanner across all
     * symbols/timeframes (still limited to the allowed auto-trade timeframes).
     */
    _maybeAutoTradeScanner() {
        if (!this.autoTradeToggle || !this.autoTradeToggle.checked) return;
        (this._scannerSignals || []).forEach(a => {
            const action = this._reversalTradeAction(a.signal);
            if (!action) return;
            const strength = Math.round((this.signalEngine._strength(action, a.tdi, a.candles) || 0) * 100);
            // Apply the signal's suggested $ SL/TP for this symbol/timeframe.
            this._autoTradeSignal(a.symbol, a.timeframe, action, strength, {
                stopLoss: a.signal.stop_loss,
                takeProfit: a.signal.take_profit,
            });
        });
    }

    /**
     * Reversal-only trade trigger: returns BUY/SELL only when the combined
     * reversal verdict is live, else null.
     */
    _reversalTradeAction(signal) {
        if (!signal) return null;
        const rev = signal.reversal;
        return (rev === 'BUY' || rev === 'SELL') ? rev : null;
    }

    /**
     * Fire one auto-trade for (symbol, timeframe, action) respecting the
     * allowed-timeframe filter and a per-signal cooldown.
     */
    _autoTradeSignal(symbol, timeframe, action, strength, opts = {}) {
        if (!this.autoTradeToggle || !this.autoTradeToggle.checked) return;
        if (!this._autoTradeTimeframes.includes(timeframe)) return;  // only 5m/15m/30m
        if (action !== 'BUY' && action !== 'SELL') return;
        const minStr = parseFloat(this.autoTradeStrength.value) || 0;
        if (strength < minStr) return;
        const now = Date.now();
        const key = `auto:${symbol}:${timeframe}:${action}`;
        if ((this._autoTradeNotified[key] || 0) > now - 90000) return; // 90s per-signal cooldown
        this._autoTradeNotified[key] = now;
        if (typeof tradingControls.autoTrade === 'function') {
            tradingControls.autoTrade(action, strength, this.autoTradePaper && this.autoTradePaper.checked, symbol, timeframe, opts);
        }
    }

    /**
     * Suggest the lot size from a % risk of the current balance.
     */
    _suggestLot() {
        if (!this.riskPct || !this.riskHint) return;
        const pct = parseFloat(this.riskPct.value);
        const balance = parseFloat((document.getElementById('balanceValue') || {}).textContent) || 0;
        const lotEl = document.getElementById('tradeLotSize');
        if (!pct || pct <= 0 || balance <= 0) {
            this.riskHint.textContent = 'Set a Risk % to auto-suggest the lot size from your balance.';
            return;
        }
        const riskAmt = balance * (pct / 100);
        if (lotEl) lotEl.value = riskAmt.toFixed(3);
        this.riskHint.textContent = `Risking $${riskAmt.toFixed(2)} (${pct}% of $${balance.toFixed(2)}) = lot ${riskAmt.toFixed(3)}.`;
    }

    // ------------------------------------------------------------------
    // Performance analytics
    // ------------------------------------------------------------------

    _updatePerformance(rows) {
        const settled = (rows || []).filter(r => r.status !== 'open' && typeof r.profit === 'number');
        // VaR/ES tiles = risk from ACTUAL trade results (per trade), not price.
        this._tradeRisk = this.risk.calculateFromTrades(settled.map(r => r.profit));
        this._updateRiskDisplay(this._tradeRisk);
        const set = (id, val, cls) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.textContent = val;
            el.className = 'perf-value' + (cls ? ' ' + cls : '');
        };
        if (!settled.length) {
            ['perfTrades','perfWinRate','perfProfitFactor','perfAvgWin','perfAvgLoss','perfBest','perfWorst','perfMaxDd','perfCall','perfPut']
                .forEach(id => set(id, '--'));
            this._renderEquityCurve([]);
            return;
        }
        const n = settled.length;
        const wins = settled.filter(r => r.profit > 0);
        const losses = settled.filter(r => r.profit <= 0);
        const grossWin = wins.reduce((s, r) => s + r.profit, 0);
        const grossLoss = Math.abs(losses.reduce((s, r) => s + r.profit, 0));
        const best = Math.max(...settled.map(r => r.profit));
        const worst = Math.min(...settled.map(r => r.profit));
        const ordered = [...settled].sort((a, b) => (a.time || 0) - (b.time || 0));

        let cum = 0, peak = 0, maxDd = 0;
        ordered.forEach(r => { cum += r.profit; if (cum > peak) peak = cum; const dd = peak - cum; if (dd > maxDd) maxDd = dd; });

        const rate = (arr) => arr.length ? Math.round(arr.filter(r => r.profit > 0).length / arr.length * 100) + '%' : '--';
        const call = settled.filter(r => /CALL|MULTUP/i.test(r.contract_type || ''));
        const put = settled.filter(r => /PUT|MULTDOWN/i.test(r.contract_type || ''));

        set('perfTrades', n);
        set('perfWinRate', Math.round(wins.length / n * 100) + '%');
        set('perfProfitFactor', grossLoss > 0 ? (grossWin / grossLoss).toFixed(2) : (grossWin > 0 ? '\u221e' : '--'));
        set('perfAvgWin', wins.length ? '$' + (grossWin / wins.length).toFixed(2) : '--', 'pos');
        set('perfAvgLoss', losses.length ? '$' + (grossLoss / losses.length).toFixed(2) : '--', 'neg');
        set('perfBest', '$' + best.toFixed(2), 'pos');
        set('perfWorst', '$' + worst.toFixed(2), 'neg');
        set('perfMaxDd', '$' + maxDd.toFixed(2), 'neg');
        set('perfCall', rate(call));
        set('perfPut', rate(put));

        this._renderEquityCurve(ordered);
    }

    _renderEquityCurve(ordered) {
        const canvas = this.equityCanvas;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        const W = canvas.width = canvas.clientWidth || 600;
        const H = 150;
        canvas.height = H;
        ctx.clearRect(0, 0, W, H);
        if (!ordered.length) {
            ctx.fillStyle = 'rgba(142,163,192,0.6)';
            ctx.font = '12px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('No settled trades yet', W / 2, H / 2);
            return;
        }
        const pts = [];
        let cum = 0;
        ordered.forEach(r => { cum += r.profit; pts.push(cum); });
        const min = Math.min(0, ...pts), max = Math.max(0, ...pts);
        const range = (max - min) || 1;
        const pad = 10;
        const x = (i) => pad + (pts.length > 1 ? (i / (pts.length - 1)) * (W - 2 * pad) : W / 2);
        const y = (v) => H - pad - ((v - min) / range) * (H - 2 * pad);

        ctx.strokeStyle = 'rgba(142,163,192,0.35)';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(pad, y(0)); ctx.lineTo(W - pad, y(0)); ctx.stroke();
        ctx.setLineDash([]);

        const grad = ctx.createLinearGradient(0, 0, 0, H);
        grad.addColorStop(0, 'rgba(16,185,129,0.25)');
        grad.addColorStop(1, 'rgba(239,68,68,0.15)');
        ctx.beginPath();
        ctx.moveTo(x(0), y(0));
        pts.forEach((v, i) => ctx.lineTo(x(i), y(v)));
        ctx.lineTo(x(pts.length - 1), y(0));
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        ctx.beginPath();
        pts.forEach((v, i) => (i === 0 ? ctx.moveTo(x(i), y(v)) : ctx.lineTo(x(i), y(v))));
        ctx.strokeStyle = pts[pts.length - 1] >= 0 ? '#10b981' : '#ef4444';
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.fillStyle = 'rgba(142,163,192,0.8)';
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText('Cumulative P/L', pad, 12);
        ctx.textAlign = 'right';
        ctx.fillText('$' + pts[pts.length - 1].toFixed(2), W - pad, H - 4);
    }

    /**
     * Walk the signal engine forward over the last ~400 bars of the open
     * symbol/timeframe and measure how often an all-aligned signal was right.
     */
    async _runBacktest() {
        const btn = this.runBacktest;
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-cpu"></i> Running…'; }
        try {
            const resp = await fetch('/api/candles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: this.symbol, timeframe: this.timeframe, count: 1000 }),
            });
            const data = await resp.json();
            if (!data.success || !data.candles || data.candles.length < 80) {
                this._renderBacktest(null, 'Not enough data to backtest.');
                return;
            }
            const candles = data.candles.map(c => ({ open: parseFloat(c.open), high: parseFloat(c.high), low: parseFloat(c.low), close: parseFloat(c.close), epoch: c.epoch }));
            const res = this._walkForward(candles);
            this._renderBacktest(res);
        } catch (err) {
            this._renderBacktest(null, 'Backtest failed: ' + err.message);
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-cpu"></i> Backtest'; }
        }
    }

    _walkForward(candles) {
        const n = candles.length;
        const lookahead = 8;
        const win = candles.slice(Math.max(50, n - 400)); // last ~400 bars
        const m = win.length;
        let signals = 0, wins = 0, buys = 0, sells = 0;
        const moves = [];
        for (let i = 50; i < m - lookahead; i += 2) { // stride 2 for speed
            const sub = win.slice(0, i + 1);
            const tdi = this.tdi.calculate(sub);
            if (!tdi) continue;
            const patterns = this.patternRecognizer.detect(sub);
            const signal = this.signalEngine.generate(tdi, patterns, sub, { stake: 1, payoutRatio: this._payoutRatio });
            const action = signal.action;
            if (action !== 'BUY' && action !== 'SELL') continue;
            const entry = parseFloat(sub[sub.length - 1].close);
            const exit = parseFloat(win[i + lookahead].close);
            const move = ((exit - entry) / entry) * 100;
            const won = (action === 'BUY' && move > 0) || (action === 'SELL' && move < 0);
            signals++;
            if (won) wins++;
            if (action === 'BUY') buys++; else sells++;
            moves.push(Math.abs(move));
        }
        return {
            signals, wins, buys, sells,
            winRate: signals ? Math.round(wins / signals * 100) : 0,
            avgMove: moves.length ? moves.reduce((s, v) => s + v, 0) / moves.length : 0,
        };
    }

    _renderBacktest(res, err) {
        const box = this.backtestBox;
        if (!box) return;
        if (err) {
            box.className = 'backtest-box';
            box.innerHTML = `<div class="backtest-err">${err}</div>`;
            return;
        }
        box.className = 'backtest-box';
        box.innerHTML = `
            <div class="backtest-title"><i class="bi bi-cpu"></i> Backtest — ${this.symbol} ${this.timeframe}</div>
            <div class="backtest-stats">
                <span>Signals: <b>${res.signals}</b></span>
                <span>BUY: <b>${res.buys}</b> · SELL: <b>${res.sells}</b></span>
                <span>Win rate: <b class="${res.winRate >= 50 ? 'pos' : 'neg'}">${res.winRate}%</b></span>
                <span>Avg |move|: <b>${res.avgMove.toFixed(2)}%</b> <small>(next ${8} bars)</small></span>
            </div>`;
    }

    /**
     * Clear all recorded trades and start history fresh from now.
     */
    async _resetHistory() {
        if (!confirm('Clear all trades and start afresh? Trades placed after this will show; older ones are hidden.')) return;
        try {
            const resp = await fetch('/api/clear', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this._historyRows = [];
                this._scannerSignals = [];
                this._autoTradeNotified = {};
                if (this.historyBody) {
                    this.historyBody.innerHTML = '<tr class="history-empty"><td colspan="9"><i class="bi bi-journal-x"></i>Fresh start — no trades yet.</td></tr>';
                }
                if (this.positionsBody) {
                    this.positionsBody.innerHTML = '<div class="positions-empty"><i class="bi bi-briefcase"></i>No open positions.</div>';
                }
                this._updatePerformance([]);
                this._renderEquityCurve([]);
                this._notify('Reset', 'All trades cleared. History starts fresh from now.');
            } else {
                this._notify('Reset Failed', data.error || 'Could not reset history');
            }
        } catch (err) {
            this._notify('Reset Error', String(err));
        }
    }

    /**
     * Export the current trading history as a CSV download.
     */
    _exportCsv() {
        const rows = this._historyRows || [];
        if (!rows.length) { this._notify('Export', 'No history to export yet.'); return; }
        const head = ['Time', 'Instrument', 'Type', 'Contract ID', 'Lot Size', 'SL ($)', 'TP ($)', 'Profit/Loss', 'Result'];
        const cell = (v) => `"${String(v == null ? '' : v).replace(/"/g, '""')}"`;
        const lines = [head.join(',')];
        rows.forEach(r => {
            const time = r.time ? new Date(r.time * 1000).toLocaleString() : '';
            const sl = (r.stop_loss || 0) > 0 ? (+r.stop_loss).toFixed(2) : '';
            const tp = (r.take_profit || 0) > 0 ? (+r.take_profit).toFixed(2) : '';
            const profit = r.status === 'open' ? '' : (r.profit ?? 0).toFixed(2);
            const status = { won: 'Won', lost: 'Lost', open: 'Open' }[r.status] || r.status || '';
            const lot = Number(r.lot_size ?? r.stake ?? 0).toFixed(3);
            lines.push([time, r.symbol || '', r.contract_type || '', r.contract_id || '', lot, sl, tp, profit, status].map(cell).join(','));
        });
        const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `BotTraderX5_history_${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
        this._notify('Export', `Exported ${rows.length} trades to CSV`);
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
        this._historyRows = rows || [];
        this._updatePerformance(this._historyRows);
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
        const countEl = document.getElementById('positionsCount');
        const netEl = document.getElementById('positionsNet');
        const openEl = document.getElementById('statOpen');
        const count = (positions && positions.length) || 0;
        if (countEl) countEl.textContent = count;
        if (openEl) openEl.textContent = count;

        if (!positions || !positions.length) {
            this.positionsBody.innerHTML = '<div class="positions-empty"><i class="bi bi-briefcase"></i>No open positions.</div>';
            if (netEl) {
                netEl.className = 'positions-net';
                netEl.textContent = 'Net: $0.00';
                netEl.title = 'No open positions';
            }
            return;
        }
        let netPl = 0, exposure = 0, hasPl = false;
        this.positionsBody.innerHTML = positions.map(p => {
            const type = p.contract_type || '--';
            const dirClass = /DOWN|PUT|SELL/i.test(type) ? 'down' : 'up';
            const stake = (p.stake ?? 0).toFixed(2);
            exposure += p.stake || 0;
            const expiry = p.expiry_time ? new Date(p.expiry_time * 1000).toLocaleString() : '--';
            const lot = Number(p.lot_size ?? p.stake ?? 0);
            const sl = (p.stop_loss ?? 0) > 0 ? `$${(+p.stop_loss).toFixed(2)}` : '—';
            const tp = (p.take_profit ?? 0) > 0 ? `$${(+p.take_profit).toFixed(2)}` : '—';
            const profit = p.profit;
            const hasProfit = typeof profit === 'number' && !isNaN(profit);
            if (hasProfit) { netPl += profit; hasPl = true; }
            const plCls = hasProfit ? (profit >= 0 ? 'pos' : 'neg') : '';
            const plTxt = hasProfit ? (profit >= 0 ? '+' : '') + profit.toFixed(2) : '—';
            return `
            <div class="position-item" data-contract-id="${p.contract_id}">
                <div class="position-top">
                    <span class="position-symbol">${p.symbol || '--'}</span>
                    <span class="position-type ${dirClass}">${type}</span>
                    <span class="position-pl ${plCls}">${plTxt}</span>
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

        // Net open position (unrealized P/L) + exposure in the header.
        if (netEl) {
            if (hasPl) {
                const netCls = netPl >= 0 ? 'pos' : 'neg';
                netEl.className = `positions-net ${netCls}`;
                netEl.textContent = `Net P/L: ${netPl >= 0 ? '+' : ''}$${netPl.toFixed(2)}`;
                netEl.title = `Net unrealized P/L · Exposure: $${exposure.toFixed(2)}`;
            } else {
                netEl.className = 'positions-net';
                netEl.textContent = 'Net: $0.00';
                netEl.title = 'No profit data for open positions';
            }
        }

        this.positionsBody.querySelectorAll('.position-item').forEach(item => {
            const cid = item.dataset.contractId;
            const closeBtn = item.querySelector('.pos-close');
            closeBtn.addEventListener('click', () => this._closePosition(cid, closeBtn));
        });
    }

    /**
     * Close every open position at once.
     */
    async _closeAllPositions() {
        if (!confirm('Close ALL open positions?')) return;
        const btn = document.getElementById('closeAllPositions');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-x-circle"></i> Closing…'; }
        try {
            const resp = await fetch('/api/close_all', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this._notify('Closed All', `Closed ${data.closed} open position(s)`);
                this._loadPositions();
                this._loadHistory();
            } else {
                this._notify('Close Failed', data.error || 'Could not close positions');
            }
        } catch (err) {
            this._notify('Close Error', String(err));
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-x-circle"></i> Close All'; }
        }
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
        const openEl = document.getElementById('statOpen');
        if (openEl) openEl.textContent = '0';
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
        // Values are $ loss magnitudes per trade (already in money terms).
        const set = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return;
            if (val === null || val === undefined || isNaN(val)) { el.textContent = '--'; return; }
            el.textContent = '$' + val.toFixed(2);
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
        if (this._tradeRisk) this._updateRiskDisplay(this._tradeRisk);
        if (this.riskPct && this.riskPct.value) this._suggestLot();
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
            // Draw the TDI indicators in the RSI pane.
            this.chart.setTDI(tdiValues);
        }

        // Pattern detection
        const patterns = this.patternRecognizer.detect(candles);

        // Risk metrics: Value at Risk / Expected Shortfall (95% / 99%)
        const risk = this.risk.calculate(candles);
        this.lastRisk = risk;  // price-volatility risk — still used by the $->points SL/TP hint
        // NOTE: the VaR/ES tiles are driven by TRADE results (see _updatePerformance).

        // Combined signal: TDI indicator + chart patterns (M/W + formations).
        const stake = parseFloat((document.getElementById('tradeLotSize') || {}).value) || 1;
        const signal = this.signalEngine.generate(tdiValues, patterns, candles, {
            stake: stake,
            payoutRatio: this._payoutRatio,
        });
        this._displaySignal(signal, { tdi: tdiValues, patterns });

        // Highlight recent single-candle formations (M/W/H&S/flags are drawn as shapes).
        const recentMarkers = patterns.filter(p =>
            p.index >= this.candles.length - 6 &&
            !['double_top', 'double_bottom', 'head_and_shoulders', 'inverted_head_shoulders',
              'bullish_flag', 'bearish_flag', 'bullish_pennant', 'bearish_pennant'].includes(p.type));
        recentMarkers.forEach(p => this.chart.highlightPattern(p.timestamp, p.type, p.direction));

        // Draw M (double-top) / W (double-bottom) shapes on the price chart.
        this.chart.drawMWPatterns(patterns);

        // Auto-trade if TDI + any one other signal align (if enabled).
        this._maybeAutoTrade(signal, tdiValues, candles);

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
        // Multiplier positions: SL/TP are $ targets directly (no points conversion).
        const multEl = document.getElementById('tradeMultiplier');
        if (multEl && multEl.value) {
            const sl = parseFloat((document.getElementById('stopLoss') || {}).value) || 0;
            const tp = parseFloat((document.getElementById('takeProfit') || {}).value) || 0;
            txtEl.textContent = (sl || tp)
                ? `SL/TP are $ targets on the ${multEl.value}× multiplier position — open until hit or closed.`
                : 'Set a $ SL/TP — the position stays open until TP/SL hits or you close it.';
            return;
        }
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
        const rev = signal.reversal || 'NEUTRAL';
        // The card now leads with the REVERSAL verdict (that's what auto-trade
        // fires on); the all-aligned signal is still shown in the sub-badges.
        const tradeAction = (rev === 'BUY' || rev === 'SELL') ? rev : action;
        const isBuy = tradeAction === 'BUY';
        const isSell = tradeAction === 'SELL';
        const badgeClass = isBuy ? 'bg-success' : (isSell ? 'bg-danger' : 'bg-secondary');
        const icon = isBuy ? 'bi-arrow-up-short' : (isSell ? 'bi-arrow-down-short' : 'bi-pause-fill');
        const cardClass = isBuy ? 'buy-active' : (isSell ? 'sell-active' : '');
        const revTag = (rev === 'BUY' || rev === 'SELL') ? ' REV' : '';

        this.decisionBadge.innerHTML = `
            <span class="badge ${badgeClass} signal-badge signal-active">
                <i class="bi ${icon}"></i> ${tradeAction === 'NEUTRAL' ? 'HOLD' : tradeAction}${revTag}
            </span>
        `;

        // TDI verdict vs Pattern verdict breakdown.
        const sub = (label, val) => {
            const v = val || '—';
            const cls = v === 'BUY' ? 'buy' : (v === 'SELL' ? 'sell' : 'neutral');
            return `<span class="signal-sub-label">${label}</span><span class="signal-sub-badge ${cls}">${v}</span>`;
        };
        if (this.decisionSignalRow) {
            this.decisionSignalRow.innerHTML =
                sub('TDI', signal.tdiAction) +
                sub('M/W', signal.patternAction) +
                sub('Candles', signal.candleAction) +
                sub('Rev', rev === 'NEUTRAL' ? '—' : rev);
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

        // Signal strength bar (how applicable the signal still is right now).
        const strengthEl = document.getElementById('signalStrength');
        if (strengthEl) {
            const pct = Math.round((signal.strength || 0) * 100);
            const cls = pct >= 50 ? 'strong' : (pct >= 25 ? 'mid' : 'weak');
            const isDir = tradeAction === 'BUY' || tradeAction === 'SELL';
            const ctx = isDir
                ? ` &middot; ${tradeAction}${revTag ? ' reversal' : ''}`
                : ' &middot; HOLD (no reversal signal)';
            strengthEl.innerHTML =
                `<div class="strength-track"><div class="strength-fill ${cls}" style="width:${pct}%"></div></div>` +
                `<span class="strength-label">Signal strength: ${pct}%${ctx}</span>`;
        }

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
