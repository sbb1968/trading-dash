#!/usr/bin/env python3
"""
intradag_report.py (trin 4) — orkestrator: lag + gate + baand.

Spejler swing_report.py 1:1. Eet strukturelt afvig (renormalisering for pending
lag) + to domaene-deltas i gaten. Alt andet er kopieret fra swings combine/gate/
overlay/baand (verificeret i produktion).

Kombinerer de tre intradag-lag til EET tal:
    teknisk  (compute_technical_intraday) — verificeret 7/7 mod Pine
    forsyning (compute_supply)            — float-centreret (commit 33d9736)
    katalysator (compute_catalyst_intraday) — STUBBET pending (trin 7)

    combined = SUM( w_renorm[lag] * lag_score[lag] )   for lag hvor lag_score not None
    final    = clamp(combined * gate - (1 - gate) * GATE_STRAF, -100, +100)

Ingen krydsimport fra swing. ParamResult + _band kommer fra technical_intraday.
ASCII i kode + konsol (cp1252).
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

import pandas as pd

from technical_intraday import (
    compute_technical_intraday, format_technical_report, ParamResult, _band,
)
from supply_score import compute_supply, format_supply_report
from catalyst_intraday import compute_catalyst_intraday, fetch_catalyst, format_catalyst_report
from data_source_intraday import (
    fetch_intraday_bars, fetch_benchmark_bars, fetch_daily_aggregates,
    fetch_supply, fetch_spread,
)


# === Konstanter ============================================================
# Teknisk tungest (den verificerede opstilling); katalysator nr. 2 (nyhed/halt er
# ofte AARSAGEN til at en small-cap loeber); forsyning bekraefter. Med katalysator
# pending renormaliserer det til teknisk 0.80 / forsyning 0.20.
LAG_WEIGHTS = {"technical": 0.60, "supply": 0.15, "catalyst": 0.25}
MANUAL_TECH_SHARE = 0.12   # manuel chart-overlay = 12 % af det tekniske lag (default None = FRA)
GATE_STRAF = 40.0          # gate-straf: lav handelbarhed skubber final NEDAD mod Fraraades
LABEL = {"technical": "Teknisk", "supply": "Forsyning", "catalyst": "Katalysator"}


# === Gate ==================================================================
def _gate_thresholds(market_cap: Optional[float]):
    """Likviditets-taerskler skaleret efter market cap. DELTA 1: ukendt cap ->
    small-cap-fallback (150k/$2M), ikke swings generiske 500k/$20M (vores univers
    ER small-caps; generiske taersker over-gater en legitim small-cap)."""
    if market_cap is None:
        return 150_000, 2_000_000
    if market_cap < 300e6:
        return 150_000, 2_000_000        # micro
    if market_cap < 2e9:
        return 300_000, 6_000_000        # small
    if market_cap < 10e9:
        return 500_000, 15_000_000       # mid
    return 750_000, 30_000_000           # large


def compute_gate(adv_shares: Optional[float] = None,
                 dollar_vol: Optional[float] = None,
                 price: Optional[float] = None,
                 spread_pct: Optional[float] = None,
                 market_cap: Optional[float] = None,
                 halted: Optional[bool] = None) -> float:
    """0..1. Mindste delfaktor styrer (bindende flaskehals). spread_pct i DECIMAL
    (0.01 = 1 %). DELTA 2: halted=True -> 0.0 (haltet aktie ikke handelbar)."""
    if halted is True:
        return 0.0
    adv_full, dol_full = _gate_thresholds(market_cap)
    f = []
    if adv_shares is not None:
        f.append(min(1.0, adv_shares / adv_full))
    if dollar_vol is not None:
        f.append(min(1.0, dollar_vol / dol_full))
    if price is not None:
        f.append(0.0 if price < 1 else 1.0)
    if spread_pct is not None:
        f.append(max(0.0, 1.0 - spread_pct))     # 0 % = 1, >=1 % = 0
    return round(min(f), 3) if f else 1.0


def _binding_gate_factor(gi: dict) -> str:
    """Hvilken delfaktor var bindende (laveste) — til rapport-visning."""
    if gi.get("halted") is True:
        return "HALTET (0)"
    adv_full, dol_full = _gate_thresholds(gi.get("market_cap"))
    cands = []
    if gi.get("adv20") is not None:
        cands.append(("adv", min(1.0, gi["adv20"] / adv_full)))
    if gi.get("dollar_vol") is not None:
        cands.append(("dollar-vol", min(1.0, gi["dollar_vol"] / dol_full)))
    if gi.get("price") is not None:
        cands.append(("pris<$1" if gi["price"] < 1 else "pris", 0.0 if gi["price"] < 1 else 1.0))
    if gi.get("spread_pct") is not None:
        cands.append((f"spread {gi['spread_pct']*100:.1f}%", max(0.0, 1.0 - gi["spread_pct"])))
    if not cands:
        return "ingen gate-input"
    name, val = min(cands, key=lambda x: x[1])
    return f"{name} bindende ({val:.3f})"


# === Manuel overlay (1:1 swing) ============================================
def manual_overlay(sr: Optional[float] = None,
                   chart_pattern: Optional[float] = None,
                   candlestick: Optional[float] = None) -> Optional[float]:
    parts = []
    if sr is not None:
        parts.append((0.40, sr))
    if chart_pattern is not None:
        parts.append((0.40, chart_pattern))
    if candlestick is not None:
        parts.append((0.20, candlestick))
    if not parts:
        return None
    tw = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / tw


# === Combine (swing + renormalisering for pending lag) =====================
def combine(layers: dict, gate: float = 1.0, manual: Optional[float] = None) -> dict:
    """layers: {'technical','supply','catalyst'} fra compute_*(). Lag med
    lag_score=None (fx pending katalysator) renormaliseres UD — samme moenster
    lagene selv bruger internt."""
    tech = layers["technical"]["lag_score"]
    if tech is not None and manual is not None:
        tech = (1 - MANUAL_TECH_SHARE) * tech + MANUAL_TECH_SHARE * manual

    raw = {
        "technical": tech,
        "supply": layers["supply"]["lag_score"],
        "catalyst": layers["catalyst"]["lag_score"],
    }
    present = {k: v for k, v in raw.items() if v is not None}
    if not present:
        combined, w_renorm = None, {}
    else:
        wsum = sum(LAG_WEIGHTS[k] for k in present)
        w_renorm = {k: LAG_WEIGHTS[k] / wsum for k in present}
        combined = sum(w_renorm[k] * present[k] for k in present)

    gate_straf = (1.0 - gate) * GATE_STRAF
    if combined is None:
        final = None
    else:
        final = combined * gate - gate_straf
        final = max(-100.0, min(100.0, final))

    # Drivere bruger den RENORMALISEREDE lagvaegt (afspejler faktisk bidrag)
    drivers = []   # (layer, navn, global_bidrag, signal)
    for layer, res in layers.items():
        lw = w_renorm.get(layer)
        if lw is None:
            continue
        for r in res["results"]:
            drivers.append((layer, r.name, lw * r.weighted, r.signal))

    return {"raw": raw, "weights_used": w_renorm, "combined": combined,
            "gate": gate, "gate_straf": gate_straf, "final": final,
            "drivers": drivers, "manual": manual}


# === Orkestrator (async, spejler _compute_swing) ===========================
async def compute_intradag_report(
        ib, symbol: str, *,
        timeframe: str = "5 mins", duration: str = "5 D",
        daily_duration: str = "45 D", spy: str = "SPY", end: str = "",
        sr: Optional[float] = None,
        chart_pattern: Optional[float] = None,
        candlestick: Optional[float] = None,
        spy_bars=None,          # NY: hvis givet, spring SPY-fetch over (scanneren deler SPY)
        supply_data=None) -> dict:   # NY: hvis givet, spring fetch_supply over (scan har TV-float)

    # 1. Data (spy_bars/supply_data kan vaere genbrugt af scanneren -> spring fetch over)
    bars = await fetch_intraday_bars(ib, symbol, timeframe=timeframe, duration=duration, end=end)
    if spy_bars is None:
        spy_bars = await fetch_benchmark_bars(ib, symbol=spy, timeframe=timeframe, duration=duration, end=end)
    daily = await fetch_daily_aggregates(ib, symbol, duration=daily_duration, end=end)

    # 2. Lag
    tech_res = compute_technical_intraday(bars, spy_bars, daily)
    if supply_data is None:
        # fetch_supply er et SYNC TV-screener-HTTP-kald -> to_thread saa det ikke
        # blokerer appens event-loop (samme backend kan koere LIVE algoer).
        supply_data = await asyncio.to_thread(fetch_supply, symbol)
    supply_res = compute_supply(supply_data)
    cat_res = compute_catalyst_intraday(fetch_catalyst(symbol))
    layers = {"technical": tech_res, "supply": supply_res, "catalyst": cat_res}

    # 3. Gate-input
    last_close = float(bars["close"].iloc[-1]) if len(bars) else None
    adv20 = None
    if "adv20" in daily.columns and len(daily):
        v = daily["adv20"].iloc[-1]
        adv20 = float(v) if pd.notna(v) else None
    dollar_vol = (adv20 * last_close) if (adv20 and last_close) else None
    spread_pct = await fetch_spread(ib, symbol)
    market_cap = supply_data.get("market_cap")     # None indtil evt. tilfoejet -> fallback
    gate = compute_gate(adv_shares=adv20, dollar_vol=dollar_vol, price=last_close,
                        spread_pct=spread_pct, market_cap=market_cap, halted=None)

    # 4. Manuel overlay (default None)
    manual = manual_overlay(sr=sr, chart_pattern=chart_pattern, candlestick=candlestick)

    # 5. Kombinér
    comb = combine(layers, gate=gate, manual=manual)

    asof = bars.index[-1].isoformat() if len(bars) else None
    return {
        "symbol": symbol, "asof": asof, "price": last_close, "timeframe": timeframe,
        "float_shares": supply_data.get("float_shares"),   # surfaces til report_to_json
        "float_pct": supply_data.get("float_pct"),
        "final": comb["final"], "band": _band(comb["final"]),
        "combined": comb["combined"], "weights_used": comb["weights_used"],
        "gate": gate, "gate_straf": comb["gate_straf"],
        "gate_inputs": {"adv20": adv20, "dollar_vol": dollar_vol, "price": last_close,
                        "spread_pct": spread_pct, "market_cap": market_cap, "halted": None},
        "manual": manual,
        "layers": {
            "technical": {**tech_res, "weight": comb["weights_used"].get("technical")},
            "supply": {**supply_res, "weight": comb["weights_used"].get("supply")},
            "catalyst": {**cat_res, "weight": comb["weights_used"].get("catalyst")},
        },
        "drivers": comb["drivers"],
    }


# === Konsol-rapport (ASCII) ================================================
def format_intradag_report(report: dict) -> str:
    L = []
    fin = report["final"]
    price = report["price"]
    L.append("=" * 78)
    L.append(f" INTRADAG-RAPPORT  {report['symbol']}  "
             f"${'-' if price is None else round(price, 2)}  =>  "
             f"{'-' if fin is None else round(fin, 1)}  ({report['band']})")
    L.append(f" asof {report['asof']}   tf {report['timeframe']}")
    L.append(f" Gate: {report['gate']:.3f}   ({_binding_gate_factor(report['gate_inputs'])})")
    L.append("=" * 78)
    L.append(format_technical_report(report["symbol"], report["layers"]["technical"]))
    L.append(format_supply_report(report["symbol"], report["layers"]["supply"]))
    L.append(format_catalyst_report(report["symbol"], report["layers"]["catalyst"]))
    L.append("")
    L.append("Top 5 drivere (global bidrag):")
    for layer, name, contrib, sig in sorted(report["drivers"], key=lambda d: abs(d[2]), reverse=True)[:5]:
        L.append(f"  {LABEL.get(layer, layer):<11}{name:<24}{contrib:>+7.1f}  (sig {sig:+.0f})")
    return "\n".join(L)


# === UI-serialisering (spejler swing_report._report_to_json) ===============
def report_to_json(report: dict) -> dict:
    """Strukturér compute_intradag_report-output til UI-JSON. Samme form som
    /swing/analyze_json (lag keyes technical/supply/catalyst), saa IntradagReport.tsx
    kan genbruge SwingReport.tsx's render. _band bruges til baade slut- OG lag-baand
    (eet vokabular i intradag-domaenet); _band(None) -> 'ingen data' (pending katalysator)."""

    def _layer(res: dict, weight) -> dict:
        groups, gmap = [], {}
        for r in res["results"]:                       # r = ParamResult
            g = gmap.get(r.group)
            if g is None:
                g = {"name": r.group, "score": 0.0, "factors": []}
                gmap[r.group] = g
                groups.append(g)
            g["factors"].append({
                "name": r.name, "raw": r.raw,
                "signal": round(r.signal, 1),
                "weight": round(r.weight, 4),
                "weighted": round(r.weighted, 2),
            })
            g["score"] += r.weighted
        for g in groups:
            g["score"] = round(g["score"], 1)
        lag = res["lag_score"]
        return {
            "score": round(lag, 1) if lag is not None else None,
            "band": _band(lag),
            "groups": groups,
            "excluded": [{"name": n, "why": w} for n, w in res["excluded"]],
            "weight": weight,
        }

    def _drivers(positive: bool):
        ds = report["drivers"]                          # (layer, name, contribution, signal)
        if positive:
            sel = sorted([d for d in ds if d[2] > 0], key=lambda x: -x[2])[:5]
        else:
            sel = sorted([d for d in ds if d[2] < 0], key=lambda x: x[2])[:5]
        return [{"layer": l, "name": n, "contribution": round(ct, 1), "signal": round(s, 1)}
                for (l, n, ct, s) in sel]

    final = report["final"]
    layers = report["layers"]
    gi = report.get("gate_inputs", {})
    return {
        "symbol": report["symbol"],
        "asof": report.get("asof"),
        "timeframe": report.get("timeframe"),
        "price": round(report["price"], 2) if report.get("price") is not None else None,
        "final": round(final, 1) if final is not None else None,
        "final_band": report.get("band"),               # allerede _band(final) fra trin 4
        "combined": round(report["combined"], 1) if report.get("combined") is not None else None,
        "gate": round(report["gate"], 3),               # .3f: 0.998 skal IKKE vise som 1.00
        "gate_straf": round(report.get("gate_straf", 0.0), 1),
        "layers": {
            "technical": _layer(layers["technical"], layers["technical"].get("weight")),
            "supply": _layer(layers["supply"], layers["supply"].get("weight")),
            "catalyst": _layer(layers["catalyst"], layers["catalyst"].get("weight")),
        },
        "drivers": {"positive": _drivers(True), "negative": _drivers(False)},
        "info": {
            "float_shares": report.get("float_shares"),
            "float_pct": report.get("float_pct"),
            "spread_pct": gi.get("spread_pct"),
            "gate_inputs": gi,        # adv20, dollar_vol, price, spread_pct, market_cap, halted
            "manual": report.get("manual"),
        },
    }


# === Selftest (deterministisk, ingen IBKR) =================================
def _mk_layer(lag_score, results=None):
    return {"results": results or [], "excluded": [], "lag_score": lag_score}


def _run_selftest():
    fails = []

    def check(name, cond):
        print(f"  [{'OK' if cond else 'FEJL'}] {name}")
        if not cond:
            fails.append(name)

    def approx(a, b, t=1e-6):
        return a is not None and b is not None and abs(a - b) <= t

    def wdict(d, expect):
        return set(d.keys()) == set(expect.keys()) and all(approx(d[k], expect[k]) for k in expect)

    # 1. Renormalisering, katalysator pending
    c = combine({"technical": _mk_layer(50.0), "supply": _mk_layer(-20.0), "catalyst": _mk_layer(None)},
                gate=1.0, manual=None)
    check("1 renorm pending: weights_used == {tech 0.80, supply 0.20}",
          wdict(c["weights_used"], {"technical": 0.80, "supply": 0.20}))
    check("1 renorm pending: combined == 36.0", approx(c["combined"], 36.0))
    check("1 renorm pending: final == 36.0", approx(c["final"], 36.0))

    # 2. Alle tre lag tilstede
    c = combine({"technical": _mk_layer(50.0), "supply": _mk_layer(-20.0), "catalyst": _mk_layer(80.0)},
                gate=1.0, manual=None)
    check("2 alle lag: weights_used == {0.60, 0.15, 0.25}",
          wdict(c["weights_used"], {"technical": 0.60, "supply": 0.15, "catalyst": 0.25}))
    check("2 alle lag: combined == 47.0", approx(c["combined"], 47.0))

    # 3. Gate som nedad-straf
    c = combine({"technical": _mk_layer(40.0), "supply": _mk_layer(None), "catalyst": _mk_layer(None)},
                gate=0.5, manual=None)
    check("3 gate nedad-straf: final == 0.0 (40*0.5 - 0.5*40)", approx(c["final"], 0.0))

    # 4. Gate = min af delfaktorer (small-cap fallback, spaend bindende)
    g = compute_gate(adv_shares=300_000, spread_pct=0.02, market_cap=None)
    check("4 gate min: 0.98 (spaend bindende, adv mættet)", approx(g, 0.98, 1e-9))

    # 5. Halt = hard zero
    check("5 halt = 0.0", compute_gate(adv_shares=1e9, spread_pct=0.0, halted=True) == 0.0)

    # 6. Clamp
    c1 = combine({"technical": _mk_layer(200.0), "supply": _mk_layer(None), "catalyst": _mk_layer(None)}, gate=1.0)
    c2 = combine({"technical": _mk_layer(-200.0), "supply": _mk_layer(None), "catalyst": _mk_layer(None)}, gate=1.0)
    check("6 clamp: +200 -> final 100.0", approx(c1["final"], 100.0))
    check("6 clamp: -200 -> final -100.0", approx(c2["final"], -100.0))

    # 7. Baand
    check("7 _band(36.0) == Medvind", _band(36.0) == "Medvind")
    check("7 _band(-60.0) == Fraraades (intradag)", _band(-60.0) == "Fraraades (intradag)")
    check("7 _band(None) == ingen data", _band(None) == "ingen data")

    # 8. Supply ogsaa udeladt
    c = combine({"technical": _mk_layer(50.0), "supply": _mk_layer(None), "catalyst": _mk_layer(None)},
                gate=1.0, manual=None)
    check("8 kun teknisk: weights_used == {tech 1.0}", wdict(c["weights_used"], {"technical": 1.0}))
    check("8 kun teknisk: combined == 50.0", approx(c["combined"], 50.0))

    print(f"\nRESULTAT: {'GROEN' if not fails else 'ROED (' + str(len(fails)) + ' fejl)'}")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Intradag-rapport: selftest eller live smoke-test")
    ap.add_argument("--selftest", action="store_true", help="koer combine/gate-assertions uden IBKR")
    ap.add_argument("--symbol", default="AMAT")
    ap.add_argument("--timeframe", default="5 mins")
    ap.add_argument("--duration", default="5 D")
    ap.add_argument("--sr", type=float, default=None)
    ap.add_argument("--pattern", type=float, default=None)
    ap.add_argument("--candle", type=float, default=None)
    args = ap.parse_args()

    if args.selftest:
        _run_selftest()
    else:
        async def _live():
            from ibkr_connect import IBKRConnection
            conn = IBKRConnection(paper_trading=True)
            if not await conn.connect():
                print("FEJL: kunne ikke forbinde til IBKR (TWS/Gateway paa 7497 paa DENNE maskine?)")
                return
            try:
                rep = await compute_intradag_report(
                    conn.ib, args.symbol, timeframe=args.timeframe, duration=args.duration,
                    sr=args.sr, chart_pattern=args.pattern, candlestick=args.candle)
                print(format_intradag_report(rep))
            finally:
                conn.disconnect()
        asyncio.run(_live())
