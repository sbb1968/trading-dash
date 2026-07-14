#!/usr/bin/env python3
r"""
resample_bars.py — byg 3/5/10/15-min bars ud fra 1-min-CSV'er
══════════════════════════════════════════════════════════════
Companion til harvest_futures_1min.py. Laeser hver `*_1min.csv` og skriver
`*_3min.csv`, `*_5min.csv`, `*_10min.csv`, `*_15min.csv` med SAMME konvention:

  - timestamp = barens START-tid (IBKR-konvention), tz-aware ET.
  - resample(label="left", closed="left") -> en 5-min bar maerket 09:30 daekker 09:30–09:34.
  - anker "start_day" (midnat ET) -> rene buckets; RTH-aabning 09:30 falder paa en
    bucket-graense for alle fire tidsrammer (3/5/10/15).
  - OHLCV: open=first, high=max, low=min, close=last, volume=sum. Tomme buckets droppes.

Aggregerer PR. FIL = pr. kontrakt-maaned, saa juni- og september-serier aldrig blandes
(ingen roll-gap-forurening). Ren, offline, idempotent (overskriver output). Stdlib + pandas.

Brug (fra backend/, EFTER hoesten er koert):
    python resample_bars.py                      # alle *_1min.csv i data_harvest/ -> 3/5/10/15
    python resample_bars.py --minutes 5,15       # kun 5- og 15-min
    python resample_bars.py --symbols MES        # kun MES-filer
    python resample_bars.py --in data_harvest --out data_harvest
    python resample_bars.py --selftest           # verificér aggregeringen uden filer

Placering: C:\Projects\trading_dash\backend\resample_bars.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

DEFAULT_DIR      = Path("data_harvest")
DEFAULT_MINUTES  = [3, 5, 10, 15]
TZ               = "America/New_York"
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


# ═══════════════════════════════════════════════════════════════════
# Kerne — ren, unit-testbar
# ═══════════════════════════════════════════════════════════════════
def load_1min(path: Path) -> pd.DataFrame:
    """Laes en 1-min-CSV til en tz-aware (ET) OHLCV-DataFrame indekseret paa timestamp."""
    df = pd.read_csv(path)
    need = {"timestamp", "open", "high", "low", "close", "volume"}
    if not need.issubset(df.columns):
        raise ValueError(f"{path.name}: mangler kolonner {need - set(df.columns)}")
    # DST-sikker parsing: en fil der spaender et DST-skift (fx 8. marts 2026) har BLANDEDE
    # offsets (-05:00 EST foer, -04:00 EDT efter). utc=True parser ENTYDIGT til UTC (haandterer
    # blandede offsets), hvorefter tz_convert saetter EST/EDT korrekt pr. timestamp. Naive
    # timestamps (uden offset) lokaliseres til ET.
    s = df["timestamp"].astype(str)
    sample = s.iloc[0] if len(s) else ""
    has_tz = sample.endswith("Z") or ("+" in sample) or ("-" in sample[11:])
    if has_tz:
        ts = pd.to_datetime(s, utc=True).dt.tz_convert(TZ)
    else:
        ts = pd.to_datetime(s).dt.tz_localize(TZ, ambiguous=True, nonexistent="shift_forward")
    df = df.drop(columns=["timestamp"]).set_index(ts).sort_index()
    df.index.name = "timestamp"
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df


def resample_ohlcv(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregér 1-min -> N-min. Start-tids-label, anker midnat ET, tomme buckets droppet."""
    freq = f"{minutes}min"
    agg = df.resample(freq, label="left", closed="left", origin="start_day").agg(_AGG)
    agg = agg.dropna(subset=["open"])          # buckets uden 1-min bars = ingen handel -> drop
    agg["volume"] = agg["volume"].round().astype("int64")
    return agg


