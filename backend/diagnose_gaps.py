"""
diagnose_gaps.py
────────────────
Diagnostisk verificering af gap-events data integritet.

Når gap-statistikker ser mistænkelige ud (fx 6% touch rate på large caps),
kører dette script 5 checks for at afgøre om det er en data-bug eller
en ægte markedsobservation.

Checks:
  1. OHLC integritet — tjek at low <= open/close <= high
  2. Top large-cap gap-events — inspicér manuelt
  3. Known events — tjek om vi har kendte 2025-gaps (NVDA, TSLA)
  4. Anomalier — gap størrelse vs intraday range
  5. Yahoo Finance sammenligning — hvis tilgængelig

Brug:
    python diagnose_gaps.py
        Kør alle 5 checks

    python diagnose_gaps.py --skip-yahoo
        Skip Yahoo-sammenligning (hvis yfinance ikke installeret)

    python diagnose_gaps.py --ticker AAPL
        Detaljeret diagnostik for én specifik ticker

Placering: C:\\Projects\\trading-dash\\backend\\diagnose_gaps.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from historical_db import get_connection


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("diagnose")


# ── ANSI colors for læselighed ───────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# ─────────────────────────────────────────────────────────────────
# Check 1: OHLC integritet
# ─────────────────────────────────────────────────────────────────

def check_ohlc_integrity() -> None:
    """Tjek at alle daily bars overholder OHLC-grundloven."""
    print()
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Check 1: OHLC integritet{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")
    print()

    with get_connection(read_only=True) as conn:
        # Find rækker hvor low > open
        cur = conn.execute("""
            SELECT ticker, date, open, high, low, close, volume
            FROM bars_daily
            WHERE low > open OR low > close OR high < open OR high < close
            LIMIT 20
        """)
        violations = cur.fetchall()

        # Total count
        cur = conn.execute("""
            SELECT COUNT(*) AS n FROM bars_daily
            WHERE low > open OR low > close OR high < open OR high < close
        """)
        total_violations = cur.fetchone()["n"]

        cur = conn.execute("SELECT COUNT(*) AS n FROM bars_daily")
        total_bars = cur.fetchone()["n"]

    print(f"  Total daily bars i databasen:  {total_bars:,}")
    print(f"  Bars med OHLC violation:       {total_violations}")

    if total_violations == 0:
        print(f"  {GREEN}✓ Alle bars har gyldig OHLC{RESET}")
    else:
        pct = total_violations / total_bars * 100
        print(f"  {RED}✗ {pct:.2f}% af bars har OHLC-violation{RESET}")
        print()
        print(f"  Eksempler:")
        for r in violations:
            print(f"    {r['ticker']:8s} {r['date']}  "
                  f"O={r['open']:>8.2f}  H={r['high']:>8.2f}  "
                  f"L={r['low']:>8.2f}  C={r['close']:>8.2f}")


# ─────────────────────────────────────────────────────────────────
# Check 2: Top large-cap gap-events i detaljer
# ─────────────────────────────────────────────────────────────────

def check_top_large_cap_gaps() -> None:
    """Vis top 10 største gap-ups for large caps med fuld data."""
    print()
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Check 2: Top 10 størst gap-ups på large caps{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")
    print()

    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT
                g.ticker, g.date, g.prev_close, g.open, g.gap_pct,
                g.day_high, g.day_low, g.day_close,
                g.gap_filled, g.gap_closed_below,
                g.volume
            FROM gap_events g
            INNER JOIN tickers t ON t.symbol = g.ticker
            WHERE t.notes LIKE '%S&P 100%'
              AND g.gap_direction = 'up'
            ORDER BY g.gap_pct DESC
            LIMIT 10
        """)
        rows = cur.fetchall()

    if not rows:
        print(f"  {YELLOW}Ingen large-cap gap-ups fundet{RESET}")
        return

    print(f"  Sortet efter gap-størrelse (største først):")
    print()
    print(f"  {'Ticker':8s} {'Date':12s} {'PrevC':>8s} {'Open':>8s} {'Gap%':>7s} "
          f"{'High':>8s} {'Low':>8s} {'Close':>8s} {'Touch':>7s} {'Below':>7s}  Sanity")
    print(f"  {'-' * 8} {'-' * 12} {'-' * 8} {'-' * 8} {'-' * 7} "
          f"{'-' * 8} {'-' * 8} {'-' * 8} {'-' * 7} {'-' * 7}  {'-' * 30}")

    for r in rows:
        # Verifér konsistens
        touched_check = r['day_low'] <= r['prev_close']
        below_check   = r['day_close'] <= r['prev_close']

        touch_match = touched_check == bool(r['gap_filled'])
        below_match = below_check == bool(r['gap_closed_below'])

        # Sanity-tegn
        sanity = []
        if r['day_low'] > r['day_high']:
            sanity.append(f"{RED}low>high{RESET}")
        if r['day_low'] > r['open']:
            sanity.append(f"{YELLOW}low>open{RESET}")
        if not touch_match:
            sanity.append(f"{RED}touch mismatch{RESET}")
        if not below_match:
            sanity.append(f"{RED}below mismatch{RESET}")

        # Highlight ekstrem situation: low er VÆSENTLIG over prev_close
        # (= ingen mulighed for at fadet kunne ramme)
        if r['day_low'] > r['prev_close']:
            distance_pct = (r['day_low'] - r['prev_close']) / r['prev_close'] * 100
            if distance_pct > 2:
                sanity.append(f"{DIM}low {distance_pct:.1f}% over prev{RESET}")

        sanity_str = " ".join(sanity) if sanity else f"{GREEN}OK{RESET}"

        print(f"  {r['ticker']:8s} {r['date']:12s} "
              f"{r['prev_close']:>8.2f} {r['open']:>8.2f} "
              f"{r['gap_pct']:>6.2f}% "
              f"{r['day_high']:>8.2f} {r['day_low']:>8.2f} {r['day_close']:>8.2f} "
              f"{'Y' if r['gap_filled'] else 'N':>7s} "
              f"{'Y' if r['gap_closed_below'] else 'N':>7s}  {sanity_str}")


