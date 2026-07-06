#!/usr/bin/env python3
"""
daytrading_scanner.py — Day trading Top-15 Konfluens-scanner (trin 6).

Rangerer dagens momentum-kandidater gennem den GATEDE konfluens
(compute_daytrading_report) — "de bedste opstillinger lige nu". Spejler
swing_top15.py's to-trins-moenster (billig pre-rank -> shortlist -> fuld score
-> top-N), men tilpasset day-tradings friskheds-krav (minutter, ikke timer).

TILPASNINGER ift. swing_top15:
  A) FRISKHED: univers er allerede LILLE (dagens gappers/RVOL fra eet TV-query);
     delt SPY (hentes EEN gang), float fra TV-scanens query (ikke per-symbol).
     Realistisk ~3-5 min for en shortlist paa ~18 navne, gentageligt i sessionen.
  B) DELT ib, IN-PROCESS: run_scan koerer som en asyncio.Task paa backendens
     DELTE ib (ikke en subprocess med egen klient, der konkurrerer om feed/pacing),
     throttlet (SCAN_DELAY_SEC) for IBKR-pacing (~6 historik/min, ellers Error 162).

Den fulde gatede score bevares (systemets faktiske output) — listen er IKKE raa
TV-metrikker. Eget TV-query (moenster kopieret fra tv_scanner/fetch_float, ikke
importeret). ASCII i kode + konsol.
"""
from __future__ import annotations

import os
import asyncio

from technical_intraday import _band
from data_source_intraday import fetch_benchmark_bars
from daytrading_report import compute_daytrading_report

_DIR = os.path.dirname(os.path.abspath(__file__))

# === Konstanter ============================================================
PRICE_MIN, PRICE_MAX = 1.0, 50.0
MIN_CHANGE_PCT       = 5.0          # mindst +5% i dag (gap/gainer-proxy)
MIN_RVOL             = 2.0          # relativ volumen
MIN_AVG_VOL          = 300_000      # likviditetsgulv
FLOAT_MAX            = None         # valgfrit loft (fx 100e6); None = lad forsynings-laget rangere float
UNIVERSE_MAX         = 50           # raa kandidater fra TV
SHORTLIST_N          = 18           # fuld-scores (pacing-balance ~3-5 min)
TOP_N                = 15           # i listen
SCAN_DELAY_SEC       = 9.0          # throttle mellem fulde scoringer (~6 historik/min)
SCAN_TF              = "5 mins"
SCAN_DURATION        = "5 D"
EXCHANGES            = ["NYSE", "NASDAQ", "AMEX"]
LATEST_JSON          = "daytrading_top15_latest.json"


def _now_pair():
    import datetime
    now = datetime.datetime.now()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M:%S"), now_utc.isoformat()


# === Trin 1: TV-momentum-univers (sync; kaldes via to_thread) ==============
def fetch_momentum_universe() -> list[dict]:
    """Dagens gappers/RVOL fra TradingViews screener (samme query-moenster som
    tv_scanner/fetch_float — kopieret, ikke importeret). Fejler ALDRIG kalderen
    (net-/felt-/lib-problem -> tom liste)."""
    try:
        from tradingview_screener import Query, col
    except ImportError:
        return []
    try:
        where = [
            col("close").between(PRICE_MIN, PRICE_MAX),
            col("change") > MIN_CHANGE_PCT,
            col("relative_volume_10d_calc") > MIN_RVOL,
            col("average_volume_30d_calc") > MIN_AVG_VOL,
            col("exchange").isin(EXCHANGES),
            col("type") == "stock",
        ]
        if FLOAT_MAX is not None:
            where.append(col("float_shares_outstanding") < FLOAT_MAX)
        _, df = (
            Query()
            .select("name", "close", "change", "relative_volume_10d_calc",
                    "float_shares_outstanding", "float_shares_percent_current",
                    "volume", "average_volume_30d_calc")
            .where(*where)
            .order_by("change", ascending=False)
            .limit(UNIVERSE_MAX)
            .get_scanner_data()
        )
    except Exception as e:
        print(f"[day trading-scan] TV-query fejlede: {type(e).__name__}: {e}")
        return []
    if df is None or df.empty:
        return []

    def _f(v):
        try:
            v = float(v)
            return None if v != v else v          # NaN -> None
        except (TypeError, ValueError):
            return None

    rows = []
    for _, r in df.iterrows():
        full = r.get("ticker", "") or ""
        sym = full.split(":")[-1] if ":" in full else full
        if not sym or len(sym) > 5 or any(c in sym for c in (".", "/", "-")):
            continue
        rows.append({
            "symbol": sym,
            "price": _f(r.get("close")),
            "gap": _f(r.get("change")),
            "rvol": _f(r.get("relative_volume_10d_calc")),
            "float_shares": _f(r.get("float_shares_outstanding")),
            "float_pct": _f(r.get("float_shares_percent_current")),
            "volume": _f(r.get("volume")),
            "avg_vol": _f(r.get("average_volume_30d_calc")),
        })
    return rows


