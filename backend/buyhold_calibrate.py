#!/usr/bin/env python3
"""
buyhold_calibrate.py — kalibrerings-harness (MAALER, auto-tuner IKKE).
═══════════════════════════════════════════════════════════════════════════
Validér at den fundamentale buy-and-hold-scoring (Lag 1-3 + gate, ~85 % af dommen)
rangerer et KENDT univers rigtigt: kvalitets-compoundere hoejt, strukturelt svage lavt.
Harnesset rapporterer fordeling + bucket-separation + Spearman; mennesket doemmer og
beslutter kurve-aendringer bagefter (separat spec). Aldrig tune paa in-sample her.

Lag 4 (trend) er UDELADT — den kraever IBKR-bars + egen metode. Alt her er FMP-baseret.

FMP rate-limiter ved ~20+ hurtige kald -> DISK-CACHE pr. ticker (gemmer scorer-INPUTTET
(f, meta), saa kurve-iteration re-scorer fra disk uden at roere FMP) + throttling.
Foerste koersel langsom; efterfoelgende oejeblikkelige.

Genbruger buyhold_fundamental (fetch + compute_*) + buyhold.compute_buyhold_gate
(single source of truth for gaten). Ingen IBKR.

Brug (fra backend/):
    python buyhold_calibrate.py                      # default UNIVERSE (labelled)
    python buyhold_calibrate.py --csv univers.csv    # ticker,bucket
    python buyhold_calibrate.py --tickers MSFT KO F  # ulabelled fordelings-scan
    # --cache-ttl-days 7  --throttle-sec 3.0  --no-cache  --api-key XXXX
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import pandas as pd

from buyhold_fundamental import (clamp, compute_growth, compute_quality, compute_valuation,
                                 fetch_buyhold_fundamentals, FINANCIAL_EXCLUDE)
from buyhold import compute_buyhold_gate            # gatens single source of truth (Fase 2)

CACHE_DIR = Path(__file__).resolve().parent / "buyhold_calibrate_cache"
LW = {"quality": 0.35, "growth": 0.25, "valuation": 0.25}   # fundamental-vaegte (trend udeladt)
GATE_STRAF = 40.0

# Univers — REDIGÉR. Buckets er DIN vurdering, ikke modellens input (ikke-cirkulaer test).
UNIVERSE = {
    "kvalitet": ["MSFT", "AAPL", "GOOGL", "V", "MA", "COST", "PG", "JNJ",
                 "KO", "HD", "UNH", "ADBE", "PEP", "MCD", "NKE"],
    # Strukturelt svage / ikke-kvalitets-compoundere (lav ROIC, kapitaltung, hoej gaeld,
    # udvanding). RKT = bekraeftet frarådes. En kvalitets-model skal rangere MSFT >> disse.
    "svag": ["RKT", "F", "T"],
}


# ── FMP disk-cache (rate-limit-fixet) ────────────────────────────────────────
def _cache_path(ticker):
    return CACHE_DIR / f"{ticker.upper()}.json"


def _cache_load(ticker, ttl_days):
    p = _cache_path(ticker)
    if not p.exists():
        return None
    if ttl_days is not None and (time.time() - p.stat().st_mtime) > ttl_days * 86400:
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))   # laeser Infinity (interest_coverage) ok
        return d["f"], d["meta"]
    except Exception:
        return None


def _cache_store(ticker, f, meta):
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(ticker).write_text(json.dumps({"f": f, "meta": meta}), encoding="utf-8")


def cached_fetch(ticker, api_key, ttl_days, throttle_sec, use_cache=True):
    """(f, meta, status). status: 'cache' | 'fetch' | 'throttled' | 'na'.
    Cacher ALDRIG en 429-throttlet pull (saa den retry'er naeste koersel)."""
    if use_cache:
        hit = _cache_load(ticker, ttl_days)
        if hit is not None:
            return hit[0], hit[1], "cache"
    f, meta = fetch_buyhold_fundamentals(ticker, api_key)
    if any("429" in e for e in meta.get("errors", [])):
        time.sleep(throttle_sec * 5)                    # backoff + ét retry
        f, meta = fetch_buyhold_fundamentals(ticker, api_key)
        if any("429" in e for e in meta.get("errors", [])):
            return f, meta, "throttled"                 # IKKE cachet
    time.sleep(throttle_sec)                             # throttle kun ved miss
    status = "fetch" if meta.get("fundamental_available") else "na"
    _cache_store(ticker, f, meta)                        # genuin 402/tom = persistent -> cache + 'na'
    return f, meta, status


# ── Scoring (sync, ingen IBKR) ──────────────────────────────────────────────
def _band(s):
    if s >= 50:  return "Staerk"
    if s >= 20:  return "Medvind"
    if s > -20:  return "Neutral"
    if s > -50:  return "Svag"
    return "Fraraades"


def score_fundamental(f, meta):
    """-> dict L1/L2/L3, gate, combined (renorm 0.85), final (gated)."""
    excl, reason = (FINANCIAL_EXCLUDE, "finansiel sektor") if meta.get("is_financial") else (set(), "")
    price = f.get("price")
    L1 = compute_quality(f, price, excl, reason)["lag_score"]
    L2 = compute_growth(f, price, excl, reason)["lag_score"]
    L3 = compute_valuation(f, price, excl, reason)["lag_score"]
    combined = (L1 * LW["quality"] + L2 * LW["growth"] + L3 * LW["valuation"]) / sum(LW.values())
    gate, _ = compute_buyhold_gate(meta.get("gate_raw", {}), meta.get("sector"))
    final = clamp(combined * gate - (1.0 - gate) * GATE_STRAF)
    return {"L1": L1, "L2": L2, "L3": L3, "gate": gate, "combined": combined, "final": final}


# ── CLI ──────────────────────────────────────────────────────────────────────
def _load_universe(args):
    """-> [(ticker, bucket|None)]. --tickers = ulabelled; --csv = ticker,bucket; ellers UNIVERSE."""
    if args.tickers:
        return [(t.upper(), None) for t in args.tickers]
    if args.csv:
        import csv as _csv
        rows = []
        with open(args.csv, newline="", encoding="utf-8") as fh:
            for r in _csv.DictReader(fh):
                t = (r.get("ticker") or "").strip().upper()
                if t:
                    rows.append((t, (r.get("bucket") or "").strip() or None))
        return rows
    return [(t, b) for b, names in UNIVERSE.items() for t in names]


def main() -> int:
    ap = argparse.ArgumentParser(description="Buy-and-Hold kalibrerings-harness (maaler, auto-tuner ikke)")
    ap.add_argument("--csv", default=None, help="CSV med kolonner ticker,bucket")
    ap.add_argument("--tickers", nargs="+", default=None, help="ulabelled fordelings-scan")
    ap.add_argument("--cache-ttl-days", type=float, default=7.0)
    ap.add_argument("--throttle-sec", type=float, default=3.0)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    a = ap.parse_args()
    if not a.api_key:
        print("FEJL: ingen FMP-noegle (FMP_API_KEY el. --api-key).")
        return 1

    out_dir = Path(__file__).resolve().parent / "buyhold_calibrate_output"
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    universe = _load_universe(a)
    emit("=" * 86)
    emit("  BUY-AND-HOLD KALIBRERING — Lag 1-3 + gate (FMP, cachet) · MAALER, auto-tuner IKKE")
    emit("=" * 86)
    emit(f"  {len(universe)} tickers · throttle {a.throttle_sec:g}s (kun ved cache-miss) · "
         f"cache-ttl {a.cache_ttl_days:g}d{' · CACHE FRA' if a.no_cache else ''}")
    emit("")

    scored, skipped = [], []
    for tk, bucket in universe:
        f, meta, status = cached_fetch(tk, a.api_key, a.cache_ttl_days, a.throttle_sec,
                                       use_cache=not a.no_cache)
        if status == "throttled" or not meta.get("fundamental_available"):
            skipped.append((tk, bucket, "throttled (429)" if status == "throttled" else "data n/a"))
            continue
        sc = score_fundamental(f, meta)
        sc.update(ticker=tk, bucket=bucket, status=status)
        scored.append(sc)

    if not scored:
        emit("  Ingen scorede tickers (alt throttlet/n.a.). Koer igen — cachede loader oejeblikkeligt.")
        for tk, b, why in skipped:
            emit(f"    {tk:<8}{(b or '—'):<10}{why}")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 0

    scored.sort(key=lambda r: r["final"], reverse=True)

    # (a) Pr. ticker
    emit("  (a) PR. TICKER (sorteret efter FUND-FINAL)")
    emit(f"  {'Ticker':<8}{'Bucket':<10}{'L1':>7}{'L2':>7}{'L3':>7}{'gate':>7}{'komb':>9}{'FINAL':>9}  band")
    emit("  " + "-" * 78)
    for r in scored:
        emit(f"  {r['ticker']:<8}{(r['bucket'] or '—'):<10}{r['L1']:>+7.1f}{r['L2']:>+7.1f}"
             f"{r['L3']:>+7.1f}{r['gate']:>7.2f}{r['combined']:>+9.1f}{r['final']:>+9.1f}  {_band(r['final'])}")
    if skipped:
        emit("\n  Udeladt af statistikken:")
        for tk, b, why in skipped:
            emit(f"    {tk:<8}{(b or '—'):<10}{why}")

    # (b) Fordeling (percentiler)
    emit("\n  (b) FORDELING (percentiler over scorede tickers)")
    df = pd.DataFrame(scored)
    qs = [0.10, 0.25, 0.50, 0.75, 0.90]
    emit(f"  {'':<12}" + "".join(f"{('p'+str(int(q*100))):>9}" for q in qs))
    for col, lbl in (("L1", "Lag 1"), ("L2", "Lag 2"), ("L3", "Lag 3"), ("final", "FUND-FINAL")):
        vals = df[col].quantile(qs)
        emit(f"  {lbl:<12}" + "".join(f"{vals[q]:>+9.1f}" for q in qs))

    # (c) Bucket-separation (kun naar labelled)
    labelled = [r for r in scored if r["bucket"] in ("kvalitet", "svag")]
    if any(r["bucket"] == "kvalitet" for r in labelled) and any(r["bucket"] == "svag" for r in labelled):
        emit("\n  (c) BUCKET-SEPARATION")
        kv = df[df["bucket"] == "kvalitet"]["final"]
        sv = df[df["bucket"] == "svag"]["final"]
        emit(f"  kvalitet:  median {kv.median():+.1f} · gns {kv.mean():+.1f} · spaend [{kv.min():+.1f}, {kv.max():+.1f}]")
        emit(f"  svag:      median {sv.median():+.1f} · gns {sv.mean():+.1f} · spaend [{sv.min():+.1f}, {sv.max():+.1f}]")
        emit(f"  -> kvalitet-median > svag-median?  {'JA' if kv.median() > sv.median() else 'NEJ'}")
        lab_df = df[df["bucket"].isin(["kvalitet", "svag"])].copy()
        lab_df["y"] = (lab_df["bucket"] == "kvalitet").astype(int)
        # Spearman = Pearson af ranks -> ingen scipy noedvendig (pandas method='spearman' kraever scipy).
        rho = lab_df["final"].rank().corr(lab_df["y"].rank())
        emit(f"  -> Spearman(FUND-FINAL, kvalitet=1/svag=0) = {rho:+.2f}" if pd.notna(rho)
             else "  -> Spearman: udefineret (for faa/ens vaerdier)")
        # Inversioner: svag-navn over det laveste kvalitets-navn
        kv_min = kv.min()
        inv = [r["ticker"] for r in labelled if r["bucket"] == "svag" and r["final"] > kv_min]
        emit(f"  -> Inversioner (svag over laveste kvalitet {kv_min:+.1f}): "
             f"{', '.join(inv) if inv else '(ingen — rent)'}")

    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
