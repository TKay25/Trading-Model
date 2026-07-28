"""Configuration for the Trading Dashboard."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

    # Deriv API Configuration
    DERIV_APP_ID = os.getenv("DERIV_APP_ID", "")
    DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")
    DERIV_ENDPOINT = "wss://ws.derivws.com/websockets/v3?app_id="

    # Trading defaults
    DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "R_100")  # Volatility 100 Index
    DEFAULT_TIMEFRAME = os.getenv("DEFAULT_TIMEFRAME", "1m")

    # Supported symbols (Volatility Indices)
    VOLATILITY_INDICES = {
        "R_10": "Volatility 10 Index",
        "R_25": "Volatility 25 Index",
        "R_50": "Volatility 50 Index",
        "R_75": "Volatility 75 Index",
        "R_100": "Volatility 100 Index",
        "1HZ10V": "Volatility 10 (1s) Index",
        "1HZ25V": "Volatility 25 (1s) Index",
        "1HZ50V": "Volatility 50 (1s) Index",
        "1HZ75V": "Volatility 75 (1s) Index",
        "1HZ100V": "Volatility 100 (1s) Index",
    }

    # Supported timeframes
    TIMEFRAMES = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }
