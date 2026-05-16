"""
analyze_gaps.py
───────────────
Sammenligner gap-events på tværs af ticker-grupper (small vs large caps).

Læser eksisterende gap_events fra databasen og laver isolerede statistikker
for hver gruppe. Det er en analyse-only operation — ingen writes til databasen.

Bruger ticker.notes-feltet til at gruppere:
  - "S&P 100" i notes  → large-cap gruppe
  - alt andet           → small-cap gruppe

Output: side-by-side rapport med touch rates per gap-størrelse.

Brug:
    python analyze_gaps.py
        Vis comparative report (small vs large)

    python analyze_gaps.py --min-gap 5
        Kun analyser gaps >= 5%

    python analyze_gaps.py --export comparative.csv
        Eksporter detaljeret per-event til CSV

Placering: C:\\Projects\\trading-dash\\backend\\analyze_gaps.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path
from typing import Optional

from historical_db import get_connection


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("analyze_gaps")


# ─────────────────────────────────────────────────────────────────
# Gruppe-definitioner
# ─────────────────────────────────────────────────────────────────
# En "gruppe" har et navn og en SQL WHERE-klausul der filtrerer tickers.notes.

GROUPS = [
    {
        "name":  "Small Caps",
        "where": "(t.notes IS NULL OR t.notes NOT LIKE '%S&P 100%')",
        "desc":  "Tickers fra IBKR scanner (small-cap momentum)",
    },
    {
        "name":  "Large Caps",
        "where": "t.notes LIKE '%S&P 100%'",
        "desc":  "S&P 100 large-cap aktier",
    },
]


# ─────────────────────────────────────────────────────────────────
# Statistik-beregning
# ─────────────────────────────────────────────────────────────────

def get_group_stats(group_where: str, min_gap_pct: float) -> dict:
    """
    Beregn alle relevante statistikker for én gruppe.

    Returnerer dict med:
      - total_tickers
      - total_events
      - up_count / down_count
      - buckets: liste af dicts med (lo, hi, count, ups, ups_touched, ups_closed)
      - edge_gap_up_5_20: dict med statistik for gap-up 5%-20%
    """
    with get_connection(read_only=True) as conn:

        # Total ticker-antal i gruppen
        cur = conn.execute(f"""
            SELECT COUNT(DISTINCT t.symbol) AS n
            FROM tickers t
            INNER JOIN bars_daily b ON b.ticker = t.symbol
            WHERE {group_where}
        """)
        total_tickers = cur.fetchone()["n"]

        # Total events
        cur = conn.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN g.gap_direction = 'up'   THEN 1 ELSE 0 END) AS ups,
                SUM(CASE WHEN g.gap_direction = 'down' THEN 1 ELSE 0 END) AS downs
            FROM gap_events g
            INNER JOIN tickers t ON t.symbol = g.ticker
            WHERE {group_where}
              AND ABS(g.gap_pct) >= ?
        """, (min_gap_pct,))
        row = cur.fetchone()
        total_events = row["total"] or 0
        up_count     = row["ups"] or 0
        down_count   = row["downs"] or 0

        # Buckets efter gap-størrelse
        bucket_defs = [
            (3.0, 5.0,   " 3% -  5%"),
            (5.0, 7.0,   " 5% -  7%"),
            (7.0, 10.0,  " 7% - 10%"),
            (10.0, 15.0, "10% - 15%"),
            (15.0, 20.0, "15% - 20%"),
            (20.0, 100.0,"20%+     "),
        ]
        buckets = []
        for lo, hi, label in bucket_defs:
            cur = conn.execute(f"""
                SELECT
                    COUNT(*) AS n,
                    SUM(CASE WHEN g.gap_direction = 'up' THEN 1 ELSE 0 END) AS ups,
                    SUM(CASE WHEN g.gap_direction = 'up' AND g.gap_filled = 1 THEN 1 ELSE 0 END) AS ups_touched,
                    SUM(CASE WHEN g.gap_direction = 'up' AND g.gap_closed_below = 1 THEN 1 ELSE 0 END) AS ups_closed
                FROM gap_events g
                INNER JOIN tickers t ON t.symbol = g.ticker
                WHERE {group_where}
                  AND ABS(g.gap_pct) >= ? AND ABS(g.gap_pct) < ?
            """, (lo, hi))
            r = cur.fetchone()
            buckets.append({
                "label":         label,
                "lo":            lo,
                "hi":            hi,
                "total":         r["n"] or 0,
                "ups":           r["ups"] or 0,
                "ups_touched":   r["ups_touched"] or 0,
                "ups_closed":    r["ups_closed"] or 0,
            })

        # Edge-vurdering: gap-up 5%-20%
        cur = conn.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(g.gap_filled)       AS touched,
                SUM(g.gap_closed_below) AS closed_below
            FROM gap_events g
            INNER JOIN tickers t ON t.symbol = g.ticker
            WHERE {group_where}
              AND g.gap_direction = 'up'
              AND ABS(g.gap_pct) >= 5.0
              AND ABS(g.gap_pct) <= 20.0
        """)
        r = cur.fetchone()
        edge = {
            "total":         r["total"] or 0,
            "touched":       r["touched"] or 0,
            "closed_below":  r["closed_below"] or 0,
        }
        if edge["total"] > 0:
            edge["touch_rate"]      = edge["touched"] / edge["total"] * 100
            edge["close_rate"]      = edge["closed_below"] / edge["total"] * 100
        else:
            edge["touch_rate"] = 0
            edge["close_rate"] = 0

    return {
        "total_tickers":     total_tickers,
        "total_events":      total_events,
        "up_count":          up_count,
        "down_count":        down_count,
        "buckets":           buckets,
        "edge_gap_up_5_20":  edge,
    }


# ─────────────────────────────────────────────────────────────────
# Rapport-print
# ─────────────────────────────────────────────────────────────────

def print_comparative_report(group_stats: list[tuple[dict, dict]]) -> None:
    """
    Print en side-by-side rapport over gap-statistikker per gruppe.

    Args:
        group_stats: liste af (group_def, stats) tupler
    """
    print()
    print("=" * 90)
    print("  Gap-events — Comparative Analyse: Small Cap vs Large Cap")
    print("=" * 90)

    # ── Header med gruppe-info ────────────────────────────────
    print()
    print(f"  {'':18s}  {'Small Caps':>20s}  {'Large Caps':>20s}")
    print(f"  {'-' * 18}  {'-' * 20}  {'-' * 20}")

    small = group_stats[0][1]
    large = group_stats[1][1]

    print(f"  {'Total tickers:':18s}  {small['total_tickers']:>20d}  {large['total_tickers']:>20d}")
    print(f"  {'Total events:':18s}  {small['total_events']:>20d}  {large['total_events']:>20d}")
    print(f"  {'  Gap-up:':18s}  {small['up_count']:>20d}  {large['up_count']:>20d}")
    print(f"  {'  Gap-down:':18s}  {small['down_count']:>20d}  {large['down_count']:>20d}")
    print()

    # ── Bucket-distribution ──────────────────────────────────
    print(f"  Gap-up touch rate per gap-størrelse:")
    print()
    print(f"  {'Bucket':12s}  {'Small Caps':>30s}  {'Large Caps':>30s}")
    print(f"  {'-' * 12}  {'-' * 30}  {'-' * 30}")

    for s_bucket, l_bucket in zip(small["buckets"], large["buckets"]):
        s_ups = s_bucket["ups"]
        l_ups = l_bucket["ups"]
        s_touch_pct = (s_bucket["ups_touched"] / s_ups * 100) if s_ups > 0 else 0
        l_touch_pct = (l_bucket["ups_touched"] / l_ups * 100) if l_ups > 0 else 0

        s_str = f"{s_ups:>4} ups, {s_touch_pct:>5.1f}% touched" if s_ups > 0 else "      ingen ups"
        l_str = f"{l_ups:>4} ups, {l_touch_pct:>5.1f}% touched" if l_ups > 0 else "      ingen ups"

        # Highlight forskelle
        diff = l_touch_pct - s_touch_pct
        diff_str = f"{diff:+.1f}" if (s_ups > 0 and l_ups > 0) else ""

        print(f"  {s_bucket['label']:12s}  {s_str:>30s}  {l_str:>30s}   diff: {diff_str}")

    print()

    # ── Edge-vurdering ───────────────────────────────────────
    print(f"  ★ Edge-vurdering for Gap Fade (gap-up 5%-20%):")
    print()
    print(f"  {'':18s}  {'Small Caps':>20s}  {'Large Caps':>20s}")
    print(f"  {'-' * 18}  {'-' * 20}  {'-' * 20}")

    s_edge = small["edge_gap_up_5_20"]
    l_edge = large["edge_gap_up_5_20"]

    print(f"  {'Total events:':18s}  {s_edge['total']:>20d}  {l_edge['total']:>20d}")
    print(f"  {'Touched:':18s}  {s_edge['touch_rate']:>19.1f}%  {l_edge['touch_rate']:>19.1f}%")
    print(f"  {'Closed below:':18s}  {s_edge['close_rate']:>19.1f}%  {l_edge['close_rate']:>19.1f}%")

    print()
    print(f"  Konklusion:")

    # Konklusion baseret på large cap touch rate
    if l_edge["total"] < 30:
        print(f"    ⚠  For få large-cap events ({l_edge['total']}) — statistisk usikkert")
    elif l_edge["touch_rate"] >= 60:
        print(f"    ✓ LOVENDE: Large caps har {l_edge['touch_rate']:.1f}% touch rate — Gap Fade har edge")
    elif l_edge["touch_rate"] >= 55:
        print(f"    ◐ MARGINAL: Large caps har {l_edge['touch_rate']:.1f}% touch rate — edge er svag men eksisterer")
    elif l_edge["touch_rate"] >= 50:
        print(f"    ◐ SVAG: Large caps har {l_edge['touch_rate']:.1f}% touch rate — knap break-even")
    else:
        print(f"    ✗ INGEN EDGE: Large caps har kun {l_edge['touch_rate']:.1f}% touch rate")

    # Sammenligning
    if s_edge["total"] > 0 and l_edge["total"] > 0:
        diff = l_edge["touch_rate"] - s_edge["touch_rate"]
        if abs(diff) > 5:
            direction = "højere" if diff > 0 else "lavere"
            print(f"    Large caps har {abs(diff):.1f} percentpoints {direction} touch rate end small caps")

    print()
    print("=" * 90)


# ─────────────────────────────────────────────────────────────────
# CSV-eksport
# ─────────────────────────────────────────────────────────────────

def export_comparative_csv(output_path: Path) -> int:
    """Eksporter detaljeret per-event data med group-label."""
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT
                g.ticker, g.date, g.prev_close, g.open, g.gap_pct, g.gap_direction,
                g.day_high, g.day_low, g.day_close, g.gap_filled, g.gap_closed_below,
                g.volume,
                CASE
                    WHEN t.notes LIKE '%S&P 100%' THEN 'large_cap'
                    ELSE 'small_cap'
                END AS group_label
            FROM gap_events g
            INNER JOIN tickers t ON t.symbol = g.ticker
            ORDER BY g.date DESC, ABS(g.gap_pct) DESC
        """)
        rows = cur.fetchall()

    if not rows:
        return 0

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([k for k in rows[0].keys()])
        for row in rows:
            writer.writerow([row[k] for k in row.keys()])

    return len(rows)


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sammenlign gap-statistikker på tværs af ticker-grupper"
    )
    parser.add_argument(
        "--min-gap", type=float, default=3.0,
        help="Min gap-procent (default: 3.0)",
    )
    parser.add_argument(
        "--export", type=str,
        help="Eksporter detaljeret per-event data til CSV",
    )
    args = parser.parse_args()

    # Saml stats per gruppe
    group_stats = []
    for group in GROUPS:
        logger.info(f"Analyserer gruppe: {group['name']}")
        stats = get_group_stats(group["where"], args.min_gap)
        group_stats.append((group, stats))

    # Print comparative report
    print_comparative_report(group_stats)

    # Optional CSV export
    if args.export:
        path = Path(args.export)
        count = export_comparative_csv(path)
        logger.info(f"✓ Eksporteret {count} events → {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
