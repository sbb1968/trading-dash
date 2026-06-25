#!/usr/bin/env python3
"""
buyhold.py — Buy-and-Hold orkestrator: 4 lag + risiko-gate -> SAMLET score.

Parallel til swingens _compute_swing. Samler:
  Lag 1-3 (buyhold_fundamental, FMP) + Lag 4 (buyhold_trend, IBKR-bars) × risiko-gate.
Risiko-gaten er den langsigtede pendant til swingens tradability-gate: strukturelle
fatale fejl (konkurs/udvanding/FCF-distress/gaelds-distress) traekker den mod 0.
Genbruger swingens GATE_STRAF + combine-FORMEL (combined×gate − (1−gate)×GATE_STRAF).

CLI kraever IBKR forbundet (Lag 4 henter bars) + FMP-noegle (Lag 1-3):
    python buyhold.py KO
"""
from __future__ import annotations

import asyncio
from typing import Optional

from buyhold_fundamental import (FINANCIAL_EXCLUDE, compute_growth, compute_quality,
                                 compute_valuation, fetch_buyhold_fundamentals, is_financial)
from buyhold_trend import compute_buyhold_trend, fetch_trend_bars

try:
    from swing_report import GATE_STRAF          # genbrug swingens gate-straf
except Exception:
    GATE_STRAF = 40.0

# Lag-vaegte for helheden (renormaliseres over lag MED score).
BUYHOLD_WEIGHTS = {"quality": 0.35, "growth": 0.25, "valuation": 0.25, "trend": 0.15}
LAYER_LABEL = {"quality": "Kvalitet", "growth": "Vaekst & holdbarhed",
               "valuation": "Vaerdiansaettelse", "trend": "Langsigtet trend"}


# ── Risiko-gate: per-signal 0..1-faktorer fra meta["gate_raw"] ───────────────
def _ramp(x, lo, hi, lo_val, hi_val):
    """Lineaer rampe: x<=lo -> lo_val, x>=hi -> hi_val, ellers interpoleret."""
    if x <= lo:
        return lo_val
    if x >= hi:
        return hi_val
    return lo_val + (x - lo) / (hi - lo) * (hi_val - lo_val)


def _altman_factor(z):
    if z is None:
        return 1.0
    if z >= 2.99:
        return 1.0
    if z >= 1.81:
        return _ramp(z, 1.81, 2.99, 0.6, 1.0)
    return max(0.0, (z / 1.81) * 0.6)                 # distress -> mod 0


def _dilution_factor(d):                              # d = total %-aendring i aktie-antal
    if d is None:
        return 1.0
    if d <= 2:
        return 1.0                                    # tilbagekoeb/fladt
    if d <= 50:
        return _ramp(d, 2, 50, 1.0, 0.5)
    return max(0.0, _ramp(d, 50, 300, 0.5, 0.0))      # kraftig udvanding -> 0


def _fcf_factor(sign):
    if sign is None:
        return 1.0
    return 1.0 if sign > 0 else 0.3                   # vedvarende negativ FCF = strukturel risiko


def _netdebt_factor(nd):
    if nd is None:
        return 1.0
    if nd < 4:
        return 1.0
    if nd <= 6:
        return _ramp(nd, 4, 6, 1.0, 0.5)
    return 0.3


def _current_factor(cr):
    if cr is None:
        return 1.0
    return 0.9 if cr < 1.0 else 1.0                   # mild daempning ved likviditetsstress


def compute_buyhold_gate(gate_raw: dict, sector: Optional[str]):
    """gate = produkt(per-signal-faktorer), klemt [0,1]. Finans-undtagelse: Altman Z +
    nettogaeld/EBITDA er ikke kalibreret til banker -> de-emfaseres (1.0), men udvanding
    + FCF-fortegn beholdes (gyldige roede flag). Returnerer (gate, inputs)."""
    gr = gate_raw or {}
    fin = is_financial(sector)
    facs = {
        "altman":    1.0 if fin else _altman_factor(gr.get("altman_z")),
        "dilution":  _dilution_factor(gr.get("dilution_pct")),
        "fcf":       _fcf_factor(gr.get("fcf_sign")),
        "netdebt":   1.0 if fin else _netdebt_factor(gr.get("netdebt_ebitda")),
        "current":   _current_factor(gr.get("current_ratio")),
    }
    gate = 1.0
    for v in facs.values():
        gate *= v
    gate = max(0.0, min(1.0, gate))
    inputs = {
        "gate": gate, "financial_deemphasis": fin, "factors": facs,
        "altman_z": gr.get("altman_z"), "dilution_pct": gr.get("dilution_pct"),
        "fcf_sign": gr.get("fcf_sign"), "netdebt_ebitda": gr.get("netdebt_ebitda"),
        "current_ratio": gr.get("current_ratio"),
    }
    return gate, inputs


