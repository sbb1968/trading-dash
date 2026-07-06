#!/usr/bin/env python3
"""
buyhold_fundamentals_probe.py — FMP felt-tilgaengeligheds-probe (Buy-and-Hold Fase 0)
═══════════════════════════════════════════════════════════════════════════════════
Vi maa IKKE spec'e 15 scorer'e mod FMP-felter vi ikke har bekraeftet. FMP's
katalysator-endpoints er 402-laaste; vi ved ikke hvad regnskabs-/ratio-/key-metrics-
/financial-growth-endpointsene giver paa den aktuelle noegle. Denne probe svarer paa
det EMPIRISK — laes-only, ingen scoring, ingen model.

For hver testticker kaldes Buy-and-Hold-modellens kandidat-endpoints, og PR. FELT
rapporteres: OK (udfyldt, med sample-vaerdi) / TOM (felt findes ikke / null) /
402-LAAST (endpoint premium-laast) / FEJL. Til sidst en AGGREGERING pr. felt paa tvaers
af tickers: TILGAENGELIG / LAAST / MANGLER + hvilket lag/faktor det tjener.

Spejler FMP-klienten i fundamental_score.fetch_fundamentals_fmp (samme base=/stable,
urllib, ?apikey=, _first). Resultatet beskaerer faktor-saettet FOER Fase 1: kun
bekraeftede felter bygges; resten markeres "udeladt — data ikke tilgaengelig" (som vi
gjorde med short-interest).

Brug (fra backend/ — kraever FMP-noegle):
    python buyhold_fundamentals_probe.py
    python buyhold_fundamentals_probe.py --api-key XXXX --tickers MRVL KO JNJ RKT

Output: stdout + buyhold_fundamentals_probe_output/summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\buyhold_fundamentals_probe.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Auto-indlaes backend/.env saa API-noegler virker uden manuel miljoe-opsaetning.
from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name(".env"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE = "https://financialmodelingprep.com/stable"
GREEN, YELLOW, RED = "✅", "⚠️ ", "❌"

# Default-testtickers: kvalitets-compounder, dividende-aristokrat, healthcare, mindre/levered.
DEFAULT_TICKERS = ["MRVL", "KO", "JNJ", "RKT"]

# (endpoint-label, path-template {s}=symbol, har_historik, [(fmp_felt, tjener)])
# Kandidat-felter daekker Buy-and-Hold-modellens [FMP-NY]-faktorer. Felt-navne varierer
# i FMP /stable -> vi prober flere stavemaader (fx roicTTM vs returnOnInvestedCapitalTTM);
# proben afsloerer hvilket der findes.
ENDPOINTS = [
    ("ratios-ttm", "ratios-ttm?symbol={s}", False, [
        ("netProfitMarginTTM",                         "Lag1 Net margin"),
        ("grossProfitMarginTTM",                       "Lag1 Bruttomargin [NY]"),
        ("currentRatioTTM",                            "Lag1 Current ratio [NY]"),
        ("interestCoverageTTM",                        "Lag1 Rentedaekning [NY]"),
        ("debtToEquityRatioTTM",                       "Lag1 Gaeld/EK"),
        ("operatingCashFlowSalesRatioTTM",             "Lag1 FCF-margin (afledt)"),
        ("freeCashFlowOperatingCashFlowRatioTTM",      "Lag1 FCF/OCF-konvertering [NY]"),
        ("priceToEarningsRatioTTM",                    "Lag3 P/E"),
        ("priceEarningsToGrowthRatioTTM",              "Lag3 PEG"),
        ("priceToFreeCashFlowsRatioTTM",               "Lag3 P/FCF [NY]"),
        ("dividendYielTTM",                            "Lag3 Dividende-yield [NY]"),
        ("dividendYieldTTM",                           "Lag3 Dividende-yield [NY] (alt)"),
        ("dividendPayoutRatioTTM",                     "Lag3 Payout [NY]"),
        ("payoutRatioTTM",                             "Lag3 Payout [NY] (alt)"),
    ]),
    ("key-metrics-ttm", "key-metrics-ttm?symbol={s}", False, [
        ("returnOnEquityTTM",                          "Lag1 ROE"),
        ("returnOnInvestedCapitalTTM",                 "Lag1 ROIC [NY]"),
        ("roicTTM",                                    "Lag1 ROIC [NY] (alt)"),
        ("netDebtToEBITDATTM",                         "Lag1 Nettogaeld/EBITDA [NY]"),
        ("evToEBITDATTM",                              "Lag3 EV/EBITDA [NY]"),
        ("enterpriseValueOverEBITDATTM",               "Lag3 EV/EBITDA [NY] (alt)"),
        ("freeCashFlowYieldTTM",                       "Lag3 FCF-yield [NY]"),
        ("earningsYieldTTM",                           "Lag3 Earnings yield"),
        ("altmanZScoreTTM",                            "Gate Altman Z [NY]"),
    ]),
    ("financial-growth", "financial-growth?symbol={s}&period=annual&limit=5", True, [
        ("epsgrowth",                                  "Lag2 EPS-vaekst (1y)"),
        ("revenueGrowth",                              "Lag2 Omsaetningsvaekst (1y)"),
        ("freeCashFlowGrowth",                         "Lag2 FCF-vaekst [NY]"),
        ("fiveYRevenueGrowthPerShare",                 "Lag2 Omsaetnings-CAGR 5y [NY]"),
        ("fiveYNetIncomeGrowthPerShare",              "Lag2 EPS-CAGR 5y [NY]"),
        ("tenYRevenueGrowthPerShare",                  "Lag2 Omsaetnings-CAGR 10y"),
    ]),
    ("income-statement", "income-statement?symbol={s}&period=annual&limit=5", True, [
        ("revenue",                                    "Lag2 Omsaetning (CAGR/trend-input)"),
        ("netIncome",                                  "Lag2 Nettoresultat"),
        ("grossProfit",                                "Lag1 Bruttomargin-input"),
        ("operatingIncome",                            "Lag2 Margin-trend-input"),
        ("eps",                                        "Lag2 EPS (CAGR-input)"),
        ("weightedAverageShsOut",                      "Gate Udvanding (aktie-antal-trend) [NY]"),
    ]),
    ("balance-sheet-statement", "balance-sheet-statement?symbol={s}&period=annual&limit=5", True, [
        ("totalDebt",                                  "Lag1/Gate Gaeld"),
        ("netDebt",                                    "Lag1 Nettogaeld-input"),
        ("totalCurrentAssets",                         "Lag1 Current ratio-input"),
        ("totalCurrentLiabilities",                    "Lag1 Current ratio-input"),
        ("totalStockholdersEquity",                    "Lag1 Gaeld/EK-input"),
    ]),
    ("cash-flow-statement", "cash-flow-statement?symbol={s}&period=annual&limit=5", True, [
        ("freeCashFlow",                               "Lag1/Gate FCF (kvalitet/distress)"),
        ("operatingCashFlow",                          "Lag1 OCF"),
        ("netIncome",                                  "Lag1 FCF/nettoresultat-konvertering [NY]"),
        ("capitalExpenditure",                         "Lag1 Capex (FCF-input)"),
    ]),
    ("ratios", "ratios?symbol={s}&period=annual&limit=5", True, [
        ("grossProfitMargin",                          "Lag2 Margin-trend 5y [NY]"),
        ("netProfitMargin",                            "Lag2 Margin-trend 5y"),
        ("returnOnInvestedCapital",                    "Lag2 Reinvesteringsafkast (ROIC-historik) [AFLEDT]"),
    ]),
    ("profile", "profile?symbol={s}", False, [
        ("sector",                                     "kontekst sektor"),
        ("marketCap",                                  "kontekst markedsvaerdi"),
        ("companyName",                                "header firmanavn"),
        ("beta",                                       "Lag4 kontekst (volatilitet)"),
    ]),
    ("enterprise-values", "enterprise-values?symbol={s}&limit=1", False, [
        ("enterpriseValue",                            "Lag3 EV/EBITDA-input"),
        ("numberOfShares",                             "Gate udvanding-input"),
    ]),
]


def get(path: str, key: str):
    """Returnér (status, data). status: 'OK' | '402-LAAST' | 'HTTP n' | 'FEJL ...'."""
    url = f"{BASE}/{path}{'&' if '?' in path else '?'}apikey={key}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return "OK", json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return (f"402-LAAST" if e.code == 402 else f"HTTP {e.code}"), None
    except Exception as e:
        return f"FEJL {type(e).__name__}", None


def first(data):
    if isinstance(data, list):
        return data[0] if data else {}
    return data if isinstance(data, dict) else {}


def sample(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, float):
        return f"{v:.4g}"
    s = str(v)
    return s[:26] + ("…" if len(s) > 26 else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="FMP felt-tilgaengeligheds-probe (Buy-and-Hold Fase 0, laes-only)")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    ap.add_argument("--tickers", nargs="+", default=DEFAULT_TICKERS)
    args = ap.parse_args()

    out_dir = Path.cwd() / "buyhold_fundamentals_probe_output"
    out_dir.mkdir(exist_ok=True)
    lines: list[str] = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    if not args.api_key:
        emit("FEJL: ingen FMP-noegle. Saet FMP_API_KEY i miljoeet eller brug --api-key.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    emit("=" * 80)
    emit("  FMP FELT-TILGAENGELIGHEDS-PROBE — Buy-and-Hold Fase 0  (laes-only, ingen scoring)")
    emit("=" * 80)
    emit(f"Base: {BASE}   Tickers: {', '.join(args.tickers)}")
    emit("Pr. felt: ✅ udfyldt (sample) · ⚠️ findes men null/tom · ❌ mangler · 402 = endpoint laast")
    emit("")

    # felt -> set af statusser paa tvaers af tickers (til aggregering)
    field_status: dict[tuple, set] = {}
    field_purpose: dict[tuple, str] = {}

    for tk in args.tickers:
        emit("─" * 80)
        emit(f"  {tk}")
        emit("─" * 80)
        for label, tmpl, has_hist, fields in ENDPOINTS:
            status, data = get(tmpl.format(s=tk), args.api_key)
            if status != "OK":
                emit(f"   {RED} {label:<26} {status}")
                for fld, purpose in fields:
                    field_status.setdefault((label, fld), set()).add("LAAST" if "402" in status else "FEJL")
                    field_purpose[(label, fld)] = purpose
                continue
            n = len(data) if isinstance(data, list) else 1
            rec = first(data)
            hist = f" · {n} aarsrecords" if has_hist else ""
            emit(f"   {GREEN} {label:<26} OK{hist}")
            for fld, purpose in fields:
                field_purpose[(label, fld)] = purpose
                present = isinstance(rec, dict) and fld in rec
                val = rec.get(fld) if present else None
                if present and val is not None:
                    emit(f"        {GREEN} {fld:<34} = {sample(val)}   ({purpose})")
                    field_status.setdefault((label, fld), set()).add("OK")
                elif present:
                    emit(f"        {YELLOW}{fld:<34} = null              ({purpose})")
                    field_status.setdefault((label, fld), set()).add("NULL")
                else:
                    emit(f"        {RED} {fld:<34} mangler            ({purpose})")
                    field_status.setdefault((label, fld), set()).add("MANGLER")
        emit("")

    # ── Aggregering: hvert felt -> dom paa tvaers af tickers ──
    emit("=" * 80)
    emit("  AGGREGERING — kan vi bygge faktoren? (paa tvaers af alle tickers)")
    emit("=" * 80)
    avail, locked, missing = [], [], []
    for (label, fld), statuses in field_status.items():
        purpose = field_purpose[(label, fld)]
        if "OK" in statuses:
            avail.append((label, fld, purpose))
        elif statuses & {"LAAST"}:
            locked.append((label, fld, purpose))
        else:
            missing.append((label, fld, purpose))

    emit(f"\n  ✅ TILGAENGELIG (udfyldt for >=1 ticker) — BYG disse ({len(avail)}):")
    for label, fld, purpose in sorted(avail, key=lambda x: x[2]):
        emit(f"     {purpose:<42} ← {label}.{fld}")
    emit(f"\n  🔒 402-LAAST endpoint ({len(locked)}):")
    for label, fld, purpose in sorted(locked, key=lambda x: x[2]):
        emit(f"     {purpose:<42} ← {label}.{fld}")
    emit(f"\n  ❌ MANGLER/NULL paa alle tickers — UDELAD (som short-interest) ({len(missing)}):")
    for label, fld, purpose in sorted(missing, key=lambda x: x[2]):
        emit(f"     {purpose:<42} ← {label}.{fld}")

    emit("\n  → Smid denne aggregering til Desktop Claude: kun TILGAENGELIG-faktorer bygges i")
    emit("    Fase 1; LAAST/MANGLER markeres 'udeladt — data ikke tilgaengelig'.")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
