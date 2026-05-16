"""
download_daily_ibkr.py
──────────────────────
Henter 12 måneders daglige OHLCV-bars fra IBKR og gemmer i historical.db.

Designprincipper:
  - Respekterer IBKR rate-limits (500ms mellem requests)
  - Genstartbar: skipper tickers der allerede er færdige
  - Idempotent: INSERT OR REPLACE — sikker at re-køre
  - Per-ticker fejl-isolation: én fejl stopper ikke hele jobbet
  - Alt logges i download_log tabellen

Brug:
    python download_daily_ibkr.py --test
        Kører på 5 test-tickers (GME, AMC, AAPL, SPY, TSLA)

    python download_daily_ibkr.py --tickers universe.txt
        Kører på alle tickers i universe.txt (én per linje)

    python download_daily_ibkr.py --retry-failed
        Kører kun på tickers der fejlede ved sidste forsøg

    python download_daily_ibkr.py --months 12 --tickers universe.txt
        Specificér antal måneder (default 12)

Placering: C:\\Projects\\trading-dash\\backend\\download_daily_ibkr.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Python 3.14 event loop fix (KRITISK før ib_async import) ──
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock

from historical_db import get_connection, init_database

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("daily_downloader")

# Sluk ib_async's egen verbose logging
logging.getLogger("ib_async").setLevel(logging.WARNING)


# ── Konfiguration ────────────────────────────────────────────
IBKR_HOST       = "127.0.0.1"
IBKR_PORT       = 7497          # Paper trading port
IBKR_CLIENT_ID  = 10            # Unik client id (forskellig fra live algo)
CONNECT_TIMEOUT = 15            # sek

# Rate limiting
SLEEP_BETWEEN_REQUESTS = 0.5    # sek — 2 req/sek = god margin under IBKR's 6/2sek
PACING_VIOLATION_WAIT  = 60     # sek — vent når pacing-fejl rammer
MAX_RETRIES_PER_TICKER = 3      # forsøg per ticker ved transient fejl

# Test-univers (bruges af --test)
TEST_TICKERS = ["GME", "AMC", "AAPL", "SPY", "TSLA"]


# ─────────────────────────────────────────────────────────────────
# Hjælpefunktioner
# ─────────────────────────────────────────────────────────────────

def load_tickers_from_file(path: Path) -> list[str]:
    """Læs ticker-symboler fra en tekstfil (én per linje)."""
    if not path.exists():
        raise FileNotFoundError(f"Ticker-fil findes ikke: {path}")

    tickers = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip().upper()
            # Skip tomme linjer og kommentarer (linje starter med #)
            if not line or line.startswith("#"):
                continue
            tickers.append(line)

    # Fjern dubletter, bevar rækkefølge
    seen = set()
    unique = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def get_already_completed(date_start: str, date_end: str) -> set[str]:
    """
    Find tickers der allerede har success-status for denne dato-range.
    Bruges til at skippe ved genstart.
    """
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT DISTINCT ticker
            FROM download_log
            WHERE download_type = 'daily'
              AND date_range_start = ?
              AND date_range_end   = ?
              AND status = 'success'
        """, (date_start, date_end))
        return {row["ticker"] for row in cur.fetchall()}


def get_failed_tickers(date_start: str, date_end: str) -> list[str]:
    """Find tickers der fejlede ved sidste forsøg."""
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT DISTINCT ticker
            FROM download_log
            WHERE download_type = 'daily'
              AND date_range_start = ?
              AND date_range_end   = ?
              AND status = 'failed'
              AND ticker NOT IN (
                  SELECT ticker FROM download_log
                  WHERE download_type = 'daily'
                    AND date_range_start = ?
                    AND date_range_end   = ?
                    AND status = 'success'
              )
        """, (date_start, date_end, date_start, date_end))
        return [row["ticker"] for row in cur.fetchall()]


def ensure_ticker_in_universe(symbol: str, exchange: str = "") -> None:
    """Sørg for at ticker eksisterer i tickers-tabellen (insert eller ignore)."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO tickers (symbol, added_date, exchange)
            VALUES (?, ?, ?)
        """, (symbol, datetime.now().strftime("%Y-%m-%d"), exchange))


def log_download_start(ticker: str, date_start: str, date_end: str) -> int:
    """Log at en download starter. Returnér log_id."""
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO download_log
                (download_type, ticker, date_range_start, date_range_end,
                 started_at, status)
            VALUES ('daily', ?, ?, ?, ?, 'in_progress')
        """, (ticker, date_start, date_end, datetime.now().isoformat()))
        return cur.lastrowid


def log_download_success(log_id: int, bars_inserted: int) -> None:
    """Marker download som success."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE download_log
            SET status = 'success',
                completed_at = ?,
                bars_inserted = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), bars_inserted, log_id))


def log_download_failure(log_id: int, error_msg: str) -> None:
    """Marker download som failed."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE download_log
            SET status = 'failed',
                completed_at = ?,
                error_msg = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), error_msg[:500], log_id))


