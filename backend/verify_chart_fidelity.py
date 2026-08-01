#!/usr/bin/env python3
"""
verify_chart_fidelity.py
────────────────────────
Fase 1b, punkt 4 — verifikations-harness.

Sammenligner for hver lukket handel de GEMTE snapshot-bars (payload.chart_bars, det
algoen faktisk saa) med de GEN-HENTEDE bars fra IBKR (samme sti Handels-charten brugte
foer snapshottet). Saa ved vi EMPIRISK hvor stabil IBKR er paa vores navne: reviderer de
historiske bars? Forsvinder udloebne futures? Er 1-min historik intakt?

Rent read-only diagnoselag. Roerer IKKE handelsstien.

Brug (paa en workstation med TWS forbundet):
    python verify_chart_fidelity.py                 # sidste 30 dage, alle kilder
    python verify_chart_fidelity.py --days 60 --source "Konfluens 2"
    python verify_chart_fidelity.py --csv afvig.csv # skriv pr-handel-rapport til CSV
    python verify_chart_fidelity.py --selftest      # ingen DB/IBKR — test sammenlignings-logikken

Kolonner pr. handel: n_snap, n_refetch, n_match (faelles bar-timestamps), close_max_afv%
(stoerste relative close-afvigelse), close_med_afv%, n_over_tol (bars over --tol), verdikt.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import trade_chart
import trade_queries


# ═══════════════════════════════════════════════════════════════════
# Ren sammenlignings-logik (unit-testbar uden DB/IBKR)
# ═══════════════════════════════════════════════════════════════════
def compare_bars(snap_df: pd.DataFrame, refetch_df: pd.DataFrame,
                 tol_pct: float = 0.05) -> dict:
    """Sammenlign to ET-indekserede OHLCV-DataFrames paa faelles bar-timestamps.

    Returnerer en dict med afvigelses-statistik. Afvigelse maales paa Close (relativt,
    i procent). tol_pct = graense (%) for hvornaar en bar taeller som "afvigende".

    Robust: tom overlap -> n_match=0 og None-felter. Sammenligner kun bars hvis timestamp
    findes i BEGGE (indre join) — mismatch i antal rapporteres separat.
    """
    out = {
        "n_snap":       int(len(snap_df)) if snap_df is not None else 0,
        "n_refetch":    int(len(refetch_df)) if refetch_df is not None else 0,
        "n_match":      0,
        "close_max_afv_pct": None,
        "close_med_afv_pct": None,
        "n_over_tol":   0,
        "worst_ts":     None,
    }
    if snap_df is None or refetch_df is None or snap_df.empty or refetch_df.empty:
        return out

    common = snap_df.index.intersection(refetch_df.index)
    out["n_match"] = int(len(common))
    if len(common) == 0:
        return out

    s = snap_df.loc[common, "Close"].astype(float)
    r = refetch_df.loc[common, "Close"].astype(float)
    denom = s.abs().where(s.abs() > 1e-9, 1e-9)
    afv_pct = ((s - r).abs() / denom) * 100.0

    out["close_max_afv_pct"] = round(float(afv_pct.max()), 4)
    out["close_med_afv_pct"] = round(float(afv_pct.median()), 4)
    out["n_over_tol"] = int((afv_pct > tol_pct).sum())
    try:
        out["worst_ts"] = afv_pct.idxmax().isoformat()
    except Exception:
        out["worst_ts"] = None
    return out


def verdict(cmp: dict, tol_pct: float) -> str:
    """GROEN/GUL/ROED pr. handel ud fra sammenligningen."""
    if cmp["n_snap"] == 0:
        return "INGEN-SNAP"          # gammel handel uden gemt snapshot
    if cmp["n_refetch"] == 0:
        return "INGEN-REFETCH"       # IBKR gav intet (udloebet kontrakt slettet / feed nede)
    if cmp["n_match"] == 0:
        return "INGEN-OVERLAP"       # tidsstempler passer slet ikke (tz/kontrakt-fejl)
    if cmp["close_max_afv_pct"] is None:
        return "?"
    if cmp["n_over_tol"] == 0:
        return "GROEN"               # alle faelles bars inden for tolerance
    if cmp["close_max_afv_pct"] <= tol_pct * 5:
        return "GUL"                 # smaa afvigelser paa nogle bars
    return "ROED"                    # store afvigelser — stol ikke paa gen-hentning her


# ═══════════════════════════════════════════════════════════════════
# Selftest — ingen DB/IBKR
# ═══════════════════════════════════════════════════════════════════
def _mk_df(start, n, close0=100.0, step=0.5):
    import pytz
    et = pytz.timezone("America/New_York")
    idx = [et.localize(start) + timedelta(minutes=15 * i) for i in range(n)]
    rows = [{"Open": close0 + i * step, "High": close0 + i * step + 1,
             "Low": close0 + i * step - 1, "Close": close0 + i * step, "Volume": 1000}
            for i in range(n)]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def selftest() -> int:
    ok = True
    base = datetime(2026, 6, 16, 4, 0)

    # 1) Identiske -> 0 afvigelse, GROEN
    a = _mk_df(base, 10)
    c = compare_bars(a, a.copy(), tol_pct=0.05)
    assert c["n_match"] == 10 and c["close_max_afv_pct"] == 0.0, c
    assert verdict(c, 0.05) == "GROEN", verdict(c, 0.05)
    print(f"  [1] identiske -> n_match={c['n_match']} max_afv={c['close_max_afv_pct']}% GROEN  OK")

    # 2) Konstant offset (roll-gap-lignende) -> stor afvigelse, ROED
    b = a.copy(); b["Close"] = b["Close"] + 50.0
    c2 = compare_bars(a, b, tol_pct=0.05)
    assert c2["n_over_tol"] == 10 and verdict(c2, 0.05) == "ROED", (c2, verdict(c2, 0.05))
    print(f"  [2] +50 offset -> n_over_tol={c2['n_over_tol']} max_afv={c2['close_max_afv_pct']}% ROED  OK")

    # 3) Delvis overlap (forskudt vindue) -> kun faelles bars sammenlignes
    d = _mk_df(datetime(2026, 6, 16, 5, 0), 10)   # starter 4 bars senere
    c3 = compare_bars(a, d, tol_pct=0.05)
    print(f"  [3] forskudt vindue -> n_snap={c3['n_snap']} n_refetch={c3['n_refetch']} n_match={c3['n_match']}  OK")
    assert 0 < c3["n_match"] < 10, c3

    # 4) Ingen overlap -> INGEN-OVERLAP
    e = _mk_df(datetime(2026, 6, 20, 4, 0), 5)
    c4 = compare_bars(a, e, tol_pct=0.05)
    assert c4["n_match"] == 0 and verdict(c4, 0.05) == "INGEN-OVERLAP", (c4, verdict(c4, 0.05))
    print(f"  [4] ingen overlap -> n_match=0 INGEN-OVERLAP  OK")

    # 5) Tom refetch -> INGEN-REFETCH
    c5 = compare_bars(a, pd.DataFrame(), tol_pct=0.05)
    assert verdict(c5, 0.05) == "INGEN-REFETCH", verdict(c5, 0.05)
    print(f"  [5] tom refetch -> INGEN-REFETCH  OK")

    print("\nSELFTEST BESTAAET" if ok else "\nSELFTEST FEJLEDE")
    return 0 if ok else 1


# ═══════════════════════════════════════════════════════════════════
# Live-koersel mod DB + IBKR
# ═══════════════════════════════════════════════════════════════════
async def run(args) -> int:
    import aiosqlite
    from ibkr_connect import IBKRConnection

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"FEJL: journal-DB ikke fundet: {db_path.resolve()}")
        return 2

    date_from = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    conn = IBKRConnection(paper_trading=True)
    connected = await conn.connect()
    if not connected:
        print("FEJL: kunne ikke forbinde til IBKR (TWS/Gateway). Kraeves for gen-hentning.")
        return 3

    db = await aiosqlite.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        trades = await trade_queries.list_trades(
            db, date_from=date_from, source=args.source, status="closed", limit=args.limit)
    finally:
        pass

    # Kun handler MED gemt snapshot er meningsfulde at verificere (ellers intet at sammenligne).
    rows = []
    print(f"\nVerificerer {len(trades)} lukkede handler (>= {date_from}"
          f"{', ' + args.source if args.source else ''}) — tol {args.tol}%\n")
    header = f"{'dato':10} {'symbol':7} {'kilde':16} {'n_snap':>6} {'n_ref':>6} {'n_match':>7} {'max%':>7} {'med%':>7} {'>tol':>5}  verdikt"
    print(header); print("-" * len(header))

    n_verified = 0
    for t in trades:
        snap_df = trade_chart.bars_from_snapshot(t)
        entry_dt = trade_chart._parse_iso_utc(t.get("entry_time_utc"))
        exit_dt = trade_chart._parse_iso_utc(t.get("exit_time_utc"))
        if not (entry_dt and exit_dt and t.get("symbol")):
            continue

        refetch_df = pd.DataFrame()
        try:
            refetch_df = await trade_chart.fetch_trade_bars(
                conn, t["symbol"], t.get("source", ""), entry_dt, exit_dt)
        except Exception as e:
            print(f"  (gen-hentning fejlede {t.get('symbol')}: {e})")

        cmp = compare_bars(snap_df, refetch_df, tol_pct=args.tol)
        v = verdict(cmp, args.tol)
        dato = (t.get("entry_time_et") or "")[:10]
        print(f"{dato:10} {str(t.get('symbol')):7} {str(t.get('source'))[:16]:16} "
              f"{cmp['n_snap']:>6} {cmp['n_refetch']:>6} {cmp['n_match']:>7} "
              f"{('' if cmp['close_max_afv_pct'] is None else cmp['close_max_afv_pct']):>7} "
              f"{('' if cmp['close_med_afv_pct'] is None else cmp['close_med_afv_pct']):>7} "
              f"{cmp['n_over_tol']:>5}  {v}")
        rows.append({"date": dato, "symbol": t.get("symbol"), "source": t.get("source"),
                     "trade_id": t.get("trade_id"), **cmp, "verdict": v})
        if cmp["n_snap"] > 0:
            n_verified += 1

    await db.close()
    # IBKRConnection.disconnect() er SYNKRON og returnerer None. Den blev await'et,
    # hvilket kastede TypeError paa vej ud — efter alt arbejdet var gjort, saa
    # harnessen aldrig naaede at printe sin opsummering.
    if hasattr(conn, "disconnect"):
        conn.disconnect()

    # Opsummering
    from collections import Counter
    tally = Counter(r["verdict"] for r in rows)
    print("\nOpsummering:", dict(tally))
    print(f"  {n_verified}/{len(rows)} handler havde et gemt snapshot at verificere.")
    green = tally.get("GROEN", 0)
    if n_verified:
        print(f"  {green}/{n_verified} snapshot-handler matcher gen-hentning inden for tolerance "
              f"({round(100*green/n_verified,1)}%).")

    if args.csv and rows:
        pd.DataFrame(rows).to_csv(args.csv, index=False)
        print(f"\nSkrev pr-handel-rapport: {args.csv}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Verificér snapshot-bars mod IBKR-gen-hentning pr. handel.")
    ap.add_argument("--days", type=int, default=30, help="hvor mange dage tilbage (default 30)")
    ap.add_argument("--source", type=str, default=None, help="filtrér paa én algo (fx 'Konfluens 2')")
    ap.add_argument("--tol", type=float, default=0.05, help="tolerance i %% for close-afvigelse (default 0.05)")
    ap.add_argument("--limit", type=int, default=200, help="maks antal handler")
    ap.add_argument("--db", type=str, default="trading_dash.db", help="sti til journal-DB")
    ap.add_argument("--csv", type=str, default=None, help="skriv pr-handel-rapport til denne CSV")
    ap.add_argument("--selftest", action="store_true", help="test sammenlignings-logikken uden DB/IBKR")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
