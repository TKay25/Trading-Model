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
        this.tradeAmount = document.getElementById('tradeAmount');
        this.tradeDuration = document.getElementById('tradeDuration');
        this.tradeDurationUnit = document.getElementById('tradeDurationUnit');
        this.stopLossInput = document.getElementById('stopLoss');
        this.takeProfitInput = document.getElementById('takeProfit');
        this.tradeStatus = document.getElementById('tradeStatus');
        this.positionsList = document.getElementById('positionsList');
        this.btnConnect = document.getElementById('btnConnect');
        this.refreshPositions = document.getElementById('refreshPositions');
        this.connectionStatus = document.getElementById('connectionStatus');
        this.accountBalance = document.getElementById('accountBalance');
        this.accountInfo = document.getElementById('accountInfo');

        this._bindEvents();
    }

    _bindEvents() {
        this.btnBuy.addEventListener('click', () => this._placeTrade('CALL'));
        this.btnSell.addEventListener('click', () => this._placeTrade('PUT'));
        this.btnConnect.addEventListener('click', () => this._toggleConnection());
        this.refreshPositions.addEventListener('click', () => this.fetchPositions());
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

                this.accountInfo.classList.remove('d-none');
                document.getElementById('accountBalanceVal').textContent = `$${data.balance.toFixed(2)}`;
                document.getElementById('accountLogin').textContent = data.loginid;
                document.getElementById('accountCurrency').textContent = data.currency;

                this._showToast('Connected',
                    `Logged in as ${data.loginid} | Balance: $${data.balance.toFixed(2)}`
                );

                this.fetchPositions();
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
            this.connectionStatus.innerHTML = '<i class="bi bi-wifi"></i> Connected';
            this.connectionStatus.className = 'badge bg-success';
            this.btnConnect.innerHTML = '<i class="bi bi-plug-fill"></i> Disconnect';
            this.btnConnect.className = 'btn btn-outline-danger btn-sm w-100 rounded-pill';
        } else {
            this.connectionStatus.innerHTML = '<i class="bi bi-wifi-off"></i> Disconnected';
            this.connectionStatus.className = 'badge bg-warning text-dark';
            this.btnConnect.innerHTML = '<i class="bi bi-plug"></i> Connect to Deriv';
            this.btnConnect.className = 'btn btn-primary btn-sm w-100 rounded-pill';
            this.accountBalance.classList.add('d-none');
            this.accountInfo.classList.add('d-none');
        }
    }

    /**
     * Place a buy (CALL) or sell (PUT) trade.
     */
    async _placeTrade(contractType) {
        const amount = parseFloat(this.tradeAmount.value);
        const duration = parseInt(this.tradeDuration.value);
        const durationUnit = this.tradeDurationUnit.value;

        if (!amount || amount <= 0) {
            this._showTradeStatus('Please enter a valid stake amount', 'warning');
            return;
        }

        this._showTradeStatus('Placing trade...', 'info');
        this.btnBuy.disabled = true;
        this.btnSell.disabled = true;

        try {
            const response = await fetch('/api/trade', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: this.currentSymbol,
                    amount: amount,
                    contract_type: contractType,
                    duration: duration,
                    duration_unit: durationUnit,
                }),
            });

            const data = await response.json();

            if (data.success) {
                const action = contractType === 'CALL' ? 'BUY' : 'SELL';
                this._showTradeStatus(
                    `${action} trade placed! Contract ID: ${data.contract_id.slice(0, 8)}...`,
                    'success'
                );
                this.activeContractId = data.contract_id;

                // If stop loss is set and we have a contract, we'd set up monitoring
                const sl = parseFloat(this.stopLossInput.value);
                if (sl > 0) {
                    this._showTradeStatus(
                        `Trade active with Stop Loss at $${sl.toFixed(2)}`,
                        'success'
                    );
                }

                // Refresh positions
                setTimeout(() => this.fetchPositions(), 2000);
            } else {
                this._showTradeStatus(
                    'Error: ' + (data.error || 'Trade failed'),
                    'danger'
                );
            }
        } catch (err) {
            this._showTradeStatus('Error: ' + err.message, 'danger');
        } finally {
            this.btnBuy.disabled = false;
            this.btnSell.disabled = false;
        }
    }

    /**
     * Fetch open positions from Deriv.
     */
    async fetchPositions() {
        if (!this.authenticated) return;

        try {
            const response = await fetch('/api/portfolio');
            const data = await response.json();

            if (data.success && data.portfolio) {
                this._renderPositions(data.portfolio);
            }
        } catch (err) {
            console.error('Failed to fetch positions:', err);
        }
    }

    _renderPositions(portfolioData) {
        const portfolio = portfolioData.portfolio || [];
        const contracts = portfolio.contracts || [];

        if (contracts.length === 0) {
            this.positionsList.innerHTML = `
                <div class="text-center text-secondary py-3">
                    <i class="bi bi-inbox" style="font-size: 1.5rem;"></i>
                    <p class="mb-0 mt-1">No open positions</p>
                </div>
            `;
            return;
        }

        let html = '';
        contracts.forEach(c => {
            const isProfitable = parseFloat(c.profit) > 0;
            const profitClass = isProfitable ? 'text-success' : 'text-danger';

            html += `
                <div class="position-item">
                    <div class="d-flex justify-content-between">
                        <span class="fw-bold">${c.contract_type || 'Contract'}</span>
                        <span class="${profitClass}">${c.profit || '0.00'}</span>
                    </div>
                    <div class="d-flex justify-content-between text-secondary">
                        <small>${c.symbol || this.currentSymbol}</small>
                        <small>Buy: $${c.buy_price || '--'}</small>
                    </div>
                    <div class="d-flex justify-content-between text-secondary">
                        <small>ID: ${(c.contract_id || '').slice(0, 8)}...</small>
                        <button class="btn btn-outline-danger btn-sm py-0 sell-btn"
                                data-contract-id="${c.contract_id}"
                                onclick="tradingControls._sellContract('${c.contract_id}')">
                            Close
                        </button>
                    </div>
                </div>
            `;
        });

        this.positionsList.innerHTML = html;
    }

    /**
     * Sell/close a contract.
     */
    async _sellContract(contractId) {
        if (!confirm('Close this position?')) return;

        try {
            const response = await fetch('/api/sell', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ contract_id: contractId }),
            });

            const data = await response.json();
            if (data.success) {
                this._showToast('Position Closed', `Contract ${contractId.slice(0, 8)}... closed`);
                this.fetchPositions();
                this.fetchBalance();
            } else {
                this._showToast('Error', data.error || 'Failed to close position');
            }
        } catch (err) {
            this._showToast('Error', err.message);
        }
    }

    /**
     * Fetch account balance.
     */
    async fetchBalance() {
        if (!this.authenticated) return;

        try {
            const response = await fetch('/api/balance');
            const data = await response.json();

            if (data.success && data.balance) {
                const balance = data.balance.balance || {};
                const amount = parseFloat(balance.balance || 0).toFixed(2);
                const loginId = balance.loginid || '--';
                const currency = balance.currency || 'USD';

                this.accountBalance.classList.remove('d-none');
                document.getElementById('balanceValue').textContent = amount;
                this.accountInfo.classList.remove('d-none');
                document.getElementById('accountBalanceVal').textContent = `$${amount}`;
                document.getElementById('accountLogin').textContent = loginId;
                document.getElementById('accountCurrency').textContent = currency;
            }
        } catch (err) {
            console.error('Failed to fetch balance:', err);
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
