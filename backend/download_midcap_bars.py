#!/usr/bin/env python3
"""
download_midcap_bars.py — hent 1-min bars for mid/large-cap-universet til bar_cache
═══════════════════════════════════════════════════════════════════════════════════
Henter historiske 1-min RTH bars fra IBKR for en liste af aktier og gemmer dem i
SAMME bar_cache-format som K2/small-cap-backtesten bruger ({TICKER}_{start}_{end}_
1min.csv), så washout_reclaim_backtest.py kan køre mod mid/large-cap uændret.

Dette er led 2 af 3:
  1. Kandidat-liste: kør din fetch_tv_intraday_volatility-screener BREDT (lavt/intet
     top-N-loft) og gem navnene til en fil, ét symbol pr. linje. Den anvender allerede
     markedsværdi-filteret ($5B+), så poolen ER mid/large-cap; vi henter bare bars.
  2. → DETTE SCRIPT: download bars for poolen.
  3. Rekonstruér per-dag-universet fra bars (separat script, efter download).

KØR I ET SIKKERT VINDUE (weekend / efter US-luk). En stor 1-min-download er mange
hundrede til tusinder af IBKR-kald og kan tage TIMER; kørt samtidig med K2 kan den
æde pacing-budgettet. Brug et eget client-id (default 31).

Robust: resumér (springer tickers der allerede er cachet for intervallet over),
pacing-backoff (sover 60s og prøver igen ved pacing-fejl), fortsætter ved fejl på
enkelt-tickers og rapporterer dem til sidst. --dry-run estimerer omfang uden at hente.

Køres fra backend/ (hvor bar_cache ligger):
    python download_midcap_bars.py --tickers midcap.txt --start 2026-03-01 --end 2026-05-31
    python download_midcap_bars.py --symbols PLTR,SOFI,RIVN --start 2026-03-01 --end 2026-05-31
    python download_midcap_bars.py --tickers midcap.txt --start 2026-03-01 --end 2026-05-31 --dry-run

Kun ib_async + stdlib. Python 3.14: event-loop-fix øverst.

Placering: C:\\Projects\\trading_dash\\backend\\download_midcap_bars.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import sys
import time
from datetime import datetime, date as date_cls, time as dtime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone.utc

CACHE_DIRNAME = "bar_cache"
PACING_CODES = {162, 420}                       # pacing-violation surfacerer typisk her
BENIGN = {2104, 2106, 2107, 2108, 2158, 2119, 2100, 2150, 2103, 2105}


# ── Hjælpere (ren logik, unit-testbar) ────────────────────────────────────────
def read_tickers(args) -> list[str]:
    syms: list[str] = []
    if args.symbols:
        syms += [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.tickers:
        for line in Path(args.tickers).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                syms.append(line.upper())
    # dedup, bevar rækkefølge
    seen, out = set(), []
    for s in syms:
        if s not in seen:
            seen.add(s); out.append(s)
    return out


def cache_path(cache_dir: Path, ticker: str, start: date_cls, end: date_cls) -> Path:
    return cache_dir / f"{ticker}_{start.isoformat()}_{end.isoformat()}_1min.csv"


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


def existing_cover(cache_dir: Path, ticker: str, start: date_cls, end: date_cls):
    """Returnér en eksisterende cache-fil hvis interval DÆKKER [start,end] (resumér)."""
    if not cache_dir.exists():
        return None
    for p in cache_dir.glob(f"{ticker}_*_1min.csv"):
        pr = _parse_cache_range(p.name)
        if pr and pr[1] <= start and pr[2] >= end:
            return p
    return None


def request_windows(start_date: date_cls, end_date: date_cls, window_days: int):
    """endDateTime'er (UTC) der vandrer BAGLÆNS fra end til start, ét pr. vindue."""
    out = []
    cur = datetime.combine(end_date, dtime(20, 0), tzinfo=ET)   # 20:00 ET = efter US-luk
    floor = datetime.combine(start_date, dtime(0, 0), tzinfo=ET)
    while cur > floor:
        out.append(cur.astimezone(timezone.utc))
        cur = cur - timedelta(days=window_days)
    return out


