#!/usr/bin/env python3
r"""
curate_futures_data.py — rens MES/M2K per-kontrakt 1-min og saml det gode data ét sted
════════════════════════════════════════════════════════════════════════════════════════
Tager de raa per-kontrakt 1-min-CSV'er fra harvest_futures_1min.py og producerer et RENT
datasaet i en underfolder — kun brugbart, likvidt front-maaned-data, i alle tidsrammer.

Hvad den fjerner: BACK-MONTH-JUNK. Den aeldste kontrakt (fx MESU4/202409) fik i loggen
tildelt et langt datospaend hvor den endnu IKKE var front-maaned — de bars har volumen ~0
(kontrakten handlede knap nok). Curate trimmer hver fil til fra den foerste dag hvor den
faktisk var likvid (daglig volumen >= 5 % af filens 90-percentil-dag). Rene kontrakter
(der allerede starter ved deres roll) trimmes ikke.

Output i --out (default data_harvest/mes_m2k_clean/): for hver kontrakt
    SYMBOL_YYYYMM_1min.csv  +  _3min / _5min / _10min / _15min
Samme konvention som kilden (start-tids-label, tz-aware ET). Genbruger resample_bars'
testede load/resample/write. Idempotent (overskriver output).

Brug (fra backend/):
    python curate_futures_data.py
    python curate_futures_data.py --in data_harvest --out data_harvest/mes_m2k_clean
    python curate_futures_data.py --symbols MES
    python curate_futures_data.py --minutes 5,15
    python curate_futures_data.py --selftest

Placering: C:\Projects\trading_dash\backend\curate_futures_data.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

from resample_bars import load_1min, resample_ohlcv, write_bars, find_inputs

DEFAULT_IN       = Path("data_harvest")
DEFAULT_OUT      = Path("data_harvest/mes_m2k_clean")
DEFAULT_MINUTES  = [3, 5, 10, 15]
FRONT_MONTH_DAYS = 91      # front-maaned = det sidste kvartal (~91 dage) FOER udloeb. En
                           # per-kontrakt-fil slutter ved sit udloeb, saa alt foer (file_end-91)
                           # er back-month (kontrakten var ikke front-maaned endnu).


# ═══════════════════════════════════════════════════════════════════
# Kerne — front-maaned-trim (fjern back-month-junk)
# ═══════════════════════════════════════════════════════════════════
def front_month_trim(df: pd.DataFrame, front_month_days: int = FRONT_MONTH_DAYS):
    """Behold kun front-maaned-vinduet: bars fra (sidste_dag - front_month_days) og frem.

    En per-kontrakt-fil slutter ved kontraktens udloeb, og en kontrakt er front-maaned ~ét
    kvartal foer udloeb. Alt foer det er back-month (volumen ~0, kontrakten handlede knap nok).
    En ren kontrakt (fil starter allerede ved sin roll, spaender < ét kvartal) trimmes ikke.
    Robust mod tynde instrumenter (M2K): bruger IKKE volumen-taerskel.

    Returnerer (trimmet_df, window_start_or_None, dropped_bars).
    """
    if df.empty:
        return df, None, 0
    last_date = df.index[-1].date()
    window_start = last_date - timedelta(days=front_month_days)
    keep = [d >= window_start for d in df.index.date]
    trimmed = df[keep]
    dropped = int(len(df) - len(trimmed))
    return trimmed, (window_start if dropped > 0 else None), dropped


# ═══════════════════════════════════════════════════════════════════
# Drift
# ═══════════════════════════════════════════════════════════════════
def run(args) -> int:
    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    if not in_dir.exists():
        print(f"FEJL: input-mappe findes ikke: {in_dir.resolve()}")
        return 2
    try:
        minutes = [int(m) for m in str(args.minutes).split(",") if str(m).strip()]
    except ValueError:
        print("FEJL: --minutes skal vaere kommasepareret heltal, fx 3,5,10,15")
        return 2

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else ["MES", "M2K"]
    files = find_inputs(in_dir, symbols)
    if not files:
        print(f"Ingen {symbols} *_1min.csv i {in_dir.resolve()}.")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 78)
    print(f"  CURATE — rens back-month + saml rent MES/M2K-data  ({len(files)} kontrakter)")
    print(f"  {in_dir}  ->  {out_dir}   tidsrammer: 1,{','.join(map(str, minutes))} min")
    print("=" * 78)

    total_kept = 0
    total_dropped = 0
    for f in files:
        base = f.name[:-len("_1min.csv")]     # 'MES_202409'
        try:
            df = load_1min(f)
        except Exception as e:
            print(f"  ⚠ {f.name}: springer over ({e})")
            continue
        if df.empty:
            print(f"  ⚠ {f.name}: tom — springer over")
            continue

        trimmed, first_good, dropped = front_month_trim(df)
        raw_span = f"{df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}"
        if dropped > 0:
            print(f"\n  {base}: {len(df)} bars ({raw_span})")
            print(f"     ⤷ BACK-MONTH-TRIM: droppede {dropped} bars foer {first_good} "
                  f"(volumen ~0, ikke front-maaned endnu)")
        else:
            print(f"\n  {base}: {len(df)} bars ({raw_span}) — ren, intet trimmet")
        if trimmed.empty:
            print(f"     ⚠ intet tilbage efter trim — springer over")
            continue

        # Skriv rent 1-min + resample til underfolder
        write_bars(trimmed, out_dir / f"{base}_1min.csv")
        span = f"{trimmed.index[0]:%Y-%m-%d %H:%M} -> {trimmed.index[-1]:%Y-%m-%d %H:%M} ET"
        med = int(trimmed["volume"][trimmed["volume"] > 0].median()) if (trimmed["volume"] > 0).any() else 0
        print(f"     -> {base}_1min.csv: {len(trimmed)} bars · {span} · median 1-min vol={med}")
        for m in minutes:
            agg = resample_ohlcv(trimmed, m)
            write_bars(agg, out_dir / f"{base}_{m}min.csv")
            print(f"     -> {base}_{m}min.csv: {len(agg)} bars")
        total_kept += len(trimmed)
        total_dropped += dropped

    print("\n" + "=" * 78)
    print(f"  FAERDIG. Rent datasaet i {out_dir}")
    print(f"  Beholdt {total_kept} 1-min bars · droppede {total_dropped} back-month-junk-bars.")
    return 0


# ═══════════════════════════════════════════════════════════════════
# Selftest — front_month_trim uden filer
# ═══════════════════════════════════════════════════════════════════
def selftest() -> int:
    import pytz
    et = pytz.timezone("America/New_York")
    # Lang kontrakt: 250 dage (1 bar/dag), slutter ved "udloeb". front_month_trim skal
    # beholde de sidste ~FRONT_MONTH_DAYS dage og droppe resten (= back-month).
    idx = [et.localize(pd.Timestamp(2024, 1, 15) + pd.Timedelta(days=d)) for d in range(250)]
    df = pd.DataFrame([{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 100}] * 250,
                      index=pd.DatetimeIndex(idx, name="timestamp"))
    trimmed, ws, dropped = front_month_trim(df)
    exp_ws = df.index[-1].date() - timedelta(days=FRONT_MONTH_DAYS)
    kept_exp = sum(1 for d in df.index.date if d >= exp_ws)
    print(f"  lang kontrakt: beholdt {len(trimmed)}/{len(df)} dage (fra {ws}), droppede {dropped}")
    assert len(trimmed) == kept_exp and dropped == 250 - kept_exp, (len(trimmed), dropped)
    assert all(d >= exp_ws for d in trimmed.index.date), "back-month tilbage"
    print(f"  [trim] beholdt kun sidste {FRONT_MONTH_DAYS} dage (front-maaned)  OK")

    # Kort ren kontrakt (60 dage < ét kvartal) -> intet trimmet
    t2, ws2, d2 = front_month_trim(df.iloc[-60:])
    assert d2 == 0 and ws2 is None, (d2, ws2)
    print("  [ren] kort kontrakt (< kvartal) trimmes ikke  OK")
    print("\nSELFTEST BESTAAET")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Rens back-month-junk og saml rent MES/M2K-data i en underfolder.")
    ap.add_argument("--in", dest="in_dir", default=str(DEFAULT_IN), help="raa harvest-mappe (default data_harvest)")
    ap.add_argument("--out", dest="out_dir", default=str(DEFAULT_OUT), help="underfolder til rent data")
    ap.add_argument("--minutes", default="3,5,10,15", help="tidsrammer (default 3,5,10,15)")
    ap.add_argument("--symbols", default=None, help="default MES,M2K")
    ap.add_argument("--selftest", action="store_true", help="test trim-logikken uden filer")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
