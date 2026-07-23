#!/usr/bin/env python3
"""
data_source.py — Daglig OHLCV-kilde til swing-vaerktoejet.

load_bars(ticker) returnerer en daglig OHLCV-DataFrame
(Open/High/Low/Close/Volume, DatetimeIndex) — praecis det format
technical_score og swing_report forventer. Returnerer None hvis data ikke
kan skaffes (swing_report falder paent tilbage og foreslaar --source yfinance).

Kilde-prioritet:
  1) In-memory cache (samme proces)
  2) Disk-cache  data_swing_cache/{TICKER}_daily.csv  hvis aendret i dag
  3) IBKR on-demand via ib_async (qualify Stock + reqHistoricalDataAsync 1 day)

DESIGNVALG (genskabelse): den oprindelige data_source laeste fra en bars_daily-
cache som en SEPARAT downloader skulle fylde foerst ("tilfoej tickeren til
download-universet"). Den downloader er ogsaa tabt, og det ritual passer
daarligt til et MANUELT vaerktoej hvor man slaar én ticker op ad gangen. Derfor
henter vi nu on-demand direkte fra IBKR og cacher resultatet til disk, saa
gentagne opslag samme dag er oejeblikkelige. IBKR forbliver primaerkilden (jf.
dine "Aabne beslutninger"); yfinance er fallback via `swing_report --source
yfinance` (som bruger technical_score._yf_ohlcv).

Krav: TWS/Gateway koerer paa porten nedenfor. Vi forbinder med et hoejt,
tilfaeldigt client-id saa vi ikke kolliderer med den koerende live-backend.
"""
from __future__ import annotations

import asyncio
import random
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

# Python 3.14 event-loop fix (samme som resten af kodebasen).
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7497          # paper trading
DURATION = "2 Y"          # ~2 aar daily — daekker 200-MA + 52-ugers range
CONNECT_TIMEOUT = 15

CACHE_DIR = Path(__file__).parent / "data_swing_cache"
CACHE_DIR.mkdir(exist_ok=True)

_MEM: dict[str, Optional[pd.DataFrame]] = {}   # in-memory cache pr. proces


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}_daily.csv"


def _read_cache(ticker: str) -> Optional[pd.DataFrame]:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    # "Frisk" = filen er aendret i dag. Handelsdag-granularitet er rigeligt
    # for et manuelt vaerktoej der kigger paa daglige bars.
    if date.fromtimestamp(p.stat().st_mtime) != date.today():
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        return df if not df.empty else None
    except Exception:
        return None


def _write_cache(ticker: str, df: pd.DataFrame) -> None:
    try:
        df.to_csv(_cache_path(ticker))
    except Exception:
        pass   # cache-fejl maa aldrig vaelte et opslag


async def _resolve_stock(ib, ticker: str):
    """Find en handlbar STK-kontrakt for `ticker` — som TWS' soegefelt goer.

    Hurtig vej (uaendret): US-aktie paa SMART/USD, daekker de allerfleste opslag.
    Falder den, proever vi:
      2) Klasse-aktie: TradingView/Yahoo skriver 'BRK.B'/'VPLAY.B', men IBKR
         bruger MELLEMRUM ('BRK B'). Vi mapper dot -> mellemrum, stadig US/USD.
      3) IBKRs egen symbol-soegning (reqMatchingSymbols) — praecis det TWS goer.
         Finder udenlandske/klasse-aktier med rigtig boers OG valuta automatisk
         (fx VPLAY.B -> Stockholm / SEK), saa vi ikke laengere haardkoder USD.
    Returnerer en kvalificeret kontrakt (conId sat) eller None.
    """
    from ib_async import Stock
    sym = ticker.upper().strip()

    async def _try(contract):
        try:
            q = await ib.qualifyContractsAsync(contract)
        except Exception:
            return None
        return q[0] if q and getattr(q[0], "conId", 0) else None

    # 1) US-aktie (hurtig, uaendret adfaerd)
    c = await _try(Stock(sym, "SMART", "USD"))
    if c:
        return c

    # 2) US klasse-aktie: dot -> mellemrum (fx BRK.B -> 'BRK B')
    if "." in sym:
        c = await _try(Stock(sym.replace(".", " "), "SMART", "USD"))
        if c:
            return c

    # 3) IBKR symbol-soegning (som TWS) — udenlandske/klasse-aktier med rigtig
    #    boers og valuta. Vi tager foerste kvalificerbare STK-match.
    try:
        matches = await ib.reqMatchingSymbolsAsync(sym.replace(".", " "))
    except Exception:
        matches = None
    for m in (matches or []):
        cd = getattr(m, "contract", None)
        if cd is None or getattr(cd, "secType", None) != "STK":
            continue
        c = await _try(cd)
        if c:
            return c
    return None


