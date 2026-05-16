"""
gap_finder.py
─────────────
Analyserer daily bars i historical.db og finder gap-events.

En "gap" defineres som:
    gap_pct = (today.open - yesterday.close) / yesterday.close × 100

Hvor abs(gap_pct) >= min_gap_pct (default 3%).

For hver gap-event gemmes:
  - gap_filled: True hvis prisen rørte yesterday.close i løbet af dagen
  - gap_closed_below: True hvis aktien lukkede på den modsatte side af close
                     (for gap-up: closed below yesterday.close)

Output:
  - gap_events-tabel populeres
  - tickers.is_gap_active sættes for tickers med >= 1 event
  - Sammenfattende statistik printes

Brug:
    python gap_finder.py
        Alle gaps >= 3% — anbefaling for fuld dataindsamling

    python gap_finder.py --min-gap 5
        Kun gaps >= 5%

    python gap_finder.py --direction up
        Kun gap-ups (relevant for Gap Fade short-strategi)

    python gap_finder.py --stats-only
        Vis kun nuværende statistik, kør ikke ny detection

    python gap_finder.py --export gap_events.csv
        Eksporter alle events til CSV til Excel-analyse

Placering: C:\\Projects\\trading-dash\\backend\\gap_finder.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

from historical_db import get_connection, init_database


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gap_finder")


# ── Konfiguration ────────────────────────────────────────────
DEFAULT_MIN_GAP_PCT = 3.0
MAX_GAP_PCT         = 100.0    # filter outliers (split/merger artefakter)


# ─────────────────────────────────────────────────────────────────
# Schema-migration: tilføj gap_closed_below kolonne hvis mangler
# ─────────────────────────────────────────────────────────────────

def ensure_schema_migration() -> None:
    """
    Tilføj gap_closed_below kolonne til gap_events hvis den ikke findes.
    Idempotent — sikker at køre flere gange.
    """
    with get_connection() as conn:
        # Tjek om kolonnen findes
        cur = conn.execute("PRAGMA table_info(gap_events)")
        cols = {row["name"] for row in cur.fetchall()}

        if "gap_closed_below" not in cols:
            logger.info("Tilføjer gap_closed_below kolonne til gap_events")
            conn.execute(
                "ALTER TABLE gap_events ADD COLUMN gap_closed_below INTEGER DEFAULT 0"
            )
            logger.info("✓ Schema migreret")


# ─────────────────────────────────────────────────────────────────
# Gap detection — kerne-logik
# ─────────────────────────────────────────────────────────────────

def find_gaps_for_ticker(
    ticker: str,
    min_gap_pct: float,
    direction_filter: Optional[str] = None,
) -> list[dict]:
    """
    Find alle gap-events for én ticker.

    Args:
        ticker: Symbol
        min_gap_pct: Minimum gap-procent (absolut værdi)
        direction_filter: None | "up" | "down" — filter på retning

    Returns:
        Liste af dict per gap-event, klar til database-insert.
    """
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT date, open, high, low, close, volume
            FROM bars_daily
            WHERE ticker = ?
            ORDER BY date ASC
        """, (ticker,))
        bars = cur.fetchall()

    if len(bars) < 2:
        return []

    events = []

    # Iterer fra dag 2 (vi behøver yesterday)
    for i in range(1, len(bars)):
        yesterday = bars[i - 1]
        today     = bars[i]

        prev_close = yesterday["close"]
        if prev_close <= 0:
            continue   # ugyldig data, skip

        open_today = today["open"]
        gap_pct = (open_today - prev_close) / prev_close * 100.0

        # Filtrer outliers (sandsynligvis split/merger artefakter)
        if abs(gap_pct) > MAX_GAP_PCT:
            continue

        # Filtrer på tærskel
        if abs(gap_pct) < min_gap_pct:
            continue

        direction = "up" if gap_pct > 0 else "down"

        # Filtrer på direction hvis angivet
        if direction_filter and direction != direction_filter:
            continue

        # Beregn "fyldte gap"-metrics
        if direction == "up":
            # Gap-up: gap er "touched" hvis dagens low <= yesterday's close
            gap_filled = today["low"] <= prev_close
            # "Closed below": aktien lukkede under yesterday's close (fuld fade)
            gap_closed_below = today["close"] <= prev_close
        else:
            # Gap-down: gap er "touched" hvis dagens high >= yesterday's close
            gap_filled = today["high"] >= prev_close
            # "Closed above" (i down-tilfælde): close >= prev_close
            gap_closed_below = today["close"] >= prev_close

        events.append({
            "ticker":           ticker,
            "date":             today["date"],
            "prev_close":       prev_close,
            "open":             open_today,
            "gap_pct":          round(gap_pct, 4),
            "gap_direction":    direction,
            "day_high":         today["high"],
            "day_low":          today["low"],
            "day_close":        today["close"],
            "gap_filled":       1 if gap_filled else 0,
            "gap_closed_below": 1 if gap_closed_below else 0,
            "volume":           today["volume"],
        })

    return events


