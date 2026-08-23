"""Configuration for the Trading Dashboard."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

    # Deriv API Configuration
    # Use `or` so an empty value in .env still falls back to the default
    DERIV_APP_ID = os.getenv("DERIV_APP_ID") or "1089"  # new-API app id (Deriv-App-ID header)
    DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN") or ""  # Personal Access Token (PAT)
    DERIV_ACCOUNT_TYPE = os.getenv("DERIV_ACCOUNT_TYPE") or "demo"  # 'demo' or 'real'
    DERIV_ENDPOINT = "https://api.derivws.com"

    # Trading defaults
    DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "R_75")  # Volatility 75 Index
    DEFAULT_TIMEFRAME = os.getenv("DEFAULT_TIMEFRAME", "5m")

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
