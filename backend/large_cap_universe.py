"""
large_cap_universe.py
─────────────────────
Indsætter S&P 100 large-cap tickers i historical.db til Gap Fade-test
på large caps.

Hvorfor S&P 100 og ikke S&P 500?
  - Mest likvide US large caps — Gap Fade teorien forventer stærkest edge her
  - 100 tickers ≈ 3 min daily download (vs 12 min for S&P 500)
  - Statistisk tilstrækkelig: 100 tickers × 12 mdr ≈ 25.000 daily bars

Listen er kurateret pr. 2025 — opdateres sjældent (~5 tickers/år ændring).
Hvis du opdager en delisted ticker, fjern den manuelt og re-kør scriptet.

Brug:
    python large_cap_universe.py
        Indsætter tickers i database + skriver large_cap_universe.txt

    python large_cap_universe.py --output minfil.txt
        Brug andet output-filnavn

    python large_cap_universe.py --dry-run
        Vis listen uden at gemme

Placering: C:\\Projects\\trading-dash\\backend\\large_cap_universe.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from historical_db import get_connection, init_database


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("large_cap_universe")


# ─────────────────────────────────────────────────────────────────
# S&P 100 — hardkodet liste
# ─────────────────────────────────────────────────────────────────
# Kurateret pr. begyndelsen af 2025. Sorteret alfabetisk.
#
# Disse er de 100 mest likvide US large-cap aktier, dækker:
#   - Big Tech (AAPL, MSFT, GOOGL, META, AMZN, NVDA)
#   - Finansielle (JPM, BAC, GS, MS, BLK, V, MA)
#   - Healthcare (JNJ, PFE, UNH, ABBV, MRK, LLY)
#   - Energi (XOM, CVX, COP)
#   - Industri (BA, CAT, GE, HON, RTX)
#   - Consumer (KO, PEP, PG, WMT, MCD, NKE, COST)
#   - Telecom (T, VZ, TMUS)

SP100_TICKERS = [
    # A
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMGN", "AMT", "AMZN", "AVGO",
    "AXP",
    # B
    "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK.B",
    # C
    "C", "CAT", "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO",
    "CVS", "CVX",
    # D, E
    "DE", "DHR", "DIS", "DOW", "DUK", "EMR", "EXC",
    # F, G
    "F", "FDX", "GD", "GE", "GILD", "GM", "GOOG", "GOOGL", "GS",
    # H, I
    "HD", "HON", "IBM", "INTC",
    # J, K
    "JNJ", "JPM", "KHC", "KO",
    # L
    "LIN", "LLY", "LMT", "LOW",
    # M
    "MA", "MCD", "MDLZ", "MDT", "MET", "META", "MMM", "MO", "MRK", "MS",
    "MSFT",
    # N, O
    "NEE", "NFLX", "NKE", "NVDA", "ORCL", "OXY",
    # P
    "PEP", "PFE", "PG", "PM", "PYPL",
    # Q, R
    "QCOM", "RTX",
    # S
    "SBUX", "SCHW", "SO", "SPG",
    # T
    "T", "TGT", "TMO", "TMUS", "TSLA", "TXN",
    # U, V
    "UNH", "UNP", "UPS", "USB", "V", "VZ",
    # W, X
    "WBA", "WFC", "WMT", "XOM",
]


# ─────────────────────────────────────────────────────────────────
# Validering
# ─────────────────────────────────────────────────────────────────

def validate_list() -> None:
    """Sanity check på listen før vi bruger den."""
    # Tjek for dubletter
    if len(SP100_TICKERS) != len(set(SP100_TICKERS)):
        dubs = [t for t in SP100_TICKERS if SP100_TICKERS.count(t) > 1]
        raise ValueError(f"Dubletter i SP100_TICKERS: {set(dubs)}")

    # Tjek antallet (vi tillader 95-105 da listen ændrer sig over tid)
    n = len(SP100_TICKERS)
    if not (95 <= n <= 105):
        logger.warning(f"S&P 100 liste indeholder {n} tickers (forventet ~100)")

    # Tjek for tomme strings
    for t in SP100_TICKERS:
        if not t or not t.strip():
            raise ValueError("Tom ticker i SP100_TICKERS")
        if not t.replace(".", "").replace("-", "").isalnum():
            raise ValueError(f"Ugyldig ticker-format: {t!r}")


# ─────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────

def insert_tickers() -> tuple[int, int]:
    """
    Indsæt tickers i tickers-tabellen.
    Returnér (nye, opdaterede).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    new_count = 0
    updated_count = 0

    with get_connection() as conn:
        for symbol in SP100_TICKERS:
            cur = conn.execute("SELECT symbol, notes FROM tickers WHERE symbol = ?", (symbol,))
            existing = cur.fetchone()

            if existing is None:
                # Ny ticker
                conn.execute("""
                    INSERT INTO tickers
                        (symbol, exchange, added_date, is_active, is_gap_active, notes)
                    VALUES (?, '', ?, 1, 0, 'S&P 100 large cap')
                """, (symbol, today))
                new_count += 1
            else:
                # Eksisterer — opdater notes (kombiner hvis allerede har notes fra small-cap)
                old_notes = existing["notes"] or ""
                if "S&P 100" not in old_notes:
                    new_notes = f"{old_notes}; S&P 100 large cap" if old_notes else "S&P 100 large cap"
                    conn.execute("""
                        UPDATE tickers
                        SET notes = ?, is_active = 1
                        WHERE symbol = ?
                    """, (new_notes, symbol))
                updated_count += 1

    return new_count, updated_count


# ─────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────

def write_universe_file(path: Path) -> None:
    """Skriv tickers til text-fil (én per linje, sorteret)."""
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# S&P 100 large-cap univers — kurateret 2025\n")
        f.write(f"# Genereret {datetime.now().isoformat()}\n")
        f.write(f"# {len(SP100_TICKERS)} tickers\n")
        f.write(f"#\n")
        for symbol in sorted(SP100_TICKERS):
            f.write(f"{symbol}\n")
    logger.info(f"✓ Univers skrevet: {path} ({len(SP100_TICKERS)} tickers)")


def print_summary() -> None:
    """Print en oversigt over listen."""
    print()
    print("=" * 70)
    print(f"  S&P 100 Large Cap Univers")
    print("=" * 70)
    print(f"  Antal tickers: {len(SP100_TICKERS)}")
    print()

    # Vis i kolonner (5 per linje)
    sorted_tickers = sorted(SP100_TICKERS)
    print(f"  Tickers (alfabetisk):")
    for i in range(0, len(sorted_tickers), 5):
        row = sorted_tickers[i:i+5]
        print(f"    {'  '.join(f'{t:6s}' for t in row)}")
    print()
    print("=" * 70)


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Indsæt S&P 100 large caps i historical.db"
    )
    parser.add_argument(
        "--output", type=str, default="large_cap_universe.txt",
        help="Output-fil (default: large_cap_universe.txt)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Vis listen uden at gemme",
    )
    args = parser.parse_args()

    # Validér listen
    try:
        validate_list()
    except ValueError as e:
        logger.error(f"Liste-validering fejlede: {e}")
        return 1

    logger.info(f"S&P 100 liste valideret: {len(SP100_TICKERS)} tickers")

    # Vis summary
    print_summary()

    if args.dry_run:
        logger.info("DRY RUN — intet er gemt")
        return 0

    # Init database og gem
    init_database()
    new, updated = insert_tickers()
    logger.info(f"✓ Database: {new} nye tickers, {updated} opdaterede")

    write_universe_file(Path(args.output))

    return 0


if __name__ == "__main__":
    sys.exit(main())
