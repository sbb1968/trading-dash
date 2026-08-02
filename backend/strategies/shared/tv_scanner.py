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


def _to_float(x) -> float:
    """Robust float-coercion for screener-celler der kan være None/NaN/tekst."""
    try:
        if x is None:
            return 0.0
        return float(x)
    except (ValueError, TypeError):
        return 0.0


def _query_intraday_volatility(
    top_n:        int   = VOL_TOP_N,
    price_min:    float = VOL_PRICE_MIN,
    price_max:    float = VOL_PRICE_MAX,
    mkt_cap_min:  float = VOL_MKT_CAP_MIN,
    mkt_cap_max:  float = VOL_MKT_CAP_MAX,
    min_avg_vol:  int   = VOL_MIN_AVG_VOL,
    atr_pct_min:  Optional[float] = VOL_ATR_PCT_MIN,
    exchanges:    Optional[list[str]] = None,
    # ── Valgfrie filtre (None = anvendes IKKE) ──────────────────
    # Tilfoejet for K2 1/8-2026. De er valgfri fordi BuyTheDip deler denne
    # funktion og skal blive PRAECIS som den var — den sender dem ikke med,
    # og faar dermed bit-for-bit samme forespoergsel som foer.
    vol_m_min:    Optional[float] = None,   # Volatility.M nedre graense (%)
    vol_m_max:    Optional[float] = None,   # Volatility.M oevre graense (%)
    perf_w_min:   Optional[float] = None,   # Perf.W (ugens afkast) skal overstige dette (%)
    types:        Optional[list[str]] = None,   # default ['stock', 'dr']
    order_by:     str = "change",           # felt der sorteres paa, faldende
) -> tuple[int, list[dict]]:
    """
    Kør "Intraday Volatility"-screener-queryen og returnér (pool_size, rows).

    pool_size = TradingViews TOTALE antal kandidater der matcher WHERE-filtrene
    FØR `.limit(top_n)` — dvs. hvor stor puljen reelt er. get_scanner_data()
    returnerer dette tal som første element (blev tidligere kasseret som `_`).

    rows = liste af dicts, én pr. ticker, sorteret som screeneren (change DESC):
        {symbol, price, change, volume, avg_vol_30d, market_cap, exchange,
         atrp_1w, atrp_1d, rvol, volatility_d}

    (0, []) hvis biblioteket mangler eller API'et fejler.

    Del 1-instrumentering: SELECT er udvidet med ATRP|1D, relative_volume_10d_calc
    og Volatility.D — men WHERE er UÆNDRET, så udvælgelsen er bit-for-bit den samme
    som før. Vi måler kun; vi gater ikke (endnu).
    """
    try:
        from tradingview_screener import Query, col
    except ImportError:
        logger.error("tradingview-screener er ikke installeret. "
                     "Kør: pip install tradingview-screener")
        return 0, []

    exch = exchanges if exchanges is not None else VOL_EXCHANGES

    # Filtrene bygges dynamisk, saa en kalder der ikke oensker et filter simpelthen
    # lader det vaere None. Feltnavnene er verificeret mod TV's API 1/8-2026:
    # 'Volatility.M' og 'Perf.W' returnerer tal — 'Perf.1W' returnerer None og ville
    # have gjort filteret tavst virkningsloest (samme faelde som 'ATRP|1D' tidligere).
    filters = [
        col('close').between(price_min, price_max),
        col('market_cap_basic').between(mkt_cap_min, mkt_cap_max),
        col('exchange').isin(exch),
        col('average_volume_30d_calc') > min_avg_vol,
        col('type').isin(types if types is not None else ['stock', 'dr']),
    ]
    if atr_pct_min is not None:
        filters.append(col('ATRP|1W') > atr_pct_min)
    if vol_m_min is not None and vol_m_max is not None:
        filters.append(col('Volatility.M').between(vol_m_min, vol_m_max))
    if perf_w_min is not None:
        filters.append(col('Perf.W') > perf_w_min)

    try:
        pool_size, df = (
            Query()
            .select('name', 'close', 'change', 'volume',
                    'average_volume_30d_calc', 'market_cap_basic',
                    'exchange', 'ATRP|1W',
                    'ATRP',                         # dagligt ATR% (TV's default-
                                                    # timeframe ER daglig → BARE 'ATRP';
                                                    # 'ATRP|1D' verificeret → None)
                    'relative_volume_10d_calc',     # RVOL i dag
                    'Volatility.D',                 # dagens (high-low)/low i %
                    'Volatility.M',                 # maanedens — K2 filtrerer OG sorterer paa den
                    'Perf.W')                       # ugens afkast i %
            .where(*filters)
            .order_by(order_by, ascending=False)
            .limit(top_n)
            .get_scanner_data()
        )
    except Exception as e:
        logger.error(f"TV intraday-volatility query fejlede: {e}")
        return 0, []

    pool_size = int(pool_size or 0)
    if df is None or df.empty:
        return pool_size, []

    rows: list[dict] = []
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
            price  = float(row.get('close', 0) or 0)
            change = float(row.get('change', 0) or 0)
            volume = int(row.get('volume', 0) or 0)
        except (ValueError, TypeError):
            continue

        rows.append({
            "symbol":       symbol,
            "price":        price,
            "change":       change,
            "volume":       volume,
            "avg_vol_30d":  _to_float(row.get('average_volume_30d_calc')),
            "market_cap":   _to_float(row.get('market_cap_basic')),
            "exchange":     str(row.get('exchange') or ''),
            "atrp_1w":      _to_float(row.get('ATRP|1W')),
            "atrp_1d":      _to_float(row.get('ATRP')),   # daglig ATR% = bare 'ATRP'
            "rvol":         _to_float(row.get('relative_volume_10d_calc')),
            "volatility_d": _to_float(row.get('Volatility.D')),
            "volatility_m": _to_float(row.get('Volatility.M')),
            "perf_w":       _to_float(row.get('Perf.W')),
        })

    # Log kun de filtre der FAKTISK blev anvendt — ellers staar der en ATR-graense
    # i loggen som forespoergslen slet ikke brugte.
    aktive = [f"pris ${price_min}-${price_max}",
              f"mkt-cap ${mkt_cap_min/1e6:,.0f}M-${mkt_cap_max/1e9:,.0f}B",
              f"avg-vol >{min_avg_vol:,}"]
    if atr_pct_min is not None:
        aktive.append(f"ATR-1W >{atr_pct_min}%")
    if vol_m_min is not None and vol_m_max is not None:
        aktive.append(f"Volatility-1M {vol_m_min}-{vol_m_max}%")
    if perf_w_min is not None:
        aktive.append(f"Perf-1W >{perf_w_min}%")
    logger.info(f"TV intraday-volatility: {len(rows)} af {pool_size} i puljen "
                f"({' · '.join(aktive)}, sorteret paa {order_by}): "
                f"{', '.join(r['symbol'] for r in rows)}")
    return pool_size, rows