# ─────────────────────────────────────────────────────────────────
# Database operations
# ─────────────────────────────────────────────────────────────────

def clear_existing_events(min_gap_pct: float, direction_filter: Optional[str]) -> int:
    """
    Slet eksisterende events der matcher de samme parametre.
    Dette gør re-kørsel idempotent uden dubletter.

    Returnerer antal slettede rækker.
    """
    where_clauses = ["ABS(gap_pct) >= ?"]
    params: list = [min_gap_pct]

    if direction_filter:
        where_clauses.append("gap_direction = ?")
        params.append(direction_filter)

    where_sql = " AND ".join(where_clauses)

    with get_connection() as conn:
        cur = conn.execute(f"DELETE FROM gap_events WHERE {where_sql}", params)
        return cur.rowcount


def insert_gap_events(events: list[dict]) -> int:
    """Indsæt liste af gap-events. INSERT OR REPLACE = idempotent."""
    if not events:
        return 0

    rows = [(
        e["ticker"],
        e["date"],
        e["prev_close"],
        e["open"],
        e["gap_pct"],
        e["gap_direction"],
        e["day_high"],
        e["day_low"],
        e["day_close"],
        e["gap_filled"],
        e["gap_closed_below"],
        e["volume"],
    ) for e in events]

    with get_connection() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO gap_events
                (ticker, date, prev_close, open, gap_pct, gap_direction,
                 day_high, day_low, day_close, gap_filled, gap_closed_below, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

    return len(rows)


def update_is_gap_active() -> int:
    """
    Sæt is_gap_active = 1 for tickers med >= 1 gap-event.
    Returnerer antal tickers markeret som gap-aktive.
    """
    with get_connection() as conn:
        # Reset alle først
        conn.execute("UPDATE tickers SET is_gap_active = 0")

        # Sæt flag for tickers med events
        cur = conn.execute("""
            UPDATE tickers
            SET is_gap_active = 1
            WHERE symbol IN (
                SELECT DISTINCT ticker FROM gap_events
            )
        """)
        return cur.rowcount


def get_universe_tickers() -> list[str]:
    """Hent alle aktive tickers med daily data."""
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT DISTINCT t.symbol
            FROM tickers t
            INNER JOIN bars_daily b ON b.ticker = t.symbol
            WHERE t.is_active = 1
            ORDER BY t.symbol
        """)
        return [row["symbol"] for row in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────
# Statistik
# ─────────────────────────────────────────────────────────────────

def print_statistics() -> None:
    """Print sammenfattende statistik om gap_events."""
    with get_connection(read_only=True) as conn:

        # Total counts
        cur = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN gap_direction = 'up'   THEN 1 ELSE 0 END) AS up_count,
                SUM(CASE WHEN gap_direction = 'down' THEN 1 ELSE 0 END) AS down_count,
                AVG(ABS(gap_pct)) AS avg_pct,
                MAX(ABS(gap_pct)) AS max_pct,
                MIN(ABS(gap_pct)) AS min_pct
            FROM gap_events
        """)
        row = cur.fetchone()
        total = row["total"] or 0

        print()
        print("=" * 70)
        print("  Gap Events — Statistik")
        print("=" * 70)
        print()

        if total == 0:
            print("  Ingen gap events i databasen.")
            print("  Kør gap_finder.py for at finde events.")
            print("=" * 70)
            return

        print(f"  Total events:       {total}")
        print(f"    Gap-up:           {row['up_count']}")
        print(f"    Gap-down:         {row['down_count']}")
        print(f"  Gap-størrelse:")
        print(f"    Gennemsnit:       {row['avg_pct']:.2f}%")
        print(f"    Min:              {row['min_pct']:.2f}%")
        print(f"    Max:              {row['max_pct']:.2f}%")
        print()

        # ── Distribution efter gap-størrelse ──────────────
        print(f"  Distribution efter gap-størrelse:")
        buckets = [
            (3.0, 5.0,   "  3% -  5%"),
            (5.0, 7.0,   "  5% -  7%"),
            (7.0, 10.0,  "  7% - 10%"),
            (10.0, 15.0, " 10% - 15%"),
            (15.0, 20.0, " 15% - 20%"),
            (20.0, 100.0," 20%+     "),
        ]
        for lo, hi, label in buckets:
            cur = conn.execute("""
                SELECT
                    COUNT(*) AS n,
                    SUM(CASE WHEN gap_direction = 'up' THEN 1 ELSE 0 END) AS ups,
                    SUM(CASE WHEN gap_direction = 'up' AND gap_filled = 1 THEN 1 ELSE 0 END) AS ups_filled,
                    SUM(CASE WHEN gap_direction = 'up' AND gap_closed_below = 1 THEN 1 ELSE 0 END) AS ups_closed_below
                FROM gap_events
                WHERE ABS(gap_pct) >= ? AND ABS(gap_pct) < ?
            """, (lo, hi))
            r = cur.fetchone()
            n = r["n"] or 0
            ups = r["ups"] or 0
            ups_filled = r["ups_filled"] or 0
            ups_closed = r["ups_closed_below"] or 0

            fill_pct = (ups_filled / ups * 100) if ups > 0 else 0
            close_pct = (ups_closed / ups * 100) if ups > 0 else 0

            print(f"    {label}  total: {n:>4}  ups: {ups:>4}  "
                  f"ups touched: {fill_pct:>5.1f}%  ups closed below: {close_pct:>5.1f}%")

        print()

        # ── Top 10 tickers efter antal events ─────────────
        cur = conn.execute("""
            SELECT ticker, COUNT(*) AS n,
                   AVG(ABS(gap_pct)) AS avg_pct
            FROM gap_events
            GROUP BY ticker
            ORDER BY n DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        if rows:
            print(f"  Top 10 tickers (flest gap events):")
            for r in rows:
                print(f"    {r['ticker']:8s} {r['n']:>4} events  avg {r['avg_pct']:.1f}%")
            print()

        # ── Edge-relevant: Gap-up fade rate ───────────────
        cur = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(gap_filled) AS filled,
                SUM(gap_closed_below) AS closed_below
            FROM gap_events
            WHERE gap_direction = 'up'
              AND ABS(gap_pct) >= 5.0
              AND ABS(gap_pct) <= 20.0
        """)
        r = cur.fetchone()
        if r and (r["total"] or 0) > 0:
            n = r["total"]
            print(f"  ★ Edge-vurdering for Gap Fade (gap-up 5%-20%):")
            print(f"    Total events:        {n}")
            print(f"    Touched yesterday's close:  {r['filled']} ({r['filled']/n*100:.1f}%)")
            print(f"    Closed below yest close:    {r['closed_below']} ({r['closed_below']/n*100:.1f}%)")

            if r["filled"] / n >= 0.60:
                print(f"    → LOVENDE: >60% touch rate er statistisk edge")
            elif r["filled"] / n >= 0.50:
                print(f"    → MARGINAL: ~50% touch rate, edge usikker")
            else:
                print(f"    → SVAGT: <50% touch rate, Gap Fade strategi tvivlsom")

        print()
        print("=" * 70)


