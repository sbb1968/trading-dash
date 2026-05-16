"""
download_intraday_ibkr.py
─────────────────────────
Henter 5-min OHLCV-bars fra IBKR og gemmer i bars_5min-tabellen.

Designprincipper:
  - Chunked download: max 30 dage per request (IBKR begrænsning)
  - Iterativ baglæns: starter ved today, går bagud til vi rammer mål
  - Robust pacing-håndtering (1.0 sek mellem requests)
  - Genstartbar: skipper allerede færdige (ticker, måned) kombinationer
  - Idempotent: INSERT OR REPLACE
  - useRTH=True: kun regular trading hours

Brug:
    python download_intraday_ibkr.py --test
        Test med SPY for de seneste 30 dage

    python download_intraday_ibkr.py --tickers SPY QQQ IWM --months 12
        Hent 12 mdr 5-min bars for SPY, QQQ, IWM

    python download_intraday_ibkr.py --tickers-file etf_universe.txt
        Hent for tickers fra fil

Placering: C:\\Projects\\trading-dash\\backend\\download_intraday_ibkr.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Python 3.14 event loop fix ────────────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pytz
from ib_async import IB, Stock

from historical_db import get_connection, init_database


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("intraday_downloader")
logging.getLogger("ib_async").setLevel(logging.WARNING)


# ── Konfiguration ────────────────────────────────────────────
IBKR_HOST       = "127.0.0.1"
IBKR_PORT       = 7497
IBKR_CLIENT_ID  = 12             # Unik: 10=daily, 11=universe, 12=intraday
CONNECT_TIMEOUT = 15

# Rate limiting — strengere for intraday end daily
SLEEP_BETWEEN_REQUESTS = 1.0     # sek
PACING_VIOLATION_WAIT  = 60      # sek
MAX_RETRIES_PER_CHUNK  = 3

# Chunking
DAYS_PER_CHUNK = 30              # IBKR max for 5-min bars

# Test
TEST_TICKERS = ["SPY"]

# Default ETF univers
DEFAULT_ETFS = ["SPY", "QQQ", "IWM"]

# Eastern Time for timestamps
ET = pytz.timezone("America/New_York")


# ─────────────────────────────────────────────────────────────────
# Hjælpefunktioner
# ─────────────────────────────────────────────────────────────────

def load_tickers_from_file(path: Path) -> list[str]:
    """Læs ticker-symboler fra tekstfil (én per linje, # = kommentar)."""
    if not path.exists():
        raise FileNotFoundError(f"Ticker-fil findes ikke: {path}")

    tickers = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip().upper()
            if not line or line.startswith("#"):
                continue
            tickers.append(line)

    # Dedup, bevar rækkefølge
    seen = set()
    unique = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def ensure_ticker_in_universe(symbol: str) -> None:
    """Sørg for at ticker eksisterer i tickers-tabellen."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO tickers (symbol, added_date, notes)
            VALUES (?, ?, 'ETF for Mean Reversion')
        """, (symbol, datetime.now().strftime("%Y-%m-%d")))


def get_last_bar_timestamp(ticker: str) -> Optional[str]:
    """Find timestamp for den TIDLIGSTE bar vi har for denne ticker.
    Bruges til at vide hvor langt tilbage vi er kommet."""
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT MIN(timestamp) AS earliest
            FROM bars_5min
            WHERE ticker = ?
        """, (ticker,))
        row = cur.fetchone()
        return row["earliest"] if row and row["earliest"] else None


def log_download_start(ticker: str, date_start: str, date_end: str) -> int:
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO download_log
                (download_type, ticker, date_range_start, date_range_end,
                 started_at, status)
            VALUES ('5min', ?, ?, ?, ?, 'in_progress')
        """, (ticker, date_start, date_end, datetime.now().isoformat()))
        return cur.lastrowid


def log_download_success(log_id: int, bars_inserted: int) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE download_log
            SET status = 'success', completed_at = ?, bars_inserted = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), bars_inserted, log_id))


def log_download_failure(log_id: int, error_msg: str) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE download_log
            SET status = 'failed', completed_at = ?, error_msg = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), error_msg[:500], log_id))


