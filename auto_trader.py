"""Server-side AutoTrader.

Moves the auto-trade OPEN and CLOSE logic (previously in the browser) into a
background daemon thread so the bot keeps trading even when the dashboard page
is closed. It uses the SAME reversal engine as the frontend (TDI + Bollinger/
RSI bounce + M/W/H&S + candlestick confluence) and the persistent shared Deriv
connection, so it behaves exactly like the old in-browser auto-trader:

  - OPEN  : a BUY/SELL reversal on ANY timeframe (1m-1d, 1m-confirmed strength
            >= min) opens a MULTIPLIER trade WITH a hard stop-loss and
            take-profit (fractions of the stake: SL -20% / TP +500% by default,
            see _sl_tp). Exits are whichever fires first — hard SL/TP, the
            reversal exit, a manual close, or Deriv's -100% auto-close.
            MULTIPLE positions per symbol are allowed (same direction); the bot
            never opens the opposite direction of an existing position (no
            self-hedging) and caps open positions per symbol.
  - CLOSE : a trade-strength reversal OPPOSITE to a held direction, on the SAME
            TIMEFRAME the position was opened, closes those positions (e.g. a
            BUY reversal on 15m closes a SELL opened on 15m). A signal on a
            different timeframe, or below the open threshold, does NOT close.
            STRICT FLIP (config `strict_flip`, default true): when a close
            happens, the only opening allowed on that symbol in the same cycle is
            the opposite direction on that SAME timeframe — so a reversal on 30m
            re-enters on 30m, never on a different timeframe it also happens to
            have a signal for.

Every position the bot holds is registered with the PositionMonitor, which
enforces the SL/TP AND records the trade's P/L path (MAE/MFE excursions) to
trade_paths.json — the training data for the adaptive-exit layer.

Config is pushed from the dashboard (POST /api/auto/config) and persisted to a
small JSON file so the choices survive restarts.
"""
import asyncio
import collections
import json
import logging
import os
import threading
import time

from trading_service import Candle, PatternRecognizer
from reversal_engine import ReversalEngine
from deriv_api import SharedDerivConnection

logger = logging.getLogger(__name__)

_CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_trader_config.json")
_ALL_TFS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")   # trade ALL timeframes
_CONFIRM_TF = "1m"
_SIGNAL_TFS = _ALL_TFS
_COOLDOWN = 90.0          # seconds between auto-actions per (symbol, timeframe)
# 60s cycle + 100 candles keeps the 70-fetch scan light enough that Deriv never
# throttles the account (which was starving the frontend's shared connection).
_INTERVAL = 60            # seconds between scan/decision cycles
_SCAN_COUNT = 100         # candles per timeframe per symbol
_MAX_POSITIONS_PER_SYMBOL = 5   # safety cap on open positions per symbol


