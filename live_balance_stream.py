"""Persistent authenticated balance stream (background thread).

Maintains a single authenticated WebSocket to the account-scoped Deriv endpoint
and subscribes to real-time balance updates (`{"balance": 1, "subscribe": 1}`).
The rest of the app reads the latest balance and can block on ``wait_for_update``
for SSE-style push as trades settle.
"""
import asyncio
import json
import logging
import threading

import websockets

from deriv_api import DerivAPI

logger = logging.getLogger(__name__)


class LiveBalanceStream:
    """Persistent authenticated WebSocket that streams account balance."""

    def __init__(self):
        self._app_id = ""
        self._token = ""
        self._account_type = "demo"
        self._balance = None        # {balance, currency, loginid}
        self._version = 0
        self._active = False
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._thread = None
        self._running = False

    # ---- lifecycle ----------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="deriv-balance")
        self._thread.start()

    def stop(self):
        self._running = False
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=3)

    # ---- control / read -------------------------------------------------
    def activate(self, app_id, token, account_type="demo"):
        """Start streaming balance for the given credentials."""
        with self._lock:
            self._app_id = app_id
            self._token = token
            self._account_type = account_type
            self._active = True
            self._cond.notify_all()

    def deactivate(self):
        """Stop streaming (e.g. on disconnect)."""
        with self._lock:
            self._active = False
            self._balance = None
            self._version += 1
            self._cond.notify_all()

    def get_state(self):
        with self._lock:
            return {
                "version": self._version,
                "active": self._active,
                "balance": dict(self._balance) if self._balance else None,
            }

    def wait_for_update(self, version, timeout=30.0):
        """Block until the balance version exceeds `version`, or timeout."""
        with self._cond:
            if self._version > version:
                return True
            self._cond.wait(timeout)
            return self._version > version

    # ---- background loop -------------------------------------------------
    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._loop())
        except Exception:
            logger.exception("Live balance stream loop crashed")
        finally:
            loop.close()

    async def _loop(self):
        while self._running:
            with self._lock:
                active = self._active
                app_id, token, atype = self._app_id, self._token, self._account_type
            if not active or not token:
                # Wait until activated (or a short poll).
                with self._cond:
                    self._cond.wait(timeout=2)
                continue
            try:
                await self._run_session(app_id, token, atype)
            except Exception as e:
                logger.error(f"Live balance stream session error: {e!r}")
                await asyncio.sleep(3)

    async def _run_session(self, app_id, token, atype):
        api = DerivAPI(app_id=app_id, api_token=token, account_type=atype)
        ws_url = await api.get_authenticated_ws_url()
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
            logger.info("Balance stream connected (authenticated)")
            await ws.send(json.dumps({"balance": 1, "subscribe": 1, "req_id": 1}))
            async for message in ws:
                if not self._running:
                    break
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if data.get("msg_type") == "balance" and "balance" in data:
                    self._update(data["balance"])

    def _update(self, bal):
        try:
            info = {
                "balance": float(bal["balance"]),
                "currency": bal.get("currency"),
                "loginid": bal.get("loginid"),
            }
        except (KeyError, TypeError, ValueError):
            return
        with self._lock:
            self._balance = info
            self._version += 1
            self._cond.notify_all()
