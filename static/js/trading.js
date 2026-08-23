/**
 * Trading Controls Module
 * Handles buy/sell orders, stop loss, take profit, and position management.
 */

class TradingControls {
    constructor() {
        this.apiToken = '';
        this.authenticated = false;
        this.currentSymbol = 'R_75';
        this.activeContractId = null;
        this.balanceSource = null;

        // DOM elements
        this.btnBuy = document.getElementById('btnBuy');
        this.btnSell = document.getElementById('btnSell');
        this.tradeLotSize = document.getElementById('tradeLotSize');
        this.stopLossInput = document.getElementById('stopLoss');
        this.takeProfitInput = document.getElementById('takeProfit');
        this.tradeStatus = document.getElementById('tradeStatus');
        this.btnConnect = document.getElementById('btnConnect');
        this.connectionStatus = document.getElementById('connectionStatus');
        this.accountBalance = document.getElementById('accountBalance');
        this.balanceValue = document.getElementById('balanceValue');
        // The sidebar connect bar (index.html) mirrors the nav status/balance but
        // must use UNIQUE ids — the nav already owns connectionStatus/balanceValue.
        this.sidebarStatus = document.getElementById('sidebarConnectionStatus');
        this.sidebarBalance = document.getElementById('sidebarAccountBalance');
        this.sidebarBalanceValue = document.getElementById('sidebarBalanceValue');

        this._multCache = {};    // symbol -> valid multiplier values
        this._multReqSeq = 0;    // guard against out-of-order refresh responses
        this._bindEvents();
        this.refreshMultiplierOptions(this.currentSymbol);
    }

    _bindEvents() {
        this.btnBuy.addEventListener('click', () => this._placeTrade('BUY'));
        this.btnSell.addEventListener('click', () => this._placeTrade('SELL'));
        this.btnConnect.addEventListener('click', () => this._toggleConnection());
    }

    /**
     * Update current trading symbol (and its valid multiplier options).
     */
    setSymbol(symbol) {
        this.currentSymbol = symbol;
        this.refreshMultiplierOptions(symbol);
    }

    /**
     * Rebuild the multiplier dropdown to only show the values Deriv accepts
     * for `symbol`, preserving the current selection when still valid (else
     * snapping to the nearest valid value).
     */
    async refreshMultiplierOptions(symbol) {
        const seq = ++this._multReqSeq;
        const valid = await this._fetchValidMultipliers(symbol);
        const sel = document.getElementById('tradeMultiplier');
        if (!sel || !valid || valid.length === 0) return;
        // Ignore stale responses: if a newer symbol change happened while this
        // fetch was in flight, another refresh has (or will) take over.
        if (seq !== this._multReqSeq) return;
        const current = parseInt(sel.value) || 100;
        const next = valid.includes(current) ? current : this._nearestMult(current, valid);
        sel.innerHTML = valid.map(v => `<option value="${v}">${v}×</option>`).join('');
        sel.value = String(next);
    }

    /**
     * Resolve a multiplier value that Deriv accepts for `symbol`: the current
     * dropdown selection when valid, otherwise the nearest valid one. Auto-trades
     * rely on this so they never fail on a market with a different multiplier set.
     */
    async _resolveMultiplier(symbol) {
        const selected = parseInt((document.getElementById('tradeMultiplier') || {}).value) || 100;
        const valid = await this._fetchValidMultipliers(symbol);
        if (!valid || valid.length === 0) return selected;
        if (valid.includes(selected)) return selected;
        return this._nearestMult(selected, valid);
    }

    async _fetchValidMultipliers(symbol) {
        if (this._multCache[symbol]) return this._multCache[symbol];
        try {
            const r = await fetch(`/api/multipliers?symbol=${encodeURIComponent(symbol)}`);
            const d = await r.json();
            this._multCache[symbol] = Array.isArray(d.multipliers) ? d.multipliers : [];
            return this._multCache[symbol];
        } catch (_) {
            return [];
        }
    }

    _nearestMult(selected, valid) {
        return valid.reduce((a, b) => (Math.abs(b - selected) < Math.abs(a - selected) ? b : a));
    }