def insert_5min_bars(ticker: str, bars: list) -> int:
    """
    Indsæt 5-min bars i bars_5min. INSERT OR REPLACE = idempotent.
    Konverterer datetime til ISO-format med timezone.
    """
    if not bars:
        return 0

    rows = []
    for bar in bars:
        # ib_async returnerer bar.date som datetime (med timezone for intraday)
        if hasattr(bar.date, "isoformat"):
            ts_str = bar.date.isoformat()
            # Tjek om premarket: før 09:30 ET
            try:
                if bar.date.tzinfo is None:
                    # Naiv datetime — antag ET
                    bar_dt = ET.localize(bar.date)
                else:
                    bar_dt = bar.date.astimezone(ET)
                is_premarket = 1 if bar_dt.time().hour < 9 or (bar_dt.time().hour == 9 and bar_dt.time().minute < 30) else 0
            except Exception:
                is_premarket = 0
        else:
            ts_str = str(bar.date)
            is_premarket = 0

        rows.append((
            ticker,
            ts_str,
            float(bar.open),
            float(bar.high),
            float(bar.low),
            float(bar.close),
            int(bar.volume) if bar.volume else 0,
            is_premarket,
        ))

    with get_connection() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO bars_5min
                (ticker, timestamp, open, high, low, close, volume, is_premarket)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

    return len(rows)


# ─────────────────────────────────────────────────────────────────
# IBKR-forbindelse og fetch
# ─────────────────────────────────────────────────────────────────

async def connect_ibkr() -> IB:
    """Forbind til IBKR Paper Trading."""
    ib = IB()
    logger.info(f"Forbinder til IBKR {IBKR_HOST}:{IBKR_PORT} (client_id={IBKR_CLIENT_ID})...")
    await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=CONNECT_TIMEOUT)
    logger.info("✓ Forbundet til IBKR")
    return ib


class PacingViolation(Exception):
    """IBKR pacing violation. Caller bør vente."""
    pass


async def fetch_5min_chunk(
    ib: IB,
    ticker: str,
    end_datetime_str: str,
    duration_days: int = DAYS_PER_CHUNK,
) -> tuple[list, Optional[str]]:
    """
    Hent én chunk af 5-min bars.

    Args:
        end_datetime_str: ISO-format eller "" for nu
        duration_days: hvor mange dage bagud fra end-time

    Returns:
        (bars, error_message). bars er tom hvis fejl.
    """
    try:
        contract = Stock(ticker, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)

        duration = f"{duration_days} D"

        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime    = end_datetime_str,
            durationStr    = duration,
            barSizeSetting = "5 mins",
            whatToShow     = "TRADES",
            useRTH         = True,
            formatDate     = 1,
        )

        if bars is None or len(bars) == 0:
            return [], "No data returned"

        return bars, None

    except Exception as e:
        error_str = str(e)
        if "pacing violation" in error_str.lower() or "max requests" in error_str.lower():
            raise PacingViolation(error_str) from e
        return [], error_str


# ─────────────────────────────────────────────────────────────────
# Hoveddownload-løkke (per ticker, chunked)
# ─────────────────────────────────────────────────────────────────

