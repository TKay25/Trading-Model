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
        self._listen_task = None
        self._request_counter = 0

    async def connect(self, timeout: float = 20.0):
        """Establish WebSocket connection to Deriv."""
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20),
                timeout=timeout
            )
            logger.info("Connected to Deriv WebSocket API")

            # Start the background listener that routes responses to pending requests.
            # Without this, _send_request() would wait forever.
            self._listen_task = asyncio.ensure_future(self._listen())

            if self.api_token:
                ok = await self.authenticate()
                if not ok:
                    logger.warning("Authentication failed with the provided token")

            return True
        except asyncio.TimeoutError:
            logger.error("Timed out connecting to Deriv WebSocket API")
            return False
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

        # NOTE: Deriv returns msg_type "authorize" on BOTH success and failure,
        # so we must confirm there's no error and the "authorize" payload exists.
        if (response.get("msg_type") == "authorize"
                and "error" not in response
                and "authorize" in response):
            self._authenticated = True
            loginid = response.get("authorize", {}).get("loginid", "unknown")
            logger.info(f"Authenticated as {loginid}")
            return True

        err = response.get("error", {}).get("message", str(response))
        logger.error(f"Authentication failed: {err}")
        return False

    async def get_ticks(self, symbol: str, count: int = 100):
        """Fetch historical tick data for a symbol."""
        ticks_req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
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

    async def _send_request(self, request: dict, timeout: float = 20.0) -> dict:
        """Send a request and wait for the response.

        The response is routed back by the background _listen() task, so this
        method just waits on the future with a timeout.
        """
        if not self._ws:
            raise ConnectionError("WebSocket not connected. Call connect() first.")

        self._request_counter += 1
        req_id = self._request_counter  # Deriv requires req_id to be an integer
        request["req_id"] = req_id

        # Set up future for this request
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests[req_id] = future

        try:
            await self._ws.send(json.dumps(request))
            # Wait for matching response (with timeout so we never hang forever)
            response = await asyncio.wait_for(future, timeout=timeout)
            return json.loads(response) if isinstance(response, str) else response
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"Timeout waiting for Deriv response to request {req_id}"
            ) from None
        finally:
            self._pending_requests.pop(req_id, None)

    async def _listen(self):
        """Listen for incoming messages and route them to pending requests."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                data = json.loads(message)
                req_id = data.get("req_id")
                if req_id is not None and req_id in self._pending_requests:
                    future = self._pending_requests.pop(req_id, None)
                    if future and not future.done():
                        future.set_result(message)
                elif data.get("msg_type") == "tick":
                    # Handle real-time tick updates
                    pass  # Will be used for live chart updates
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Deriv WebSocket connection closed")
        except Exception as e:
            logger.error(f"Deriv listener error: {e}")
        finally:
            # Fail any requests still waiting so they don't hang
            for future in self._pending_requests.values():
                if not future.done():
                    future.set_exception(ConnectionError("Deriv WebSocket connection closed"))
            self._pending_requests.clear()

    async def close(self):
        """Close the WebSocket connection."""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listen_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._authenticated = False
        logger.info("Deriv WebSocket connection closed")