def fetch_tv_intraday_volatility(
    top_n:        int   = VOL_TOP_N,
    price_min:    float = VOL_PRICE_MIN,
    price_max:    float = VOL_PRICE_MAX,
    mkt_cap_min:  float = VOL_MKT_CAP_MIN,
    mkt_cap_max:  float = VOL_MKT_CAP_MAX,
    min_avg_vol:  int   = VOL_MIN_AVG_VOL,
    atr_pct_min:  float = VOL_ATR_PCT_MIN,
    exchanges:    Optional[list[str]] = None,
) -> list[dict]:
    """
    Hent "Intraday Volatility"-universet fra TradingView screener API.

    Returnerer liste af dicts (én pr. ticker), sorteret efter dagsændring
    (change %) FALDENDE — samme rækkefølge som kolonnen i Sørens screener.
    Se _query_intraday_volatility for feltbeskrivelse. Tom liste ved fejl.

    (Del 1 skiftede returtypen fra tuple til dict. De eneste kaldere var
    build_volatility_universe* + standalone-verifikationen — alle opdateret.)
    """
    _pool, rows = _query_intraday_volatility(
        top_n=top_n, price_min=price_min, price_max=price_max,
        mkt_cap_min=mkt_cap_min, mkt_cap_max=mkt_cap_max,
        min_avg_vol=min_avg_vol, atr_pct_min=atr_pct_min, exchanges=exchanges,
    )
    return rows


async def build_volatility_universe_rows(
    *,
    top_n:        int,
    price_min:    float,
    price_max:    float,
    mkt_cap_min:  float,
    mkt_cap_max:  float,
    min_avg_vol:  int,
    atr_pct_min:  Optional[float] = None,
    exchanges:    Optional[list[str]] = None,
    vol_m_min:    Optional[float] = None,
    vol_m_max:    Optional[float] = None,
    perf_w_min:   Optional[float] = None,
    types:        Optional[list[str]] = None,
    order_by:     str = "change",
    timeout:      float = 15.0,
    log_tag:      str = "TV",
) -> tuple[int, list[dict]]:
    """
    Delt async-wrapper om _query_intraday_volatility: kører den blokerende screener
    i en executor med timeout + fejlhåndtering og returnerer (pool_size, rows) — den
    fulde række pr. ticker (sorteret efter dagsændring faldende, som screeneren) plus
    puljestørrelsen FØR limit.

    Hoistet fra K2's og BuyTheDips ENS _scan_volatility_universe. Hver strategi kalder
    med SINE EGNE filter-konstanter, så univers-uafhængigheden bevares — kun den
    duplikerede wrapper er fælles. Del 1: kalderne logger rows+pool_size i
    universe_selected-eventet (ingen adfærdsændring). (0, []) ved timeout/fejl
    (kalderen falder så tilbage til retry/fallback).
    """
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        pool_size, rows = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: _query_intraday_volatility(
                    top_n=top_n, price_min=price_min, price_max=price_max,
                    mkt_cap_min=mkt_cap_min, mkt_cap_max=mkt_cap_max,
                    min_avg_vol=min_avg_vol, atr_pct_min=atr_pct_min,
                    exchanges=exchanges,
                    vol_m_min=vol_m_min, vol_m_max=vol_m_max,
                    perf_w_min=perf_w_min, types=types, order_by=order_by,
                ),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{log_tag}] TV-screener (volatility) timeout")
        return 0, []
    except Exception as e:
        logger.error(f"[{log_tag}] TV-screener (volatility) fejl: {e}")
        return 0, []
    return pool_size, rows


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
    Bagudkompatibel wrapper: som build_volatility_universe_rows, men returnerer KUN
    symbolerne (list[str]). Bevaret for kaldere der ikke behøver rows/pool_size.
    """
    _pool, rows = await build_volatility_universe_rows(
        top_n=top_n, price_min=price_min, price_max=price_max,
        mkt_cap_min=mkt_cap_min, mkt_cap_max=mkt_cap_max,
        min_avg_vol=min_avg_vol, atr_pct_min=atr_pct_min,
        exchanges=exchanges, timeout=timeout, log_tag=log_tag,
    )
    return [r["symbol"] for r in rows]


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
