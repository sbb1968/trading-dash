"""
test_strategy_base.py
─────────────────────
Test af de hoistede base-metoder _parse_bar + _fetch_bars (Hoisting Batch A, trin 2).
Kalder metoderne på et minimalt fake-self (ingen TWS, ingen fuld __init__).

Kør:  python test_strategy_base.py
"""

import asyncio
import types
from datetime import datetime

from strategy_base import BaseStrategy, Bar


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


class _Conn:
    """Mock IBKR-conn: returnér ret, eller rejs exception."""
    def __init__(self, ret=None, exc=False):
        self._ret = ret
        self._exc = exc
    async def get_historical_bars(self, ticker, duration, bar_size, what_to_show="TRADES"):
        if self._exc:
            raise RuntimeError("boom")
        return self._ret


def _fake(conn=None):
    """Minimalt self med name + conn + bundet _parse_bar (kalder base'ens)."""
    ns = types.SimpleNamespace(name="TEST", conn=conn)
    ns._parse_bar = lambda raw: BaseStrategy._parse_bar(ns, raw)
    return ns


def _bar_dict(c=10.0):
    return {"datetime": datetime(2026, 6, 17, 9, 30), "open": 9.0, "high": 11.0,
            "low": 8.0, "close": c, "volume": 1000}


def main():
    print("Test: BaseStrategy._parse_bar + _fetch_bars (hoist Batch A trin 2)")

    # ── _parse_bar ──
    b = BaseStrategy._parse_bar(_fake(), _bar_dict(10.5))
    check("_parse_bar velformet dict → Bar", isinstance(b, Bar), type(b))
    check("_parse_bar felter korrekte", b and b.close == 10.5 and b.open == 9.0 and b.volume == 1000.0,
          (b.close, b.open, b.volume) if b else None)
    check("_parse_bar naiv ts lokaliseret til ET (tz-aware)", b and b.timestamp.tzinfo is not None)

    bad_ts = {"datetime": "ikke-en-dato", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1}
    check("_parse_bar ikke-datetime ts → None", BaseStrategy._parse_bar(_fake(), bad_ts) is None)

    bad_close = {"datetime": datetime(2026, 6, 17, 9, 30), "open": 1, "high": 2, "low": 0,
                 "close": None, "volume": 1}
    check("_parse_bar defekt close → None (kaster ikke)", BaseStrategy._parse_bar(_fake(), bad_close) is None)

    # ── _fetch_bars ──
    ns = _fake(_Conn(ret=[_bar_dict(10.0), _bar_dict(20.0)]))
    out = asyncio.run(BaseStrategy._fetch_bars(ns, "X", duration="1 D", bar_size="1 min"))
    check("_fetch_bars 2 dicts → 2 Bars", len(out) == 2 and all(isinstance(x, Bar) for x in out), out)
    check("_fetch_bars rækkefølge bevaret", out[0].close == 10.0 and out[1].close == 20.0,
          [x.close for x in out])

    ns = _fake(_Conn(exc=True))
    check("_fetch_bars conn rejser → []", asyncio.run(BaseStrategy._fetch_bars(ns, "X", duration="1 D", bar_size="1 min")) == [])

    ns = _fake(_Conn(ret=[]))
    check("_fetch_bars tom → []", asyncio.run(BaseStrategy._fetch_bars(ns, "X", duration="1 D", bar_size="1 min")) == [])

    ns = _fake(_Conn(ret=[_bar_dict(10.0), bad_close]))
    out = asyncio.run(BaseStrategy._fetch_bars(ns, "X", duration="1 D", bar_size="1 min"))
    check("_fetch_bars filtrerer defekte bars fra (1 gyldig af 2)", len(out) == 1 and out[0].close == 10.0, out)

    # ── _preflight_connection_and_account ──
    class _AcctConn:
        def __init__(self, connected=True, nlv=100000.0):
            self.connected = connected
            self._nlv = nlv
        def get_account_summary(self):
            return {"net_liquidation": self._nlv}

    def _pf(connected=True, nlv=100000.0):
        return types.SimpleNamespace(_status=lambda *a, **k: None, _risk_manager=None,
                                     conn=_AcctConn(connected, nlv))

    ok, err, checks = asyncio.run(BaseStrategy._preflight_connection_and_account(_pf()))
    check("preflight forbundet + NLV>0 → ok + 2 checks",
          ok and err == "" and checks[0] == "IBKR forbundet"
          and checks[1].startswith("Konto aktiv — NLV: $"), (ok, err, checks))
    ok, err, checks = asyncio.run(BaseStrategy._preflight_connection_and_account(_pf(connected=False)))
    check("preflight ikke forbundet → (False, 'IBKR ikke forbundet', [])",
          ok is False and err == "IBKR ikke forbundet" and checks == [], (ok, err, checks))
    ok, err, checks = asyncio.run(BaseStrategy._preflight_connection_and_account(_pf(nlv=0)))
    check("preflight NLV=0 → (False, 'Ingen konto-data', ['IBKR forbundet'])",
          ok is False and err == "Ingen konto-data" and checks == ["IBKR forbundet"], (ok, err, checks))

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()