async def _fetch_ibkr(ticker: str) -> Optional[pd.DataFrame]:
    try:
        from ib_async import IB
    except ImportError:
        return None
    ib = IB()
    cid = random.randint(70, 89)   # hoejt id -> kolliderer ikke med live-backend
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=cid, timeout=CONNECT_TIMEOUT)
    except Exception:
        return None
    try:
        c = await _resolve_stock(ib, ticker)
        if c is None:
            return None
        bars = await ib.reqHistoricalDataAsync(
            c, endDateTime="", durationStr=DURATION, barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=2)
        if not bars:
            return None
        df = pd.DataFrame(
            [{"Open": b.open, "High": b.high, "Low": b.low,
              "Close": b.close, "Volume": b.volume} for b in bars],
            index=pd.to_datetime([b.date for b in bars]),
        )
        return df if not df.empty else None
    except Exception:
        return None
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def _run(coro):
    """Koer en coroutine synkront — robust uanset event-loop-tilstand."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # baeltet+seler: kun relevant hvis kaldt inde fra async kontekst
            return asyncio.run_coroutine_threadsafe(coro, loop).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def fetch_float(ticker: str) -> dict:
    """
    Hent FLOAT (frit omsaettelige aktier) for EEN ticker fra TradingViews
    screener - samme bibliotek (tradingview-screener) som tv_scanner.py.

    Felt: float_shares_outstanding (verificeret 40/40 udfyldt juni 2026).
    Sekundaert: float_shares_percent_current (float i % af udestaaende aktier).
    TV's screener baerer IKKE short interest - alle short-felter er tomme
    (verificeret), saa kun float hentes her.

    set_tickers() kraever 'EXCHANGE:SYMBOL'. Vi kender ikke boersen paa forhaand,
    saa vi proever NASDAQ/NYSE/AMEX og bruger det foerste hit med udfyldt float
    (en US-aktie ligger kun paa een af dem; de oevrige giver 0 rows).

    Returnerer {'float_shares': float, 'float_pct': float|None} ved hit, ellers
    {} (swing_report viser saa bare ingen float-linje). Fejler ALDRIG kalderen:
    net-/lib-problemer giver tom dict.
    """
    try:
        from tradingview_screener import Query
    except ImportError:
        return {}
    sym = ticker.strip().upper()
    for exch in ("NASDAQ", "NYSE", "AMEX"):
        try:
            _, df = (
                Query()
                .select("float_shares_outstanding", "float_shares_percent_current")
                .set_tickers(f"{exch}:{sym}")
                .get_scanner_data()
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        row = df.iloc[0]
        fl = row.get("float_shares_outstanding")
        try:
            fl = float(fl)
        except (TypeError, ValueError):
            continue
        if fl != fl or fl <= 0:          # NaN eller ugyldig -> proev naeste boers
            continue
        pct = row.get("float_shares_percent_current")
        try:
            pct = float(pct)
            if pct != pct:               # NaN
                pct = None
        except (TypeError, ValueError):
            pct = None
        return {"float_shares": fl, "float_pct": pct}
    return {}


# --- Spread (live IBKR bid/ask, kun i US-aabningstid) ----------------------
def fetch_spread(ticker: str) -> Optional[float]:
    """
    Live bid/ask-spread i PROCENT for EEN ticker fra IBKR ((ask-bid)/mid*100).

    KUN i US-aabningstid (man-fre 09:30-16:00 ET); ellers None, fordi bid/ask er
    staale/tomme uden for RTH. Returnerer None hvis markedet er lukket, der ikke
    er quote, eller noget fejler. Fejler ALDRIG kalderen.

    INFO-linje, IKKE i gaten (gatens spread_pct-faktor er bevidst ikke fodret).
    I et typisk weekend-/aften-koersels-vindue er svaret None.
    """
    from datetime import datetime, time as dtime
    import pytz
    now = datetime.now(pytz.timezone("America/New_York"))
    if now.weekday() >= 5 or not (dtime(9, 30) <= now.time() <= dtime(16, 0)):
        return None
    return _run(_fetch_spread_ibkr(ticker.upper()))


async def _fetch_spread_ibkr(ticker: str) -> Optional[float]:
    try:
        from ib_async import IB, Stock
    except ImportError:
        return None
    ib = IB()
    cid = random.randint(70, 89)   # samme hoeje id-rum som bar-hentning
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=cid, timeout=CONNECT_TIMEOUT)
    except Exception:
        return None
    try:
        c = Stock(ticker, "SMART", "USD")
        await ib.qualifyContractsAsync(c)
        tks = await asyncio.wait_for(ib.reqTickersAsync(c), timeout=8)
        if not tks:
            return None
        tk = tks[0]
        try:
            bid = float(tk.bid)
            ask = float(tk.ask)
        except (TypeError, ValueError):
            return None
        # IBKR giver -1 (eller NaN) naar der ikke er quote
        if bid != bid or ask != ask or bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2.0
        return ((ask - bid) / mid * 100.0) if mid > 0 else None
    except Exception:
        return None
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def load_bars(ticker: str) -> Optional[pd.DataFrame]:
    """Daglig OHLCV for én ticker. None hvis utilgaengelig."""
    t = ticker.upper()
    if t in _MEM:
        return _MEM[t]

    df = _read_cache(t)
    if df is None:
        df = _run(_fetch_ibkr(t))
        if df is not None and not df.empty:
            _write_cache(t, df)

    _MEM[t] = df
    return df


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    df = load_bars(t)
    if df is None or df.empty:
        print(f"Ingen data for {t}. Er TWS/Gateway forbundet paa port {IBKR_PORT}?")
    else:
        print(f"{t}: {len(df)} daglige bars  "
              f"{df.index[0].date()} -> {df.index[-1].date()}")
        print(df.tail())