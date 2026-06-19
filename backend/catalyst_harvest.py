#!/usr/bin/env python3
"""
catalyst_harvest.py — Katalysator-møllen Trin 2, leverance 1: data-harvest
═══════════════════════════════════════════════════════════════════════════
Henter Dow Jones-nyhedshistorik (PAGERET, så 300-loftet ikke skaber huller) +
1-min kurser for et fast univers af likvide large/mid-caps. Læs-only mod IBKR
(ingen ordrer). Output er CSV'er som catalyst_backtest.py (leverance 2) læser.

Forudsætning: Trin 1 (catalyst_history_probe.py) gav GRØN — DJ-historik er dyb
(~90-130 dage) + minut-præcis. Providere bekræftet: DJ-N, DJ-RTG, DJ-RTPRO,
DJ-RTA, DJ-RTE.

KØR IKKE mens en strategi handler på kontoen. Egen client-id (default 28).
Python 3.14 → *Async-varianter.

Brug (fra backend/):
    python catalyst_harvest.py --symbols NVDA,TSLA,AAPL --days 45   # validering
    python catalyst_harvest.py                                       # fuldt univers, 150 dage
    python catalyst_harvest.py --limit 30 --days 120                 # første 30 navne
    python catalyst_harvest.py --skip-bars     # kun nyheder (hurtig dækningstest)

Output:
    catalyst_data/news/{TICKER}_news.csv   (ts_utc, provider, headline, article_id)
    catalyst_data/bars/{TICKER}_1min.csv   (ts_utc, open, high, low, close, volume)
    catalyst_harvest_output/summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\catalyst_harvest.py
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

import pytz

ET = pytz.timezone("America/New_York")

# ── Konfig ────────────────────────────────────────────────────
IBKR_HOST   = "127.0.0.1"
IBKR_PORT   = 7497
CLIENT_ID   = 28          # ledigt; kolliderer ikke med backend (default) / downloads (10-12)

# DJ-providere Trin 1 bekræftede som historik-egnede (filtreres mod berettigede).
DJ_PROVIDERS = ["DJ-N", "DJ-RTG", "DJ-RTPRO", "DJ-RTA", "DJ-RTE"]

NEWS_TOTAL_PER_CALL = 300     # IBKR-loft pr. reqHistoricalNews-kald
NEWS_MAX_PAGES      = 80      # sikkerhedsventil mod runaway-pagering
BAR_CHUNK_DAYS      = 30      # 1-min: målt OK i ét kald (~11.700 bars, ~17s) 2026-06-19
SLEEP_BETWEEN       = 0.6     # sek mellem IBKR-kald (pacing)
PACING_WAIT         = 60      # sek ved pacing violation
THIN_NEWS_FLAG      = 20      # <dette antal nyheder = for tynd, markeres

DEFAULT_DAYS = 150            # solid zone ~90-130; vi stopper halen ved ~150

# Fast univers (~120 likvide large/mid-caps, nyhedsaktive, sektor-spredt).
# Hardkodet → reproducerbar harvest (ikke et live-scan der ændrer sig).
UNIVERSE = [
    # Mega/large-cap tech + comms
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "ADBE", "CRM", "ORCL", "CSCO", "INTC", "QCOM", "TXN", "MU", "AMAT", "INTU",
    "IBM", "NOW", "PANW", "SNOW", "PLTR", "UBER", "SHOP", "ABNB", "MRVL", "SMCI",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "AXP", "BLK", "COF",
    "PYPL", "SQ", "COIN", "SOFI", "V", "MA",
    # Healthcare / pharma / biotech
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "BMY", "GILD",
    "AMGN", "MRNA", "CVS", "DHR",
    # Consumer / retail
    "WMT", "HD", "COST", "TGT", "LOW", "NKE", "SBUX", "MCD", "DIS", "CMCSA",
    "PG", "KO", "PEP", "PM", "CHWY", "LULU",
    # Industrials / energy / materials
    "BA", "CAT", "GE", "HON", "UPS", "LMT", "RTX", "DE", "MMM",
    "XOM", "CVX", "COP", "OXY", "SLB", "FCX",
    # Autos / EV / semis-adjacent / high-news mid-caps
    "F", "GM", "RIVN", "LCID", "NIO", "ON", "ENPH", "FSLR",
    "DAL", "AAL", "UAL", "CCL", "RCL", "MAR",
    # Telecom / utilities / misc large
    "T", "VZ", "TMUS", "NEE", "DUK",
    # Volatile / news-driven mid-caps
    "GME", "AMC", "DKNG", "RBLX", "HOOD", "AFRM", "DDOG", "NET", "CRWD", "ZS",
    "ROKU", "PINS", "SNAP", "TTD", "U", "DASH", "CVNA", "UPST",
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_news_time(t) -> datetime | None:
    """IBKR-artikeltid 'YYYY-MM-DD HH:MM:SS[.f]' → tz-aware UTC."""
    s = str(t)[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_midnight_only(dt: datetime) -> bool:
    return (dt.hour, dt.minute, dt.second) == (0, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Nyheds-harvest (PAGERET bagud i tid)
# ─────────────────────────────────────────────────────────────────────────────
async def harvest_news(ib, conid: int, provider_str: str, days: int, emit_dbg=None):
    """Returnér (rows, dropped_midnight). rows = liste af dict med ts_utc/provider/
    headline/article_id, dedup'et, midnat-kun droppet, ældre end `days` afskåret.

    Pagering (IBKR-semantik målt empirisk 2026-06-19): startDateTime er den NYESTE
    grænse — kaldet returnerer op til 300 artikler BAGUD derfra; endDateTime ignoreres.
    Så vi walker bagud: cursor i startDateTime-slottet, cursor=ældste−1s pr. side,
    indtil cutoff nået, ingen fremdrift, eller tomt. (IKKE stop på <300 — IBKR
    returnerer ofte lidt færre end totalResults selv når der er flere.)"""
    cutoff = _now_utc() - timedelta(days=days)
    seen = set()                # (ts_iso, headline, provider)
    rows = []
    dropped_midnight = 0
    cursor = ""                 # tom = nu (nyeste grænse)
    prev_oldest = None
    for _page in range(NEWS_MAX_PAGES):
        try:
            items = await asyncio.wait_for(
                ib.reqHistoricalNewsAsync(conid, provider_str, cursor, "", NEWS_TOTAL_PER_CALL),
                timeout=30)
        except Exception as e:
            if "pacing" in str(e).lower():
                await asyncio.sleep(PACING_WAIT)
                continue
            if emit_dbg:
                emit_dbg(f"      reqHistoricalNews fejl: {e}")
            break
        if not items:
            break
        parsed = [(i, _parse_news_time(i.time)) for i in items]
        valid = [(i, dt) for (i, dt) in parsed if dt is not None]
        if not valid:
            break
        oldest = min(dt for (_i, dt) in valid)
        for i, dt in valid:
            if _is_midnight_only(dt):
                dropped_midnight += 1
                continue
            if dt < cutoff:
                continue
            key = (dt.isoformat(), str(i.headline), i.providerCode)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "ts_utc":     dt.isoformat(),
                "provider":   i.providerCode,
                "headline":   str(i.headline).replace("\n", " ").strip(),
                "article_id": getattr(i, "articleId", "") or "",
            })
        # Stop: nået cutoff, eller ingen fremdrift (ældste rykkede sig ikke bagud).
        if oldest < cutoff:
            break
        if prev_oldest is not None and oldest >= prev_oldest:
            break
        prev_oldest = oldest
        cursor = (oldest - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S.0")
        await asyncio.sleep(SLEEP_BETWEEN)
    rows.sort(key=lambda r: r["ts_utc"])
    return rows, dropped_midnight


# ─────────────────────────────────────────────────────────────────────────────
# 1-min bar-harvest (chunked bagud i tid)
# ─────────────────────────────────────────────────────────────────────────────
async def harvest_bars(ib, contract, days: int, emit_dbg=None):
    """Returnér liste af bar-dict (ts_utc/open/high/low/close/volume), RTH 1-min,
    dækkende ~`days` bagud. Chunked BAR_CHUNK_DAYS pr. kald, walk back.

    Pagering forankres på ÆLDSTE UTC-timestamp set indtil nu, og end_str formatteres
    ALTID som eksplicit UTC ('YYYYMMDD HH:MM:SS UTC') — så IBKR ikke fejltolker en naiv
    dato (det gjorde at kun første 30-dages chunk blev hentet). Stop: nået target,
    ingen fremdrift, tomt, eller lille chunk (enden af historik)."""
    target_start = _now_utc() - timedelta(days=days)
    end_str = ""                # tom = nu
    by_ts = {}                  # ts_iso → row (dedup)
    oldest_utc = None
    max_chunks = days // BAR_CHUNK_DAYS + 6
    for _chunk in range(max_chunks):
        bars = None
        for attempt in range(2):                 # ét retry ved transient fejl/timeout
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{BAR_CHUNK_DAYS} D",
                    barSizeSetting="1 min", whatToShow="TRADES", useRTH=True, formatDate=1),
                    timeout=60)
                break
            except Exception as e:
                if "pacing" in str(e).lower():
                    await asyncio.sleep(PACING_WAIT)
                    continue
                if attempt == 0:
                    await asyncio.sleep(3)
                    continue
                if emit_dbg:
                    emit_dbg(f"      reqHistoricalData fejl: {e}")
                bars = None
        if not bars:
            break
        chunk_oldest = None
        for b in bars:
            d = b.date
            if hasattr(d, "tzinfo") and d.tzinfo is not None:
                dt_utc = d.astimezone(timezone.utc)
            elif hasattr(d, "isoformat"):
                dt_utc = ET.localize(d).astimezone(timezone.utc)   # naiv → antag ET
            else:
                continue
            by_ts[dt_utc.isoformat()] = {
                "ts_utc": dt_utc.isoformat(),
                "open":   float(b.open), "high": float(b.high), "low": float(b.low),
                "close":  float(b.close), "volume": int(b.volume) if b.volume else 0,
            }
            if chunk_oldest is None or dt_utc < chunk_oldest:
                chunk_oldest = dt_utc
        if chunk_oldest is None:
            break
        if oldest_utc is not None and chunk_oldest >= oldest_utc:   # ingen fremdrift
            break
        oldest_utc = chunk_oldest
        if oldest_utc <= target_start or len(bars) < 50:
            break
        end_str = (oldest_utc - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
        await asyncio.sleep(SLEEP_BETWEEN)
    return sorted(by_ts.values(), key=lambda r: r["ts_utc"])


# ─────────────────────────────────────────────────────────────────────────────
async def main_async(args, emit) -> int:
    from ib_async import IB, Stock

    symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else list(UNIVERSE))
    if args.limit:
        symbols = symbols[:args.limit]

    news_dir = Path.cwd() / "catalyst_data" / "news"
    bars_dir = Path.cwd() / "catalyst_data" / "bars"
    news_dir.mkdir(parents=True, exist_ok=True)
    bars_dir.mkdir(parents=True, exist_ok=True)

    emit("=" * 78)
    emit("  KATALYSATOR-HARVEST — Dow Jones-nyheder (pageret) + 1-min kurser")
    emit("=" * 78)
    emit(f"Tid: {datetime.now():%Y-%m-%d %H:%M}   navne: {len(symbols)}   periode: {args.days} dage")
    emit(f"Nyheder: {'JA' if not args.skip_news else 'SPRINGES OVER'}   "
         f"bars: {'JA' if not args.skip_bars else 'SPRINGES OVER'}")

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  ❌ FORBINDELSESFEJL: {e} (tjek TWS + port {args.port} + ledigt client-id {args.client_id})")
        return 1

    try:
        emit(f"  ✅ Forbundet. Konto: {', '.join(ib.managedAccounts()) or '(ingen)'}")
        # Provider-string
        provider_str = ""
        if not args.skip_news:
            try:
                provs = await ib.reqNewsProvidersAsync()
                eligible = {getattr(p, "code", "") for p in provs}
                use = [c for c in DJ_PROVIDERS if c in eligible]
                provider_str = "+".join(use)
                emit(f"  DJ-providere: {provider_str or '(INGEN berettiget!)'}")
            except Exception as e:
                emit(f"  ❌ provider-fejl: {e}")

        totals = {"news": 0, "bars": 0, "dropped": 0, "thin": [], "no_data": []}
        emit("")
        emit(f"  {'ticker':<8}{'nyheder':>9}{'spænd (ældste→nyeste)':>30}{'dropped':>9}{'bars':>9}")
        emit("  " + "─" * 70)

        for idx, sym in enumerate(symbols, 1):
            contract = Stock(sym, "SMART", "USD")
            try:
                await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10)
            except Exception as e:
                emit(f"  {sym:<8}  ❌ kvalificering: {e}")
                totals["no_data"].append(sym)
                continue
            conid = getattr(contract, "conId", None)
            if not conid:
                emit(f"  {sym:<8}  ❌ ingen conId")
                totals["no_data"].append(sym)
                continue

            n_news, span, dropped, n_bars = 0, "", 0, 0

            if not args.skip_news and provider_str:
                rows, dropped = await harvest_news(ib, conid, provider_str, args.days)
                n_news = len(rows)
                if rows:
                    span = f"{rows[0]['ts_utc'][:10]} → {rows[-1]['ts_utc'][:10]}"
                    with (news_dir / f"{sym}_news.csv").open("w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=["ts_utc", "provider", "headline", "article_id"])
                        w.writeheader()
                        w.writerows(rows)
                totals["news"] += n_news
                totals["dropped"] += dropped
                if n_news < THIN_NEWS_FLAG:
                    totals["thin"].append(sym)

            if not args.skip_bars:
                bars = await harvest_bars(ib, contract, args.days)
                n_bars = len(bars)
                if bars:
                    with (bars_dir / f"{sym}_1min.csv").open("w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=["ts_utc", "open", "high", "low", "close", "volume"])
                        w.writeheader()
                        w.writerows(bars)
                totals["bars"] += n_bars
                if n_bars == 0:
                    totals["no_data"].append(sym)

            thin = " ⚠tynd" if (not args.skip_news and n_news < THIN_NEWS_FLAG) else ""
            emit(f"  {sym:<8}{n_news:>9}{span:>30}{dropped:>9}{n_bars:>9}{thin}  [{idx}/{len(symbols)}]")

        emit("")
        emit("  " + "═" * 70)
        emit(f"  TOTAL: {totals['news']:,} nyheder · {totals['bars']:,} 1-min bars · "
             f"{totals['dropped']:,} midnat-kun droppet")
        if totals["thin"]:
            emit(f"  Tynde navne (<{THIN_NEWS_FLAG} nyheder, medtaget men markeret): "
                 f"{', '.join(totals['thin'])}")
        if totals["no_data"]:
            emit(f"  Uden data/fejl: {', '.join(sorted(set(totals['no_data'])))}")
        emit("")
        emit(f"  CSV'er: {news_dir}  ·  {bars_dir}")
    finally:
        ib.disconnect()
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Katalysator data-harvest (DJ-nyheder + 1-min, læs-only)")
    ap.add_argument("--host", default=IBKR_HOST)
    ap.add_argument("--port", type=int, default=IBKR_PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--symbols", default=None, help="kommasepareret; default = fuldt hardkodet univers")
    ap.add_argument("--limit", type=int, default=None, help="kun første N navne (validering)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="lookback for BÅDE nyheder og bars")
    ap.add_argument("--skip-news", action="store_true")
    ap.add_argument("--skip-bars", action="store_true")
    args = ap.parse_args()

    out_dir = Path.cwd() / "catalyst_harvest_output"
    out_dir.mkdir(exist_ok=True)
    lines: list[str] = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    try:
        rc = asyncio.run(main_async(args, emit))
    except KeyboardInterrupt:
        emit("\nAfbrudt.")
        rc = 130
    finally:
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        emit(f"\nFil: {out_dir / 'summary.txt'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