def write_bars(agg: pd.DataFrame, path: Path) -> None:
    out = agg.reset_index()
    out["timestamp"] = out["timestamp"].apply(lambda t: t.isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    out.to_csv(tmp, index=False, columns=["timestamp", "open", "high", "low", "close", "volume"])
    tmp.replace(path)


def out_name(base: str, minutes: int) -> str:
    return f"{base}_{minutes}min.csv"


# ═══════════════════════════════════════════════════════════════════
# Drift
# ═══════════════════════════════════════════════════════════════════
def find_inputs(in_dir: Path, symbols) -> list:
    files = sorted(in_dir.glob("*_1min.csv"))
    if symbols:
        want = set(s.strip().upper() for s in symbols)
        files = [f for f in files if f.name.split("_", 1)[0].upper() in want]
    return files


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

    files = find_inputs(in_dir, args.symbols.split(",") if args.symbols else None)
    if not files:
        print(f"Ingen *_1min.csv i {in_dir.resolve()}"
              f"{' (efter --symbols-filter)' if args.symbols else ''}.")
        return 0

    print("=" * 74)
    print(f"  RESAMPLE 1-min -> {minutes} min   ({len(files)} fil(er))   {in_dir} -> {out_dir}")
    print("=" * 74)

    for f in files:
        base = f.name[:-len("_1min.csv")]     # 'MES_202606'
        try:
            df = load_1min(f)
        except Exception as e:
            print(f"  ⚠ {f.name}: springer over ({e})")
            continue
        if df.empty:
            print(f"  ⚠ {f.name}: tom — springer over")
            continue
        span = f"{df.index[0]:%Y-%m-%d %H:%M} -> {df.index[-1]:%Y-%m-%d %H:%M} ET"
        print(f"\n  {f.name}: {len(df)} 1-min bars · {span}")
        for m in minutes:
            agg = resample_ohlcv(df, m)
            p = out_dir / out_name(base, m)
            write_bars(agg, p)
            print(f"     -> {p.name}: {len(agg)} {m}-min bars")

    print("\n  Faerdig.")
    return 0


# ═══════════════════════════════════════════════════════════════════
# Selftest — uden filer
# ═══════════════════════════════════════════════════════════════════
def selftest() -> int:
    import pytz
    et = pytz.timezone(TZ)
    # 15 x 1-min bars 09:30..09:44 ET; kendt OHLCV.
    idx = [et.localize(pd.Timestamp(2026, 6, 16, 9, 30) + pd.Timedelta(minutes=i)) for i in range(15)]
    rows = []
    for i in range(15):
        o = 100 + i
        rows.append({"open": o, "high": o + 2, "low": o - 1, "close": o + 0.5, "volume": 10 + i})
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx, name="timestamp"))

    agg5 = resample_ohlcv(df, 5)
    print("5-min buckets:")
    print(agg5)
    # 15 bars -> 3 buckets (09:30, 09:35, 09:40), hver = 5 sub-bars.
    assert len(agg5) == 3, f"forventede 3 buckets, fik {len(agg5)}"
    assert [t.strftime("%H:%M") for t in agg5.index] == ["09:30", "09:35", "09:40"], list(agg5.index)
    b0 = agg5.iloc[0]
    assert b0["open"] == 100.0, b0["open"]                 # first af bar 0..4
    assert b0["high"] == 100 + 4 + 2, b0["high"]           # max high = bar4.high = 106
    assert b0["low"] == 100 - 1, b0["low"]                 # min low = bar0.low = 99
    assert b0["close"] == 100 + 4 + 0.5, b0["close"]       # last close = bar4.close = 104.5
    assert b0["volume"] == sum(10 + i for i in range(5)), b0["volume"]   # 10+11+12+13+14=60
    print("  [5-min] open/high/low/close/volume korrekt · anker 09:30  OK")

    # 15-min: præcis 1 bucket der spænder hele 09:30..09:44.
    agg15 = resample_ohlcv(df, 15)
    assert len(agg15) == 1 and agg15.index[0].strftime("%H:%M") == "09:30", list(agg15.index)
    assert agg15.iloc[0]["high"] == 100 + 14 + 2, agg15.iloc[0]["high"]  # max = bar14.high = 116
    assert agg15.iloc[0]["volume"] == sum(10 + i for i in range(15)), agg15.iloc[0]["volume"]
    print("  [15-min] én bucket 09:30, high/volume korrekt  OK")

    # 10-min: buckets 09:30 (10 bars) + 09:40 (5 bars) — anker paa hele 10-min.
    agg10 = resample_ohlcv(df, 10)
    assert [t.strftime("%H:%M") for t in agg10.index] == ["09:30", "09:40"], list(agg10.index)
    print("  [10-min] buckets 09:30 + 09:40 (ren anker)  OK")

    # DST: en fil der krydser 8. marts 2026 har BLANDEDE offsets (-05:00 EST / -04:00 EDT).
    # load_1min skal parse dem uden at fejle og give korrekt ET.
    import os, tempfile
    csv_rows = [
        "timestamp,open,high,low,close,volume",
        "2026-03-06T10:00:00-05:00,100,101,99,100.5,10",    # EST (foer skift)
        "2026-03-06T10:01:00-05:00,100.5,101,99,100.6,12",
        "2026-03-09T10:00:00-04:00,110,111,109,110.5,20",   # EDT (efter skift)
        "2026-03-09T10:01:00-04:00,110.5,111,109,110.6,22",
    ]
    with tempfile.NamedTemporaryFile("w", suffix="_1min.csv", delete=False, newline="") as fh:
        fh.write("\n".join(csv_rows)); tmp = fh.name
    try:
        d2 = load_1min(Path(tmp))
        assert len(d2) == 4, len(d2)
        assert str(d2.index.tz) in ("America/New_York",), str(d2.index.tz)
        # begge dage viser 10:00 ET trods forskellige UTC-offsets
        assert [t.strftime("%m-%d %H:%M") for t in d2.index] == \
               ["03-06 10:00", "03-06 10:01", "03-09 10:00", "03-09 10:01"], list(d2.index)
        a5 = resample_ohlcv(d2, 5)
        assert len(a5) == 2, len(a5)     # to separate dage -> to buckets
    finally:
        os.unlink(tmp)
    print("  [DST] blandede offsets -05:00/-04:00 parses -> ET korrekt  OK")

    print("\nSELFTEST BESTAAET")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Byg 3/5/10/15-min bars ud fra 1-min-CSV'er.")
    ap.add_argument("--in", dest="in_dir", default=str(DEFAULT_DIR), help="input-mappe (default data_harvest)")
    ap.add_argument("--out", dest="out_dir", default=None, help="output-mappe (default = input-mappe)")
    ap.add_argument("--minutes", default="3,5,10,15", help="kommasepareret (default 3,5,10,15)")
    ap.add_argument("--symbols", default=None, help="filtrér paa symbol(er), fx MES,M2K")
    ap.add_argument("--selftest", action="store_true", help="verificér aggregeringen uden filer")
    args = ap.parse_args()
    if args.out_dir is None:
        args.out_dir = args.in_dir
    if args.selftest:
        return selftest()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