    /**
     * Set signal-based stop loss and take profit.
     */
    setSignalLevels(signal) {
        if (signal && signal.stop_loss) {
            this.stopLossInput.value = signal.stop_loss.toFixed(2);
        }
        if (signal && signal.take_profit) {
            this.takeProfitInput.value = signal.take_profit.toFixed(2);
        }
    }

    /**
     * Connect/disconnect from Deriv API.
     */
    async _toggleConnection() {
        if (this.authenticated) {
            // Disconnect: also clear the server-side session token
            this.authenticated = false;
            this.apiToken = '';
            this._closeBalanceStream();
            this._stopRefreshTimer();
            if (window.app) { window.app._clearHistory(); window.app._clearPositions(); }
            this._updateConnectionUI(false);
            try {
                await fetch('/api/disconnect', { method: 'POST' });
            } catch (_) {}
            this._showToast('Disconnected', 'Disconnected from Deriv API');
            return;
        }

        // Check if the backend has a token configured (from .env)
        let hasEnvToken = false;
        try {
            const configRes = await fetch('/api/config');
            const config = await configRes.json();
            hasEnvToken = !!config.has_token;
        } catch (_) {}

        // Always let the user enter a token. Leaving it blank uses the .env token.
        const hint = hasEnvToken
            ? 'Leave blank to use the token in .env, or paste a new Deriv API token.'
            : 'Enter your Deriv API token (deriv.com \u2192 Settings \u2192 API Token, scopes: Read + Trade).';
        const entered = await this._promptForToken(hint);
        if (entered === null) return;  // user cancelled

        const trimmed = entered.trim();
        if (!trimmed && !hasEnvToken) {
            this._showToast('No Token', 'Enter an API token or set DERIV_API_TOKEN in .env');
            return;
        }

        await this._performConnect(trimmed);  // '' means "use the .env token"
    }

    /**
     * Auto-connect on page load using the token in .env (no user interaction).
     * The market-data stream already connects automatically; this also brings
     * in the account balance, trades and history without clicking Connect.
     */
    async _autoConnect() {
        if (this.authenticated) return;
        let hasEnvToken = false;
        try {
            const res = await fetch('/api/config');
            const cfg = await res.json();
            hasEnvToken = !!cfg.has_token;
        } catch (_) {}
        if (!hasEnvToken) return;
        await this._performConnect('');
    }