def export_to_csv(output_path: Path) -> int:
    """Eksporter alle gap_events til CSV."""
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT ticker, date, prev_close, open, gap_pct, gap_direction,
                   day_high, day_low, day_close, gap_filled, gap_closed_below, volume
            FROM gap_events
            ORDER BY date DESC, ABS(gap_pct) DESC
        """)
        rows = cur.fetchall()

    if not rows:
        return 0

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ticker", "date", "prev_close", "open", "gap_pct", "gap_direction",
            "day_high", "day_low", "day_close", "gap_filled", "gap_closed_below", "volume",
        ])
        for row in rows:
            writer.writerow([row[k] for k in row.keys()])

    return len(rows)


# ─────────────────────────────────────────────────────────────────
# Hovedflow
# ─────────────────────────────────────────────────────────────────

def run_gap_finder(
    min_gap_pct: float,
    direction_filter: Optional[str],
) -> dict:
    """
    Kør gap detection over alle tickers.
    Returnér statistik-dict.
    """
    tickers = get_universe_tickers()
    if not tickers:
        logger.error("Ingen tickers fundet med daily data — kør download_daily_ibkr.py først")
        return {"tickers": 0, "events": 0}

    logger.info(f"Analyserer {len(tickers)} tickers")
    logger.info(f"Min gap: {min_gap_pct}%  Retning: {direction_filter or 'begge'}")

    # Ryd eksisterende events der matcher samme kriterier
    deleted = clear_existing_events(min_gap_pct, direction_filter)
    if deleted > 0:
        logger.info(f"Slettet {deleted} eksisterende events (re-kørsel)")

    total_events = 0
    tickers_with_events = 0

    for i, ticker in enumerate(tickers, 1):
        events = find_gaps_for_ticker(ticker, min_gap_pct, direction_filter)
        if events:
            insert_gap_events(events)
            total_events += len(events)
            tickers_with_events += 1

        # Progress hver 50 tickers
        if i % 50 == 0:
            logger.info(f"  [{i}/{len(tickers)}] Behandlet — events indtil videre: {total_events}")

    logger.info(f"✓ Færdig: {total_events} events fundet på {tickers_with_events} tickers")

    # Opdater is_gap_active flag
    n_active = update_is_gap_active()
    logger.info(f"✓ Adaptive filter: {n_active} tickers markeret som gap-aktive")

    return {
        "tickers":             len(tickers),
        "tickers_with_events": tickers_with_events,
        "events":              total_events,
    }


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find gap-events i daily bars"
    )
    parser.add_argument(
        "--min-gap", type=float, default=DEFAULT_MIN_GAP_PCT,
        help=f"Min gap-procent (default: {DEFAULT_MIN_GAP_PCT})",
    )
    parser.add_argument(
        "--direction", choices=["up", "down"],
        help="Filter på retning (default: begge)",
    )
    parser.add_argument(
        "--stats-only", action="store_true",
        help="Vis kun statistik, kør ikke detection",
    )
    parser.add_argument(
        "--export", type=str,
        help="Eksporter events til CSV-fil",
    )
    args = parser.parse_args()

    # Sørg for schema er klar
    init_database()
    ensure_schema_migration()

    # Hvis stats-only: spring detection over
    if args.stats_only:
        print_statistics()
        return 0

    # Hvis export: kør detection først hvis tom, eksporter
    if args.export:
        # Tjek om der er noget at eksportere
        with get_connection(read_only=True) as conn:
            cur = conn.execute("SELECT COUNT(*) AS n FROM gap_events")
            n = cur.fetchone()["n"]

        if n == 0:
            logger.info("gap_events er tom — kør detection først")
            run_gap_finder(args.min_gap, args.direction)

        path = Path(args.export)
        count = export_to_csv(path)
        logger.info(f"✓ Eksporteret {count} events → {path}")
        return 0

    # Standard: kør detection + vis statistik
    try:
        run_gap_finder(args.min_gap, args.direction)
    except KeyboardInterrupt:
        logger.warning("Afbrudt af bruger")
        return 130

    print_statistics()
    return 0


if __name__ == "__main__":
    sys.exit(main())
