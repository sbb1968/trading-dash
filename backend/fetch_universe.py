"""
fetch_universe.py
─────────────────
Find vores univers af small-cap tickers til Gap Fade backtest.

Kører flere IBKR scanner-queries og kombinerer resultaterne. Skriver
til universe.txt (én ticker per linje) og indsætter i tickers-tabellen
i historical.db.

Designprincipper:
  - 5 forskellige scans for diversitet (vindere, tabere, volumen)
  - Filter i Python efter scan (mere pålideligt end IBKR's egen filter)
  - Dry-run mode så du kan inspicere resultatet før commit
  - Idempotent: re-kør sikkert, opdaterer eksisterende univers

Brug:
    python fetch_universe.py --dry-run
        Vis hvad scanneren finder uden at gemme

    python fetch_universe.py
        Find tickers og gem i universe.txt + database

    python fetch_universe.py --output mit_univers.txt
        Brug et andet output-filnavn

    python fetch_universe.py --max-per-scan 100
        Hent flere fra hver scan (default 50)

Placering: C:\\Projects\\trading-dash\\backend\\fetch_universe.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Python 3.14 event loop fix ────────────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, ScannerSubscription, TagValue

from historical_db import get_connection, init_database


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fetch_universe")
logging.getLogger("ib_async").setLevel(logging.WARNING)


# ── Konfiguration ────────────────────────────────────────────
IBKR_HOST       = "127.0.0.1"
IBKR_PORT       = 7497
IBKR_CLIENT_ID  = 11             # Anden end downloader (10) og live algo
CONNECT_TIMEOUT = 15

# Pris-grænser for vores univers (small caps)
PRICE_MIN = 1.0
PRICE_MAX = 20.0

# Min. volumen filter (gennemsnits-dagsvolumen)
MIN_AVG_VOLUME = 500_000

# Default antal resultater per scan
DEFAULT_MAX_PER_SCAN = 50

# Output-fil default
DEFAULT_OUTPUT = "universe.txt"


# ── Scan-definitioner ────────────────────────────────────────
# Hver scan giver ~50 unikke tickers; tilsammen får vi 200-400 efter dedup.
#
# Vi bruger STK.US.MAJOR som lokation — det er din eksisterende live algo's
# scanner-univers, så vi får samme aktie-type.

SCANS = [
    {
        "name":      "Top Gainers",
        "scan_code": "TOP_PERC_GAIN",
        "rationale": "Aktier i bevægelse op — kandidater til breakout og gap-up",
    },
    {
        "name":      "Top Losers",
        "scan_code": "TOP_PERC_LOSE",
        "rationale": "Aktier i bevægelse ned — kandidater til reversal og gap-down",
    },
    {
        "name":      "Most Active",
        "scan_code": "MOST_ACTIVE",
        "rationale": "Højeste absolutte volumen — likvide og handelbare",
    },
    {
        "name":      "Hot by Volume",
        "scan_code": "HOT_BY_VOLUME",
        "rationale": "Højest relativ volumen vs gennemsnit — newsworthy",
    },
    {
        "name":      "Top Volatility",
        "scan_code": "HIGH_OPT_IMP_VOLAT",
        "rationale": "Høj implicit volatilitet — gapper ofte",
    },
]


# ─────────────────────────────────────────────────────────────────
# Scanner-funktioner
# ─────────────────────────────────────────────────────────────────

async def run_single_scan(
    ib: IB,
    scan_code: str,
    max_results: int,
) -> list[dict]:
    """
    Kør én scanner-query og returnér rå resultater.

    Returnerer liste af dicts med felter: symbol, exchange, marketName.
    """
    sub = ScannerSubscription(
        instrument        = "STK",                    # Stocks only
        locationCode      = "STK.US.MAJOR",           # NYSE, NASDAQ, AMEX, ARCA
        scanCode          = scan_code,
        abovePrice        = PRICE_MIN,
        belowPrice        = PRICE_MAX,
        aboveVolume       = MIN_AVG_VOLUME,
        numberOfRows      = max_results,
    )

    # Yderligere filtre som TagValues
    # NB: usdMarketCapAbove er deaktiveret af IBKR — fjernet
    # Market cap-filtrering opnås indirekte via pris ($1-20) + volumen (>500k)
    filters = [
        TagValue("avgVolumeAbove", str(MIN_AVG_VOLUME)),
    ]

    try:
        results = await ib.reqScannerDataAsync(sub, [], filters)
    except Exception as e:
        logger.error(f"Scanner '{scan_code}' fejlede: {e}")
        return []

    if not results:
        return []

    output = []
    for r in results:
        # r er ScanData, har contractDetails med .contract
        c = r.contractDetails.contract
        output.append({
            "symbol":     c.symbol,
            "exchange":   c.primaryExchange or c.exchange or "",
            "marketName": getattr(r.contractDetails, "marketName", ""),
            "scan_code":  scan_code,
        })

    return output


async def fetch_all_scans(
    ib: IB,
    max_per_scan: int,
) -> dict[str, dict]:
    """
    Kør alle definerede scans. Returnér unique tickers som dict.

    Key: ticker symbol. Value: dict med info inkl. hvilke scans fandt den.
    """
    universe: dict[str, dict] = {}

    for scan in SCANS:
        logger.info(f"Scanner: {scan['name']} ({scan['scan_code']})")
        logger.info(f"         {scan['rationale']}")

        results = await run_single_scan(ib, scan["scan_code"], max_per_scan)

        new_count = 0
        for r in results:
            symbol = r["symbol"]
            if symbol in universe:
                # Allerede fundet — tilføj denne scan til 'sources'
                universe[symbol]["sources"].append(scan["name"])
            else:
                universe[symbol] = {
                    "symbol":   symbol,
                    "exchange": r["exchange"],
                    "sources":  [scan["name"]],
                }
                new_count += 1

        logger.info(f"         → {len(results)} resultater ({new_count} nye, {len(results) - new_count} dupes)")

        # Kort pause mellem scans (rate limit margin)
        await asyncio.sleep(1.0)

    return universe


# ─────────────────────────────────────────────────────────────────
# Filtrering — fjern uønskede tickers efter scan
# ─────────────────────────────────────────────────────────────────

def looks_like_etf_or_fund(symbol: str) -> bool:
    """
    Heuristik for at fange ETFs/funds der slap igennem.
    IBKR's STK filter er ikke 100% pålideligt.
    """
    # Mange ETFs har 3-5 bogstavers symboler, men det gør almindelige aktier også
    # Bedste heuristik: kendte ETF-suffikser og prefikser
    etf_indicators = [
        # Leveraged ETF suffikser
        "X",   # ofte i 3x ETFs (TQQQ, SOXL)... men også normale aktier
        # Vi tager en konservativ tilgang og kun fanger oplagte
    ]
    # Konservativ: lad det stå hvis vi ikke er sikre.
    # Det er bedre at have nogle ETFs i universet end at smide rigtige aktier ud.
    return False


def filter_universe(universe: dict[str, dict]) -> dict[str, dict]:
    """
    Filtrér universe-dict ned til kvalitet-godkendte tickers.

    Lige nu er hovedfiltret allerede klaret af IBKR scanner.
    Denne funktion er placeholder for fremtidige filtre (ETF, OTC, etc.)
    """
    filtered = {}
    removed = []

    for symbol, info in universe.items():
        # Tom symbol = corrupted data
        if not symbol or not symbol.strip():
            removed.append((symbol, "tom symbol"))
            continue

        # Heuristisk ETF-filter (forsigtigt)
        if looks_like_etf_or_fund(symbol):
            removed.append((symbol, "ser ud til at være ETF"))
            continue

        # Bevar
        filtered[symbol] = info

    if removed:
        logger.info(f"Filtreret væk: {len(removed)} tickers")
        for sym, reason in removed[:10]:
            logger.info(f"  - {sym}: {reason}")
        if len(removed) > 10:
            logger.info(f"  ... og {len(removed) - 10} mere")

    return filtered


# ─────────────────────────────────────────────────────────────────
# Output — fil og database
# ─────────────────────────────────────────────────────────────────

def write_universe_file(universe: dict[str, dict], path: Path) -> None:
    """Skriv tickers til text-fil (én per linje, sorteret)."""
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# Gap Fade univers — genereret {datetime.now().isoformat()}\n")
        f.write(f"# {len(universe)} unique tickers fra IBKR scanner\n")
        f.write(f"# Kriterier: pris ${PRICE_MIN}-${PRICE_MAX}, vol >{MIN_AVG_VOLUME:,}\n")
        f.write(f"#\n")
        for symbol in sorted(universe.keys()):
            f.write(f"{symbol}\n")
    logger.info(f"✓ Univers skrevet: {path} ({len(universe)} tickers)")


def save_to_database(universe: dict[str, dict]) -> tuple[int, int]:
    """
    Indsæt univers i tickers-tabellen.
    Returnér (nye, opdaterede).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    new_count = 0
    updated_count = 0

    with get_connection() as conn:
        for symbol, info in universe.items():
            # Tjek om allerede findes
            cur = conn.execute("SELECT symbol FROM tickers WHERE symbol = ?", (symbol,))
            existing = cur.fetchone()

            if existing is None:
                # Ny ticker
                conn.execute("""
                    INSERT INTO tickers
                        (symbol, exchange, added_date, is_active, is_gap_active, notes)
                    VALUES (?, ?, ?, 1, 0, ?)
                """, (
                    symbol,
                    info["exchange"],
                    today,
                    f"Fra scans: {', '.join(info['sources'])}",
                ))
                new_count += 1
            else:
                # Eksisterer — opdater notes med seneste scan-info
                conn.execute("""
                    UPDATE tickers
                    SET is_active = 1,
                        exchange = COALESCE(NULLIF(?, ''), exchange),
                        notes = ?
                    WHERE symbol = ?
                """, (
                    info["exchange"],
                    f"Fra scans: {', '.join(info['sources'])}",
                    symbol,
                ))
                updated_count += 1

    return new_count, updated_count


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def print_summary(universe: dict[str, dict]) -> None:
    """Print en oversigt over hvad vi fandt."""
    print()
    print("=" * 70)
    print(f"  Universe Scanner — Resultat")
    print("=" * 70)
    print(f"  Total unique tickers: {len(universe)}")
    print()

    # Fordeling efter børs
    by_exchange: dict[str, int] = {}
    for info in universe.values():
        ex = info["exchange"] or "UNKNOWN"
        by_exchange[ex] = by_exchange.get(ex, 0) + 1

    print(f"  Fordeling efter børs:")
    for ex, count in sorted(by_exchange.items(), key=lambda x: -x[1]):
        print(f"    {ex:15s} {count}")
    print()

    # Fordeling efter antal scans (kvalitet-indikator)
    by_source_count: dict[int, int] = {}
    for info in universe.values():
        n = len(info["sources"])
        by_source_count[n] = by_source_count.get(n, 0) + 1

    print(f"  Fordeling efter antal scans fundet i:")
    for n, count in sorted(by_source_count.items()):
        print(f"    {n} scan(s):       {count}  {'(høj kvalitet)' if n >= 3 else ''}")
    print()

    # Vis de første 20 tickers som sample
    sorted_syms = sorted(universe.keys())
    sample = sorted_syms[:20]
    print(f"  Sample (første 20):")
    print(f"    {', '.join(sample)}")
    if len(sorted_syms) > 20:
        print(f"    ... og {len(sorted_syms) - 20} mere")
    print()
    print("=" * 70)