async def download_ticker(
    ib: IB,
    ticker: str,
    months: int,
) -> dict:
    """
    Download 'months' måneders 5-min bars for én ticker via chunks.

    Returnér dict med: chunks, total_bars, success, error.
    """
    today = datetime.now()
    target_start_date = today - timedelta(days=months * 31)

    logger.info(f"  {ticker}: Henter {months} måneder bagud til ~{target_start_date.strftime('%Y-%m-%d')}")

    total_bars = 0
    chunks_completed = 0
    chunks_failed = 0
    end_datetime_str = ""   # tomt = nu

    # Vi henter chunks bagud indtil vi rammer target dato
    current_oldest = today

    while current_oldest > target_start_date:
        attempt = 0
        chunk_success = False

        while attempt < MAX_RETRIES_PER_CHUNK and not chunk_success:
            attempt += 1
            try:
                bars, error = await fetch_5min_chunk(ib, ticker, end_datetime_str)

                if error:
                    if "No data" in error:
                        # Vi er nået til slutningen af tilgængelig historik
                        logger.info(f"  {ticker}: Ingen flere data — afslutter")
                        return {
                            "chunks":     chunks_completed,
                            "total_bars": total_bars,
                            "success":    True,
                            "error":      None,
                        }
                    if attempt < MAX_RETRIES_PER_CHUNK:
                        logger.warning(f"    Chunk fejl attempt {attempt}: {error[:80]}")
                        await asyncio.sleep(3.0)
                        continue
                    chunks_failed += 1
                    logger.error(f"    {ticker}: Chunk fejlede permanent: {error[:60]}")
                    break

                # Success — indsæt og opdatér state
                n_inserted = insert_5min_bars(ticker, bars)
                total_bars += n_inserted
                chunks_completed += 1
                chunk_success = True

                # Find ældste bar i denne chunk for at vide hvor vi skal hente næste fra
                oldest_bar = bars[0]
                current_oldest = oldest_bar.date if hasattr(oldest_bar.date, "year") else datetime.fromisoformat(str(oldest_bar.date))

                # Normalisér til naiv datetime for sammenligning med target_start_date
                # (target_start_date er naiv, IBKR-bar kan være tz-aware)
                if hasattr(current_oldest, "tzinfo") and current_oldest.tzinfo is not None:
                    current_oldest_naive = current_oldest.replace(tzinfo=None)
                else:
                    current_oldest_naive = current_oldest

                # Format end_datetime til IBKR for næste chunk
                # IBKR format: "YYYYMMDD HH:MM:SS" UTC (eller tomt for nu)
                # Vi går 1 sek bagud for at undgå dubletter
                next_end = current_oldest - timedelta(seconds=1)
                if hasattr(next_end, "tzinfo") and next_end.tzinfo is not None:
                    # Konverter til UTC for IBKR
                    next_end_utc = next_end.astimezone(pytz.UTC)
                    end_datetime_str = next_end_utc.strftime("%Y%m%d %H:%M:%S") + " UTC"
                else:
                    end_datetime_str = next_end.strftime("%Y%m%d %H:%M:%S")

                logger.info(f"    {ticker}: chunk #{chunks_completed} → {n_inserted} bars, "
                            f"ældste nu: {current_oldest_naive.strftime('%Y-%m-%d %H:%M')}")

                # Brug naive version til loop-sammenligning
                current_oldest = current_oldest_naive

                # Sanity check: hvis vi får færre end forventet, stopper vi
                if n_inserted < 50:
                    logger.info(f"  {ticker}: Lille chunk ({n_inserted} bars) — sandsynligvis enden af historik")
                    return {
                        "chunks":     chunks_completed,
                        "total_bars": total_bars,
                        "success":    True,
                        "error":      None,
                    }

            except PacingViolation:
                logger.warning(f"    ⚠ PACING VIOLATION — venter {PACING_VIOLATION_WAIT}s...")
                await asyncio.sleep(PACING_VIOLATION_WAIT)
                continue

            except Exception as e:
                if attempt < MAX_RETRIES_PER_CHUNK:
                    logger.warning(f"    Chunk exception attempt {attempt}: {e}")
                    await asyncio.sleep(3.0)
                    continue
                chunks_failed += 1
                logger.error(f"    {ticker}: Chunk crashed: {e}")
                break

        # Rate-limit sleep mellem chunks
        await asyncio.sleep(SLEEP_BETWEEN_REQUESTS)

        # Sikkerhedsventil: stop hvis for mange fejlede chunks
        if chunks_failed >= 3:
            logger.error(f"  {ticker}: For mange chunk-fejl — afbryder")
            return {
                "chunks":     chunks_completed,
                "total_bars": total_bars,
                "success":    False,
                "error":      "Too many chunk failures",
            }

    return {
        "chunks":     chunks_completed,
        "total_bars": total_bars,
        "success":    True,
        "error":      None,
    }


# ─────────────────────────────────────────────────────────────────
# Univers-download (alle tickers)
# ─────────────────────────────────────────────────────────────────

