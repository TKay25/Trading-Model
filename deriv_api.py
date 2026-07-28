"""Deriv API WebSocket client for real-time trading."""
import json
import asyncio
import websockets
import logging
from datetime import datetime
from flask import current_app

logger = logging.getLogger(__name__)


class DerivAPI:
    """WebSocket client for interacting with the Deriv trading API."""

    def __init__(self, app_id: str, api_token: str = ""):
        self.app_id = app_id
        self.api_token = api_token
        self.ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        self._ws = None
        self._authenticated = False
        self._pending_requests = {}

    async def connect(self):
        """Establish WebSocket connection to Deriv."""
        try:
            self._ws = await websockets.connect(self.ws_url)
            logger.info("Connected to Deriv WebSocket API")

            if self.api_token:
                await self.authenticate()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Deriv: {e}")
            return False

    async def authenticate(self):
        """Authenticate with the API token."""
        if not self.api_token:
            logger.warning("No API token provided for authentication")
            return False

        auth_req = {
            "authorize": self.api_token
        }
        response = await self._send_request(auth_req)
        if response.get("msg_type") == "authorize":
            self._authenticated = True
            logger.info(f"Authenticated as {response.get('authorize', {}).get('loginid', 'unknown')}")
            return True
        return False

    async def get_ticks(self, symbol: str, count: int = 100):
        """Fetch historical tick data for a symbol."""
        ticks_req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "start": 1,
            "style": "ticks"
        }
        return await self._send_request(ticks_req)

    async def get_candles(self, symbol: str, granularity: int, count: int = 100):
        """Fetch OHLC candle data.

        Args:
            symbol: Trading symbol (e.g., 'R_100')
            granularity: Candle interval in seconds (60=1m, 300=5m, etc.)
            count: Number of candles to fetch
        """
        candles_req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "start": 1,
            "style": "candles",
            "granularity": granularity
        }
        return await self._send_request(candles_req)

    async def get_active_symbols(self):
        """Get list of all active trading symbols."""
        req = {"active_symbols": "brief"}
        return await self._send_request(req)

    async def get_asset_index(self):
        """Get asset index / market data."""
        req = {"asset_index": 1}
        return await self._send_request(req)

    async def buy_contract(self, symbol: str, amount: float, contract_type: str = "CALL",
                          duration: int = 1, duration_unit: str = "m"):
        """Place a trade.

        Args:
            symbol: Trading symbol
            amount: Stake amount
            contract_type: 'CALL' or 'PUT'
            duration: Contract duration
            duration_unit: 'm' (minutes), 'h' (hours), 'd' (days), 't' (ticks)
        """
        if not self._authenticated:
            raise PermissionError("Not authenticated. Please provide API token.")

        proposal = await self._buy_proposal(symbol, amount, contract_type, duration, duration_unit)
        if not proposal or "proposal" not in proposal:
            return {"error": "Failed to get proposal", "details": proposal}

        proposal_id = proposal["proposal"]["id"]

        buy_req = {
            "buy": proposal_id,
            "price": amount
        }
        return await self._send_request(buy_req)

    async def _buy_proposal(self, symbol: str, amount: float, contract_type: str,
                            duration: int, duration_unit: str):
        """Get a price proposal for a contract before buying."""
        proposal_req = {
            "proposal": 1,
            "amount": amount,
            "barrier": "+0.1",
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": duration,
            "duration_unit": duration_unit,
            "symbol": symbol
        }
        return await self._send_request(proposal_req)

    async def sell_contract(self, contract_id: str):
        """Sell/close an existing contract before expiry."""
        sell_req = {
            "sell": contract_id
        }
        return await self._send_request(sell_req)

    async def get_portfolio(self):
        """Get current portfolio of open positions."""
        if not self._authenticated:
            raise PermissionError("Not authenticated.")
        req = {"portfolio": 1}
        return await self._send_request(req)

    async def get_profit_table(self):
        """Get profit/loss history."""
        if not self._authenticated:
            raise PermissionError("Not authenticated.")
        req = {"profit_table": 1}
        return await self._send_request(req)

    async def get_balance(self):
        """Get account balance."""
        if not self._authenticated:
            raise PermissionError("Not authenticated.")
        req = {"balance": 1}
        return await self._send_request(req)

    async def _send_request(self, request: dict) -> dict:
        """Send a request and wait for the response."""
        if not self._ws:
            raise ConnectionError("WebSocket not connected. Call connect() first.")

        req_id = str(id(request))
        request["req_id"] = req_id

        # Set up future for this request
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[req_id] = future

        try:
            await self._ws.send(json.dumps(request))
            # Wait for matching response
            response = await future
            return json.loads(response) if isinstance(response, str) else response
        finally:
            self._pending_requests.pop(req_id, None)

    async def _listen(self):
        """Listen for incoming messages and route to pending requests."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                data = json.loads(message)
                req_id = data.get("req_id")
                if req_id and req_id in self._pending_requests:
                    self._pending_requests[req_id].set_result(message)
                elif data.get("msg_type") == "tick":
                    # Handle real-time tick updates
                    pass  # Will be used for live chart updates
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Deriv WebSocket connection closed")

    async def close(self):
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
            self._authenticated = False
            logger.info("Deriv WebSocket connection closed")