async def main_async(args) -> int:
    # Init database
    init_database()

    # Forbind
    ib = IB()
    logger.info(f"Forbinder til IBKR {IBKR_HOST}:{IBKR_PORT} (client_id={IBKR_CLIENT_ID})...")
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=CONNECT_TIMEOUT)
    except Exception as e:
        logger.error(f"Kunne ikke forbinde til IBKR: {e}")
        logger.error("Tjek at TWS kører og at port 7497 er åben")
        return 1
    logger.info("✓ Forbundet til IBKR")

    try:
        # Kør alle scans
        universe = await fetch_all_scans(ib, args.max_per_scan)

        # Filtrer
        universe = filter_universe(universe)

        if not universe:
            logger.error("Scanner returnerede 0 tickers — tjek IBKR's market data status")
            return 1

        # Vis summary
        print_summary(universe)

        # Gem (medmindre dry-run)
        if args.dry_run:
            logger.info("DRY RUN — intet er gemt")
            logger.info("Kør uden --dry-run for at gemme")
        else:
            output_path = Path(args.output)
            write_universe_file(universe, output_path)
            new, updated = save_to_database(universe)
            logger.info(f"✓ Database: {new} nye tickers, {updated} opdaterede")

        return 0

    finally:
        ib.disconnect()
        logger.info("Frakoblet IBKR")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find univers af small-cap tickers via IBKR scanner"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Vis resultater uden at gemme",
    )
    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUTPUT,
        help=f"Output-fil (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--max-per-scan", type=int, default=DEFAULT_MAX_PER_SCAN,
        help=f"Max resultater per scan (default: {DEFAULT_MAX_PER_SCAN})",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("Afbrudt af bruger")
        return 130


if __name__ == "__main__":
    sys.exit(main())