def _to_et(ts):
    """IBKR bar.date → ET-aware datetime (håndterer epoch-int/float, datetime, str)."""
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET)
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(ET)
    except (TypeError, ValueError):
        s = str(ts).strip().replace("US/Eastern", "").strip()
        for fmt in ("%Y%m%d %H:%M:%S", "%Y%m%d  %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=ET)
            except ValueError:
                continue
        raise


def write_csv(path: Path, rows: list[tuple]):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        w.writerows(rows)


# ── IBKR-hentning ─────────────────────────────────────────────────────────────
async def fetch_ticker(ib, ticker, start_date, end_date, window_days, sleep_s, errbox):
    from ib_async import Stock
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(ticker, "SMART", "USD")), timeout=15)
    except Exception as e:
        return None, f"qualify-fejl: {e}"
    if not q:
        return None, "kunne ikke kvalificeres"
    contract = q[0]

    by_ts: dict[str, tuple] = {}
    dur = f"{window_days} D"
    for end_dt in request_windows(start_date, end_date, window_days):
        res = None
        for attempt in range(3):
            errbox.clear()
            try:
                res = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_dt, durationStr=dur,
                    barSizeSetting="1 min", whatToShow="TRADES", useRTH=True,
                    formatDate=2), timeout=90)
            except Exception:
                res = None
            codes = [c for (_, c, _) in errbox if c not in BENIGN]
            if res is None and any(c in PACING_CODES for c in codes):
                await asyncio.sleep(60); continue          # pacing → backoff
            if res is None and attempt < 2:
                await asyncio.sleep(3); continue
            break
        if res:
            for b in res:
                dt = _to_et(b.date)
                iso = dt.isoformat()
                by_ts[iso] = (iso, b.open, b.high, b.low, b.close, int(b.volume))
        await asyncio.sleep(sleep_s)

    if not by_ts:
        return [], "0 bars (ingen data i intervallet?)"
    rows = [by_ts[k] for k in sorted(by_ts)]
    lo, hi = start_date.isoformat(), end_date.isoformat()
    rows = [r for r in rows if lo <= r[0][:10] <= hi]
    return rows, None


async def main_async(args) -> int:
    cache_dir = Path(args.bar_cache)
    if not cache_dir.is_absolute():
        cache_dir = Path.cwd() / cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    start = date_cls.fromisoformat(args.start)
    end = date_cls.fromisoformat(args.end)
    tickers = read_tickers(args)
    if not tickers:
        print("Ingen tickers — brug --tickers <fil> eller --symbols A,B,C.")
        return 1

    n_windows = len(request_windows(start, end, args.window))
    todo = [t for t in tickers if existing_cover(cache_dir, t, start, end) is None]
    skip = len(tickers) - len(todo)
    est_req = len(todo) * n_windows
    est_min = est_req * (args.sleep + 0.4) / 60.0

    print("=" * 74)
    print("  DOWNLOAD MID/LARGE-CAP 1-MIN BARS → bar_cache")
    print("=" * 74)
    print(f"  Interval: {start} → {end}  ·  {len(tickers)} tickers ({skip} allerede cachet)")
    print(f"  Vindue: {args.window} D/kald  ·  ~{n_windows} kald/ticker  ·  ~{est_req} kald i alt")
    print(f"  Estimeret tid: ~{est_min:.0f} min ved {args.sleep:.1f}s/kald (+ pacing-backoff)\n")

    if args.dry_run:
        print("  --dry-run: henter intet. Tickers der ville blive hentet:")
        print("   ", ", ".join(todo) if todo else "(ingen — alt er cachet)")
        return 0
    if not todo:
        print("  Alt er allerede cachet for intervallet. Intet at gøre.")
        return 0

    from ib_async import IB
    ib = IB()
    errbox: list = []
    ib.errorEvent += lambda reqId, code, msg, *_: errbox.append((reqId, code, msg))
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        print(f"❌ Kunne ikke forbinde til IBKR: {e}")
        print(f"   Tjek TWS/Gateway, port {args.port}, og at client-id {args.client_id} er ledigt.")
        return 1
    print("✅ Forbundet til IBKR\n")

    ok, failed = 0, []
    t0 = time.time()
    try:
        for i, t in enumerate(todo, 1):
            rows, err = await fetch_ticker(ib, t, start, end, args.window, args.sleep, errbox)
            if err and not rows:
                failed.append((t, err))
                print(f"  [{i}/{len(todo)}] {t}: ⚠️  {err}")
                continue
            path = cache_path(cache_dir, t, start, end)
            write_csv(path, rows)
            span = f"{rows[0][0][:10]}…{rows[-1][0][:10]}" if rows else "?"
            ok += 1
            print(f"  [{i}/{len(todo)}] {t}: ✅ {len(rows)} bars ({span}) → {path.name}")
    finally:
        ib.disconnect()

    mins = (time.time() - t0) / 60.0
    print("\n" + "=" * 74)
    print(f"  FÆRDIG på {mins:.0f} min — {ok} hentet, {len(failed)} fejlede, {skip} sprunget over")
    if failed:
        print("  Fejlede (kør scriptet igen for at genoptage; cachede springes over):")
        for t, e in failed:
            print(f"    {t}: {e}")
    print(f"\n  Næste skridt: rekonstruér per-dag-universet fra bar_cache, kør så")
    print(f"  washout_reclaim_backtest.py mod det. Sig til, så bygger jeg rekonstruktionen.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Hent 1-min bars for mid/large-cap-universet til bar_cache")
    ap.add_argument("--tickers", help="fil med ét symbol pr. linje (# = kommentar)")
    ap.add_argument("--symbols", help="komma-separeret liste, fx PLTR,SOFI,RIVN")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--bar-cache", default="bar_cache")
    ap.add_argument("--window", type=int, default=5, help="dage pr. IBKR-kald (default 5)")
    ap.add_argument("--sleep", type=float, default=1.2, help="sekunder mellem kald (pacing)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=31, help="eget id — undgå kollision med backend")
    ap.add_argument("--dry-run", action="store_true", help="estimér omfang uden at hente")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt — cachede tickers er gemt; kør igen for at genoptage.")
        return 130


if __name__ == "__main__":
    sys.exit(main())