    /**
     * Run the actual /api/connect flow with a given token and update the UI.
     */
    async _performConnect(token) {
        this.apiToken = token || '';
        this._showToast('Connecting', 'Connecting to Deriv API...');
        try {
            const response = await fetch('/api/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_token: this.apiToken }),
            });

            const data = await response.json();

            if (data.success) {
                this.authenticated = true;
                this._updateConnectionUI(true);
                this._connectBalanceStream();
                this._startRefreshTimer();
                if (window.app) { window.app._loadHistory(); window.app._loadPositions(); }

                // Update account info (nav + sidebar)
                this._setBalanceValue(data.balance);
                this._showBalance(true);
                this._updateStatBalance(data.balance);

                this._showToast('Connected',
                    `Logged in as ${data.loginid} | Balance: $${data.balance.toFixed(2)}`
                );
            } else {
                this._showToast('Connection Failed', data.error || 'Authentication failed');
                this._updateConnectionUI(false);
            }
        } catch (err) {
            this._showToast('Connection Error', 'Failed to connect: ' + err.message);
            this._updateConnectionUI(false);
        }
    }

    /**
     * Show the token input modal and resolve with the entered value.
     * Resolves with null if the user cancels/dismisses the modal.
     */
    _promptForToken(hint) {
        return new Promise((resolve) => {
            const modalEl = document.getElementById('tokenModal');
            const input = document.getElementById('tokenModalInput');
            const hintEl = document.getElementById('tokenModalHint');
            const okBtn = document.getElementById('tokenModalOk');
            const warnEl = document.getElementById('tokenModalWarn');

            hintEl.textContent = hint;
            input.value = '';
            if (warnEl) warnEl.classList.add('d-none');

            // Accept whatever the user types — including blank. A blank value
            // means "use the token in .env"; the backend handles that. Deriv's
            // server is the final judge of whether a token is valid.
            const onOk = () => {
                if (warnEl) warnEl.classList.add('d-none');
                finish(input.value);
            };
            const onHidden = () => finish(null);
            const onKey = (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    onOk();
                }
            };

            let done = false;
            const finish = (value) => {
                if (done) return;
                done = true;
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
                resolve(value);
            };

            okBtn.addEventListener('click', onOk, { once: true });
            modalEl.addEventListener('hidden.bs.modal', onHidden, { once: true });
            input.addEventListener('keydown', onKey, { once: true });
            modalEl.addEventListener('shown.bs.modal', () => input.focus(), { once: true });

            bootstrap.Modal.getOrCreateInstance(modalEl).show();
        });
    }

    _updateConnectionUI(connected) {
        const setBadge = (el, online, label) => {
            if (!el) return;
            el.innerHTML = `<span class="status-dot"></span> ${label}`;
            el.className = `status-badge ${online ? 'status-online' : 'status-offline'}`;
        };
        if (connected) {
            setBadge(this.connectionStatus, true, 'Connected');     // nav
            setBadge(this.sidebarStatus, true, 'Connected');        // sidebar
            this.btnConnect.innerHTML = '<i class="bi bi-plug-fill"></i> Disconnect';
            this.btnConnect.className = 'connect-btn disconnect';
        } else {
            setBadge(this.connectionStatus, false, 'Offline');      // nav
            setBadge(this.sidebarStatus, false, 'Offline');         // sidebar
            this.btnConnect.innerHTML = '<i class="bi bi-plug"></i> Connect';
            this.btnConnect.className = 'connect-btn';
            this._showBalance(false);
        }
    }

    /**
     * Set the balance shown in BOTH the nav and the sidebar connect bar.
     */
    _setBalanceValue(value) {
        const text = (typeof value === 'number' ? value : parseFloat(value) || 0).toFixed(2);
        if (this.balanceValue) this.balanceValue.textContent = text;
        if (this.sidebarBalanceValue) this.sidebarBalanceValue.textContent = text;
    }

    /**
     * Show/hide the balance container in BOTH the nav and the sidebar.
     */
    _showBalance(show) {
        if (this.accountBalance) this.accountBalance.classList.toggle('d-none', !show);
        if (this.sidebarBalance) this.sidebarBalance.classList.toggle('d-none', !show);
    }

    /**
     * Open the live balance SSE stream so the balance updates as trades settle.
     */
    _connectBalanceStream() {
        this._closeBalanceStream();
        const es = new EventSource('/api/balance/stream');
        this.balanceSource = es;
        es.onmessage = (ev) => {
            try {
                const data = JSON.parse(ev.data);
                if (data && data.balance && typeof data.balance.balance === 'number') {
                    this._setBalanceValue(data.balance.balance);
                    this._updateStatBalance(data.balance.balance);
                }
            } catch (err) {
                console.warn('Bad balance SSE payload:', err);
            }
        };
        // EventSource reconnects automatically on error.
    }

    _closeBalanceStream() {
        if (this.balanceSource) {
            this.balanceSource.close();
            this.balanceSource = null;
        }
    }

    /**
     * Sync the Balance KPI tile with the current account balance.
     */
    _updateStatBalance(balance) {
        const el = document.getElementById('statBalance');
        if (el) el.textContent = '$' + (Number(balance) || 0).toFixed(2);
        // Recompute the dollar VaR/ES tiles whenever the balance changes.
        if (window.app && typeof window.app._refreshRiskMoney === 'function') {
            window.app._refreshRiskMoney();
        }
    }

    /**
     * Periodically refresh live positions + history while connected, so
     * live trades appear in the Trading History section and settle in place.
     */
    _startRefreshTimer() {
        this._stopRefreshTimer();
        this._refreshTimer = setInterval(() => {
            if (window.app) { window.app._loadHistory(); window.app._loadPositions(); }
        }, 8000);
    }

    _stopRefreshTimer() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    }

    /**
     * Open a Buy (Long) or Sell (Short) position.
     */
    async _placeTrade(direction) {
        const lotSize = parseFloat(this.tradeLotSize.value);

        if (!lotSize || lotSize <= 0) {
            this._showTradeStatus('Enter a valid lot size', 'warning');
            return;
        }
        // Deriv enforces a minimum stake (~$0.35); the API surfaces the exact
        // minimum if a multiplier is entered too small — we show that error.
        if (lotSize < 0.35) {
            this._showTradeStatus(`Lot size too small — Deriv minimum stake is $0.35 (you entered $${lotSize.toFixed(3)}). Increase the lot size.`, 'warning');
            return;
        }
        if (window.app) window.app._lastDirection = direction;

        // Resolve a multiplier Deriv accepts for THIS symbol (auto-trades can
        // target markets whose valid multiplier set excludes the current pick).
        const multiplier = await this._resolveMultiplier(this.currentSymbol);

        this._showTradeStatus(`Opening ${direction} ${multiplier}× position...`, 'info');
        this.btnBuy.disabled = true;
        this.btnSell.disabled = true;

        try {
            const response = await fetch('/api/trade', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: this.currentSymbol,
                    lot_size: lotSize,
                    direction: direction,
                    multiplier: multiplier,
                    stop_loss: parseFloat(this.stopLossInput.value) || 0,
                    take_profit: parseFloat(this.takeProfitInput.value) || 0,
                    break_even: !!(document.getElementById('breakEvenToggle') || {}).checked,
                    trail: !!(document.getElementById('trailToggle') || {}).checked,
                }),
            });

            const data = await response.json();

            if (data.success) {
                const used = data.multiplier || multiplier;
                this._showTradeStatus(
                    `${direction} ${used}× position opened — open until SL/TP or close`,
                    'success'
                );
                // Refresh positions AND history so the live trade shows
                // immediately in the Trading History section.
                if (window.app) { window.app._loadPositions(); window.app._loadHistory(); }
            } else {
                this._showTradeStatus('Error: ' + (data.error || 'Trade failed'), 'danger');
            }
        } catch (err) {
            this._showTradeStatus('Error: ' + err.message, 'danger');
        } finally {
            this.btnBuy.disabled = false;
            this.btnSell.disabled = false;
        }
    }

    /**
     * Auto-trade an all-aligned signal from the signal engine.
     * Paper mode only simulates; otherwise it places a real (demo/live) trade.
     */
    async autoTrade(action, strength, paper = true, symbol = null, timeframe = null) {
        const dir = action === 'BUY' ? 'BUY' : 'SELL';
        const sym = symbol || (window.app ? window.app.symbol : this.currentSymbol);
        const tf = timeframe || (window.app ? window.app.timeframe : '');
        if (paper) {
            this._showTradeStatus(`PAPER ${dir} @ ${sym} ${tf} (strength ${strength}%)`, 'success');
            this._showToast(`Paper ${dir}`, `${sym} ${tf} — all-aligned signal, strength ${strength}%. No real trade placed.`);
            return;
        }
        if (!this.authenticated) {
            this._showTradeStatus('Connect your account (or enable Paper mode) to auto-trade', 'warning');
            return;
        }
        this.currentSymbol = sym;
        // Note: we intentionally do NOT refresh the multiplier dropdown here —
        // it should keep showing the user's chart symbol. _placeTrade resolves a
        // valid multiplier for the auto-trade target via _resolveMultiplier().
        if (window.app) window.app._lastDirection = dir;
        await this._placeTrade(dir);
    }

    /**
     * Show status message on the trade panel.
     */
    _showTradeStatus(message, type = 'info') {
        this.tradeStatus.classList.remove('d-none');
        this.tradeStatus.className = `mt-2 alert alert-${type} py-1 px-2 mb-0 small`;
        this.tradeStatus.textContent = message;

        // Auto-hide after 5 seconds
        clearTimeout(this._statusTimeout);
        this._statusTimeout = setTimeout(() => {
            this.tradeStatus.classList.add('d-none');
        }, 5000);
    }

    /**
     * Show a toast notification.
     */
    _showToast(title, message) {
        const toastEl = document.getElementById('notificationToast');
        document.getElementById('toastTitle').textContent = title;
        document.getElementById('toastMessage').textContent = message;

        const toast = new bootstrap.Toast(toastEl, { autohide: true, delay: 4000 });
        toast.show();
    }
}

// Global instance
const tradingControls = new TradingControls();

// Auto-connect to the account using the token in .env (no click needed).
document.addEventListener('DOMContentLoaded', () => tradingControls._autoConnect());
