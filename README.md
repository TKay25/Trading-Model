# Trading Dashboard

A real-time trading dashboard for **Deriv Volatility Indices** with:
- 📊 Interactive candlestick charts (TradingView Lightweight Charts)
- 📈 **TDI (Trader's Dynamic Index)** indicator with RSI, smoothed RSI, market base line, and volatility bands
- 🔍 Automated market **reversal pattern detection** (engulfing, hammers, pin bars, shooting stars)
- 💰 **One-click trading** (Buy CALL / Sell PUT) with stop loss & take profit
- 🔗 Live connection to your **Deriv account** for real volatility indices
- 🎨 Drawing tools (trendlines, Fibonacci retracement)
- 🌙 Dark Bootstrap 5 UI

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API Keys

Copy the example env file and fill in your Deriv credentials:

```bash
cp .env.example .env
```

Edit `.env`:
```
DERIV_APP_ID=your-app-id
DERIV_API_TOKEN=your-api-token
```

> **Get your credentials**: Log in to [Deriv.com](https://deriv.com) → Settings → API Token
> - Create a token with **Admin** or **Trade** scopes
> - Your App ID is shown on the same page

### 3. Run the App

```bash
python app.py
```

Open your browser to **http://localhost:5000**

## Features

### Chart
- Switch between timeframes: 1m, 5m, 15m, 30m, 1h, 4h, 1d
- Choose from 10 Volatility Index symbols
- Drawing tools: trendlines, Fibonacci retracement, rectangles
- Detected patterns highlighted directly on the chart

### TDI Indicator
The Trader's Dynamic Index combines:
- **RSI (13)**: Momentum oscillator
- **RSI Smoothed (8)**: Moving average of RSI
- **Market Base Line (34)**: Price SMA for trend direction
- **Volatility Bands (1.6185x)**: Standard deviation channels

### Trading Signals
Signals are generated when TDI aligns with reversal patterns:
- **BUY**: RSI crossing above smoothed line + price above market base + bullish pattern
- **SELL**: RSI crossing below smoothed line + price below market base + bearish pattern
- **Overbought/Oversold**: Extreme RSI levels (>80 or <20) with pattern confirmation

### Trading
- Place CALL (Buy) or PUT (Sell) contracts
- Set custom stake amount and duration
- Auto-calculated stop loss and take profit based on ATR
- View and close open positions

## Volatility Indices Available

| Symbol | Index |
|--------|-------|
| R_10 | Volatility 10 Index |
| R_25 | Volatility 25 Index |
| R_50 | Volatility 50 Index |
| R_75 | Volatility 75 Index |
| R_100 | Volatility 100 Index |
| 1HZ10V | Volatility 10 (1s) Index |
| 1HZ25V | Volatility 25 (1s) Index |
| 1HZ50V | Volatility 50 (1s) Index |
| 1HZ75V | Volatility 75 (1s) Index |
| 1HZ100V | Volatility 100 (1s) Index |

## Project Structure

```
├── app.py                    # Flask main application
├── config.py                 # Configuration
├── deriv_api.py              # Deriv WebSocket API client
├── trading_service.py        # TDI analysis + signal generation
├── requirements.txt          # Python dependencies
├── .env.example              # Environment variable template
├── README.md
├── static/
│   ├── css/
│   │   └── style.css         # Custom styles
│   └── js/
│       ├── app.js            # Main app orchestrator
│       ├── chart.js          # Chart component (Lightweight Charts)
│       ├── indicators.js     # TDI calculator + Pattern Recognizer
│       └── trading.js        # Trading controls (buy/sell/positions)
└── templates/
    ├── base.html             # Base Bootstrap template
    └── index.html            # Main dashboard layout
```

## Deploy to Render

### One-click (Render Blueprint)

1. Push this repo to GitHub
2. In [Render Dashboard](https://dashboard.render.com), click **New → Blueprint**
3. Connect your repo — Render will auto-detect `render.yaml`
4. Set the secret env vars (`DERIV_APP_ID`, `DERIV_API_TOKEN`) in Render's dashboard

### Manual Deploy

1. Push to GitHub
2. In Render: **New Web Service** → connect your repo
3. Fill in:
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT`
4. Add these **Environment Variables**:
   - `SECRET_KEY` → generate a random string
   - `DERIV_APP_ID` → your Deriv app ID
   - `DERIV_API_TOKEN` → your Deriv API token
   - `DEFAULT_SYMBOL` → `R_100`
   - `DEFAULT_TIMEFRAME` → `1m`

### After Deploy

Open your Render URL → click **Connect to Deriv** → if you set env vars it connects automatically, otherwise paste your API token.

## Notes

- The app works in **demo mode** (with simulated data) even without Deriv credentials
- For **live trading**, set `DERIV_API_TOKEN` — ONLY use with demo/real money you're willing to risk
- Always test strategies on a demo account first
- Trading volatility indices carries significant risk