def insert_daily_bars(ticker: str, bars: list) -> int:
    """
    Indsæt daily bars i bars_daily. INSERT OR REPLACE = idempotent.
    Returnér antal bars indsat.
    """
    if not bars:
        return 0

    rows = []
    for bar in bars:
        # ib_async returnerer bar.date som datetime.date — konvertér til ISO string
        date_str = bar.date.strftime("%Y-%m-%d") if hasattr(bar.date, "strftime") else str(bar.date)
        rows.append((
            ticker,
            date_str,
            float(bar.open),
            float(bar.high),
            float(bar.low),
            float(bar.close),
            int(bar.volume) if bar.volume else 0,
        ))

    with get_connection() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO bars_daily
                (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)

    return len(rows)


# ─────────────────────────────────────────────────────────────────
# IBKR-forbindelse
# ─────────────────────────────────────────────────────────────────

async def connect_ibkr() -> IB:
    """Forbind til IBKR Paper Trading."""
    ib = IB()
    logger.info(f"Forbinder til IBKR {IBKR_HOST}:{IBKR_PORT} (client_id={IBKR_CLIENT_ID})...")
    await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=CONNECT_TIMEOUT)
    logger.info("✓ Forbundet til IBKR")
    return ib


async def fetch_daily_bars(
    ib: IB,
    ticker: str,
    months: int,
) -> tuple[list, Optional[str]]:
    """
    Hent daily bars for én ticker.
    Returnér (bars, error_message). Hvis succes er error_message None.
    """
    try:
        contract = Stock(ticker, "SMART", "USD")
        # qualifyContractsAsync fejler hvis ticker er delisted/ukendt
        await ib.qualifyContractsAsync(contract)

        # IBKR duration format: "12 M" = 12 måneder
        duration = f"{months} M"

        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime    = "",          # tom = nu
            durationStr    = duration,
            barSizeSetting = "1 day",
            whatToShow     = "TRADES",
            useRTH         = True,        # kun regular trading hours for daily
            formatDate     = 1,           # ISO format
        )

        if bars is None or len(bars) == 0:
            return [], "No data returned from IBKR (delisted eller ingen data)"

        return bars, None

    except Exception as e:
        error_str = str(e)
        # Pacing violation = vent og bobl op
        if "pacing violation" in error_str.lower() or "max" in error_str.lower():
            raise PacingViolation(error_str) from e
        return [], error_str


class PacingViolation(Exception):
    """Raised when IBKR returns a pacing violation. Caller should wait."""
    pass


# ─────────────────────────────────────────────────────────────────
# Hovedløkken
# ─────────────────────────────────────────────────────────────────

