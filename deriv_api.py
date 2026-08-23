"""Deriv API WebSocket client for real-time trading (NEW Deriv API).

Uses Deriv's NEW API:
  - REST base: https://api.derivws.com
  - Auth: "Deriv-App-ID" header + "Authorization: Bearer <PAT>" (PAT = Personal
    Access Token, e.g. tokens starting with `pat_`).
  - Public market data: wss://api.derivws.com/trading/v1/options/ws/public (no auth)
  - Account WebSocket (demo/real): URL obtained from the REST OTP endpoint.
    The OTP is single-use and valid for 120 seconds.
"""
import json
import asyncio
import traceback
import urllib.request
import urllib.error
import websockets
import logging

logger = logging.getLogger(__name__)

PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
REST_BASE = "https://api.derivws.com"


class DerivAPI:
    """WebSocket client for interacting with the Deriv trading API (new API)."""

    def __init__(self, app_id: str, api_token: str = "", account_type: str = "demo"):
        self.app_id = app_id
        self.api_token = api_token
        self.account_type = account_type  # 'demo' or 'real'
        self._ws = None
        self._authenticated = False
        self._pending_requests = {}
        self._listen_task = None
        self._request_counter = 0

    # ------------------------------------------------------------------
    # REST helpers (new API auth: Deriv-App-ID header + Bearer token)
    # ------------------------------------------------------------------
    def _rest_headers(self):
        headers = {
            "Deriv-App-ID": self.app_id,
            "Accept": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def _rest(self, method: str, path: str, body=None):
        """Synchronous REST call against the new Deriv API.

        Returns (status_code, parsed_json). Raises ConnectionError with the full
        server response on HTTP errors so nothing is hidden.
        """
        url = REST_BASE + path
        req = urllib.request.Request(url, method=method, headers=self._rest_headers())
        if body is not None:
            req.data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"Deriv REST {method} {path} -> HTTP {e.code}: {err_body}")
            raise ConnectionError(
                f"Deriv REST {method} {path} failed: HTTP {e.code}: {err_body}"
            ) from None
        except Exception as e:
            logger.error(f"Deriv REST {method} {path} failed: {e!r}")
            logger.error("Full exception traceback:\n" + traceback.format_exc())
            raise

    async def _get_accounts(self):
        _, data = self._rest("GET", "/trading/v1/options/accounts")
        return data.get("data", [])

    async def _get_otp_ws_url(self, account_id: str) -> str:
        _, data = self._rest(
            "POST", f"/trading/v1/options/accounts/{account_id}/otp", body={}
        )
        url = (data.get("data") or {}).get("url")
        if not url:
            raise ConnectionError(f"OTP response missing 'data.url': {json.dumps(data)}")
        return url

    def _pick_account_id(self, accounts) -> str:
        for acc in accounts:
            if acc.get("account_type") == self.account_type:
                return acc.get("account_id")
        if accounts:
            kinds = sorted({a.get("account_type") for a in accounts})
            raise ConnectionError(
                f"No '{self.account_type}' account found. Available: {kinds}"
            )
        raise ConnectionError("Deriv returned no trading accounts")

    # ------------------------------------------------------------------
    # Connection (new API)
    # ------------------------------------------------------------------
    async def get_authenticated_ws_url(self) -> str:
        """Run the REST OTP flow and return the account-scoped WebSocket URL.

        Requires app_id + PAT token. Reuses get-accounts -> pick demo/real ->
        POST /otp. The returned URL embeds a single-use OTP (valid 120s).
        """
        if not self.api_token:
            raise ConnectionError("No API token provided for authenticated connection")
        accounts = await self._get_accounts()
        account_id = self._pick_account_id(accounts)
        return await self._get_otp_ws_url(account_id)

    async def connect(self, timeout: float = 20.0, authenticated: bool = False):
        """Establish a WebSocket connection to Deriv.

        authenticated=False -> public endpoint (market data, no token required).
        authenticated=True  -> REST OTP flow (requires app_id + PAT token):
                               list accounts -> pick demo/real -> get OTP URL,
                               then connect to the account-scoped WebSocket.
        Raises on failure after logging the full error.
        """
        try:
            if authenticated:
                ws_url = await self.get_authenticated_ws_url()
                self._authenticated = True
                logger.info(
                    "Authenticated via OTP (type=%s)",
                    self.account_type,
                )
            else:
                ws_url = PUBLIC_WS_URL

            self._ws = await asyncio.wait_for(
                websockets.connect(ws_url, ping_interval=20, ping_timeout=20),
                timeout=timeout
            )
            logger.info(
                "Connected to Deriv WebSocket API (%s)",
                "authenticated" if authenticated else "public",
            )

            # Background listener routes responses to pending requests.
            self._listen_task = asyncio.ensure_future(self._listen())
            return True
        except asyncio.TimeoutError:
            logger.error("Timed out connecting to Deriv WebSocket API")
            raise
        except Exception as e:
            # Print the FULL error as-is so nothing is hidden.
            logger.error(f"Failed to connect to Deriv: {e!r}")
            logger.error("Full exception traceback:\n" + traceback.format_exc())
            raise

    # ------------------------------------------------------------------
    # Market data (public, no auth required)
    # ------------------------------------------------------------------
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

    async def get_valid_multipliers(self, symbol: str):
        """Return the multiplier values Deriv accepts for `symbol`.

        The acceptable multipliers differ per underlying (e.g. R_75/R_100 accept
        50/100/200/300/500 but R_50 accepts 80/200/400/600/800), so the client
        must use the symbol-specific set. The `contracts_for` request (new API
        takes ONLY the symbol — no contract_type/currency allowed) returns a
        `multiplier_range` on the MULTUP entry.
        """
        resp = await self._send_request({"contracts_for": symbol})
        info = resp.get("contracts_for", {}) if isinstance(resp, dict) else {}
        avail = info.get("available", []) if isinstance(info, dict) else []
        values = []
        for item in avail:
            if isinstance(item, dict) and item.get("contract_type") == "MULTUP":
                rng = item.get("multiplier_range", [])
                if isinstance(rng, list):
                    values = [int(v) for v in rng if isinstance(v, (int, float))]
                break
        return sorted(set(values))

    # ------------------------------------------------------------------
    # Trading (auth required)
    # ------------------------------------------------------------------
    async def buy_contract(self, symbol: str, amount: float, contract_type: str = "CALL",
                           duration: int = 1, duration_unit: str = "m"):
        """Place a binary trade (CALL or PUT).

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

    async def buy_multiplier(self, symbol: str, amount: float, direction: str = "BUY",
                             multiplier: int = 100):
        """Open an open-ended MULTIPLIER position (day-trading product).

        A multiplier position has NO fixed expiry: it stays open until the
        take-profit/stop-loss is hit (or the loss exceeds the stake, closing at
        100%) or the user closes it manually. P/L tracks the market in real time
        and is multiplied by `multiplier` (valid: 50/100/200/300/500).
        direction BUY -> MULTUP, SELL -> MULTDOWN.
        """
        if not self._authenticated:
            raise PermissionError("Not authenticated. Please provide API token.")
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        proposal = await self._buy_proposal(
            symbol, amount, contract_type, None, None,
            extra={"multiplier": multiplier},
        )
        if not proposal or "proposal" not in proposal:
            return {"error": "Failed to get proposal", "details": proposal}
        proposal_id = proposal["proposal"]["id"]
        buy_req = {"buy": proposal_id, "price": amount}
        return await self._send_request(buy_req)

    async def _buy_proposal(self, symbol: str, amount: float, contract_type: str,
                            duration: int = None, duration_unit: str = None,
                            barrier: str = None, extra: dict = None):
        """Get a price proposal for a contract before buying.

        New API uses `underlying_symbol` instead of legacy `symbol`. Standard
        CALL/PUT on synthetics must NOT include a barrier (Deriv returns
        `InvalidBarrier` otherwise); pass `barrier` only for contract types
        that require one. `duration=None` means open-ended (used by multipliers).
        `extra` adds any additional proposal fields (e.g. multiplier).
        """
        proposal_req = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "underlying_symbol": symbol
        }
        if duration is not None:
            proposal_req["duration"] = duration
            proposal_req["duration_unit"] = duration_unit
        if extra:
            proposal_req.update(extra)
        if barrier:
            proposal_req["barrier"] = barrier
        return await self._send_request(proposal_req)

    async def sell_contract(self, contract_id: str):
        """Sell/close an existing contract before expiry.

        New API requires `price` (0 = sell at market).
        """
        sell_req = {
            "sell": int(contract_id),
            "price": 0
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

    # ------------------------------------------------------------------
    # Request/response plumbing (response shapes are the same as legacy)
    # ------------------------------------------------------------------
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
