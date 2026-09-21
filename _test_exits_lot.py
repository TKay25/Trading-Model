"""Prove `_exits` returns a LOT-SIZE stop in the new mode.

The monitor test (_test_lot_be.py) proves the ENFORCEMENT rule. This proves what
_OPENING a trade now hands to that monitor: stop_loss == the whole lot, with the
multiplier still chosen by the ATR ceiling (which is what keeps the -100% close
outside normal noise) and the ATR target still the take profit.

Called UNBOUND with a dummy `self`, so it tests the real method without needing a
fully wired AutoTrader (the only instance method _exits uses is _multipliers_for;
_risk_cap is a staticmethod).
"""
import auto_trader as at


class Dummy:
    # _exits reaches for self._risk_cap, which is a staticmethod on AutoTrader.
    _risk_cap = staticmethod(at.AutoTrader._risk_cap)

    def _multipliers_for(self, symbol):
        return [40, 100, 200, 300, 400]


CFG = {"stop_mode": "lot", "sl_atr_k": 1.5, "tp_atr_k": 6.0,
       "min_stop_move_pct": 0.05, "max_risk_pct": 0.8,
       "risk_by_tf": {"1m": 0.20, "5m": 0.30, "15m": 0.55, "30m": 0.0}}

print("stake=1.00, symbol 1HZ100V, atr 0.801% (15m-style)\n")
sl, tp, mult, info = at.AutoTrader._exits(Dummy(), CFG, 1.0, 100, 0.801, "1HZ100V", "15m")
print(f"  LOT mode   -> stop_loss=${sl:.2f}  take_profit=${tp:.2f}  mult={mult}")
print(f"                mode={info.get('mode')}  risk_pct={info.get('risk_pct')} "
      f"(= {round((info.get('risk_pct') or 0) * 100)}% of stake, the whole lot)")
print(f"                {info.get('note')}")

# The effective -100% distance should stay outside the noise: the ATR stop costs
# `cap` of the stake, so the -100% close sits 1.5/cap ATR away.
cap = 0.55
print(f"\n  -100% close sits {1.5 / cap:.1f}x ATR away at the 15m cap of {cap:.0%}")

# Same trade in ATR mode, for contrast.
CFG2 = dict(CFG, stop_mode="atr")
sl2, tp2, mult2, info2 = at.AutoTrader._exits(Dummy(), CFG2, 1.0, 100, 0.801, "1HZ100V", "15m")
print(f"\n  ATR mode   -> stop_loss=${sl2:.2f}  take_profit=${tp2:.2f}  mult={mult2} "
      f"(risk {round((info2.get('risk_pct') or 0) * 100)}% of stake)")

# A disabled timeframe must still be refused in lot mode.
sl3, tp3, mult3, info3 = at.AutoTrader._exits(Dummy(), CFG, 1.0, 100, 0.801, "1HZ100V", "30m")
print(f"\n  30m (cap 0) -> multiplier={mult3}  skipped={info3.get('skipped')}")

ok = (abs(sl - 1.0) < 1e-9 and mult is not None and mult3 is None
      and info.get("mode") == "lot")
print("\nRESULT:", "PASS" if ok else "FAIL",
      f"(lot stop={sl}, 30m refused={mult3 is None})")
