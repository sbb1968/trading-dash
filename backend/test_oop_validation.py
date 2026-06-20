"""
test_oop_validation.py
──────────────────────
Obligatoriske tests for OOP-validerings-pipelinen (point-in-time univers + filter).
Kører UDEN daily_cache/TWS — kun syntetiske bars + in-memory metrikker.

Dækker SPEC'ens 5 krav:
  1. Uge-ATR look-ahead-ren (kun indeks < i).
  2. chg1d top-25 korrekt + tie-break ticker-alfabetisk.
  3. random = matchet n + reproducerbar (fast seed).
  4. filter_events_pit dropper/beholder korrekt; None = alt.
  5. build_pool(start,end) udelukker dage uden for vinduet.

Kør:  python test_oop_validation.py
"""

import random
from datetime import date

import screener_lab as sl
from screener_lab import _weekly_atr_pct, build_pool
from velocity_backtest import Event, filter_events_pit


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


def _bar(d, o, h, l, c, v=1_000_000):
    return (date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + d), o, h, l, c, v)


def _m(price=10.0, chg1d=0.0, atr=8.0, avg_vol=1_000_000, green=3):
    """Metrik-dict som build_pool læser."""
    return {"price": price, "atr_pct": atr, "atr_pct_1w": atr, "avg_vol": avg_vol,
            "rvol": 1.0, "chg1d": chg1d, "chg1w": 0.0, "chg1m": 0.0, "green": green}


def main():
    print("Test: OOP-validering (point-in-time univers + filter)")

    # ── 1. Uge-ATR look-ahead-ren ────────────────────────────────────────────
    # Byg ~90 daglige bars; beregn _weekly_atr_pct ved i, mutér så bars[i:] til
    # ekstreme værdier og bekræft resultatet er IDENTISK (kun indeks < i bruges).
    base = [_bar(k, 10 + 0.1 * k, 10.5 + 0.1 * k, 9.5 + 0.1 * k, 10 + 0.1 * k) for k in range(90)]
    i = 85
    atr_before = _weekly_atr_pct(base, i)
    mutated = list(base)
    for k in range(i, len(mutated)):                      # ekstreme dag-i-og-frem
        d0 = mutated[k]
        mutated[k] = (d0[0], d0[1], 9999.0, -9999.0, d0[4], d0[5])
    atr_after = _weekly_atr_pct(mutated, i)
    check("1 uge-ATR look-ahead-ren (bars[i:] ændrer ikke resultatet)",
          atr_before == atr_after and atr_before > 0, f"{atr_before} vs {atr_after}")

    # ── 2. chg1d top-N korrekt + tie-break alfabetisk ────────────────────────
    D = date(2026, 1, 20)
    # ZED og AAA har SAMME chg1d (5.0) → tie-break: alfabetisk (AAA før ZED).
    metrics = {
        "MID":  {D: _m(chg1d=3.0)},
        "HIGH": {D: _m(chg1d=9.0)},
        "AAA":  {D: _m(chg1d=5.0)},
        "ZED":  {D: _m(chg1d=5.0)},
        "LOW":  {D: _m(chg1d=-2.0)},
    }
    defn = dict(name="t", price=(5, 50), atr_pct_min=5.0, avg_vol_min=500_000,
                momentum_min_green=0, rank_by="chg1d", top_n=3)
    pool = build_pool(metrics, defn)
    check("2 chg1d top-3 = [HIGH, AAA, ZED] (desc + alfa-tie-break)",
          pool[D] == ["HIGH", "AAA", "ZED"], pool[D])

    # ── 3. random reproducerbar + matchet n ──────────────────────────────────
    big = {f"T{n:03d}": {D: _m(chg1d=float(n))} for n in range(40)}
    rdefn = dict(name="r", price=(5, 50), atr_pct_min=5.0, avg_vol_min=500_000,
                 momentum_min_green=0, rank_by="random", top_n=25)
    p1 = build_pool(big, rdefn, rng=random.Random(42))
    p2 = build_pool(big, rdefn, rng=random.Random(42))
    check("3a random m. fast seed reproducerbar", p1[D] == p2[D], (p1[D][:3], p2[D][:3]))
    cdefn = dict(rdefn, rank_by="chg1d")
    pc = build_pool(big, cdefn)
    check("3b random = matchet navn-dage (samme eligible+top_n som chg1d)",
          len(p1[D]) == len(pc[D]) == 25, (len(p1[D]), len(pc[D])))

    # ── 4. filter_events_pit ─────────────────────────────────────────────────
    barsA, barsB = object(), object()   # dummy bar-objekter (kun id/identitet bruges)
    evA = Event(idx=0, day=D, direction="up")
    evB = Event(idx=0, day=D, direction="up")
    inp = [("AAA", barsA, [evA]), ("BBB", barsB, [evB])]
    universe = {D: {"AAA"}}
    flat = filter_events_pit(inp, universe)
    check("4a univers-filter beholder kun AAA på dag D",
          [s for s, _b, _e in flat] == ["AAA"], [s for s, _b, _e in flat])
    flat_none = filter_events_pit(inp, None)
    check("4b universe=None beholder alt", len(flat_none) == 2, len(flat_none))
    # event på dag UDEN for universets nøgler → droppes (universe.get → tom)
    evOther = Event(idx=0, day=date(2026, 1, 21), direction="up")
    flat_other = filter_events_pit([("AAA", barsA, [evOther])], universe)
    check("4c event på dag uden univers-nøgle droppes", flat_other == [], flat_other)

    # ── 5. build_pool(start, end) udelukker dage uden for vinduet ────────────
    D1, D2, D3 = date(2026, 1, 10), date(2026, 1, 20), date(2026, 1, 30)
    multi = {"AAA": {D1: _m(chg1d=1.0), D2: _m(chg1d=1.0), D3: _m(chg1d=1.0)}}
    rngpool = build_pool(multi, defn, start=D2, end=D2)
    check("5 date-range [D2,D2] udelukker D1 og D3",
          list(rngpool.keys()) == [D2], list(rngpool.keys()))

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()
