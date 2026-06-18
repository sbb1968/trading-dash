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

# Neutrale default-konstanter (tidligere importeret fra K1's config; nu selvstændige i
# den delte screener — alle strategier sender alligevel deres EGNE værdier ind).
UNIVERSE_PRICE_MIN   = 5.0
UNIVERSE_PRICE_MAX   = 50.0
UNIVERSE_MIN_VOLUME  = 500_000
UNIVERSE_TOP_N       = 25

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
# "Intraday Volatility"-screener (Konfluens 2)
# ─────────────────────────────────────────────────────────────────
# Replikerer Sørens TradingView "Intraday Volatility"-screener 1:1. I stedet
# for top-gainers (som gav elendige kandidater til K2) filtrerer vi på
# mellem-/large-cap aktier med høj ugentlig ATR — dvs. likvide navne der
# bevæger sig nok intraday til at K2's impuls-setup giver mening.
#
# Feltnavnene er VERIFICERET empirisk mod TradingViews API (juni 2026):
#   - average_volume_30d_calc  = "Avg vol 30D"
#   - market_cap_basic         = "Mkt cap"
#   - ATRP|1W                  = "ATR % (14)" på 1-uges timeframe  ← den svære
#   - exchange ∈ NASDAQ/NYSE/AMEX/CBOE  (TV bundter "NYSE Arca" under AMEX —
#     fx UEC; 'NYSE ARCA'/'CBOE' som koder giver 0 almindelige aktier)
#   - type ∈ stock/dr           (dr = depositary receipts/ADR'er som SKM, NOK,
#     ASX, BILI — de er med i Sørens screener, så vi filtrerer dem IKKE fra)

# Default-filtre — matcher screenshottets screener nøjagtigt.
VOL_EXCHANGES     = ["NASDAQ", "NYSE", "AMEX", "CBOE"]
VOL_PRICE_MIN     = 5.0
VOL_PRICE_MAX     = 50.0
VOL_MKT_CAP_MIN   = 5_000_000_000      # 5 B
VOL_MKT_CAP_MAX   = 1_000_000_000_000  # 1 T
VOL_MIN_AVG_VOL   = 500_000            # Avg vol 30D > 500k
VOL_ATR_PCT_MIN   = 5.0                # ATR(14) 1W > 5%
VOL_TOP_N         = 25


def fetch_tv_intraday_volatility(
    top_n:        int   = VOL_TOP_N,
    price_min:    float = VOL_PRICE_MIN,
    price_max:    float = VOL_PRICE_MAX,
    mkt_cap_min:  float = VOL_MKT_CAP_MIN,
    mkt_cap_max:  float = VOL_MKT_CAP_MAX,
    min_avg_vol:  int   = VOL_MIN_AVG_VOL,
    atr_pct_min:  float = VOL_ATR_PCT_MIN,
    exchanges:    Optional[list[str]] = None,
) -> list[tuple[str, float, float, int]]:
    """
    Hent "Intraday Volatility"-universet fra TradingView screener API.

    Returnerer liste af tuples: (symbol, price, change_pct, volume), sorteret
    efter seneste dagsændring (change %) FALDENDE — samme rækkefølge som
    kolonnen i Sørens screener.

    Filtre (alle server-side hos TradingView):
      - US-aktier på NASDAQ/NYSE/AMEX/CBOE (AMEX dækker TV's "NYSE Arca")
      - pris price_min ≤ close ≤ price_max
      - market cap mkt_cap_min ≤ cap ≤ mkt_cap_max
      - 30-dages gennemsnitsvolumen > min_avg_vol
      - ATR(14) på 1-uges timeframe > atr_pct_min (%)
      - type stock eller dr (depositary receipts/ADR'er er med, som i screeneren)

    Tom liste hvis biblioteket mangler eller API'et fejler.
    """
    try:
        from tradingview_screener import Query, col
    except ImportError:
        logger.error("tradingview-screener er ikke installeret. "
                     "Kør: pip install tradingview-screener")
        return []

    exch = exchanges if exchanges is not None else VOL_EXCHANGES

    try:
        _, df = (
            Query()
            .select('name', 'close', 'change', 'volume',
                    'average_volume_30d_calc', 'market_cap_basic',
                    'exchange', 'ATRP|1W')
            .where(
                col('close').between(price_min, price_max),
                col('market_cap_basic').between(mkt_cap_min, mkt_cap_max),
                col('exchange').isin(exch),
                col('average_volume_30d_calc') > min_avg_vol,
                col('ATRP|1W') > atr_pct_min,
                col('type').isin(['stock', 'dr']),
            )
            .order_by('change', ascending=False)
            .limit(top_n)
            .get_scanner_data()
        )
    except Exception as e:
        logger.error(f"TV intraday-volatility query fejlede: {e}")
        return []

    if df is None or df.empty:
        return []

    results = []
    for _, row in df.iterrows():
        # 'ticker' er fx "NASDAQ:GLXY" — vi vil have kun "GLXY"
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

    logger.info(f"TV intraday-volatility returnerede {len(results)} kandidater "
                f"(pris ${price_min}-${price_max}, mkt-cap "
                f"${mkt_cap_min/1e9:.0f}B-${mkt_cap_max/1e12:.0f}T, "
                f"avg-vol >{min_avg_vol:,}, ATR-1W >{atr_pct_min}%): "
                f"{', '.join(t[0] for t in results)}")
    return results


async def build_volatility_universe(
    *,
    top_n:        int,
    price_min:    float,
    price_max:    float,
    mkt_cap_min:  float,
    mkt_cap_max:  float,
    min_avg_vol:  int,
    atr_pct_min:  float,
    exchanges:    Optional[list[str]] = None,
    timeout:      float = 15.0,
    log_tag:      str = "TV",
) -> list[str]:
    """
    Delt async-wrapper om fetch_tv_intraday_volatility: kører den blokerende screener
    i en executor med timeout + fejlhåndtering og returnerer KUN symbolerne (sorteret
    efter dagsændring faldende, som screeneren).

    Hoistet fra K2's og BuyTheDips ENS _scan_volatility_universe. Hver strategi kalder
    med SINE EGNE filter-konstanter, så univers-uafhængigheden bevares — kun den
    duplikerede wrapper er fælles. Tom liste ved timeout/fejl (kalderen falder så
    tilbage til retry/fallback).
    """
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        results = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: fetch_tv_intraday_volatility(
                    top_n=top_n, price_min=price_min, price_max=price_max,
                    mkt_cap_min=mkt_cap_min, mkt_cap_max=mkt_cap_max,
                    min_avg_vol=min_avg_vol, atr_pct_min=atr_pct_min,
                    exchanges=exchanges,
                ),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{log_tag}] TV-screener (volatility) timeout")
        return []
    except Exception as e:
        logger.error(f"[{log_tag}] TV-screener (volatility) fejl: {e}")
        return []
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