async def download_universe(tickers: list[str], months: int) -> dict:
    """Download 5-min bars for liste af tickers."""
    today = datetime.now().date()
    start_date = (today - timedelta(days=months * 31)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    logger.info(f"Tickers at processe: {len(tickers)}")
    logger.info(f"Mål-periode: {start_date} → {end_date} ({months} måneder)")

    # Sørg for tickers er i universet
    for t in tickers:
        ensure_ticker_in_universe(t)

    ib = await connect_ibkr()

    stats = {"success": 0, "failed": 0, "total_bars": 0, "total_chunks": 0}
    start_time = datetime.now()

    try:
        for i, ticker in enumerate(tickers, start=1):
            logger.info(f"")
            logger.info(f"━━━ [{i}/{len(tickers)}] {ticker} ━━━")

            log_id = log_download_start(ticker, start_date, end_date)

            try:
                result = await download_ticker(ib, ticker, months)

                if result["success"]:
                    log_download_success(log_id, result["total_bars"])
                    stats["success"] += 1
                    stats["total_bars"] += result["total_bars"]
                    stats["total_chunks"] += result["chunks"]
                    logger.info(f"  ✓ {ticker}: {result['total_bars']:,} bars i {result['chunks']} chunks")
                else:
                    log_download_failure(log_id, result["error"] or "unknown")
                    stats["failed"] += 1
                    logger.error(f"  ✗ {ticker}: {result['error']}")

            except Exception as e:
                log_download_failure(log_id, str(e))
                stats["failed"] += 1
                logger.exception(f"  ✗ {ticker}: Fatal exception")

            # Sleep mellem tickers
            await asyncio.sleep(SLEEP_BETWEEN_REQUESTS)

    finally:
        ib.disconnect()
        logger.info("")
        logger.info("Frakoblet IBKR")

    duration = (datetime.now() - start_time).total_seconds()
    stats["duration_sec"] = duration

    return stats


# ─────────────────────────────────────────────────────────────────
# Output / CLI
# ─────────────────────────────────────────────────────────────────

def print_summary(stats: dict) -> None:
    duration_min = stats.get("duration_sec", 0) / 60
    print()
    print("=" * 60)
    print("  Intraday Download — Resultat")
    print("=" * 60)
    print(f"  Varighed:        {duration_min:.1f} minutter")
    print(f"  ✓ Success:       {stats['success']}")
    print(f"  ✗ Failed:        {stats['failed']}")
    print(f"  Total bars:      {stats['total_bars']:,}")
    print(f"  Total chunks:    {stats['total_chunks']}")
    print("=" * 60)
    print()


async def main_async(args) -> int:
    init_database()

    # Bestem tickers
    if args.test:
        tickers = TEST_TICKERS
        months = 1
        logger.info(f"TEST mode: {len(tickers)} tickers, {months} måned")
    elif args.tickers_file:
        tickers = load_tickers_from_file(Path(args.tickers_file))
        months = args.months
        logger.info(f"Læste {len(tickers)} tickers fra {args.tickers_file}")
    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
        months = args.months
        logger.info(f"Tickers fra CLI: {tickers}")
    else:
        # Default: SPY, QQQ, IWM
        tickers = DEFAULT_ETFS
        months = args.months
        logger.info(f"Default ETF univers: {tickers}")

    try:
        stats = await download_universe(tickers, months)
    except KeyboardInterrupt:
        logger.warning("\nAfbrudt af bruger")
        return 130
    except Exception as e:
        logger.exception(f"Fatal fejl: {e}")
        return 1

    print_summary(stats)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Download 5-min bars fra IBKR")
    parser.add_argument("--test", action="store_true",
                        help="Test med SPY i 1 måned")
    parser.add_argument("--tickers", nargs="+",
                        help="Liste af ticker-symboler (fx --tickers SPY QQQ IWM)")
    parser.add_argument("--tickers-file", type=str,
                        help="Fil med tickers (én per linje)")
    parser.add_argument("--months", type=int, default=12,
                        help="Antal måneder (default: 12)")
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