# ─────────────────────────────────────────────────────────────────
# Check 3: Known events fra 2025
# ─────────────────────────────────────────────────────────────────

# Kendte large-cap gap events (fra finansiel presse 2025)
# Approksimerede datoer — vi accepterer +/- 1 dag i tjekket
KNOWN_EVENTS = [
    # Ticker, date_iso, expected_gap_direction, expected_min_pct, beskrivelse
    ("TSLA",  "2025-04-23", "up",   3.0, "Q1 2025 earnings + Musk politics retreat"),
    ("NVDA",  "2025-08-28", "down", 3.0, "Q2 earnings — guidance disappointment"),
    ("META",  "2025-01-30", "up",   5.0, "Q4 2024 earnings beat"),
    ("AAPL",  "2025-08-01", "down", 3.0, "Q3 earnings + tariff concerns"),
    ("GOOGL", "2025-04-25", "up",   5.0, "Q1 earnings + AI optimisme"),
]


def check_known_events() -> None:
    """Tjek om vi har kendte large-cap gaps fra 2025."""
    print()
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Check 3: Kendte 2025-gap-events{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")
    print()
    print(f"  Vi tjekker om databasen har disse kendte gap-dage")
    print(f"  (acceptere +/- 3 dage forskydning):")
    print()

    with get_connection(read_only=True) as conn:
        for ticker, date, direction, min_pct, description in KNOWN_EVENTS:
            # Tjek for events +/- 3 dage
            cur = conn.execute("""
                SELECT date, gap_pct, gap_direction, gap_filled, day_low, prev_close, open
                FROM gap_events
                WHERE ticker = ?
                  AND date >= date(?, '-3 days')
                  AND date <= date(?, '+3 days')
                ORDER BY date
            """, (ticker, date, date))
            events = cur.fetchall()

            print(f"  {BOLD}{ticker} omkring {date}{RESET} — {description}")

            if not events:
                # Tjek om vi overhovedet har daily data omkring den dato
                cur = conn.execute("""
                    SELECT date, open, close, high, low
                    FROM bars_daily
                    WHERE ticker = ?
                      AND date >= date(?, '-3 days')
                      AND date <= date(?, '+3 days')
                    ORDER BY date
                """, (ticker, date, date))
                bars = cur.fetchall()

                if not bars:
                    print(f"    {YELLOW}Ingen daily bars omkring denne dato{RESET}")
                else:
                    print(f"    {YELLOW}Bars findes, men ingen gap detekteret:{RESET}")
                    for b in bars:
                        print(f"      {b['date']}  O={b['open']:.2f} H={b['high']:.2f} "
                              f"L={b['low']:.2f} C={b['close']:.2f}")
            else:
                for e in events:
                    direction_match = e['gap_direction'] == direction
                    size_match = abs(e['gap_pct']) >= min_pct

                    if direction_match and size_match:
                        marker = f"{GREEN}✓{RESET}"
                    else:
                        marker = f"{YELLOW}?{RESET}"

                    print(f"    {marker} {e['date']}  gap={e['gap_pct']:+6.2f}%  "
                          f"dir={e['gap_direction']:4s}  filled={'Y' if e['gap_filled'] else 'N'}  "
                          f"low={e['day_low']:.2f} prev_close={e['prev_close']:.2f}")
            print()


# ─────────────────────────────────────────────────────────────────
# Check 4: Anomalier — gap størrelse vs intraday range
# ─────────────────────────────────────────────────────────────────

def check_intraday_range_anomalies() -> None:
    """
    Find events hvor intraday range (high-low) er suspekt lille
    sammenlignet med gap-størrelse. Det indikerer ofte at IBKR's
    daily bar mangler ekstended hours data eller har aggregeringsfejl.
    """
    print()
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Check 4: Gap-events med suspekt smal intraday range{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")
    print()
    print(f"  Hvis gap er stort, men dagens range (high-low) er meget lille,")
    print(f"  tyder det på at aktien knapt rørte sig efter åbning — eller at")
    print(f"  vi mangler intraday volatilitetsdata.")
    print()

    with get_connection(read_only=True) as conn:
        # Find large-cap gap-ups >= 5% hvor (high-low)/open < 1%
        cur = conn.execute("""
            SELECT
                g.ticker, g.date, g.gap_pct,
                g.prev_close, g.open, g.day_high, g.day_low, g.day_close,
                ((g.day_high - g.day_low) / g.open * 100.0) AS range_pct
            FROM gap_events g
            INNER JOIN tickers t ON t.symbol = g.ticker
            WHERE t.notes LIKE '%S&P 100%'
              AND g.gap_direction = 'up'
              AND ABS(g.gap_pct) >= 5.0
              AND ((g.day_high - g.day_low) / g.open * 100.0) < 2.0
            ORDER BY g.gap_pct DESC
            LIMIT 15
        """)
        rows = cur.fetchall()

        # Count totalt
        cur = conn.execute("""
            SELECT COUNT(*) AS n
            FROM gap_events g
            INNER JOIN tickers t ON t.symbol = g.ticker
            WHERE t.notes LIKE '%S&P 100%'
              AND g.gap_direction = 'up'
              AND ABS(g.gap_pct) >= 5.0
        """)
        total = cur.fetchone()["n"]

    if total == 0:
        print(f"  {YELLOW}Ingen large-cap gap-ups >= 5% fundet{RESET}")
        return

    print(f"  Total large-cap gap-ups >= 5%:  {total}")
    print(f"  Med range < 2% af open:         {len(rows)}")

    if not rows:
        print(f"  {GREEN}✓ Ingen mistænkelig smal-range gaps{RESET}")
        return

    pct = len(rows) / total * 100
    if pct > 30:
        print(f"  {RED}✗ {pct:.0f}% af large-cap gaps har suspekt smal range — "
              f"dette antyder en data-bug{RESET}")
    elif pct > 15:
        print(f"  {YELLOW}⚠ {pct:.0f}% har smal range — værd at undersøge{RESET}")
    else:
        print(f"  {GREEN}✓ Kun {pct:.0f}% har smal range (normalt){RESET}")

    print()
    print(f"  Eksempler (sorteret efter gap-størrelse):")
    print()
    print(f"  {'Ticker':8s} {'Date':12s} {'Gap%':>7s} {'Open':>8s} {'High':>8s} "
          f"{'Low':>8s} {'Range%':>7s}  Note")
    for r in rows:
        # Hvis low > prev_close = umuligt at fade (kommentar)
        cant_fade = r['day_low'] > r['prev_close']
        note = f"{RED}low>prev_close — kan IKKE fade{RESET}" if cant_fade else ""

        print(f"  {r['ticker']:8s} {r['date']:12s} {r['gap_pct']:>6.2f}% "
              f"{r['open']:>8.2f} {r['day_high']:>8.2f} {r['day_low']:>8.2f} "
              f"{r['range_pct']:>6.2f}%  {note}")


# ─────────────────────────────────────────────────────────────────
# Check 5: Yahoo Finance sammenligning
# ─────────────────────────────────────────────────────────────────

def check_yahoo_comparison(skip: bool = False) -> None:
    """Hent samme data fra Yahoo og sammenlign for top-3 large-cap gaps."""
    print()
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Check 5: Yahoo Finance sammenligning{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")
    print()

    if skip:
        print(f"  {DIM}Skipped (--skip-yahoo){RESET}")
        return

    try:
        import yfinance as yf
    except ImportError:
        print(f"  {YELLOW}yfinance ikke installeret — skip{RESET}")
        print(f"  Installer med: pip install yfinance")
        return

    # Hent top-3 large-cap gap-ups
    with get_connection(read_only=True) as conn:
        cur = conn.execute("""
            SELECT g.ticker, g.date, g.prev_close, g.open, g.day_high, g.day_low, g.day_close, g.gap_pct
            FROM gap_events g
            INNER JOIN tickers t ON t.symbol = g.ticker
            WHERE t.notes LIKE '%S&P 100%'
              AND g.gap_direction = 'up'
            ORDER BY g.gap_pct DESC
            LIMIT 3
        """)
        rows = cur.fetchall()

    if not rows:
        print(f"  {YELLOW}Ingen large-cap gaps at sammenligne{RESET}")
        return

    print(f"  Sammenligner IBKR vs Yahoo for top-3 gap-ups:")
    print()

    for r in rows:
        ticker = r['ticker']
        date = r['date']

        print(f"  {BOLD}{ticker} på {date}{RESET}")
        print(f"    IBKR:   prev_close={r['prev_close']:.2f}  open={r['open']:.2f}  "
              f"high={r['day_high']:.2f}  low={r['day_low']:.2f}  close={r['day_close']:.2f}")

        try:
            # Hent fra Yahoo: dagen før, dagen, dagen efter
            tk = yf.Ticker(ticker)
            # Yahoo bruger end-date eksklusiv, derfor +2 dage
            from datetime import datetime, timedelta
            d = datetime.strptime(date, "%Y-%m-%d")
            start = (d - timedelta(days=2)).strftime("%Y-%m-%d")
            end   = (d + timedelta(days=2)).strftime("%Y-%m-%d")

            hist = tk.history(start=start, end=end, auto_adjust=False)
            if hist.empty:
                print(f"    {YELLOW}Yahoo: ingen data{RESET}")
                continue

            # Find dagens row
            day_str = date
            yahoo_day = None
            for idx, row in hist.iterrows():
                if idx.strftime("%Y-%m-%d") == day_str:
                    yahoo_day = row
                    break

            if yahoo_day is None:
                print(f"    {YELLOW}Yahoo: dagens data ikke fundet i resultater{RESET}")
                for idx, row in hist.iterrows():
                    print(f"    Yahoo har: {idx.strftime('%Y-%m-%d')}")
                continue

            # Find prev_close
            prev_yahoo = None
            for idx, row in hist.iterrows():
                if idx.strftime("%Y-%m-%d") < day_str:
                    prev_yahoo = row
            yahoo_prev_close = prev_yahoo["Close"] if prev_yahoo is not None else None

            print(f"    Yahoo:  prev_close={yahoo_prev_close:.2f}  open={yahoo_day['Open']:.2f}  "
                  f"high={yahoo_day['High']:.2f}  low={yahoo_day['Low']:.2f}  close={yahoo_day['Close']:.2f}")

            # Differencer
            open_diff = abs(yahoo_day['Open'] - r['open']) / r['open'] * 100
            low_diff = abs(yahoo_day['Low'] - r['day_low']) / r['day_low'] * 100

            if open_diff > 1.0 or low_diff > 1.0:
                print(f"    {RED}⚠ Stor afvigelse: open diff {open_diff:.2f}%, low diff {low_diff:.2f}%{RESET}")
            else:
                print(f"    {GREEN}✓ Data matcher (open diff {open_diff:.2f}%, low diff {low_diff:.2f}%){RESET}")

        except Exception as e:
            print(f"    {YELLOW}Yahoo fejl: {e}{RESET}")

        print()


# ─────────────────────────────────────────────────────────────────
# Specifik ticker-mode
# ─────────────────────────────────────────────────────────────────

def diagnose_ticker(ticker: str) -> None:
    """Detaljeret diagnose for én specifik ticker."""
    print()
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Detaljeret diagnostik: {ticker}{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")
    print()

    with get_connection(read_only=True) as conn:
        # Ticker info
        cur = conn.execute("SELECT * FROM tickers WHERE symbol = ?", (ticker,))
        info = cur.fetchone()
        if not info:
            print(f"  {RED}Ticker {ticker} ikke i databasen{RESET}")
            return

        print(f"  Symbol:        {info['symbol']}")
        print(f"  Notes:         {info['notes']}")
        print(f"  Is active:     {info['is_active']}")
        print(f"  Is gap active: {info['is_gap_active']}")

        # Bar-count
        cur = conn.execute("""
            SELECT COUNT(*) AS n, MIN(date) AS earliest, MAX(date) AS latest
            FROM bars_daily WHERE ticker = ?
        """, (ticker,))
        bc = cur.fetchone()
        print(f"  Daily bars:    {bc['n']}  ({bc['earliest']} → {bc['latest']})")

        # Gap-events
        cur = conn.execute("""
            SELECT * FROM gap_events
            WHERE ticker = ?
            ORDER BY ABS(gap_pct) DESC
        """, (ticker,))
        events = cur.fetchall()
        print(f"  Gap events:    {len(events)}")
        print()

        if events:
            print(f"  Top 10 events (sorted by abs gap):")
            print()
            print(f"  {'Date':12s} {'PrevC':>8s} {'Open':>8s} {'Gap%':>7s} "
                  f"{'High':>8s} {'Low':>8s} {'Close':>8s} {'Touch':>6s}")
            for e in events[:10]:
                print(f"  {e['date']:12s} "
                      f"{e['prev_close']:>8.2f} {e['open']:>8.2f} {e['gap_pct']:>6.2f}% "
                      f"{e['day_high']:>8.2f} {e['day_low']:>8.2f} {e['day_close']:>8.2f} "
                      f"{'Y' if e['gap_filled'] else 'N':>6s}")


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostisk verifikation af gap-events data")
    parser.add_argument("--ticker", type=str, help="Detaljeret diagnose for én ticker")
    parser.add_argument("--skip-yahoo", action="store_true", help="Skip Yahoo-sammenligning")
    args = parser.parse_args()

    if args.ticker:
        diagnose_ticker(args.ticker.upper())
        return 0

    # Kør alle 5 checks
    check_ohlc_integrity()
    check_top_large_cap_gaps()
    check_known_events()
    check_intraday_range_anomalies()
    check_yahoo_comparison(skip=args.skip_yahoo)

    print()
    print(f"{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  Diagnostik færdig{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}")
    print()
    print(f"  Vurder resultaterne ovenfor:")
    print()
    print(f"  Hvis Check 1 fejler            → korrupt data, re-download")
    print(f"  Hvis Check 2 viser low>prev    → ikke en bug, gaps fader bare ikke")
    print(f"  Hvis Check 3 mangler kendte    → vi har huller i historikken")
    print(f"  Hvis Check 4 viser >30% smal   → IBKR daily bars mangler intraday data")
    print(f"  Hvis Check 5 viser stor diff   → IBKR-data afviger fra Yahoo")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