class AutoTrader:
    """Background thread that opens/closes multiplier trades on reversals."""

    def __init__(self, resolve_multiplier, record_trade, position_monitor, ledger,
                 symbols, timeframes, app_id, account_type, token,
                 valid_multipliers=None, params_fn=None, pruned_fn=None):
        # resolve_multiplier(symbol, requested) -> nearest valid multiplier
        self._resolve_multiplier = resolve_multiplier
        # valid_multipliers(symbol) -> list of multipliers Deriv accepts. Needed
        # by `_exits` to cap exposure so an ATR-sized stop stays affordable.
        self._valid_multipliers = valid_multipliers
        # Per-market exit parameters honed by strategy_learner.py. Both are
        # optional: without them the bot just uses the global config.
        #   params_fn(symbol, tf) -> {"sl_atr_k": x, "tp_atr_k": y} | None
        #   pruned_fn(symbol, tf) -> bool  (learner measured no edge here)
        self._params_fn = params_fn
        self._pruned_fn = pruned_fn
        # record_trade(contract_id, symbol, contract_type, lot, mult, sl, tp,
        #              timeframe, signal_strength)
        self._record_trade = record_trade
        self._ledger = ledger        # trade_ledger dict (contract_id -> meta incl. timeframe)
        self._pm = position_monitor
        self._symbols = list(symbols)
        self._tfs = dict(timeframes)
        self._engine = ReversalEngine()
        # DEDICATED connection: the AutoTrader's heavy scan (40 candle fetches
        # every cycle) must NOT share the frontend's connection, or frontend
        # requests (/api/candles, /api/connect, /api/multipliers) get starved
        # and time out on Render while the bot keeps trading.
        self._token = token
        self._conn = SharedDerivConnection(app_id=app_id, account_type=account_type)
        self._conn.start()
        # Separate PUBLIC connection for the heavy candle scan (market data needs
        # no auth): keeps the authenticated connection and the account's rate
        # limit free for trades/portfolio, and stops the scan from blocking it.
        self._pub_conn = SharedDerivConnection(
            app_id=app_id, account_type=account_type, public=True
        )
        self._pub_conn.start()

        self._lock = threading.Lock()
        self._thread = None
        self._running = False

        # config (persisted so it survives restarts / works with the page closed).
        # Default is AUTO-TRADE ON: after a restart the bot keeps trading with no
        # browser open. The dashboard toggles can still turn it off (that choice
        # is persisted too).
        self._cfg = {
            "enabled": True,
            # Strength gate. HISTORY: 0 -> 30 (2026-08-24) -> 50 (2026-08-25) ->
            # 25 (2026-09-20). Lowered because live signals were peaking at ~21%
            # (3 signals found, strengths 21/18/18) so a 50% gate meant the bot
            # almost never opened anything. NOTE the 2026-09-20 analysis found
            # strength did NOT predict profit — under fixed exits EVERY bucket
            # was positive, 0-9% included — so this is a VOLUME throttle, not a
            # risk filter. The learner can re-fit it from data.
            "min_strength": 25,
            "paper": False,
            "exit_on_reversal": True,
            "stake": 1.0,
            "multiplier": 400,
            # USER RULE (2026-09-20): as soon as the NET P/L OF THE OPEN TRADES
            # reaches this many $, close EVERY open position to bank it, then
            # CARRY ON OPENING new trades (the bot is never paused).
            # This is the floating P/L the dashboard shows as "Net P/L" on the
            # Open Positions panel — it does NOT include already-realised losses.
            # See `profit_target_mode` to include realised P/L instead.
            # 0 = feature off.
            # HISTORY: an earlier $15 target used realised+floating equity and was
            # removed because it cut winners short (closed 17 positions 6s after
            # boot); this rebuild is the user's explicit re-introduction at $8 on
            # the narrower OPEN-P/L basis, and it no longer blocks the rest of
            # the cycle, so new trades open immediately after the close-all.
            "profit_target": 8.0,
            # Basis for the rule above:
            #   "open"   (default): live/floating P/L of OPEN positions only.
            #   "equity" (legacy):  finished P/L since the last flat point PLUS
            #                       floating, i.e. (cash - baseline) + proceeds.
            "profit_target_mode": "open",
            # HARD EXITS (USER RULE 2026-09-20). Two modes, see `_exits`:
            #   "atr"   (DEFAULT, 2026-09-20): stop/target are k x ATR% of PRICE,
            #           then converted to $ via the position's exposure.
            #   "stake" (legacy, 2026-09-20 build): a flat % of the stake.
            # WHY atr is the default: the stake-relative stop was MEASURED to sit
            # INSIDE one 1-minute candle's range. SL -20% of stake at 100x = a
            # 0.2% price move while R_100's median 1m range is 0.18% (and at 400x
            # -20% = only 0.05%), so it was hit by ordinary noise: 10/10 live
            # trades stopped out with a best-ever MFE of +0.06% of stake.
            # Because $ risk = stake x multiplier x stop_move%, the multiplier is
            # CAPPED so the stop never costs more than `max_risk_pct` of the
            # stake, and a trade is SKIPPED when even the symbol's lowest valid
            # multiplier cannot satisfy that (its -100% Deriv close would fire
            # first anyway, so no real stop exists on that symbol).
            "stop_mode": "atr",
            "sl_atr_k": 1.5,             # stop   = 1.5 x ATR%(signal timeframe)
            "tp_atr_k": 6.0,             # target = 6.0 x ATR%   (R:R 1:4)
            # GUARD ONLY — not a strategy parameter. A FLAT %-of-price floor is
            # WRONG because ATR% varies ~10x across these symbols (R_10 1m ATR is
            # 0.019%, R_100 1m is 0.159%): a 0.5% floor made R_10's required stop
            # 26x its own ATR and excluded 6 of 10 symbols for no good reason.
            # Measured 2026-09-20 (10 syms x 7 tfs): risk per trade at the lowest
            # multiplier with a 1.5xATR stop is ~10-13% at 1m, 23-31% at 5m,
            # 39-53% at 15m, 56-78% at 30m and >100% (impossible) at 4h/1d — and
            # those figures are nearly IDENTICAL across symbols. The binding
            # constraint is the TIMEFRAME, not the symbol.
            # So this stays tiny: it only stops a degenerate/zero ATR from
            # producing a stop inside the spread.
            "min_stop_move_pct": 0.05,
            # MAX RISK PER TRADE, as a fraction of the stake. Raised 0.30 -> 0.80
            # on 2026-09-20 so the bot trades MORE TIMEFRAMES (user request:
            # "trade across all timeframes"). Measured requirement at the symbol's
            # LOWEST valid multiplier with a 1.5xATR stop:
            #     1m  9-13%    5m 23-31%    15m 39-53%    30m 56-78%
            #     1h 89-103%   4h 170-225%  1d 461-539%
            # So 0.80 admits 1m/5m/15m/30m on every symbol. 1h/4h/1d are NOT a
            # tuning choice: risk% = multiplier x stop_move%, and at the lowest
            # multiplier Deriv accepts, a stop for those timeframes would cost
            # MORE than the stake — meaning Deriv's own -100% auto-close fires
            # first and the "stop" is pure illusion. For reference, at 4h/1d the
            # -100% close is only 0.3-0.8x that bar's ATR, i.e. one ordinary
            # candle ends the position. Those timeframes need binaries (fixed
            # stake = fixed risk, no intra-trade mark), not multipliers.
            "max_risk_pct": 0.80,
            # PER-TIMEFRAME risk caps (fractions of the stake), overriding the
            # global above. Why they exist: a single global cap couples the two
            # things we want independent. Raising it to admit 30m also LEVERAGED
            # UP 1m/5m (they went from 10-13% / 23-31% risk to 50-76%), because
            # the cap picks the multiplier. Per-timeframe caps let the short
            # timeframes stay conservative while the longer ones (which need more
            # risk to host a real stop at Deriv's lowest multiplier) are allowed.
            # Set a timeframe to 0 to disable trading it entirely.
            "risk_by_tf": {"1m": 0.20, "5m": 0.30, "15m": 0.55, "30m": 0.80},
            "stop_loss_pct": 0.20,       # stake-mode only (see stop_mode)
            "take_profit_pct": 5.0,      # stake-mode only
            # BREAK-EVEN + TRAILING STOP FOR AUTO-TRADES (user rule 2026-09-21).
            # The monitor has always supported these; auto-trades simply never
            # enabled them, so a position that ran into good profit could still
            # round-trip all the way back to its original stop. Now the stop
            # RATCHETS UP as the trade works:
            #   break_even_enabled: once profit >= break_even_pct x TP, the stop
            #                       moves to 0 (the worst case becomes no loss).
            #   trail_enabled:      the stop then follows the best profit reached,
            #                       never more than trail_pct x TP behind it, so a
            #                       close can lock in a PROFIT, never a loss.
            # Both are fractions of TP, and TP = tp_atr_k x ATR, so with the
            # defaults below the stop arms at +1.5xATR and trails 1.5xATR behind
            # the best profit — deliberately symmetric with the entry stop.
            # The stop NEVER moves down and never below the original stop-loss.
            # TRADE-OFF: this caps the fat-tail winners the ATR exit depends on
            # (exit_study: atr 1.5/6 has mean +8.66% but MEDIAN -11.67%), so it is
            # worth measuring rather than assuming it helps. Set either to false to
            # restore the previous fixed-stop behaviour.
            "break_even_enabled": True,
            "break_even_pct": 0.25,      # arm at 25% of TP = 1.5xATR of profit
            "trail_enabled": True,
            "trail_pct": 0.25,           # trail 25% of TP = 1.5xATR behind best profit
            # BLACKLIST NO LONGER USED (2026-09-20, user decision): the bot now
            # trades ALL indices x ALL timeframes. The old 8-symbol blocklist
            # was fitted to per-symbol net P/L that turned out to have only 6/10
            # sign agreement between train and test halves (n≈50-80 each) — i.e.
            # noise. With fixed exits every market/timeframe was profitable, and
            # even the "MULTDOWN loses -$46" effect was an exit artifact (it
            # became +$20/+$92). Per-market edge is left to the learning layer.
            "blocked_symbols": [],
            "blocked_strength": 60.0,     # dormant unless a symbol is blocked
            "blocked_timeframes": [],
            # The strategy learner (strategy_learner.py) continuously re-fits the
            # exit geometry per (symbol, timeframe) from live results and may
            # prune markets whose measured edge is not positive. It can ONLY
            # change k_sl/k_tp and prune markets — never leverage, risk limits,
            # stake or the on/off switch. false = freeze learning.
            "learning": True,
            # NATIVE (EXCHANGE-SIDE) SL/TP — the single most important fix
            # (2026-09-21). Attaches the stop and target to the CONTRACT via
            # Deriv's `contract_update`, so enforcement no longer depends on our
            # 2s polling loop. Why this outranks any parameter change: of 1330
            # settled trades, 30.8% died at Deriv's own -100% close, which fires
            # ONLY when our stop was never armed, and 1096 of 1330 had no exit
            # attribution at all (the monitor never managed them). The SAME
            # entries with the stop actually enforced were +8.66%/trade in
            # exit_study.json against -8.6%/trade live — an identical 36.8% win
            # rate, so the gap is execution, not signal. Native limits also
            # remove the -22%..-27% overshoot measured on a -20% stop (poll lag)
            # and the proposal_open_contract quota dependency.
            # The position monitor STAYS ON as a backstop and remains the thing
            # that records MAE/MFE for the learner. false = monitor-only again.
            "native_sl_tp": True,
            # STRICT FLIP (user rule 2026-09-20): when a position is closed by a
            # reversal on (symbol, timeframe), the ONLY opening allowed on that
            # symbol in that cycle is the opposite direction on that SAME
            # timeframe. Without this, a closed 30m position could be replaced by
            # a 5m one (same direction, same instrument) — defensible, but not
            # literally "re-enter on the timeframe whose reversal closed it".
            # false = allow the cross-timeframe replacement again.
            "strict_flip": True,
            # ADOPTION MODE (user rule 2026-09-21). A position open before a restart
            # is UNTRACKED, so adoption must invent a stop for it from a fresh ATR.
            # If that stop is already breached by the position's CURRENT P/L,
            # enforcing it sells the position the moment it is adopted — a restart
            # then realises losses nothing else would have (measured 2026-09-21:
            # ~11 positions sold at -0.11..-0.92 within seconds of a restart, at
            # stops of -0.12).
            #   "forward" (DEFAULT): keep protecting it, but anchor the stop one
            #            full ATR of risk FROM NOW (stop = current P/L - risk), so
            #            it cannot fire immediately. The past loss is sunk; it is
            #            not a reason to sell at an arbitrary moment.
            #   "skip":  do not adopt a breached position at all — report it.
            #   "strict": old behaviour — install the entry-anchored stop and let
            #            it fire immediately.
            "adopt_mode": "forward",
        }
        self._cooldown = {}        # (symbol, tf) -> last OPEN ts
        self._exit_cooldown = {}   # (symbol, direction, tf) -> last close ts
        self._last_adopt = None    # last time we adopted untracked positions
        # Contract ids already given NATIVE (exchange-side) SL/TP, so the mirror
        # sweep sends `contract_update` once per position instead of every sweep.
        # A rejection is deliberately NOT recorded, so it retries next time.
        self._native_done = set()
        self._skip_reasons = collections.Counter()   # why this cycle refused to trade
        self._last_cycle = {"at": None, "opened": [], "closed": [], "scan": 0,
                            "message": "not started"}
        # Account balance at server start / after the last profit-target close-all.
        # Realised net profit = current balance - baseline (balance only moves on
        # settlement on this account). Set on the first balance fetch of a cycle.
        self._baseline = None
        self._load_cfg()

    # ------------------------------------------------------------- config
    def update_config(self, **kw):
        with self._lock:
            for k in ("enabled", "min_strength", "paper", "exit_on_reversal",
                      "stake", "multiplier", "profit_target", "learning",
                      "profit_target_mode",
                      "blocked_symbols", "blocked_timeframes", "blocked_strength",
                      "strict_flip",
                      "stop_mode", "sl_atr_k", "tp_atr_k", "min_stop_move_pct",
                      "max_risk_pct", "risk_by_tf", "stop_loss_pct", "take_profit_pct",
                      "break_even_enabled", "break_even_pct",
                      "trail_enabled", "trail_pct",
                      "adopt_mode", "native_sl_tp"):
                if k in kw:
                    self._cfg[k] = kw[k]
            cfg = dict(self._cfg)
        self._save_cfg()
        return cfg

    # ----------------------------------------------------- exit helpers
    def _sl_tp(self, cfg, stake):
        """Legacy stake-relative stop/target in $ for a position of `stake`.
        Returns (stop_loss$, take_profit$) — both may be 0 (off)."""
        try:
            sl = float(cfg.get("stop_loss_pct") or 0) * float(stake or 0)
            tp = float(cfg.get("take_profit_pct") or 0) * float(stake or 0)
        except (TypeError, ValueError):
            return 0.0, 0.0
        return max(0.0, round(sl, 2)), max(0.0, round(tp, 2))

    @staticmethod
    def _atr_pct(candles, period=14):
        """Mean candle range as a % of price (ATR-style volatility proxy).

        Volatility indices have no overnight gaps, so the high-low range is a
        faithful ATR. Returns None when there is not enough data.
        """
        tail = list(candles or [])[-period:]
        vals = []
        for c in tail:
            try:
                hi, lo, cl = float(c.high), float(c.low), float(c.close)
                if cl > 0:
                    vals.append((hi - lo) / cl * 100.0)
            except (AttributeError, TypeError, ValueError):
                continue
        return (sum(vals) / len(vals)) if vals else None

    def _multipliers_for(self, symbol):
        """Sorted list of multiplier values Deriv accepts for `symbol` (or [])."""
        try:
            vals = self._valid_multipliers(symbol) if self._valid_multipliers else []
            out = sorted({int(v) for v in (vals or []) if v})
            return out
        except Exception as e:
            logger.debug("Multiplier lookup failed for %s: %r", symbol, e)
            return []

    @staticmethod
    def _risk_cap(cfg, timeframe, default=0.80):
        """Max stop cost as a fraction of the stake for this timeframe.

        `risk_by_tf` overrides the global `max_risk_pct` per timeframe; a value
        of 0 means "do not trade this timeframe". Always clamped below 100%: a
        stop costing >= the stake can never fire before Deriv's -100% auto-close.
        """
        try:
            per_tf = cfg.get("risk_by_tf") or {}
            if timeframe and timeframe in per_tf:
                cap = float(per_tf[timeframe])
            else:
                cap = float(cfg.get("max_risk_pct") or default)
        except (TypeError, ValueError):
            cap = default
        return min(max(cap, 0.0), 0.98)

    def _exits(self, cfg, stake, multiplier, atr_pct, symbol="", timeframe=None):
        """Resolve (stop_loss$, take_profit$, multiplier, info) for a new trade.

        ATR MODE (default) — the stop lives in MARKET terms, not stake terms:
            stop_move% = sl_atr_k x ATR%(signal tf), floored at min_stop_move_pct
            stop$      = stake x multiplier x stop_move% / 100
        Because $ risk scales with the multiplier, a market-sized stop is only
        affordable at low multipliers, so the multiplier is capped:
            multiplier <= max_risk_pct x 100 / stop_move%
        The largest VALID multiplier at or below that ceiling is used. If even
        the symbol's lowest valid multiplier exceeds the ceiling, the trade is
        REJECTED (multiplier None): that symbol cannot support a real stop — its
        Deriv -100% auto-close would always fire first — and we refuse to open a
        position whose stop sits inside the noise (that was the 10/10 stop-out
        bug). Risk is therefore <= max_risk_pct of the stake on every trade.
        """
        try:
            stake_f = float(stake or 0)
            mult_req = float(multiplier or 0)
        except (TypeError, ValueError):
            return 0.0, 0.0, multiplier, {"mode": "invalid"}

        mode = str(cfg.get("stop_mode") or "atr").lower()
        if mode != "atr" or not atr_pct:
            sl, tp = self._sl_tp(cfg, stake_f)
            return sl, tp, multiplier, {
                "mode": "stake",
                "risk_pct": round(float(cfg.get("stop_loss_pct") or 0), 4),
                "risk_usd": round(sl, 2),
            }

        try:
            k_sl = float(cfg.get("sl_atr_k") or 1.5)
            k_tp = float(cfg.get("tp_atr_k") or 6.0)
            floor = float(cfg.get("min_stop_move_pct") or 0)
        except (TypeError, ValueError):
            k_sl, k_tp, floor = 1.5, 6.0, 0.0
        max_risk = self._risk_cap(cfg, timeframe)
        if max_risk <= 0:
            return 0.0, 0.0, None, {"mode": "atr", "skipped": "timeframe_disabled"}

        stop_move = max(k_sl * float(atr_pct), floor)     # % of price
        # Keep the configured R:R even when the floor (or a later floor) widened
        # the stop — otherwise a floored stop would silently compress the target.
        ratio = (k_tp / k_sl) if k_sl > 0 else 1.0
        tp_move = stop_move * ratio
        ceiling = (max_risk * 100.0 / stop_move) if stop_move > 0 else 0.0

        allowed = self._multipliers_for(symbol)
        if allowed:
            affordable = [m for m in allowed if m <= ceiling]
            if not affordable:
                widest = (max_risk * 100.0 / allowed[0]) if allowed[0] else 0.0
                logger.info(
                    "Auto-trade SKIPPED %s: needs a %.2f%% price stop, but its lowest "
                    "valid multiplier (%sx) allows at most %.2f%% within %.0f%% of the "
                    "stake — no real stop exists on this symbol (Deriv's -100%% close "
                    "fires first)",
                    symbol, stop_move, allowed[0], widest, max_risk * 100)
                return 0.0, 0.0, None, {
                    "mode": "atr", "skipped": "multiplier_floor",
                    "atr_pct": round(float(atr_pct), 4),
                    "stop_move_pct": round(stop_move, 4),
                    "widest_stop_pct": round(widest, 4),
                    "risk_pct": round(allowed[0] * stop_move / 100.0, 4),
                }
            multiplier = max(affordable)     # most exposure that still fits the limit

        try:
            mult_f = float(multiplier or 0)
        except (TypeError, ValueError):
            mult_f = 0.0
        sl = min(stake_f, round(stake_f * mult_f * stop_move / 100.0, 2))
        tp = round(stake_f * mult_f * tp_move / 100.0, 2)
        return sl, tp, multiplier, {
            "mode": "atr",
            "atr_pct": round(float(atr_pct), 4),
            "stop_move_pct": round(stop_move, 4),
            "tp_move_pct": round(tp_move, 4),
            "risk_pct": round(sl / stake_f, 4) if stake_f else None,
            "risk_usd": sl,
            "multiplier": multiplier,
        }

    # ------------------------------------------------------ blacklist helpers
    def _blocked_symbols(self):
        return set(self._cfg.get("blocked_symbols") or [])

    def _blocked_tfs(self):
        return set(self._cfg.get("blocked_timeframes") or [])

    def _min_strength_for(self, symbol):
        """Effective strength threshold to OPEN/CLOSE on `symbol`.

        Unblocked markets trade from `min_strength`. Blacklisted markets are
        still scanned but need a STRONGER signal (`blocked_strength`, default
        60%) — never lower than min_strength (so a blocked market can't become
        easier to trade than an unblocked one)."""
        base = float(self._cfg.get("min_strength") or 0)
        if symbol in self._blocked_symbols():
            return max(base, float(self._cfg.get("blocked_strength") or 60))
        return base

    def _signal_timeframes(self):
        """Timeframes whose signals may open/close trades (blacklist filtered).
        Falls back to all when everything is blocked so the bot can still run."""
        blocked = self._blocked_tfs()
        tfs = [tf for tf in _SIGNAL_TFS if tf not in blocked]
        return tfs or list(_SIGNAL_TFS)

    def _scan_timeframes(self):
        """Timeframes to fetch candles for: enabled signal tfs + the 1m confirm
        series (always needed to compute strength)."""
        tfs = self._signal_timeframes()
        if _CONFIRM_TF not in tfs:
            tfs = list(tfs) + [_CONFIRM_TF]
        return tfs

    def get_config(self):
        with self._lock:
            return dict(self._cfg)

    def get_status(self):
        with self._lock:
            return {"config": dict(self._cfg), **dict(self._last_cycle)}

    def _save_cfg(self):
        try:
            with open(_CONF_PATH, "w") as f:
                json.dump(self._cfg, f)
        except Exception:
            pass

    def _load_cfg(self):
        try:
            with open(_CONF_PATH) as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in self._cfg:
                    if k in data:
                        self._cfg[k] = data[k]
        except Exception:
            pass

    # ----------------------------------------------------------- lifecycle
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="auto-trader")
        self._thread.start()
        logger.info("Auto-trader started (enabled=%s exit_on_reversal=%s)",
                    self._cfg.get("enabled"), self._cfg.get("exit_on_reversal"))

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def resync(self):
        """Restart the scan loop and re-protect every open position.

        Exposed to the dashboard as "Restart Engine". The page cannot re-boot the
        server that serves it, so this is the in-page equivalent: it restarts the
        trading engine and immediately re-runs the adoption sweep, which is the
        step that guarantees no open position is left without a stop.

        Cooldowns are PRESERVED across the restart on purpose — clearing them
        would let the bot immediately re-open positions it had just opened.
        """
        with self._lock:
            cfg = dict(self._cfg)
            cooldowns = dict(self._cooldown)
            exit_cooldowns = dict(self._exit_cooldown)
        self.stop()
        self._cooldown = cooldowns
        self._exit_cooldown = exit_cooldowns
        self._last_adopt = None
        self.start()
        protected = 0
        try:
            protected = self._adopt_untracked(cfg)
        except Exception as e:
            logger.warning("resync: adoption sweep failed: %r", e)
        # We just ran it ourselves, so don't let the next cycle repeat it at once.
        self._last_adopt = time.time()
        tracked = 0
        try:
            tracked = self._pm.tracked_count()
        except Exception:
            pass
        logger.info("Auto-trader engine RESTARTED via dashboard (tracked=%d, "
                    "newly protected=%d)", tracked, protected)
        return {"tracked": tracked, "protected": protected}

    def _run(self):
        while self._running:
            try:
                self._cycle()
            except Exception:
                logger.exception("Auto-trader cycle crashed")
            # sleep in small slices so stop() can interrupt promptly
            for _ in range(20):
                if not self._running:
                    break
                time.sleep(_INTERVAL / 20)

    # --------------------------------------------------------------- cycle
    def _cycle(self):
        with self._lock:
            cfg = dict(self._cfg)
        if not (cfg.get("enabled") or cfg.get("exit_on_reversal")):
            self._set_last(at=time.time(), opened=[], closed=[], scan=0,
                           message="auto-trade off")
            return

        opened, closed = [], []
        now = time.time()
        # Diagnostics for WHY nothing opened: an idle cycle used to log only
        # "idle", which is indistinguishable from "found nothing", "all signals
        # below min_strength" and "every signal unaffordable geometrically".
        self._skip_reasons = collections.Counter()

        # ---- PROFIT TARGET (USER RULE 2026-09-20): as soon as the NET P/L of
        # the OPEN trades reaches `profit_target` $, close EVERY open position to
        # bank it, then CARRY ON OPENING new trades. The bot is never paused.
        # This is the floating P/L the dashboard shows as "Net P/L" on the Open
        # Positions panel; it does NOT include already-realised losses.
        # `profit_target_mode`: "open" (default) = floating P/L only;
        # "equity" (legacy) = (cash - baseline) + open proceeds.
        # profit_target 0 = OFF.
        # Runs BEFORE the candle scan so a scan failure never skips the lock-in,
        # and deliberately does NOT return: the user asked for new trades to be
        # opened straight after the close-all, so the cycle continues normally.
        profit_target = float(cfg.get("profit_target") or 0)
        if profit_target > 0:
            eq = self._open_equity()
            if eq is not None:
                cash = eq["cash"]
                if str(cfg.get("profit_target_mode") or "open").lower() == "equity":
                    if self._baseline is None:
                        self._baseline = cash
                    net = (cash - self._baseline) + eq["proceeds"]
                    basis = "equity"
                else:
                    net = eq["open_pnl"]
                    basis = "open-pnl"
                logger.info("AutoTrader profit-target check (%s): open_pnl=%.2f "
                            "cash=%.2f n_open=%d net=%.2f (target %.2f)",
                            basis, eq["open_pnl"], cash, eq["n_open"], net,
                            profit_target)
                if net >= profit_target:
                    had = bool(eq["cids"])
                    closed_n = (self._close_contracts(
                        eq["cids"], reason=f"profit target +${net:.2f}")
                        if had else 0)
                    # Bank it: restart the cash baseline from the post-close
                    # balance (all positions closed => flat).
                    e2 = self._open_equity()
                    if e2 is not None:
                        self._baseline = e2["cash"]
                    if had:
                        closed.append(f"profit+${net:.2f} x{closed_n}")
                    self._set_last(
                        at=now, opened=[], closed=list(closed), scan=0,
                        message=(f"profit target +${net:.2f} reached -> closed "
                                 f"{closed_n} open position(s); trading continues"))
                    logger.info("AutoTrader PROFIT TARGET +$%.2f reached (%s) -> "
                                "closed %d position(s); baseline reset to $%.2f; "
                                "trading continues", net, basis, closed_n,
                                self._baseline or 0.0)

        # 0) ADOPT unprotected positions: anything open that the monitor isn't
        # tracking (opened before a restart, or placed outside the bot) gets the
        # same SL/TP, so NO position can ride to -100% of its stake. Idempotent.
        if self._last_adopt is None or now - self._last_adopt >= 300:
            self._last_adopt = now
            self._adopt_untracked(cfg)

        # 1) candles for all symbols x all timeframes over ONE shared connection
        candles_by = self._scan_candles()
        if not candles_by:
            self._set_last(at=now, message="scan returned nothing")
            return

        # 2) reversal + 1m-confirmed strength per symbol (blacklist filtered)
        signals = {}
        n_signals = 0
        n_qualifying = 0
        for sym, by_tf in candles_by.items():
            confirm = self._norm(by_tf.get(_CONFIRM_TF))
            for tf in self._signal_timeframes():
                sc = self._norm(by_tf.get(tf))
                if not sc or len(sc) < 50:
                    continue
                try:
                    tdi = self._engine.tdi(sc)
                    pats = PatternRecognizer.detect_reversal_patterns(sc)
                    rev = self._engine.reversal(tdi, pats, sc)
                    if rev in ("BUY", "SELL"):
                        s = int(round((self._engine.strength(rev, tdi, sc, confirm) or 0) * 100))
                        n_signals += 1
                        if s >= self._min_strength_for(sym):
                            n_qualifying += 1
                        signals.setdefault(sym, []).append({
                            "tf": tf, "rev": rev, "str": s,
                            # ATR% of THIS signal timeframe: the stop/target are
                            # sized from the volatility the signal was read on.
                            "atr": self._atr_pct(sc),
                        })
                except Exception:
                    continue

        # 3) open positions per symbol: {symbol: {direction: {timeframe: [cids]}}}
        open_pos = self._open_positions()

        # ---- CLOSE: a trade-strength reversal OPPOSITE to a held direction,
        # on the SAME TIMEFRAME the position was opened, closes those positions
        # (e.g. a BUY reversal on 15m closes a SELL opened on 15m). A signal on
        # a different timeframe, or below the open threshold, does NOT close. ----
        closed_dirs = {}   # sym -> {direction: closed count this cycle}
        closed_tfs = {}    # sym -> {timeframe} closed by a reversal this cycle
        if cfg.get("exit_on_reversal"):
            for sym, dirs in open_pos.items():
                # blocked markets need the strong signal to reverse-close too
                min_str = self._min_strength_for(sym)
                for d, by_tf in dirs.items():
                    opposite_rev = "SELL" if d == "BUY" else "BUY"
                    for tf, cids in by_tf.items():
                        # only an opposite signal strong enough to OPEN a trade
                        # closes; on the same timeframe ("any" = no recorded tf)
                        def _opposes_on_tf(h):
                            if h["rev"] != opposite_rev or h["str"] < min_str:
                                return False
                            return True if tf == "any" else h["tf"] == tf
                        if not any(_opposes_on_tf(h) for h in signals.get(sym, [])):
                            continue
                        ckey = (sym, d, tf)
                        if now - self._exit_cooldown.get(ckey, 0) < _COOLDOWN:
                            continue
                        self._exit_cooldown[ckey] = now
                        try:
                            n = self._close_contracts(cids)
                            if n:
                                closed.append(f"{sym}:{d}:{tf}x{n}")
                                closed_dirs.setdefault(sym, {})[d] = \
                                    closed_dirs.get(sym, {}).get(d, 0) + n
                                closed_tfs.setdefault(sym, set()).add(tf)
                        except Exception as e:
                            logger.warning("Auto-exit failed for %s: %r", sym, e)

        # ---- OPEN: strong reversal signals on ANY timeframe. Multiple
        # positions per symbol allowed (same direction), never opens the
        # OPPOSITE direction of a held position, capped per symbol. ----
        if cfg.get("enabled"):
            cycle_opened = {}   # sym -> directions opened THIS cycle
            for sym, hits in signals.items():
                dirs = open_pos.get(sym) or {}
                total = sum(len(cids) for by_tf in dirs.values() for cids in by_tf.values())
                if total >= _MAX_POSITIONS_PER_SYMBOL:
                    self._skip_reasons["at_position_cap"] += 1
                    continue
                for hit in hits:
                    # STRICT FLIP: restrict re-entries to the timeframe whose
                    # reversal closed a position on this symbol this cycle.
                    # Checked BEFORE the open cooldown is armed, so a skipped hit
                    # gets reconsidered next cycle instead of being penalised.
                    if self._strict_flip_blocks(sym, hit, closed_tfs, cfg):
                        self._skip_reasons["flip_other_tf"] += 1
                        continue
                    opposite = "SELL" if hit["rev"] == "BUY" else "BUY"
                    opened_dirs = cycle_opened.get(sym, set())
                    # no self-hedging: skip if we still hold the opposite direction
                    # (positions closed this cycle don't count as still held)
                    held_opposite = sum(
                        len(cids) for cids in dirs.get(opposite, {}).values()) - \
                        closed_dirs.get(sym, {}).get(opposite, 0)
                    if held_opposite > 0 or opposite in opened_dirs:
                        self._skip_reasons["would_hedge"] += 1
                        continue
                    ckey = (sym, hit["tf"])
                    if now - self._cooldown.get(ckey, 0) < _COOLDOWN:
                        self._skip_reasons["cooldown"] += 1
                        continue
                    # unblocked: min_strength; blacklisted: blocked_strength (60)
                    if hit["str"] < self._min_strength_for(sym):
                        continue
                    self._cooldown[ckey] = now
                    try:
                        if self._open_trade(sym, hit, cfg):
                            opened.append(f"{sym}:{hit['rev']}@{hit['str']}% ({hit['tf']})")
                            cycle_opened.setdefault(sym, set()).add(hit["rev"])
                    except Exception as e:
                        logger.warning("Auto-open failed for %s: %r", sym, e)

        self._set_last(at=now, opened=opened, closed=closed,
                       scan=sum(len(v) for v in candles_by.values()),
                       signals=n_signals, qualifying=n_qualifying,
                       refused=dict(self._skip_reasons),
                       message=(f"opened {len(opened)}, closed {len(closed)}"
                                if (opened or closed) else
                                f"idle · {n_signals} reversal signal(s), "
                                f"{n_qualifying} above min_strength "
                                f"({cfg.get('min_strength')}%) · "
                                + ("refused: " + ", ".join(
                                    f"{k}={v}" for k, v in self._skip_reasons.items())
                                   if self._skip_reasons else "no qualifying signal")))
        if opened or closed:
            logger.info("AutoTrader cycle: opened=%s closed=%s", opened, closed)
        elif n_signals:
            logger.info("AutoTrader cycle: %d signal(s), %d above min_strength, "
                        "refused=%s", n_signals, n_qualifying, dict(self._skip_reasons))

    # ------------------------------------------------------------- helpers
    def _strict_flip_blocks(self, sym, hit, closed_tfs, cfg):
        """True when a hit must be SKIPPED under the strict-flip rule.

        USER RULE (2026-09-20): when a position is closed by a reversal on
        (symbol, timeframe), the only opening allowed on that symbol in the same
        cycle is the opposite direction on THAT SAME timeframe. Because `signals`
        holds exactly one signal per (symbol, timeframe), matching the timeframe
        already guarantees the opposite direction — the hit that closed the
        position IS the only hit allowed to replace it.
        """
        if not cfg.get("strict_flip", True):
            return False
        tfs = closed_tfs.get(sym)
        if not tfs:
            return False
        return hit.get("tf") not in tfs

    def _set_last(self, **kw):
        with self._lock:
            self._last_cycle.update(kw)

    def _call(self, coro_factory, authenticated=False, timeout=45):
        """Run a coroutine over THIS AutoTrader's DEDICATED connection so its
        heavy scanning never starves the frontend's shared connection."""
        try:
            return self._conn.call(coro_factory, token=self._token, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            raise TimeoutError(f"Deriv request timed out after {timeout}s") from None

    def _open_equity(self):
        """Snapshot for the profit-target rule; returns a dict, or None on failure.

        Keys: cash (account balance), proceeds (sum of stake_i + live_profit_i
        over open contracts), stake (sum of stake_i), open_pnl (proceeds - stake
        == the live P/L of the open trades, i.e. the dashboard's "Net P/L"),
        n_open, cids.

        Fetched over the AutoTrader's dedicated auth connection with bounded
        concurrency; a contract whose live-P/L probe fails counts only its stake
        (neutral) so a slow probe never blocks the whole check.
        """
        try:
            async def _snap(api):
                port = await api.get_portfolio()
                pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
                contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
                bal_d = await api.get_balance()
                cash = 0.0
                b = bal_d.get("balance") if isinstance(bal_d, dict) else None
                if isinstance(b, dict):
                    cash = float(b.get("balance") or 0)
                sem = asyncio.Semaphore(6)

                async def _probe(c):
                    cid = c.get("contract_id")
                    if not cid:
                        return float(c.get("buy_price") or 0)
                    async with sem:
                        try:
                            poc = await api._send_request(
                                {"proposal_open_contract": 1, "contract_id": int(cid)})
                            pd = (poc or {}).get("proposal_open_contract") or {}
                            profit = float(pd.get("profit") or 0) if isinstance(pd, dict) else 0.0
                        except Exception:
                            profit = 0.0
                    return float(c.get("buy_price") or 0) + profit

                outs = await asyncio.gather(
                    *(_probe(c) for c in contracts), return_exceptions=True)
                proceeds = 0.0
                stakes = 0.0
                cids = []
                for c, o in zip(contracts, outs):
                    cid = c.get("contract_id")
                    if cid is not None:
                        cids.append(cid)
                    stake_i = float(c.get("buy_price") or 0)
                    stakes += stake_i
                    if isinstance(o, Exception):
                        proceeds += stake_i
                    else:
                        proceeds += o
                return {"cash": cash, "proceeds": proceeds, "stake": stakes,
                        "open_pnl": proceeds - stakes, "n_open": len(contracts),
                        "cids": cids}

            return self._call(_snap, authenticated=True, timeout=45)
        except Exception as e:
            logger.warning("Auto-trader equity fetch failed: %r", e)
            return None

    @staticmethod
    def _norm(candles):
        """Raw get_candles dicts -> list of trading_service.Candle."""
        out = []
        for c in candles or []:
            try:
                out.append(Candle(
                    timestamp=int(c.get("epoch") or c.get("timestamp") or 0),
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                ))
            except (TypeError, KeyError, ValueError):
                continue
        return out

    def _scan_candles(self):
        async def _scan(api):
            # Fire the full 70-fetch scan but cap concurrency so Deriv's
            # ticks_history rate limit is never blown in one burst (that caused
            # RateLimit 400s that broke chart loads).
            sem = asyncio.Semaphore(4)

            async def _one(s, tf):
                async with sem:
                    return await api.get_candles(s, self._tfs[tf], _SCAN_COUNT)

            # Scan EVERY symbol (incl. blacklisted) so a strong reversal on a
            # blacklisted market can still open a trade (>= blocked_strength).
            keys = [(s, tf) for s in self._symbols
                    for tf in self._scan_timeframes()]
            outs = await asyncio.gather(*(_one(s, tf) for (s, tf) in keys),
                                        return_exceptions=True)
            data = {}
            for (s, tf), res in zip(keys, outs):
                if isinstance(res, dict) and "candles" in res:
                    data.setdefault(s, {})[tf] = res["candles"]
            return data
        try:
            return self._pub_conn.call(_scan, token="", timeout=45)
        except Exception as e:
            logger.warning("Auto-trader scan failed: %r", e)
            return {}

    def _open_positions(self):
        """Return {symbol: {direction: {timeframe: [contract_ids]}}} of currently
        open positions. `timeframe` comes from the trade ledger (entry tf)."""
        out = {}
        try:
            async def _port(api):
                return await api.get_portfolio()
            port = self._call(_port, authenticated=True, timeout=30)
            pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
            contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
            for c in contracts:
                cid = c.get("contract_id")
                sym = c.get("underlying_symbol")
                ct = str(c.get("contract_type") or "").upper()
                if not cid or not sym:
                    continue
                direction = "SELL" if any(x in ct for x in ("DOWN", "PUT")) else "BUY"
                tf = self._ledger_tf(cid) or "any"
                out.setdefault(sym, {}).setdefault(direction, {}).setdefault(tf, []).append(cid)
        except Exception as e:
            logger.warning("Auto-trader portfolio fetch failed: %r", e)
        return out

    def _ledger_tf(self, contract_id):
        try:
            info = self._ledger.get(int(contract_id)) or {}
            return info.get("timeframe")
        except Exception:
            return None

    def _close_contracts(self, cids, reason="same-tf reversal"):
        """Sell a specific list of contracts (reversal exit / profit-target close)."""
        if not cids:
            return 0
        async def _close(api):
            outs = await asyncio.gather(
                *(api.sell_contract(cid) for cid in cids), return_exceptions=True)
            done = []
            for cid, o in zip(cids, outs):
                if not isinstance(o, Exception) and isinstance(o, dict) \
                        and "sell" in o and "error" not in o:
                    done.append(cid)
            return done
        done = self._call(_close, authenticated=True, timeout=45)
        # Tag the path records with WHY they closed. The learner's core question is
        # "which exit rule made money?", so a generic "closed" reason would blind
        # it to the difference between a reversal exit and a stop-out.
        tag = ("reversal_exit" if "reversal" in reason
               else "profit_target" if "profit" in reason
               else reason or "closed")
        for cid in done:
            self._pm.untrack(cid, reason=tag)
        if done:
            logger.info("Auto-trader closed %d position(s) (%s)", len(done), reason)
        return len(done)

    def _open_trade(self, symbol, hit, cfg):
        stake = float(cfg.get("stake", 1.0) or 1.0)
        direction = hit["rev"]
        tf = hit.get("tf")
        requested = int(cfg.get("multiplier", 100) or 100)
        multiplier = self._resolve_multiplier(symbol, requested)
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

        # ---- learner input: prune + per-market exit geometry -----------------
        # strategy_learner.py re-fits k_sl/k_tp per (symbol, timeframe) from the
        # bot's own recorded excursions and can prune a market whose fitted edge
        # is not positive out-of-sample. Until a market has >= 30 closed trades
        # under the current geometry the learner has no verdict, and these
        # lookups return nothing — so the global config applies unchanged.
        if self._pruned_fn:
            try:
                if self._pruned_fn(symbol, tf):
                    self._skip_reasons["pruned_by_learner"] += 1
                    logger.info("Auto-trade SKIPPED %s %s: pruned by the learner "
                                "(fitted edge not positive out-of-sample)", symbol, tf)
                    return False
            except Exception:
                pass
        eff_cfg = cfg
        if self._params_fn:
            try:
                learned = self._params_fn(symbol, tf)
            except Exception:
                learned = None
            if learned:
                eff_cfg = dict(cfg)
                if learned.get("sl_atr_k"):
                    eff_cfg["sl_atr_k"] = learned["sl_atr_k"]
                if learned.get("tp_atr_k"):
                    eff_cfg["tp_atr_k"] = learned["tp_atr_k"]

        # EXITS + EXPOSURE (USER RULE 2026-09-20): ATR-based market-distance
        # stop/target; the multiplier is capped to keep the risk <= max_risk_pct
        # of the stake. multiplier None => the symbol cannot support a real stop
        # at any valid multiplier, so we do NOT trade it.
        atr_pct = hit.get("atr")
        sl, tp, multiplier, info = self._exits(eff_cfg, stake, multiplier, atr_pct, symbol, tf)
        if multiplier is None:
            self._skip_reasons[info.get("skipped") or "no_affordable_stop"] += 1
            return False

        if cfg.get("paper"):
            logger.info("PAPER auto-trade %s %s @ %s%% (%s) lot=%.3f mult=%s SL=$%.2f TP=$%.2f "
                        "[%s stop %.2f%% = %.2fx ATR, risk %.0f%% of stake]",
                        direction, symbol, hit["str"], hit["tf"], stake, multiplier, sl, tp,
                        info.get("mode"), info.get("stop_move_pct") or 0,
                        (float(eff_cfg.get("sl_atr_k") or 0)) or 0,
                        (info.get("risk_pct") or 0) * 100)
            return True

        async def _buy(api):
            return await api.buy_multiplier(symbol, stake, direction, multiplier)

        result = self._call(_buy, authenticated=True, timeout=45)
        if isinstance(result, dict) and "buy" in result:
            cid = result["buy"]["contract_id"]
            # ALWAYS track: the monitor enforces SL/TP AND records the trade's
            # P/L path (MAE/MFE) that the learning layer is fitted on.
            # break_even/trail make the stop RATCHET UP as profit grows, so a
            # winner can no longer round-trip back to its original stop.
            self._pm.track(cid, symbol, sl, tp,
                break_even=bool(cfg.get("break_even_enabled")),
                trail=bool(cfg.get("trail_enabled")),
                break_even_pct=float(cfg.get("break_even_pct") or 0.5),
                trail_pct=float(cfg.get("trail_pct") or 0.5),
                meta={
                "stake": stake,
                "multiplier": multiplier,
                "direction": direction,
                "timeframe": hit["tf"],
                "signal_strength": hit.get("str"),
                # exit geometry, so the learner can fit stops per (symbol, tf)
                # without re-deriving ATR from the candles
                "atr_pct": info.get("atr_pct"),
                "stop_move_pct": info.get("stop_move_pct"),
                "risk_pct": info.get("risk_pct"),
            })
            self._record_trade(cid, symbol, contract_type, stake, multiplier, sl, tp,
                               hit["tf"], hit.get("str"))
            if cfg.get("native_sl_tp", True):
                if self._apply_native_limits(cid, sl, tp, stake, symbol):
                    self._native_done.add(cid)
            logger.info("Auto-trade opened %s %s @ %s%% lot=%.3f mult=%s -> cid=%s "
                        "SL=$%.2f TP=$%.2f [%s stop %.2f%% of price = %.1fx ATR, risk %.1f%% of stake]",
                        direction, symbol, hit["str"], stake, multiplier, cid, sl, tp,
                        info.get("mode"), info.get("stop_move_pct") or 0,
                        float(eff_cfg.get("sl_atr_k") or 0),
                        (info.get("risk_pct") or 0) * 100)
            return True
        logger.warning("Auto-trade rejected for %s: %s", symbol, result)
        self._skip_reasons["deriv_rejected"] += 1
        return False

    def _apply_native_limits(self, cid, sl, tp, stake, symbol=""):
        """Put the stop/target ON THE CONTRACT (Deriv `contract_update`).

        Envelope, units and limits verified live in `_probe_update.py`:
          {"contract_update": 1, "contract_id": int,
           "limit_order": {"stop_loss": <positive $>, "take_profit": <positive $>}}
        `stop_loss`/`take_profit` are POSITIVE amounts; the response echoes the
        stop SIGNED, so a response value must never be fed back in.

        Failures are LOUD but non-fatal: the position monitor is still running as
        the backstop, so a rejected limit degrades to the old behaviour rather
        than leaving the position bare. The clamp below avoids the two documented
        rejections (`LimitOrderAmountTooLow` under 0.10, and anything above the
        stake, where Deriv's own -100% "stop_out" already sits).
        """
        if not sl and not tp:
            return False
        limit = {}
        try:
            if sl:
                limit["stop_loss"] = round(max(0.10, min(float(sl), float(stake) * 0.98)), 2)
            if tp:
                limit["take_profit"] = round(max(0.10, float(tp)), 2)
        except (TypeError, ValueError):
            return False

        async def _upd(api):
            return await api.update_contract_limits(
                cid, limit.get("stop_loss"), limit.get("take_profit"))

        try:
            res = self._call(_upd, authenticated=True, timeout=25)
        except Exception as e:
            logger.warning("Native SL/TP call failed for %s %s: %r "
                           "(monitor still enforcing)", symbol, cid, e)
            return False
        if isinstance(res, dict) and res.get("error"):
            err = res["error"]
            logger.warning("Native SL/TP REJECTED for %s %s: %s (monitor still "
                           "enforcing)", symbol, cid,
                           err.get("message") if isinstance(err, dict) else err)
            return False
        logger.info("Native SL/TP SET on %s %s: stop_loss=$%.2f take_profit=$%.2f "
                    "(Deriv now enforces these; the monitor is the backstop and "
                    "records the P/L path)", symbol, cid,
                    limit.get("stop_loss") or 0.0, limit.get("take_profit") or 0.0)
        return True

    def _ledger_multiplier(self, contract_id):
        try:
            info = self._ledger.get(int(contract_id)) or {}
            return info.get("multiplier")
        except Exception:
            return None

    def _tracked_by_monitor(self, contract_id):
        try:
            return self._pm.is_tracked(contract_id)
        except Exception:
            return False

    def _atr_for(self, pairs):
        """ATR% per (symbol, timeframe) pair, best-effort over the public conn.

        The ATR MUST be read on the position's OWN timeframe. A 1m ATR is a much
        smaller number than a 30m ATR, so reading 1m for everything gave adopted
        longer-timeframe positions a stop several times TIGHTER than the one they
        were opened with. Observed live 2026-09-20 on the first restart after the
        15m/30m rollout: 3 positions were adopted and stopped out at -0.12 of
        stake when their intended stops were -0.47/-0.66 (caps 55%/80%).
        """
        out = {}
        wanted = sorted({(s, tf) for (s, tf) in pairs if s})
        if not wanted:
            return out
        try:
            async def _fetch(api):
                got = {}
                for s, tf in wanted:
                    try:
                        secs = self._tfs.get(tf) or self._tfs.get("1m", 60)
                        res = await api.get_candles(s, secs, 60)
                        cs = self._norm(res.get("candles") if isinstance(res, dict) else None)
                        a = self._atr_pct(cs)
                        if a:
                            got[(s, tf)] = a
                    except Exception:
                        continue
                return got
            out = self._pub_conn.call(_fetch, token="", timeout=60) or {}
        except Exception as e:
            logger.debug("Adopt ATR fetch failed: %r", e)
        return out

    def _adopted_exits(self, cfg, stake, multiplier, atr_pct, timeframe=None):
        """(stop_loss$, take_profit$) for a position this process did NOT open.

        An existing position's multiplier is already fixed and cannot be
        re-chosen, so the only question is: is a noise-clear stop affordable at
        THAT multiplier? If it would cost more than `max_risk_pct` of the stake,
        return (None, None) — the caller then leaves the position UNPROTECTED and
        warns, rather than installing a stop inside the noise (which is what
        stopped out 17/17 trades in the stake-relative build).
        """
        if not atr_pct:
            return None, None
        try:
            stake_f = float(stake or 0)
            mult_f = float(multiplier or 0)
            k_sl = float(cfg.get("sl_atr_k") or 1.5)
            k_tp = float(cfg.get("tp_atr_k") or 6.0)
            floor = float(cfg.get("min_stop_move_pct") or 0)
        except (TypeError, ValueError):
            return None, None
        max_risk = self._risk_cap(cfg, timeframe)
        if not max_risk:
            return None, None
        if mult_f <= 0 or stake_f <= 0 or k_sl <= 0:
            return None, None
        stop_move = max(k_sl * float(atr_pct), floor)
        if (mult_f * stop_move / 100.0) > max_risk:
            return None, None
        tp_move = stop_move * (k_tp / k_sl)
        sl = min(stake_f, round(stake_f * mult_f * stop_move / 100.0, 2))
        tp = round(stake_f * mult_f * tp_move / 100.0, 2)
        return sl, tp

    def _adopt_untracked(self, cfg):
        """Give SL/TP protection to open positions this process isn't tracking.

        Positions opened before a restart (or outside the bot) would otherwise
        keep no protection at all. Since 2026-09-20 the adopted stop uses the
        SAME ATR geometry as new trades — a position whose symbol/multiplier
        cannot support a noise-clear stop is left untracked and reported, because
        installing a stake-relative stop on it just guarantees a stop-out (17/17
        closed positions did exactly that in the stake-relative build).
        Idempotent: ids already tracked are skipped, so it never double-tracks.

        Also refuses to LIQUIDATE on adoption: see `adopt_mode`. A stop computed
        from ENTRY is frequently already breached by a long-running position's
        current P/L, so installing it would sell the position the instant it is
        adopted — converting a restart into realised losses.
        """
        try:
            async def _port(api):
                return await api.get_portfolio()
            port = self._call(_port, authenticated=True, timeout=30)
        except Exception as e:
            logger.warning("Auto-trader adopt: portfolio fetch failed: %r", e)
            return 0
        pt = port.get("portfolio", {}) if isinstance(port, dict) else {}
        contracts = pt.get("contracts", []) if isinstance(pt, dict) else []
        pending = [c for c in contracts
                   if c.get("contract_id") and not self._tracked_by_monitor(c.get("contract_id"))]

        # MIRROR native limits onto positions the monitor ALREADY protects.
        # Without this the sweep only ever helps UNTRACKED positions, so the ~28
        # restored at boot (and everything opened before this feature existed)
        # would keep monitor-only protection — i.e. they lose their stop the
        # moment this process dies, which is the exact failure mode native limits
        # exist to remove.
        # Capped per sweep: a rejected contract stays out of `_native_done` and is
        # retried next time, and bursting ~30 contract_update calls at once is how
        # this account's quotas get exhausted (see the proposal_open_contract
        # rate-limit notes). 8/sweep = every position covered within ~20 minutes.
        mirrored, NATIVE_PER_SWEEP = 0, 8
        if cfg.get("native_sl_tp", True):
            for c in contracts:
                if mirrored >= NATIVE_PER_SWEEP:
                    break
                cid = c.get("contract_id")
                if not cid or cid in self._native_done:
                    continue
                lim = self._pm.limits_for(cid)
                if not lim or not (lim["stop_loss"] or lim["take_profit"]):
                    continue
                if self._apply_native_limits(cid, lim["stop_loss"], lim["take_profit"],
                                             lim.get("stake") or cfg.get("stake") or 1.0,
                                             lim.get("symbol") or ""):
                    self._native_done.add(cid)
                    mirrored += 1
            if mirrored:
                logger.info("Auto-trader mirrored native SL/TP onto %d already-protected "
                            "position(s) (Deriv enforces them now without our polling loop)",
                            mirrored)
        if not pending:
            return 0
        # Use each position's OWN timeframe (from the ledger) for its ATR — see
        # _atr_for for why reading 1m everywhere is wrong.
        tf_of = {c.get("contract_id"): (self._ledger_tf(c.get("contract_id")) or "1m")
                 for c in pending}
        atrs = self._atr_for({(c.get("underlying_symbol"), tf_of[c.get("contract_id")])
                              for c in pending})
        adopted, unprotected = 0, []
        for c in pending:
            cid = c.get("contract_id")
            sym = c.get("underlying_symbol") or ""
            tf = tf_of.get(cid) or "1m"
            try:
                stake = float(c.get("buy_price") or 0) or float(cfg.get("stake") or 1.0)
            except (TypeError, ValueError):
                stake = float(cfg.get("stake") or 1.0)
            sl, tp = self._adopted_exits(cfg, stake, self._ledger_multiplier(cid),
                                         atrs.get((sym, tf)), tf)
            if sl is None:
                unprotected.append(f"{sym}({self._ledger_multiplier(cid)}x)")
                continue
            # ALREADY-BREACHED GUARD (user rule 2026-09-21, see `adopt_mode`).
            # `sl` is a stop distance from ENTRY; this position has been running a
            # while, so its current P/L may already be past it and installing that
            # stop would liquidate it instantly. A restart must never be the thing
            # that decides to realise a loss.
            cur = None
            try:
                if c.get("profit") is not None:
                    cur = float(c.get("profit"))
            except (TypeError, ValueError):
                cur = None
            mode = str(cfg.get("adopt_mode") or "forward").lower()
            if cur is not None and cur <= -sl + 1e-9 and mode != "strict":
                # Forward anchor: one full risk budget of headroom from HERE, so it
                # cannot fire on the next poll. If that would sit beyond Deriv's own
                # -100% close there is no usable stop left, so report instead of
                # pretending to protect it.
                if mode == "skip" or (cur - sl) <= -stake:
                    unprotected.append(
                        f"{sym}({self._ledger_multiplier(cid)}x)@{-cur:+.2f}"
                        + ("" if mode == "skip" else " no-stop-room"))
                    continue
                logger.info("Auto-trader adopting %s (%sx, %s) with a FORWARD stop: "
                            "already at %+.2f, so instead of selling instantly at the "
                            "entry-anchored -%.2f the stop is anchored one ATR of risk "
                            "from here (%.2f)", sym, self._ledger_multiplier(cid), tf,
                            cur, sl, cur - sl)
                sl = round(abs(cur - sl), 2)
            ct = str(c.get("contract_type") or "").upper()
            direction = "SELL" if any(x in ct for x in ("DOWN", "PUT")) else "BUY"
            self._pm.track(cid, sym, sl, tp, meta={
                "stake": stake,
                "multiplier": self._ledger_multiplier(cid),
                "direction": direction,
                "timeframe": tf,
                "adopted": True,
                "atr_pct": (round(float(atrs[(sym, tf)]), 4) if atrs.get((sym, tf)) else None),
            })
            if cfg.get("native_sl_tp", True):
                self._apply_native_limits(cid, sl, tp, stake, sym)
            adopted += 1
        if unprotected:
            logger.warning(
                "Auto-trader found %d open position(s) it CANNOT protect: %s — at "
                "their multiplier no noise-clear stop fits inside %.0f%% of the "
                "stake, so only Deriv's -100%% close would end them. Consider "
                "closing them (Close All) or letting them run to target.",
                len(unprotected), ", ".join(sorted(set(unprotected))[:8]), 30.0)
        if adopted:
            logger.info("Auto-trader adopted %d untracked position(s) into SL/TP protection",
                        adopted)
        return adopted