# === Trin 2: billig pre-rank (ingen IBKR) — vaelg de staerkeste SHORTLIST_N ==
def _prerank(rows: list[dict]) -> list[dict]:
    """Momentum-komposit (normaliseret change + rvol + lille lav-float-bonus) til
    UDVAELGELSE. Den endelige rangering er den fulde gatede score (trin 4)."""
    cand = [r for r in rows if r.get("gap") is not None and r.get("rvol") is not None]
    if not cand:
        return []

    def _norm(vals):
        lo, hi = min(vals), max(vals)
        rng = hi - lo
        return (lambda v: (v - lo) / rng) if rng > 0 else (lambda v: 0.5)

    nch = _norm([r["gap"] for r in cand])
    nrv = _norm([r["rvol"] for r in cand])

    def _score(r):
        s = nch(r["gap"]) + nrv(r["rvol"])
        fl = r.get("float_shares")
        if fl and fl > 0:                          # lav float = bonus
            s += 0.30 if fl < 10e6 else 0.15 if fl < 30e6 else 0.0
        return s

    return sorted(cand, key=_score, reverse=True)[:SHORTLIST_N]


# === Inkrementel skrivning (atomisk) =======================================
def _write_latest(rows: list[dict], started_utc, *, total, done, progress_done):
    import json
    out = []
    for rank, r in enumerate(rows, 1):
        out.append({**r, "rank": rank})
    gen_local, gen_utc = _now_pair()
    payload = {
        "generated_local": gen_local, "generated_utc": gen_utc,
        "source": "ibkr+tv", "count": len(out), "rows": out,
        "started_utc": started_utc,
        "progress": {"done": progress_done, "total": total},
    }
    path = os.path.join(_DIR, LATEST_JSON)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)                      # atomisk (UI laeser aldrig en halv fil)
    except OSError as e:
        print(f"[day trading-scan] kunne ikke skrive {LATEST_JSON}: {e}")


# === Trin 3-5: throttlet, inkrementel scan paa delt ib =====================
async def run_scan(ib) -> None:
    """In-process async scan paa den DELTE ib. Robust: en enkelt symbol-fejl
    draeber ikke scanen; hele loopet er omsluttet af try/except."""
    started_utc = _now_pair()[1]
    try:
        rows = await asyncio.to_thread(fetch_momentum_universe)
        shortlist = _prerank(rows)
        print(f"[day trading-scan] univers={len(rows)} -> shortlist={len(shortlist)}")
        if not shortlist:
            _write_latest([], started_utc, total=0, done=True, progress_done=0)
            return

        spy_bars = await fetch_benchmark_bars(ib, symbol="SPY", timeframe=SCAN_TF, duration=SCAN_DURATION)
        scored = []
        total = len(shortlist)
        for i, row in enumerate(shortlist, 1):
            sym = row["symbol"]
            supply_data = {
                "float_shares": row.get("float_shares"), "float_pct": row.get("float_pct"),
                "short_float_pct": None, "days_to_cover": None,
            }
            try:
                rep = await compute_daytrading_report(
                    ib, sym, timeframe=SCAN_TF, spy_bars=spy_bars, supply_data=supply_data,
                    with_chart=False)
                if rep["final"] is not None:
                    scored.append({
                        "symbol": sym,
                        "final": round(rep["final"], 1),
                        "band": _band(rep["final"]),
                        "gate": round(rep["gate"], 3),
                        "price": round(rep["price"], 2) if rep.get("price") is not None else None,
                        "gap_pct": round(row["gap"], 1) if row.get("gap") is not None else None,
                        "rvol": round(row["rvol"], 1) if row.get("rvol") is not None else None,
                        "float_shares": row.get("float_shares"),
                        "spread_pct": rep["gate_inputs"].get("spread_pct"),
                    })
                    print(f"  [{i}/{total}] {sym:<6} final={rep['final']:+.1f} ({_band(rep['final'])})")
                else:
                    print(f"  [{i}/{total}] {sym:<6} ingen score (sprunget)")
            except Exception as e:
                print(f"  [{i}/{total}] {sym:<6} FEJL: {type(e).__name__}: {e}")
            # inkrementel: skriv delresultat (sorteret) + fremdrift
            partial = sorted(scored, key=lambda x: x["final"], reverse=True)[:TOP_N]
            _write_latest(partial, started_utc, total=total, done=False, progress_done=i)
            await asyncio.sleep(SCAN_DELAY_SEC)    # PACING-throttle

        top = sorted(scored, key=lambda x: x["final"], reverse=True)[:TOP_N]
        _write_latest(top, started_utc, total=total, done=True, progress_done=total)
        print(f"[day trading-scan] faerdig: {len(top)} i top-listen (af {len(scored)} scorede).")
    except Exception as e:
        print(f"[day trading-scan] FEJL i scan: {type(e).__name__}: {e}")
        _write_latest([], started_utc, total=0, done=True, progress_done=0)