# ── Combine: 4 lag × gate (spejler swing-formlen) ────────────────────────────
def combine_buyhold(layers: dict, gate: float) -> dict:
    """layers: {key: {results, excluded, lag_score}}. Renormaliserer vaegte over lag
    MED en score (>0 resultater). final = combined×gate − (1−gate)×GATE_STRAF, klemt."""
    present = {k: v for k, v in layers.items() if v.get("results")}
    wsum = sum(BUYHOLD_WEIGHTS[k] for k in present) or 1.0
    adj = {k: layers[k]["lag_score"] for k in BUYHOLD_WEIGHTS}
    combined = sum(BUYHOLD_WEIGHTS[k] / wsum * layers[k]["lag_score"] for k in present)
    gate_straf = (1.0 - gate) * GATE_STRAF
    final = max(-100.0, min(100.0, combined * gate - gate_straf))

    drivers = []
    for k, res in layers.items():
        w = BUYHOLD_WEIGHTS[k] / wsum if k in present else 0.0
        for r in res["results"]:
            drivers.append((k, r.name, w * r.weighted, r.signal))
    return {"adj": adj, "combined": combined, "gate": gate, "gate_straf": gate_straf,
            "final": final, "drivers": drivers}


def _band_final(final: float) -> str:
    if final >= 45:
        return "STAERK KOEB-OG-HOLD-KANDIDAT"
    if final >= 25:
        return "EGNET (langsigtet)"
    if final > 0:
        return "NEUTRAL / AFVENT"
    if final > -25:
        return "SVAG (langsigtet)"
    return "FRARAADES (langsigtet)"


# ── Orkestrator ──────────────────────────────────────────────────────────────
async def compute_buyhold(ib, ticker: str, api_key: str) -> dict:
    """Hele kaeden -> core dict (layers + c + gate_inputs + meta). Bars via ib, fund via FMP."""
    ticker = ticker.upper()
    # FMP er sync/blokerende -> to_thread saa det ikke fryser ib-event-loopet.
    f, meta = await asyncio.to_thread(fetch_buyhold_fundamentals, ticker, api_key)
    price = f.get("price")
    excl, reason = (FINANCIAL_EXCLUDE, "finansiel sektor") if meta.get("is_financial") else (set(), "")

    q = compute_quality(f, price, excl, reason)
    g = compute_growth(f, price, excl, reason)
    v = compute_valuation(f, price, excl, reason)

    # Lag 4 kraever ib; er den nede, ekskluderes trend-laget paent (tom -> renorm uden trend).
    try:
        weekly, daily, spy, sect = await fetch_trend_bars(ib, ticker, meta.get("sector"))
        trend = compute_buyhold_trend(weekly, daily, spy, sect)
    except Exception:
        trend = {"results": [], "excluded": [("Lag 4 (trend)", "IBKR ikke tilgaengelig")], "lag_score": 0.0}

    gate, gate_inputs = compute_buyhold_gate(meta.get("gate_raw", {}), meta.get("sector"))
    layers = {"quality": q, "growth": g, "valuation": v, "trend": trend}

    # Hul-rapport-vagt: hverken fundamental data ELLER et trend-lag med faktorer -> intet
    # at vurdere -> rejs klar fejl (endpoint -> 422, UI viser forklaring i stedet for nul-kort).
    if not meta.get("fundamental_available") and not layers["trend"].get("results"):
        if any("429" in e for e in meta.get("errors", [])):
            raise ValueError(
                f"{ticker}: FMP's daglige kvota ser ud til at vaere opbrugt (429), og der "
                f"er ingen kursdata. Proev igen efter FMP-reset.")
        raise ValueError(
            f"Ingen brugbar data for {ticker}. Hverken fundamentale noegletal (FMP) eller "
            f"kursdata (IBKR) kunne hentes. Tickeren findes muligvis ikke i datakilderne "
            f"eller er ikke US-noteret — Buy-and-Hold-modellen daekker primaert US-noterede "
            f"selskaber, og for ikke-US-noterede findes data typisk ikke under det bare "
            f"ticker. (Proev evt. en ADR-ticker hvis selskabet har en.)")

    c = combine_buyhold(layers, gate)
    c["price"] = price
    c["company_name"] = meta.get("company_name")
    c["final_band"] = _band_final(c["final"])

    # Noegletal-tiles (buy-and-hold-relevante) + flag til serialisering/UI.
    pe = (price / f["eps_ttm"]) if (f.get("eps_ttm") and price and f["eps_ttm"] > 0) else f.get("pe_ttm")
    tiles = {
        "market_cap": meta.get("market_cap"), "sector": meta.get("sector"),
        "pe": pe, "oe_yield": f.get("oe_yield"), "dividend_yield": f.get("dividend_yield"),
        "payout": f.get("payout"), "roic": f.get("roic"),
        "altman_z": meta.get("gate_raw", {}).get("altman_z"), "fcf_yield": f.get("fcf_yield"),
    }
    return {"ticker": ticker, "layers": layers, "c": c, "gate_inputs": gate_inputs,
            "meta": meta, "tiles": tiles, "fundamental_na": not meta.get("fundamental_available")}


