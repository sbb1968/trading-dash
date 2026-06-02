"""
backtest_confluence_journal.py
──────────────────────────────
Konfluens-backtest med det FAKTISKE DAGLIGE UNIVERS fra journalen.

Hvorfor denne findes
────────────────────
Den almindelige backtest_confluence.py vælger universet ÉN gang via IBKR-
scanneren — og det er DAGENS top-gainers, uanset hvilken historisk dato
man backtester. Det giver to problemer:
  • Look-ahead/survivorship: man tester aktier man allerede ved klarede sig.
  • Forkert univers på forkerte dage: en aktie kan være død/uhandlet på en
    dag hvor backtesten alligevel "handler" den.

Live bygger derimod et NYT univers hver morgen og logger det i journalen
(event_type 'universe_selected'). Denne backtest læser præcis de daglige
universer tilbage og backtester HVER dag med dén dags aktier — altså det
univers live faktisk så. Ingen scanner, ingen look-ahead.

Begrænsning: den kan kun teste de dage, hvor algoen rent faktisk kørte og
loggede et univers. Har algoen kun kørt 3 dage, kan vi kun teste de 3 dage.
Det er som det skal være — det er hele pointen med at bruge de ægte data.

Strategilogikken er UÆNDRET: vi genbruger backtest_confluence.backtest_ticker
osv. direkte, så resultaterne er sammenlignelige 1:1 med den almindelige
backtest (bortset fra universet).

Kør lokalt fra backend/ med TWS oppe (port 7497):
    python backtest_confluence_journal.py                      # alle dage i journalen
    python backtest_confluence_journal.py --start 2026-05-20 --end 2026-05-29
    python backtest_confluence_journal.py --variant baseline

TIP: start med et lille vindue (få dage). Den henter bars én dag ad gangen
pr. ticker, så et stort vindue × mange aktier = mange IBKR-kald (kan tage tid
og risikere pacing-grænser).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, date as date_cls
from pathlib import Path

# ── Python 3.14 event loop fix ────────────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB

from strategies.confluence import ConfluenceStrategy, VARIANTS, LIVE_VARIANT_KEY

# Genbrug ALT fra den almindelige backtest — ingen logik duplikeres her
from backtest_confluence import (
    fetch_5min_bars,
    backtest_ticker,
    calc_stats,
    print_summary,
    print_trades_table,
    export_trades_csv,
    WARMUP_TRADING_DAYS,
    ET,
    DATA_DIR,
    GREEN, RED, BOLD, RESET,
)

logger = logging.getLogger("backtest_confluence_journal")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("ib_async").setLevel(logging.WARNING)

# ── Konfiguration ─────────────────────────────────────────────
DB_PATH         = Path(__file__).parent / "trading_dash.db"
SOURCE_NAME     = "Konfluens"          # filtrér ORB's universer fra
IBKR_HOST       = "127.0.0.1"
IBKR_PORT       = 7497                  # paper
CLIENT_ID       = 14                    # ANDEN end live(10), fetch(11), backtest(12)
CONNECT_TIMEOUT = 15

# Samme warmup-offset som den almindelige backtest bruger
WARMUP_OFFSET = timedelta(days=int(WARMUP_TRADING_DAYS * 1.5) + 5)


# ───────────────────────────────────────────────────────────────
# Læs de daglige universer fra journalen (read-only)
# ───────────────────────────────────────────────────────────────
def read_daily_universes(start: date_cls | None,
                         end: date_cls | None) -> dict[date_cls, list[str]]:
    """
    Returnér {handelsdag: [tickers]} ud fra 'universe_selected'-events.

    ts_local er maskinens lokaltid (dansk). Universet logges ved dagsstart
    (~15:30 dansk = 09:30 ET), så ts_local-datoen = ET-handelsdagen.
    Hvis en dag har flere universe-events (fx genstart), vinder den seneste.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Fandt ikke databasen: {DB_PATH}")

    uri  = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT ts_local, payload_json
            FROM events
            WHERE event_type = 'universe_selected'
              AND source = ?
            ORDER BY ts_local ASC
            """,
            (SOURCE_NAME,),
        ).fetchall()
    finally:
        conn.close()

    by_date: dict[date_cls, list[str]] = {}
    for r in rows:
        try:
            d = datetime.fromisoformat(r["ts_local"]).date()
        except (ValueError, TypeError):
            continue
        if start and d < start:
            continue
        if end and d > end:
            continue
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        tickers = payload.get("tickers") or []
        if tickers:
            by_date[d] = list(tickers)   # seneste event for dagen vinder

    return dict(sorted(by_date.items()))


# ───────────────────────────────────────────────────────────────
# IBKR-forbindelse (egen clientId så vi ikke kolliderer med en kørende backtest)
# ───────────────────────────────────────────────────────────────
async def connect() -> IB:
    ib = IB()
    logger.info(f"Forbinder til IBKR {IBKR_HOST}:{IBKR_PORT} (client_id={CLIENT_ID})...")
    await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=CLIENT_ID, timeout=CONNECT_TIMEOUT)
    logger.info("✓ Forbundet til IBKR")
    return ib


# ───────────────────────────────────────────────────────────────
# Hoved-kørsel
# ───────────────────────────────────────────────────────────────
async def run(args) -> int:
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
    end   = datetime.strptime(args.end,   "%Y-%m-%d").date() if args.end   else None

    universes = read_daily_universes(start, end)
    if not universes:
        logger.warning("Ingen 'universe_selected'-events for Konfluens i vinduet.")
        logger.warning("Har algoen kørt og logget universer for de(n) dag(e)?")
        return 0

    days        = list(universes.keys())
    all_tickers = sorted({t for ts in universes.values() for t in ts})

    # Hent-vindue: tidligste dag minus warmup → seneste dag
    fetch_start = min(days) - WARMUP_OFFSET
    while fetch_start.weekday() >= 5:
        fetch_start -= timedelta(days=1)
    fetch_end = max(days)

    print(f"\n{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  KONFLUENS BACKTEST — FAKTISK DAGLIGT UNIVERS (fra journal){RESET}")
    print(f"{BOLD}  Dage i journal:  {days[0]}  →  {days[-1]}  ({len(days)} handelsdage){RESET}")
    print(f"{BOLD}  Unikke aktier:   {len(all_tickers)}{RESET}")
    print(f"{BOLD}  Variant:         {args.variant!r}  ({VARIANTS[args.variant].name}){RESET}")
    print(f"{BOLD}  Hent-vindue:     {fetch_start}  →  {fetch_end}{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}\n")
    for d in days:
        logger.info(f"  {d}: {len(universes[d])} aktier  →  {', '.join(universes[d][:12])}"
                    f"{' …' if len(universes[d]) > 12 else ''}")
    print()

    ib = await connect()
    strategy = ConfluenceStrategy()

    # ── Fase 1: hent hver tickers bars ÉN gang over hele spændet (cache) ──
    logger.info(f"{BOLD}Henter bars for {len(all_tickers)} unikke aktier "
                f"({fetch_start} → {fetch_end})...{RESET}")
    cache: dict[str, list] = {}
    try:
        for i, t in enumerate(all_tickers, 1):
            try:
                bars = await fetch_5min_bars(ib, t, fetch_start, fetch_end)
                cache[t] = bars
                logger.info(f"  [{i:2d}/{len(all_tickers)}] {t:6s}  {len(bars)} bars")
            except Exception as e:
                logger.warning(f"  [{i:2d}/{len(all_tickers)}] {t:6s}  fetch-fejl: {e}")
                cache[t] = []

        # ── Fase 2: backtest hver dag med dén dags univers ──
        print()
        logger.info(f"{BOLD}Backtester {len(days)} dage med deres faktiske univers...{RESET}")
        all_trades: list[dict] = []
        per_day: list[tuple] = []

        for d in days:
            day_trades: list[dict] = []
            wstart = d - WARMUP_OFFSET
            for t in universes[d]:
                bars = [b for b in cache.get(t, []) if wstart <= b.date <= d]
                if not bars:
                    continue
                trades = backtest_ticker(strategy, t, bars, args.variant, d, d)
                day_trades.extend(trades)

            all_trades.extend(day_trades)
            s = calc_stats(day_trades)
            per_day.append((d, len(universes[d]), s))
            color = GREEN if s["total_pnl"] > 0 else RED if s["total_pnl"] < 0 else ""
            logger.info(f"  {d}: {len(universes[d]):2d} aktier  →  "
                        f"{s['trades']:2d} trades, "
                        f"P&L {color}${s['total_pnl']:+,.2f}{RESET}, "
                        f"WR {s['win_rate']:.0f}%")

        # ── Per-dag-oversigt ──
        print(f"\n{BOLD}{'=' * 90}{RESET}")
        print(f"{BOLD}  PER DAG{RESET}")
        print(f"{BOLD}{'=' * 90}{RESET}")
        print(f"  {'Dato':<12} {'Aktier':>6} {'Trades':>6} {'WinRate':>8} "
              f"{'P&L':>12} {'PF':>6}")
        print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*8} {'-'*12} {'-'*6}")
        for d, n_uni, s in per_day:
            pf = s["profit_factor"]
            pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
            print(f"  {str(d):<12} {n_uni:>6} {s['trades']:>6} "
                  f"{s['win_rate']:>7.0f}% {s['total_pnl']:>+12,.2f} {pf_str:>6}")

        # ── Samlet ──
        if all_trades and len(all_trades) <= 60:
            print_trades_table(all_trades)
        elif all_trades:
            print(f"\n  ({len(all_trades)} handler i alt — se CSV for fuld liste)")

        stats = calc_stats(all_trades)
        print_summary(stats, "SAMLET (faktisk dagligt univers fra journal)")

        # ── CSV-eksport ──
        if all_trades:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = DATA_DIR / f"backtest_confluence_journal_{args.variant}_{ts}.csv"
            export_trades_csv(all_trades, path)
            logger.info(f"  Trades eksporteret: {path}  ({len(all_trades)} rækker)")
        else:
            logger.info("  Ingen handler at eksportere.")

    finally:
        ib.disconnect()
        logger.info("Frakoblet IBKR")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Konfluens-backtest med faktisk dagligt univers fra journalen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Eksempler:\n"
            "  python backtest_confluence_journal.py\n"
            "  python backtest_confluence_journal.py --start 2026-05-20 --end 2026-05-29\n"
        ),
    )
    parser.add_argument("--start", type=str, help="Startdato (YYYY-MM-DD). Udeladt = alle dage i journalen")
    parser.add_argument("--end",   type=str, help="Slutdato (YYYY-MM-DD)")
    parser.add_argument("--variant", type=str, default=LIVE_VARIANT_KEY,
                        choices=list(VARIANTS.keys()),
                        help=f"Variant (default {LIVE_VARIANT_KEY!r})")
    args = parser.parse_args()

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.warning("\nAfbrudt af bruger")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())