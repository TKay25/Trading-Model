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
            this.authenticated = false;
            this.apiToken = '';
            this._updateConnectionUI(false);
            this._showToast('Disconnected', 'Disconnected from Deriv API');
            return;
        }

        // Check if backend already has a token (from .env)
        let token = '';
        try {
            const configRes = await fetch('/api/config');
            const config = await configRes.json();
            if (config.has_token) {
                token = '__env__';  // signal to use server-side token
            }
        } catch (_) {}

        // If no .env token, prompt the user
        if (!token) {
            token = prompt(
                'Enter your Deriv API Token:\n(Get it from deriv.com > Settings > API Token > Create Token with "Read" + "Trade" scopes)'
            );
            if (!token || token.trim() === '') return;
            this.apiToken = token.trim();
        } else {
            this.apiToken = '';  // server will use .env token
        }

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

    _updateConnectionUI(connected) {
        if (connected) {
            this.connectionStatus.innerHTML = '<i class="bi bi-wifi"></i>';
            this.connectionStatus.className = 'badge bg-success';
            this.btnConnect.innerHTML = '<i class="bi bi-plug-fill"></i> Disconnect';
            this.btnConnect.className = 'btn btn-outline-danger btn-sm rounded-pill';
        } else {
            this.connectionStatus.innerHTML = '<i class="bi bi-wifi-off"></i> Off';
            this.connectionStatus.className = 'badge bg-warning text-dark';
            this.btnConnect.innerHTML = '<i class="bi bi-plug"></i> Connect';
            this.btnConnect.className = 'btn btn-primary btn-sm rounded-pill';
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