# ── CLI ──────────────────────────────────────────────────────────────────────
def _fmt_layer(label, res):
    L = [f"  [{label}]  {res['lag_score']:+.1f}"]
    last = None
    for r in res["results"]:
        if r.group != last:
            L.append(f"    ({r.group})")
            last = r.group
        L.append(f"      {r.name:<26}{r.raw:<32}{r.signal:>+7.0f} {r.weight*100:>5.1f}% {r.weighted:>+7.1f}")
    if res["excluded"]:
        L.append("      Ekskluderet: " + "; ".join(f"{n} ({w})" for n, w in res["excluded"]))
    return "\n".join(L)


def main() -> int:
    import argparse
    import asyncio
    import os
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Buy-and-Hold SAMLET score (4 lag + gate)")
    ap.add_argument("ticker")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    a = ap.parse_args()
    if not a.api_key:
        print("FEJL: ingen FMP-noegle (FMP_API_KEY el. --api-key).")
        return 1

    async def run():
        from ibkr_connect import IBKRConnection
        conn = IBKRConnection(paper_trading=True)
        if not await conn.connect():
            print("❌ Kunne ikke forbinde til IBKR (TWS/Gateway oppe?).")
            return 1
        try:
            core = await compute_buyhold(conn.ib, a.ticker.upper(), a.api_key)
        except ValueError as e:
            print(f"\n  {e}\n")
            return 0
        finally:
            conn.disconnect()
        c, meta = core["c"], core["meta"]
        print("#" * 80)
        print(f"  BUY-AND-HOLD SAMLET — {core['ticker']}  ({meta.get('company_name') or ''})")
        print(f"  LANGSIGTET vurdering (min. 1 aar).  Sektor: {meta.get('sector') or '—'}")
        if not meta.get("fundamental_available"):
            print("  ⚠️  FUNDAMENTAL DATA N/A — kun trend-laget; scoren er IKKE en fuld vurdering.")
        if meta.get("is_financial"):
            print("  ⚠️  BEGRAENSET MODEL — finansiel sektor (capex/FCF/OE/gaeld-faktorer udeladt).")
        print("#" * 80)
        for k in ("quality", "growth", "valuation", "trend"):
            print(_fmt_layer(LAYER_LABEL[k], core["layers"][k]))
        gi = core["gate_inputs"]
        print("\n  RISIKO-GATE  {:.2f}{}".format(
            gi["gate"], "  (finans: Altman/gaeld de-emfaseret)" if gi["financial_deemphasis"] else ""))
        fr = gi["factors"]
        print(f"     Altman {fr['altman']:.2f} (Z={gi['altman_z']}) · udvanding {fr['dilution']:.2f} "
              f"({gi['dilution_pct']}%) · FCF {fr['fcf']:.2f} · gaeld {fr['netdebt']:.2f} · likv {fr['current']:.2f}")
        print("  " + "-" * 70)
        print(f"  Kombineret {c['combined']:+.1f}  ×  gate {c['gate']:.2f}"
              + (f"  − gate-straf {c['gate_straf']:.1f}" if c['gate_straf'] > 0.01 else ""))
        print(f"  SAMLET {c['final']:+.1f}  [{c['final_band']}]")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
