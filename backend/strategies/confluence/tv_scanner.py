"""
strategies/confluence/tv_scanner.py
────────────────────────────────────
Henter "US Top Gainers" via TradingView's officielle screener API.

Vi bruger denne i stedet for IBKR's scanner fordi IBKR's TOP_PERC_GAIN
returnerer aktier på basis af noget der ikke matcher hvad TV (og Iben)
forventer. TV's API returnerer præcis den liste Iben kan se i sit
TradingView "US Top Gainers" screener.

Setup matcher Ibens egen TV-screener konfiguration:
  - Pris $5 ≤ close ≤ $50 (vores UNIVERSE_PRICE_MIN/MAX)
  - Børser: NYSE, NASDAQ, AMEX (matcher TV's "NYSE+NASDAQ+ARCA+CBOE")
  - Volumen > 500k (vores UNIVERSE_MIN_VOLUME)
  - Type = stock (ekskluderer ETFs, warrants osv.)
  - Sorteret efter change % descending
  - Top N (default 25)

Krav: pip install tradingview-screener

Bemærk: biblioteket bruger 15-minutters delayed data uden login.
For Konfluens-strategien er det fint — vi scanner ÉN gang ved opstart
(09:30 ET) og bruger universet hele dagen.
"""

from __future__ import annotations
import logging
import sys
from pathlib import Path
from typing import Optional

# Tillad at modulet kan køres som standalone-script direkte
# (python strategies/confluence/tv_scanner.py)
if __name__ == "__main__" and __package__ is None:
    # Tilføj backend/ til sys.path så 'strategies' kan importeres
    backend_dir = Path(__file__).resolve().parent.parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

from strategies.confluence.config import (
    UNIVERSE_PRICE_MIN,
    UNIVERSE_PRICE_MAX,
    UNIVERSE_MIN_VOLUME,
    UNIVERSE_TOP_N,
)

logger = logging.getLogger(__name__)


def fetch_tv_top_gainers(
    top_n: int = UNIVERSE_TOP_N,
    price_min: float = UNIVERSE_PRICE_MIN,
    price_max: float = UNIVERSE_PRICE_MAX,
    min_volume: int = UNIVERSE_MIN_VOLUME,
    require_all_green: bool = True,
) -> list[tuple[str, float, float, int]]:
    """
    Hent top-N gainers fra TradingView screener API.

    Returnerer liste af tuples: (symbol, price, change_pct, volume).

    Eksempel:
        [("PIII", 11.29, 180.15, 65655370),
         ("SLE",   6.04,  48.40, 45591682),
         ...]

    Hvis biblioteket fejler eller API'et er nede, returneres tom liste.

    require_all_green=True (default): kun aktier hvor 1D, 1W og 1M change er
    POSITIVE. Det filtrerer dead-cat bounces og kortvarige squeezes der
    typisk fejler i momentum-strategier. Backtest viste at filteret hæver
    win rate fra ~23% til ~48% på blandet vs grønne tickers.

    Sæt til False hvis du vil have rå top-gainer liste (kan inkludere
    aktier med negativ 1-uges eller 1-måneds trend).
    """
    try:
        from tradingview_screener import Query, col
    except ImportError:
        logger.error("tradingview-screener er ikke installeret. "
                     "Kør: pip install tradingview-screener")
        return []

    try:
        # Byg query med pris+volumen filtre
        where_clauses = [
            col('close').between(price_min, price_max),
            col('exchange').isin(['NYSE', 'NASDAQ', 'AMEX']),
            col('type') == 'stock',
            col('volume') > min_volume,
        ]

        # "Alle 3 grønne" filter — kræver positive change på alle 3 tidsrammer
        if require_all_green:
            where_clauses.extend([
                col('change') > 0,       # 1-dags change
                col('Perf.W') > 0,        # 1-uges change
                col('Perf.1M') > 0,       # 1-måneds change
            ])

        _, df = (
            Query()
            .select('name', 'close', 'change', 'Perf.W', 'Perf.1M', 'volume', 'exchange')
            .where(*where_clauses)
            .order_by('change', ascending=False)
            .limit(top_n)
            .get_scanner_data()
        )
    except Exception as e:
        logger.error(f"TV-screener query fejlede: {e}")
        return []

    if df is None or df.empty:
        return []

    results = []
    for _, row in df.iterrows():
        # 'ticker' er fx "NASDAQ:PIII" — vi vil have kun "PIII"
        ticker_full = row.get('ticker', '')
        symbol = ticker_full.split(':')[-1] if ':' in ticker_full else ticker_full
        if not symbol:
            continue

        # Filtrer warrants/units (typisk længere symboler eller med suffix)
        if len(symbol) > 5 or any(c in symbol for c in ['.', '/', '-']):
            continue

        try:
            price  = float(row.get('close', 0))
            change = float(row.get('change', 0))
            volume = int(row.get('volume', 0))
        except (ValueError, TypeError):
            continue

        results.append((symbol, price, change, volume))

    filter_desc = "alle-grønne" if require_all_green else "rå top-gainer"
    logger.info(f"TV-screener returnerede {len(results)} top gainers "
                f"({filter_desc}, pris ${price_min}-${price_max}, "
                f"vol >{min_volume:,}): {', '.join(t[0] for t in results)}")
    return results


def fetch_tv_top_gainer_symbols(top_n: int = UNIVERSE_TOP_N) -> list[str]:
    """
    Convenience-wrapper der kun returnerer symboler (ikke tuples).
    """
    results = fetch_tv_top_gainers(top_n=top_n)
    return [symbol for symbol, _, _, _ in results]


# ─────────────────────────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    print("\nHenter top 25 gainers fra TradingView screener...\n")
    results = fetch_tv_top_gainers(top_n=25)

    if not results:
        print("⚠ Ingen resultater")
    else:
        print(f"{'Symbol':10s}  {'Pris':>8s}  {'Change':>10s}  {'Volume':>14s}")
        print("─" * 50)
        for sym, price, change, vol in results:
            print(f"{sym:10s}  ${price:>7.2f}  {change:>+8.2f}%  {vol:>14,d}")
