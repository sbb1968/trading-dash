#!/usr/bin/env python3
"""
harvest_trendjoin_5min.py — host 5-min bars MED premarket til Trend-Join-backtesten
═══════════════════════════════════════════════════════════════════════════════════
Skriver data_trendjoin/{TICKER}_5min.csv (timestamp,open,high,low,close,volume; ISO-ET).
**useRTH=False** -> faar premarket + RTH med (backtesten skal bruge premarket-high + RVOL;
den session-gater selv). Default-univers = S&P 100 (large_cap_universe.SP100_TICKERS) —
de mest likvide large caps; udvid med --tickers / --tickers-file.

Henter ~450 dage 5m bagud (SMA200 paa daglige lukker kraever ~10 mdr FOER testperioden).
Bunder naturligt ved hver akties 5m-historik-graense.

RESUMERBAR + 23.45-robust (genbrugt fra den asiatiske hoester): CSV skrives efter HVER chunk,
genoptager bagud fra aeldste bar pr. fil; genforbinder ved transient drop, stopper rent ved
varigt tab. Read-only (henter kun historik). Egen client-id 52 (distinkt fra backend/asian).

Kun backtest-relateret: INTET telegram/dashboard/scheduling. Python 3.14, ib_async + stdlib.

Brug (Soerens workstation, TWS oppe, fra backend/):
    python harvest_trendjoin_5min.py                      # hele S&P 100 (langt; resumerbart)
    python harvest_trendjoin_5min.py --tickers AAPL,NVDA,TSLA
    python harvest_trendjoin_5min.py --tickers-file mine.txt --days 300

Placering: C:\\Projects\\trading_dash\\backend\\harvest_trendjoin_5min.py
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

from ib_async import IB, Stock

HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 13
BAR_SIZE      = "5 mins"
CHUNK_DAYS    = 20                  # "20 D" — sikker 5-min chunk
SLEEP_BETWEEN = 0.8
PACING_WAIT   = 60
DEFAULT_DAYS  = 450
DATA_DIR      = Path("data_trendjoin")


def _et(dt):
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(ET) if ET else dt
    return dt.replace(tzinfo=ET) if ET is not None else dt


def out_path(ticker):
    p = DATA_DIR / f"{ticker}_5min.csv"
    return p if p.is_absolute() else (Path.cwd() / p)


def load_existing(path):
    by_ts = {}
    if not path.exists():
        return by_ts
    try:
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                try:
                    dt = datetime.fromisoformat(r["timestamp"])
                except (ValueError, KeyError):
                    continue
                u = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                by_ts[u] = {"et": _et(dt), "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]),
                            "volume": int(float(r["volume"])) if r.get("volume") else 0}
    except Exception:
        return {}
    return by_ts


def write_csv(path, by_ts):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for k in sorted(by_ts):
            b = by_ts[k]
            w.writerow([b["et"].isoformat(), b["open"], b["high"], b["low"], b["close"], b["volume"]])
    tmp.replace(path)


def read_tickers(args):
    if args.tickers:
        return [s.strip().upper() for s in args.tickers.split(",") if s.strip()]
    if args.tickers_file:
        out = []
        for line in Path(args.tickers_file).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line.upper())
        return out
    try:
        from large_cap_universe import SP100_TICKERS
        return list(SP100_TICKERS)
    except Exception as e:
        print(f"  Kunne ikke laese SP100_TICKERS ({e}) — brug --tickers/--tickers-file.")
        return []


async def reconnect(ib, host, port, client_id, emit, attempts=3, delay=8):
    for a in range(1, attempts + 1):
        if ib.isConnected():
            return True
        try:
            ib.disconnect()
        except Exception:
            pass
        try:
            await asyncio.wait_for(ib.connectAsync(host, port, clientId=client_id, timeout=15), timeout=20)
            if ib.isConnected():
                emit(f"   genforbundet (forsoeg {a}/{attempts})")
                return True
        except Exception:
            pass
        await asyncio.sleep(delay)
    return False


async def qualify_stock(ib, ticker, emit):
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(ticker, "SMART", "USD")), timeout=15)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        return q[0] if (q and getattr(q[0], "conId", 0)) else None
    except Exception as e:
        emit(f"   kvalificering fejlede: {type(e).__name__}: {e}")
        return None


async def pull(ib, contract, max_days, by_ts, emit, write_cb, reconnect_cb):
    target = datetime.now(timezone.utc) - timedelta(days=max_days)
    oldest = min(by_ts) if by_ts else None
    if oldest is not None and oldest <= target:
        return [by_ts[k] for k in sorted(by_ts)]
    end_str = (oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC" if by_ts else ""
    for ci in range(max_days // CHUNK_DAYS + 8):
        bars = None
        for attempt in range(2):
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{CHUNK_DAYS} D",
                    barSizeSetting=BAR_SIZE, whatToShow="TRADES", useRTH=False, formatDate=1), timeout=90)
                break
            except Exception as e:
                msg = str(e).lower()
                if "pacing" in msg:
                    emit(f"   (pacing - venter {PACING_WAIT}s)"); await asyncio.sleep(PACING_WAIT); continue
                if "not connected" in msg or "peer closed" in msg or not ib.isConnected():
                    emit("   forbindelse tabt — genforbinder...")
                    if await reconnect_cb():
                        continue
                    raise ConnectionError("TWS-forbindelse tabt (varig)")
                if attempt == 0:
                    await asyncio.sleep(3); continue
                emit(f"   reqHistoricalData fejl: {e}"); bars = None
        if not bars:
            break
        chunk_oldest = None
        for b in bars:
            dt = _et(b.date)
            u = dt.astimezone(timezone.utc) if dt.tzinfo else dt
            by_ts[u] = {"et": dt, "open": float(b.open), "high": float(b.high),
                        "low": float(b.low), "close": float(b.close),
                        "volume": int(b.volume) if b.volume else 0}
            if chunk_oldest is None or u < chunk_oldest:
                chunk_oldest = u
        write_cb(by_ts)
        emit(f"      chunk {ci + 1}: +{len(bars)} (til {chunk_oldest.astimezone(ET):%Y-%m-%d}) · total {len(by_ts)}")
        if oldest is not None and chunk_oldest >= oldest:
            break
        oldest = chunk_oldest
        if oldest <= target or len(bars) < 20:
            break
        end_str = (oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
        await asyncio.sleep(SLEEP_BETWEEN)
    return [by_ts[k] for k in sorted(by_ts)]


async def main_async(args):
    def emit(s=""):
        print(s, flush=True)

    tickers = read_tickers(args)
    if not tickers:
        return 1
    emit("=" * 78)
    emit(f"  TREND-JOIN 5-min HARVEST (useRTH=False -> premarket+RTH) -> {DATA_DIR}/  (resumerbar)")
    emit("=" * 78)
    emit(f"  Tid: {datetime.now():%Y-%m-%d %H:%M} · {len(tickers)} tickers · client-id {args.client_id} · ~{args.days} dage")
    if len(tickers) > 20:
        emit(f"  ⚠ STORT univers: ~{len(tickers)} tickers x {args.days} dage 5-min = mange tusinde historik-")
        emit("     kald (IBKR pacing-begraenset -> kan tage TIMER) paa SAMME TWS som evt. live strategier")
        emit("     -> risiko for at pacing-sulte dem (datablind). Anbefaling: test foerst smaat med")
        emit("     --tickers AAPL,NVDA,TSLA,AMD,META  og koer fuldt univers UDEN FOR handelstid. Ctrl+C = sikkert.")

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde til TWS: {e}")
        return 1
    emit("  Forbundet.\n")

    async def do_reconnect():
        return await reconnect(ib, args.host, args.port, args.client_id, emit)

    done = 0
    try:
        for i, t in enumerate(tickers, 1):
            if not ib.isConnected() and not await do_reconnect():
                emit("  ⛔ TWS varigt nede — stopper. Resumerbar pr. fil, koer igen.")
                break
            path = out_path(t)
            by_ts = load_existing(path)
            tag = f"(resume {len(by_ts)})" if by_ts else ""
            emit(f"  [{i}/{len(tickers)}] {t} henter... {tag}")
            c = await qualify_stock(ib, t, emit)
            if c is None:
                emit(f"  [{i}/{len(tickers)}] {t:<6} sprunget over (ukvalificeret)")
                continue

            def write_cb(d, _p=path):
                write_csv(_p, d)
            try:
                bars = await pull(ib, c, args.days, by_ts, emit, write_cb, do_reconnect)
            except ConnectionError:
                emit("  ⛔ TWS-forbindelse tabt under hoest. Gemt indtil nu; koer igen naar TWS er oppe.")
                break
            if bars:
                done += 1
                emit(f"  [{i}/{len(tickers)}] {t:<6} {len(bars)} bars · "
                     f"{bars[0]['et']:%Y-%m-%d} -> {bars[-1]['et']:%Y-%m-%d} {tag}")
            else:
                emit(f"  [{i}/{len(tickers)}] {t:<6} ingen bars")
    finally:
        ib.disconnect()
    emit(f"\n  Faerdig: {done}/{len(tickers)} hostet -> {DATA_DIR}/. Bunden ikke naaet for alle? Koer igen.")
    emit("  Naeste: python backtest_trendjoinlong.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Host 5-min bars (m. premarket) til Trend-Join-backtest")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--tickers", default=None, help="kommasepareret, fx AAPL,NVDA")
    ap.add_argument("--tickers-file", default=None, help="fil med ét symbol pr. linje")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt — delvise CSV'er gemt; koer igen for at genoptage.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