async def download_universe(
    tickers: list[str],
    months: int = 12,
    skip_completed: bool = True,
) -> dict:
    """
    Download daily bars for en liste af tickers.

    Returnér statistik-dict med antal success/failed/skipped/bars.
    """
    # Beregn dato-range (bruges til genstart-logik)
    today = datetime.now().date()
    start_date = (today - timedelta(days=months * 31)).strftime("%Y-%m-%d")
    end_date   = today.strftime("%Y-%m-%d")

    logger.info(f"Dato-range: {start_date} → {end_date} ({months} måneder)")
    logger.info(f"Tickers at processe: {len(tickers)}")

    # Skip allerede færdige (medmindre user explicitly disabled det)
    if skip_completed:
        completed = get_already_completed(start_date, end_date)
        if completed:
            tickers = [t for t in tickers if t not in completed]
            logger.info(f"Skipper {len(completed)} allerede færdige tickers")
            logger.info(f"Faktiske at processe: {len(tickers)}")

    if not tickers:
        logger.info("Ingen tickers at processe — alt er allerede færdigt")
        return {"success": 0, "failed": 0, "skipped": 0, "total_bars": 0}

    # Sørg for alle tickers er i universet
    for t in tickers:
        ensure_ticker_in_universe(t)

    # Forbind til IBKR
    ib = await connect_ibkr()

    # Statistik
    stats = {"success": 0, "failed": 0, "no_data": 0, "total_bars": 0}
    start_time = datetime.now()

    try:
        for i, ticker in enumerate(tickers, start=1):
            # Progress-info
            elapsed = (datetime.now() - start_time).total_seconds()
            remaining = len(tickers) - i
            eta_sec = (elapsed / i) * remaining if i > 0 else 0
            eta_min = eta_sec / 60

            log_id = log_download_start(ticker, start_date, end_date)
            attempt = 0
            success = False

            while attempt < MAX_RETRIES_PER_TICKER and not success:
                attempt += 1
                try:
                    bars, error = await fetch_daily_bars(ib, ticker, months)

                    if error:
                        # "No data" er ikke en fejl vi retry'er — det er delisted/ukendt
                        if "No data" in error or "delisted" in error.lower():
                            log_download_failure(log_id, error)
                            stats["no_data"] += 1
                            logger.warning(f"[{i:>3}/{len(tickers)}] {ticker:6s}  no data  (ETA {eta_min:.0f}m)")
                            success = True   # stop retry-loop, men ikke success-counter
                            break
                        # Andre fejl: retry
                        if attempt < MAX_RETRIES_PER_TICKER:
                            logger.warning(f"  {ticker} fejl attempt {attempt}: {error[:80]}")
                            await asyncio.sleep(2.0)
                            continue
                        # Sidste forsøg fejlede
                        log_download_failure(log_id, error)
                        stats["failed"] += 1
                        logger.error(f"[{i:>3}/{len(tickers)}] {ticker:6s}  FAIL    {error[:60]}")
                        break

                    # Succes
                    n_inserted = insert_daily_bars(ticker, bars)
                    log_download_success(log_id, n_inserted)
                    stats["success"] += 1
                    stats["total_bars"] += n_inserted
                    success = True
                    logger.info(f"[{i:>3}/{len(tickers)}] {ticker:6s}  ✓ {n_inserted:>4} bars  (ETA {eta_min:.0f}m)")

                except PacingViolation as e:
                    logger.warning(f"  ⚠ PACING VIOLATION — venter {PACING_VIOLATION_WAIT}s...")
                    await asyncio.sleep(PACING_VIOLATION_WAIT)
                    # Retry samme ticker
                    continue

                except Exception as e:
                    if attempt < MAX_RETRIES_PER_TICKER:
                        logger.warning(f"  {ticker} unexpected fejl attempt {attempt}: {e}")
                        await asyncio.sleep(2.0)
                        continue
                    log_download_failure(log_id, str(e))
                    stats["failed"] += 1
                    logger.error(f"[{i:>3}/{len(tickers)}] {ticker:6s}  EXCEPTION  {str(e)[:60]}")
                    break

            # Rate-limit sleep mellem requests (også ved fejl, for sikkerhed)
            await asyncio.sleep(SLEEP_BETWEEN_REQUESTS)

    finally:
        ib.disconnect()
        logger.info("Frakoblet IBKR")

    return stats


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def print_summary(stats: dict, duration_sec: float) -> None:
    """Print resultat-summary efter download er færdig."""
    print()
    print("=" * 60)
    print("  Daily Download — Resultat")
    print("=" * 60)
    print(f"  Varighed:        {duration_sec / 60:.1f} minutter")
    print(f"  ✓ Success:       {stats['success']}")
    print(f"  ⚠ No data:       {stats['no_data']}")
    print(f"  ✗ Failed:        {stats['failed']}")
    print(f"  Total bars:      {stats['total_bars']:,}")
    print("=" * 60)
    print()

    if stats["failed"] > 0:
        print(f"  💡 Re-kør med --retry-failed for at prøve igen på de {stats['failed']} failures")
        print()


async def main_async(args) -> int:
    # Initialiser database hvis ikke gjort
    init_database()

    # Bestem ticker-liste
    if args.test:
        tickers = TEST_TICKERS
        logger.info(f"TEST mode: {len(tickers)} tickers")
    elif args.retry_failed:
        today = datetime.now().date()
        start_date = (today - timedelta(days=args.months * 31)).strftime("%Y-%m-%d")
        end_date   = today.strftime("%Y-%m-%d")
        tickers = get_failed_tickers(start_date, end_date)
        if not tickers:
            logger.info("Ingen failed tickers fundet — intet at retry")
            return 0
        logger.info(f"RETRY mode: {len(tickers)} failed tickers")
    elif args.tickers:
        path = Path(args.tickers)
        tickers = load_tickers_from_file(path)
        logger.info(f"Læste {len(tickers)} tickers fra {path}")
    else:
        logger.error("Specificér --test, --retry-failed eller --tickers FILE")
        return 1

    # Kør download
    start = datetime.now()
    try:
        stats = await download_universe(
            tickers,
            months=args.months,
            skip_completed=not args.no_skip,
        )
    except KeyboardInterrupt:
        logger.warning("\nAfbrudt af bruger — log er gemt, kør igen for at fortsætte")
        return 130
    except Exception as e:
        logger.exception(f"Fatal fejl: {e}")
        return 1

    duration = (datetime.now() - start).total_seconds()
    print_summary(stats, duration)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download daily bars fra IBKR til historical.db"
    )
    parser.add_argument(
        "--test", action="store_true",
        help=f"Kør på {len(TEST_TICKERS)} test-tickers ({', '.join(TEST_TICKERS)})",
    )
    parser.add_argument(
        "--tickers", type=str,
        help="Sti til fil med ticker-symboler (én per linje)",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Kør kun på tickers der fejlede ved sidste forsøg",
    )
    parser.add_argument(
        "--months", type=int, default=12,
        help="Antal måneders historik (default: 12)",
    )
    parser.add_argument(
        "--no-skip", action="store_true",
        help="Skip ikke allerede færdige tickers (re-download alt)",
    )
    args = parser.parse_args()

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
