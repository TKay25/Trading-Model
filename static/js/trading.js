/**
 * Trading Controls Module
 * Handles buy/sell orders, stop loss, take profit, and position management.
 */

class TradingControls {
    constructor() {
        this.apiToken = '';
        this.authenticated = false;
        this.currentSymbol = 'R_100';
        this.activeContractId = null;

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

        this._bindEvents();
    }

    _bindEvents() {
        this.btnBuy.addEventListener('click', () => this._placeTrade('BUY'));
        this.btnSell.addEventListener('click', () => this._placeTrade('SELL'));
        this.btnConnect.addEventListener('click', () => this._toggleConnection());
    }

    /**
     * Update current trading symbol.
     */
    setSymbol(symbol) {
        this.currentSymbol = symbol;
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

        this.apiToken = trimmed;  // '' means "use the .env token"
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

                // Update account info
                this.accountBalance.classList.remove('d-none');
                document.getElementById('balanceValue').textContent = data.balance.toFixed(2);

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

            hintEl.textContent = hint;
            input.value = '';

            const onOk = () => finish(input.value);
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
        if (connected) {
            this.connectionStatus.innerHTML = '<span class="status-dot"></span> Connected';
            this.connectionStatus.className = 'status-badge status-online';
            this.btnConnect.innerHTML = '<i class="bi bi-plug-fill"></i> Disconnect';
            this.btnConnect.className = 'connect-btn disconnect';
        } else {
            this.connectionStatus.innerHTML = '<span class="status-dot"></span> Offline';
            this.connectionStatus.className = 'status-badge status-offline';
            this.btnConnect.innerHTML = '<i class="bi bi-plug"></i> Connect';
            this.btnConnect.className = 'connect-btn';
            this.accountBalance.classList.add('d-none');
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

        this._showTradeStatus(`Opening ${direction} position...`, 'info');
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
                    stop_loss: parseFloat(this.stopLossInput.value) || 0,
                    take_profit: parseFloat(this.takeProfitInput.value) || 0,
                }),
            });

            const data = await response.json();

            if (data.success) {
                this._showTradeStatus(
                    `${direction} position opened`,
                    'success'
                );
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
