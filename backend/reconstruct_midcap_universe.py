#!/usr/bin/env python3
"""
reconstruct_midcap_universe.py — point-in-time mid/large-cap-univers fra bar_cache
═══════════════════════════════════════════════════════════════════════════════════
Led 3 af 3. Bygger et HISTORISK per-dag-univers fra de downloadede 1-min bars, så
washout_reclaim_backtest.py kan køre mod mid/large-cap på samme måde som small-cap.

For hver handelsdag udvælges — KUN ud fra information kendt FØR dagen (intet
look-ahead) — de navne fra poolen der:
  • har pris (forrige dags close) i [price_min, price_max]
  • er likvide (gennemsnitsvolumen over forrige liq_lookback dage > min_avg_vol)
  • har mindst min_history forudgående handelsdage (warmup)
og rangeres så efter en intradag-volatilitets-PROXY (gennemsnitlig dags-range %
over forrige vol_lookback dage), hvoraf de top_n bedste tages.

⚠ ÆRLIGT FORBEHOLD — volatilitets-proxy, ikke ATRP|1W:
   Screenerens kriterium er ATRP|1W (ATR(14) på UGE-timeframe). En ægte uge-ATR(14)
   kræver ~14 ugers historik; vores cache er kun marts–maj (~13 uger), så den kan
   IKKE beregnes point-in-time for tidlige dage. Vi bruger derfor "gennemsnitlig
   dags-range %" som proxy for intradag-volatilitet. Den fanger samme HENSIGT
   (hvor meget bevæger navnet sig), men er ikke en eksakt reproduktion af
   screeneren. Poolen ER allerede ATRP|1W-filtreret (sourcet fra screeneren), så
   proxyen bruges primært til at RANGERE og cappe til top_n pr. dag.

Output: én JSON pr. kalendermåned, navngivet som small-cap-konventionen:
   historical_universe_midcap_<første>_<sidste>.json   (= {dato: [tickere]})
Brug dem så med washout_reclaim_backtest.py --universe-file <fil>.

Kun stdlib. Læser samme bar_cache-format som backtesten ({TICKER}_{start}_{end}_1min.csv).

Brug (fra backend/):
    python reconstruct_midcap_universe.py --start 2026-03-01 --end 2026-05-31
    python reconstruct_midcap_universe.py --start 2026-03-01 --end 2026-05-31 --top-n 25

Placering: C:\\Projects\\trading_dash\\backend\\reconstruct_midcap_universe.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date as date_cls, timedelta
from pathlib import Path

CACHE_DIRNAME = "bar_cache"

DEFAULTS = dict(top_n=25, price_min=5.0, price_max=50.0, min_avg_vol=500_000,
                vol_lookback=14, liq_lookback=30, min_history=20,
                perf_w_min=None, perf_w_days=5)


# ── cache-stier ───────────────────────────────────────────────────────────────
def _parse_cache_range(name: str):
    if not name.endswith("_1min.csv"):
        return None
    stem = name[:-len("_1min.csv")]
    parts = stem.rsplit("_", 2)
    if len(parts) != 3:
        return None
    t, s, e = parts
    try:
        return t, date_cls.fromisoformat(s), date_cls.fromisoformat(e)
    except ValueError:
        return None


def read_pool(args) -> set:
    """Pool af tilladte navne fra --tickers <fil> og/eller --symbols A,B,C."""
    syms: set = set()
    if getattr(args, "symbols", None):
        syms |= {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    if getattr(args, "tickers", None):
        for line in Path(args.tickers).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                syms.add(line.upper())
    return syms


def cached_tickers(cache_dir: Path) -> dict[str, list[Path]]:
    """Ticker → ALLE dens periode-cache-filer (≥5 dage).

    Rettet 3/8-2026. Foer returnerede den ÉN fil pr. ticker, valgt som den
    foerste glob'en stoedte paa, og kun filer der overlappede [start,end].
    Begge dele var forkerte:

      * bar_cache har flere filer pr. ticker (fx ABSI_2026-03-20_2026-04-30 og
        ABSI_2026-04-17_2026-05-29). Resten blev tavst smidt vaek, saa dagene
        i de oevrige filer fandtes ikke.
      * Udvaelgelsen SKAL bruge historik FOER start (min_history=20 dage,
        liq_lookback=30). Overlap-filteret sorterede netop den historik fra, saa
        de foerste uger af enhver periode blev bygget paa amputeret lookback.

    Nu returneres alt; load_daily_bars fletter og de-dublerer paa timestamp.
    """
    out: dict[str, list[Path]] = {}
    if not cache_dir.exists():
        return out
    for p in sorted(cache_dir.glob("*_1min.csv")):
        if p.stat().st_size <= 40:
            continue
        pr = _parse_cache_range(p.name)
        if not pr or (pr[2] - pr[1]).days < 5:
            continue
        out.setdefault(pr[0], []).append(p)
    return out


# ── daglig aggregering fra 1-min ──────────────────────────────────────────────
def _find_col(headers, *names):
    low = {h.lower(): h for h in headers}
    for n in names:
        if n in low:
            return low[n]
    return None


def load_daily_bars(paths) -> dict[date_cls, dict]:
    """1-min CSV'er → {date: {open,high,low,close,volume}} (RTH-dags-aggregat).

    Tager én sti eller en liste. Filer for samme ticker overlapper hinanden i tid,
    saa raekkerne de-dubleres paa timestamp foer aggregering — ellers ville
    dagsvolumen blive talt dobbelt paa overlaps-dagene.
    """
    if isinstance(paths, Path):
        paths = [paths]
    rows_by_ts: dict[str, dict] = {}
    for path in paths:
        with path.open(newline="") as f:
            rdr = csv.DictReader(f)
            if not rdr.fieldnames:
                continue
            tcol = _find_col(rdr.fieldnames, "timestamp", "date", "datetime", "time")
            ocol = _find_col(rdr.fieldnames, "open")
            hcol = _find_col(rdr.fieldnames, "high")
            lcol = _find_col(rdr.fieldnames, "low")
            ccol = _find_col(rdr.fieldnames, "close")
            vcol = _find_col(rdr.fieldnames, "volume", "vol")
            if not all([tcol, ocol, hcol, lcol, ccol]):
                continue
            for r in rdr:
                ts = (r.get(tcol) or "").strip()
                if len(ts) < 10:
                    continue
                rows_by_ts[ts] = {"open": r.get(ocol), "high": r.get(hcol),
                                  "low": r.get(lcol), "close": r.get(ccol),
                                  "volume": r.get(vcol) if vcol else None}
    if not rows_by_ts:
        return {}
    ocol, hcol, lcol, ccol, vcol = "open", "high", "low", "close", "volume"

    rows_by_date: dict[str, list] = defaultdict(list)
    for ts, r in rows_by_ts.items():
        rows_by_date[ts[:10]].append((ts, r))

    daily: dict[date_cls, dict] = {}
    for dstr, rows in rows_by_date.items():
        try:
            d = date_cls.fromisoformat(dstr)
        except ValueError:
            continue
        rows.sort(key=lambda x: x[0])  # ISO sorterer = kronologisk
        def fnum(r, col):
            try:
                return float(r[col])
            except (TypeError, ValueError, KeyError):
                return None
        opens = [fnum(r, ocol) for _, r in rows if fnum(r, ocol) is not None]
        highs = [fnum(r, hcol) for _, r in rows if fnum(r, hcol) is not None]
        lows  = [fnum(r, lcol) for _, r in rows if fnum(r, lcol) is not None]
        closes= [fnum(r, ccol) for _, r in rows if fnum(r, ccol) is not None]
        vols  = [fnum(r, vcol) for _, r in rows if vcol and fnum(r, vcol) is not None]
        if not (opens and highs and lows and closes):
            continue
        daily[d] = {
            "open": opens[0], "high": max(highs), "low": min(lows),
            "close": closes[-1], "volume": sum(vols) if vols else 0.0,
        }
    return daily


# ── udvælgelse (ren logik, unit-testbar) ──────────────────────────────────────
def select_for_day(daily: dict[date_cls, dict], sorted_dates: list[date_cls],
                   d: date_cls, p: dict):
    """Returnér (kvalificerer: bool, vol_proxy: float|None) for ét navn på dag d.

    Alle beregninger bruger KUN dage STRENGT FØR d (intet look-ahead). At navnet
    har en bar på d (tradeable) er det eneste der rører d's data.
    """
    if d not in daily:
        return False, None
    prior = [dt for dt in sorted_dates if dt < d]
    if len(prior) < p["min_history"]:
        return False, None

    prior_close = daily[prior[-1]]["close"]
    if not (p["price_min"] <= prior_close <= p["price_max"]):
        return False, None

    liq_days = prior[-p["liq_lookback"]:]
    avg_vol = sum(daily[dt]["volume"] for dt in liq_days) / len(liq_days)
    if avg_vol <= p["min_avg_vol"]:
        return False, None

    # Perf 1W — ugens afkast, maalt paa dage STRENGT FOER d (intet look-ahead).
    # Live-screeneren kraever Perf.W > 6 %; uden dette filter handlede backtesten
    # ogsaa navne i nedtrend, hvor "koeb dykket" bliver "grib den faldende kniv".
    # Det er den ENESTE af de nye screener-betingelser der kan reproduceres
    # eksakt fra daglige bars — market cap kraever fundamentals, og TradingViews
    # Volatility.M kan kun tilnaermes (vol_proxy nedenfor er dags-range, ikke .M).
    if p.get("perf_w_min") is not None:
        k = p.get("perf_w_days", 5)
        if len(prior) < k + 1:
            return False, None
        for_close = daily[prior[-(k + 1)]]["close"]
        if for_close <= 0:
            return False, None
        perf_w = (prior_close - for_close) / for_close * 100.0
        if perf_w < p["perf_w_min"]:
            return False, None

    vol_days = prior[-p["vol_lookback"]:]
    ranges = []
    for dt in vol_days:
        b = daily[dt]
        if b["close"] > 0:
            ranges.append((b["high"] - b["low"]) / b["close"] * 100.0)
    if not ranges:
        return False, None
    vol_proxy = sum(ranges) / len(ranges)
    return True, vol_proxy


def build_universe(tickers_daily: dict[str, dict], start: date_cls, end: date_cls, p: dict):
    """Returnér {date_str: [tickere]} for hele [start,end] (kun ikke-tomme dage)."""
    sorted_by_t = {t: sorted(daily) for t, daily in tickers_daily.items()}
    universe: dict[str, list] = {}
    d = start
    while d <= end:
        if d.weekday() < 5:  # man-fre
            cands = []
            for t, daily in tickers_daily.items():
                ok, score = select_for_day(daily, sorted_by_t[t], d, p)
                if ok:
                    cands.append((score, t))
            if cands:
                cands.sort(key=lambda x: (-x[0], x[1]))  # vol-proxy desc, så ticker
                universe[d.isoformat()] = [t for _, t in cands[:p["top_n"]]]
        d += timedelta(days=1)
    return universe


# ── output: én fil pr. kalendermåned ──────────────────────────────────────────
def split_by_month(universe: dict[str, list]) -> dict[tuple, dict]:
    by_month: dict[tuple, dict] = defaultdict(dict)
    for dstr, ticks in universe.items():
        d = date_cls.fromisoformat(dstr)
        by_month[(d.year, d.month)][dstr] = ticks
    return by_month


def main() -> int:
    ap = argparse.ArgumentParser(description="Rekonstruér point-in-time mid/large-cap-univers fra bar_cache")
    ap.add_argument("--bar-cache", default="bar_cache")
    ap.add_argument("--tickers", help="pool-fil (fx midcap.txt) — begræns universet til DISSE navne (anbefalet)")
    ap.add_argument("--symbols", help="komma-separeret pool, fx PLTR,SOFI,RIVN")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--top-n", type=int, default=DEFAULTS["top_n"])
    ap.add_argument("--price-min", type=float, default=DEFAULTS["price_min"])
    ap.add_argument("--price-max", type=float, default=DEFAULTS["price_max"])
    ap.add_argument("--min-avg-vol", type=float, default=DEFAULTS["min_avg_vol"])
    ap.add_argument("--vol-lookback", type=int, default=DEFAULTS["vol_lookback"])
    ap.add_argument("--liq-lookback", type=int, default=DEFAULTS["liq_lookback"])
    ap.add_argument("--min-history", type=int, default=DEFAULTS["min_history"])
    ap.add_argument("--perf-w-min", type=float, default=DEFAULTS["perf_w_min"],
                    help="kraev ugens afkast >= dette %% (live-screeneren: 6.0). "
                         "Udeladt = intet Perf-filter, som foer 3/8-2026")
    ap.add_argument("--perf-w-days", type=int, default=DEFAULTS["perf_w_days"],
                    help="handelsdage i 'en uge' (default 5)")
    args = ap.parse_args()

    cache_dir = Path(args.bar_cache)
    if not cache_dir.is_absolute():
        cache_dir = Path.cwd() / cache_dir
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = date_cls.fromisoformat(args.start)
    end = date_cls.fromisoformat(args.end)
    p = dict(top_n=args.top_n, price_min=args.price_min, price_max=args.price_max,
             min_avg_vol=args.min_avg_vol, vol_lookback=args.vol_lookback,
             liq_lookback=args.liq_lookback, min_history=args.min_history,
             perf_w_min=args.perf_w_min, perf_w_days=args.perf_w_days)

    files = cached_tickers(cache_dir)
    if not files:
        print(f"Ingen periode-cache-filer i {cache_dir}.")
        return 1

    pool = read_pool(args)
    if pool:
        in_pool = {t: pth for t, pth in files.items() if t in pool}
        missing = sorted(set(pool) - set(in_pool))
        print(f"Pool: {len(pool)} navne — {len(in_pool)} cachet, {len(missing)} mangler endnu.")
        if missing:
            head = ", ".join(missing[:30]) + (" ..." if len(missing) > 30 else "")
            print(f"  Ikke cachet endnu (springes over): {head}")
        files = in_pool
        if not files:
            print("Ingen af pool-navnene er cachet endnu — vent på download.")
            return 1
    else:
        print("⚠ ADVARSEL: ingen --tickers/--symbols pool angivet — bruger ALLE")
        print(f"  {len(files)} cachede tickere. bar_cache deles med det GAMLE small-cap-")
        print("  univers, så universet bliver KONTAMINERET med small-caps og er IKKE et")
        print("  rent mid/large-cap-univers. Angiv --tickers midcap.txt for et rent univers.\n")

    print(f"Indlæser {len(files)} tickere fra {cache_dir} ...")
    tickers_daily: dict[str, dict] = {}
    for t, paths in sorted(files.items()):
        daily = load_daily_bars(paths)
        if daily:
            tickers_daily[t] = daily
    print(f"  {len(tickers_daily)} tickere med brugbare daglige bars.\n")

    universe = build_universe(tickers_daily, start, end, p)
    if not universe:
        print("Tomt univers — for lidt historik, eller ingen navne passer filtrene.")
        print("(Tjek --min-history vs hvor langt cachen rækker; tidlige dage er warmup.)")
        return 1

    by_month = split_by_month(universe)
    print(f"{'Måned':>10}  {'Dage':>5}  {'Snit/dag':>9}  {'Distinkte':>9}")
    print(f"{'─'*10}  {'─'*5}  {'─'*9}  {'─'*9}")
    written = []
    for (y, m), days in sorted(by_month.items()):
        dates = sorted(days)
        all_t = sorted({t for ts in days.values() for t in ts})
        avg = sum(len(v) for v in days.values()) / len(days)
        first, last = dates[0], dates[-1]
        fn = out_dir / f"historical_universe_midcap_{first}_{last}.json"
        fn.write_text(json.dumps(days, indent=2), encoding="utf-8")
        written.append(fn)
        print(f"{y}-{m:02d}  {len(days):>5}  {avg:>9.1f}  {len(all_t):>9}   → {fn.name}")

    print(f"\n{len(written)} fil(er) skrevet til {out_dir}/")
    print("Kør washout-backtesten mod dem, fx:")
    for fn in written:
        print(f"    python washout_reclaim_backtest.py --universe-file {fn.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
