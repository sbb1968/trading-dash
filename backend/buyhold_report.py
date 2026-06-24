#!/usr/bin/env python3
"""
buyhold_report.py — Buy-and-Hold serialisering + tekst-formatter (Fase 3).

Spejler swing_report._report_to_json (layers→groups→factors med name/raw/signal/weight/
weighted + excluded + drivers). Afvigelser for buy-and-hold: FIRE lag (quality/growth/
valuation/trend), gate-NEDBRYDNING (forklarer hvorfor en aktie frarådes), Owner-Earnings-
blok, buy-and-hold-tiles, og flag (is_financial / fundamental_na). Tallene KOMMER fra
compute_buyhold (Fase 1-2) — ren serialisering, ingen omberegning.
"""
from __future__ import annotations

LAYER_ORDER = ("quality", "growth", "valuation", "trend")
LAYER_WEIGHT = {"quality": 0.35, "growth": 0.25, "valuation": 0.25, "trend": 0.15}


def _lag_band(s: float) -> str:
    if s >= 50:
        return "Staerk"
    if s >= 20:
        return "Medvind"
    if s > -20:
        return "Neutral"
    if s > -50:
        return "Svag"
    return "Fraraades"


def _layer(layer: dict) -> dict:
    """{results, excluded, lag_score} -> {score, band, groups, excluded}."""
    groups, gmap = [], {}
    for r in layer.get("results", []):
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
    score = round(layer.get("lag_score", 0.0), 1)
    return {
        "score": score, "band": _lag_band(score), "groups": groups,
        "excluded": [{"name": n, "why": w} for n, w in layer.get("excluded", [])],
    }


def _gate_breakdown(gi: dict) -> list:
    """Per-signal 0..1-faktorer -> forklarings-tabel (signal · factor · raw)."""
    fin = gi.get("financial_deemphasis")
    fr = gi.get("factors", {})
    deemph = " (de-emfaseret, finans)" if fin else ""

    def num(v, fmt):
        return fmt.format(v) if v is not None else "n/a"

    fcf = gi.get("fcf_sign")
    fcf_raw = "positiv" if fcf == 1 else ("negativ" if fcf == -1 else "n/a")
    return [
        {"signal": "Altman Z (konkurs)", "factor": round(fr.get("altman", 1.0), 2),
         "raw": num(gi.get("altman_z"), "Z={:.2f}") + deemph},
        {"signal": "Udvanding (aktie-antal)", "factor": round(fr.get("dilution", 1.0), 2),
         "raw": num(gi.get("dilution_pct"), "{:+.0f}%")},
        {"signal": "FCF-fortegn", "factor": round(fr.get("fcf", 1.0), 2), "raw": fcf_raw},
        {"signal": "Gaeld (nettogaeld/EBITDA)", "factor": round(fr.get("netdebt", 1.0), 2),
         "raw": num(gi.get("netdebt_ebitda"), "{:.1f}") + deemph},
        {"signal": "Likviditet (current ratio)", "factor": round(fr.get("current", 1.0), 2),
         "raw": num(gi.get("current_ratio"), "{:.2f}")},
    ]


def report_to_json(core: dict) -> dict:
    """core fra compute_buyhold -> struktureret JSON til UI + _buyhold_report_html."""
    c, meta = core["c"], core["meta"]
    layers = core["layers"]

    def _drivers(positive: bool):
        if positive:
            sel = sorted([d for d in c["drivers"] if d[2] > 0], key=lambda x: -x[2])[:5]
        else:
            sel = sorted([d for d in c["drivers"] if d[2] < 0], key=lambda x: x[2])[:5]
        return [{"layer": l, "name": n, "contribution": round(ct, 1), "signal": round(s, 1)}
                for (l, n, ct, s) in sel]

    oe_val = meta.get("owner_earnings")
    owner_earnings = None
    if oe_val is not None:
        owner_earnings = {
            "value": round(oe_val, 0), "norm_years": meta.get("oe_n_years"),
            "method": meta.get("oe_step"),
            "maint_capex": round(meta["maint_capex"], 0) if meta.get("maint_capex") is not None else None,
            "ocf_latest": round(meta["ocf"], 0) if meta.get("ocf") is not None else None,
            "fcf_latest": round(meta["fcf"], 0) if meta.get("fcf") is not None else None,
        }

    return {
        "ticker": core["ticker"],
        "company": c.get("company_name"),
        "price": round(c["price"], 2) if c.get("price") is not None else None,
        "final": round(c["final"], 1),
        "final_band": c.get("final_band"),
        "combined": round(c["combined"], 1),
        "gate": round(c["gate"], 2),
        "gate_straf": round(c.get("gate_straf", 0.0), 1),
        "is_financial": bool(meta.get("is_financial")),
        "fundamental_na": bool(core.get("fundamental_na")),
        "layers": {k: {**_layer(layers[k]), "weight": LAYER_WEIGHT[k]} for k in LAYER_ORDER},
        "drivers": {"positive": _drivers(True), "negative": _drivers(False)},
        "gate_breakdown": _gate_breakdown(core["gate_inputs"]),
        "owner_earnings": owner_earnings,
        "tiles": core.get("tiles", {}),
        "chart": None,   # valgfri uge-chart (degraderet i Fase 3)
    }


# ── Tekst-formatter (genbrugt af CLI/Studio) ─────────────────────────────────
def format_buyhold_report(d: dict) -> str:
    L = ["#" * 78, f"  BUY-AND-HOLD — {d['ticker']}  ({d.get('company') or ''})",
         "  LANGSIGTET vurdering (min. 1 aar)", "#" * 78]
    if d.get("fundamental_na"):
        L.append("  ⚠ FUNDAMENTAL DATA N/A — kun trend-laget; IKKE en fuld vurdering.")
    if d.get("is_financial"):
        L.append("  ⚠ BEGRAENSET MODEL — finansiel sektor (capex/FCF/OE/gaeld udeladt).")
    L.append("")
    for k in LAYER_ORDER:
        ly = d["layers"][k]
        L.append(f"  {k.capitalize():<12}{ly['score']:>+7.1f}  [{ly['band']}]  ({round(ly['weight']*100)}%)")
    L.append("")
    L.append(f"  Kombineret {d['combined']:+.1f}  ×  gate {d['gate']:.2f}"
             + (f"  − gate-straf {d['gate_straf']:.1f}" if d['gate_straf'] > 0.01 else ""))
    if d["gate"] < 1.0:
        L.append("  Gate-nedbrydning:")
        for gb in d["gate_breakdown"]:
            L.append(f"    {gb['signal']:<28}{gb['factor']:>5.2f}   {gb['raw']}")
    oe = d.get("owner_earnings")
    if oe:
        L.append(f"  Owner Earnings ({oe['norm_years']}-aars norm., {oe['method']}): {oe['value']:,.0f}"
                 f"  [OCF {oe['ocf_latest']:,.0f} / FCF {oe['fcf_latest']:,.0f}]")
    L.append(f"  SAMLET {d['final']:+.1f}  [{d['final_band']}]")
    L.append("#" * 78)
    return "\n".join(L)
