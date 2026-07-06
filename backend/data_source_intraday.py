#!/usr/bin/env python3
"""
data_source_intraday.py — Day trading-datakilde (trin 3).

Henter bars fra IBKR og producerer praecis den triple (bars, spy_bars, daily) som
technical_intraday.py (trin 2, verificeret 7/7 mod Pine) allerede er bevist korrekt
paa. Dermed kan day trading-rapporten/scanneren koere live uden manuelle TradingView-
eksporter.

SCOPE: henter bars for GIVNE symboler. Bygger IKKE dagens univers (det er scanneren,
trin 6). Modtager en allerede forbundet ib_async IB-instans (genbrug ibkr_connect.py/
IBC) — opretter ikke sin egen forbindelse.

OUTPUT-KONTRAKT (matcher trin 2 eksakt):
    bars   : DatetimeIndex i ET, lowercase open/high/low/close/volume, een TF
             (1/3/5 min), inkl. premarket fra 04:00 ET (useRTH=False).
    spy_bars: samme TF/ET-index, open/close.
    daily  : index = ET session-dato (datetime.date, unik+sorteret), kolonner
             ALLEREDE shiftet til forrige HANDELSDAG: prev_day_close/high/low,
             adv20, adr20.

KRITISK (de tre bugs fra trin 2 — maa ikke genindfoeres):
  * Day trading-tid -> ET via unit="s" hvis numerisk epoch (ellers tolkes sekunder
    som nanosekunder). datetime64-aritmetik, ingen float(epoch_ns).
  * Daglig dato = session-dato UDEN tz-konvertering (tz_convert paa UTC-midnat
    skubber datoen en dag tilbage og oedelaegger join'et).
  * useRTH=False paa day trading (ellers nulstiller dags-akkumulatorerne ikke pr.
    dag — praecis ETH-fra-fejlen).

ASCII i kode + konsol (Windows cp1252); dansk prosa.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ib_async import Stock, util

_ET = "America/New_York"

# Tidsramme-mapper: accepter baade IBKR-strengen og en kort form.
_TF_MAP = {
    "1min": "1 min", "1 min": "1 min",
    "3min": "3 mins", "3 mins": "3 mins",
    "5min": "5 mins", "5 mins": "5 mins",
}


def _norm_tf(tf: str) -> str:
    return _TF_MAP.get(str(tf).strip().lower(), tf)


# === Tid/index-byggere (robuste mod epoch vs datetime) =====================
def _to_et_index(date_col) -> pd.DatetimeIndex:
    """Day trading-tidsstempel -> tz-aware ET-index. formatDate=2 giver epoch, men
    util.df kan levere numerisk epoch ELLER datetime afhaengig af version."""
    if pd.api.types.is_numeric_dtype(date_col):
        idx = pd.to_datetime(date_col, unit="s", utc=True)   # epoch-SEKUNDER (ikke ns!)
    else:
        idx = pd.to_datetime(date_col, utc=True)             # allerede datetime
    return pd.DatetimeIndex(idx).tz_convert(_ET)


def _session_dates(date_col) -> pd.Series:
    """Daglig dato -> kalender/session-dato UDEN tz-konvertering (daily-date-faelden)."""
    if pd.api.types.is_numeric_dtype(date_col):
        return pd.to_datetime(date_col, unit="s", utc=True).dt.date
    return pd.to_datetime(date_col).dt.date


# === Raa IBKR-historik =====================================================
async def _bars(ib, contract, *, bar_size, duration, what="TRADES", use_rth=False, end="") -> pd.DataFrame | None:
    """Eet reqHistoricalData-kald. Kvalificér foerst. formatDate=2 -> entydig epoch.
    Begge kald har timeout (et haeng maa ikke fryse en kaldende loop)."""
    await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10.0)
    raw = await asyncio.wait_for(
        ib.reqHistoricalDataAsync(
            contract, endDateTime=end, durationStr=duration,
            barSizeSetting=bar_size, whatToShow=what, useRTH=use_rth,
            formatDate=2,
        ),
        timeout=30.0,
    )
    return util.df(raw)


# === Daily-aggregater (shift(1) paa handelsdage) ===========================
def _aggregates(daily_raw: pd.DataFrame) -> pd.DataFrame:
    """Raa daglig OHLCV -> FORRIGE handelsdags prev_day_* + adv20/adr20.
    Dagsbarerne er i handelsdags-raekkefoelge (ingen kalender-huller), saa
    .shift(1) springer weekend/helligdage korrekt (fx 22. juni -> 18. junis luk)."""
    df = daily_raw.copy()
    df.index = pd.Index(_session_dates(df["date"]), name="date")   # session-dato, ingen tz
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()        # unik + sorteret
    out = pd.DataFrame(index=df.index)
    out["prev_day_close"] = df["close"].shift(1)
    out["prev_day_high"] = df["high"].shift(1)
    out["prev_day_low"] = df["low"].shift(1)
    out["adv20"] = df["volume"].rolling(20, min_periods=20).mean().shift(1)
    out["adr20"] = (df["high"] - df["low"]).rolling(20, min_periods=20).mean().shift(1)
    return out


def _norm_intraday(raw: pd.DataFrame) -> pd.DataFrame:
    """util.df-output -> ET-index, lowercase OHLCV, sorteret, unik."""
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = pd.DataFrame({
        "open": raw["open"].astype(float),
        "high": raw["high"].astype(float),
        "low": raw["low"].astype(float),
        "close": raw["close"].astype(float),
        "volume": raw["volume"].astype(float),
    })
    out.index = _to_et_index(raw["date"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


# === Offentligt API ========================================================
async def fetch_intraday_bars(ib, symbol, *, timeframe="5 mins", duration="5 D", end="") -> pd.DataFrame:
    """Stock-bars (ET-index, lowercase OHLCV, inkl. premarket fra 04:00)."""
    raw = await _bars(ib, Stock(symbol, "SMART", "USD"),
                      bar_size=_norm_tf(timeframe), duration=duration,
                      what="TRADES", use_rth=False, end=end)
    return _norm_intraday(raw)


async def fetch_benchmark_bars(ib, *, symbol="SPY", timeframe="5 mins", duration="5 D", end="") -> pd.DataFrame:
    """Benchmark-bars (SPY) paa SAMME TF/useRTH/vindue -> open/close, ET-index."""
    raw = await _bars(ib, Stock(symbol, "SMART", "USD"),
                      bar_size=_norm_tf(timeframe), duration=duration,
                      what="TRADES", use_rth=False, end=end)
    df = _norm_intraday(raw)
    return df[["open", "close"]] if not df.empty else df


async def fetch_daily_aggregates(ib, symbol, *, duration="45 D", end="") -> pd.DataFrame:
    """Daglige aggregater (session-dato-index, prev_day_* + adv20/adr20, shiftet).
    useRTH=True -> RTH-dagsbar, matcher Pine's request.security(...,'D',...)."""
    raw = await _bars(ib, Stock(symbol, "SMART", "USD"),
                      bar_size="1 day", duration=duration,
                      what="TRADES", use_rth=True, end=end)
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["prev_day_close", "prev_day_high", "prev_day_low", "adv20", "adr20"])
    return _aggregates(raw)


async def load_intraday_context(ib, symbol, *, timeframe="5 mins", duration="5 D",
                                spy="SPY", daily_duration="45 D", end="") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Hent (bars, spy_bars, daily) klar til compute_technical_intraday.
    Det rapporten/scanneren kalder."""
    bars = await fetch_intraday_bars(ib, symbol, timeframe=timeframe, duration=duration, end=end)
    spy_bars = await fetch_benchmark_bars(ib, symbol=spy, timeframe=timeframe, duration=duration, end=end)
    daily = await fetch_daily_aggregates(ib, symbol, duration=daily_duration, end=end)
    return bars, spy_bars, daily


# === Live quote: spaend + halt (IBKR, eet reqTickers-kald) =================
async def fetch_quote(ib, symbol: str) -> dict:
    """Spaend (decimal) + halt-status i EET reqTickers-snapshot.
    -> {'spread_pct': float|None, 'halted': int}. halted: -1 ukendt, 0 ikke haltet,
    1 alm. halt, 2 news-halt. Fejler ALDRIG (alt None/-1 ved fejl)."""
    try:
        contract = Stock(symbol, "SMART", "USD")
        await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10.0)
        tickers = await asyncio.wait_for(ib.reqTickersAsync(contract), timeout=10.0)
        if not tickers:
            return {"spread_pct": None, "halted": -1}
        tkr = tickers[0]
        bid, ask = tkr.bid, tkr.ask
        spread_pct = None
        if bid and ask and bid > 0 and ask > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid if mid > 0 else None
        h = getattr(tkr, "halted", -1)
        try:
            h = int(h) if h == h else -1            # NaN-guard
        except (TypeError, ValueError):
            h = -1
        return {"spread_pct": spread_pct, "halted": h}
    except Exception:
        return {"spread_pct": None, "halted": -1}


async def fetch_spread(ib, symbol: str) -> float | None:
    """Tynd bagudkompat-wrapper: kun spaendet fra fetch_quote."""
    return (await fetch_quote(ib, symbol))["spread_pct"]


# === Katalysator-nyheder: Finnhub company-news + IBKR Dow Jones (flettet) ===
# Moenstre KOPIERET fra finnhub_news / catalyst_history_probe (day trading-laget er
# selvstaendigt). Finnhub-noeglen GENBRUGES via import (allerede committet i
# finnhub_news; ingen ny secret-kopi) - som fundamental_score goer.
_DJ_HISTORICAL_CANDIDATES = ["DJ-N", "DJ-RTG", "DJ-RTPRO", "DJ-RTA", "DJ-RTE", "DJ-RTW"]
_DJ_SKIP_PROVIDERS = {"BRFG", "BRFUPDN", "DJNL", "FLY"}
_dj_provider_str = None   # cache paa modul-niveau (providere aendrer sig ikke)


def _parse_ibkr_news_time(t):
    s = str(t)[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _finnhub_company_news(symbol: str, max_age_hours: int) -> list[dict]:
    """Finnhub company-news (REST; ingen IBKR-pacing). Tom liste ved enhver fejl."""
    try:
        import aiohttp
        from finnhub_news import FINNHUB_API_KEY, FINNHUB_BASE   # genbrug noeglen
    except Exception:
        return []
    to_date = datetime.now().date()
    from_date = to_date - timedelta(days=2)
    params = {"symbol": symbol, "from": from_date.isoformat(),
              "to": to_date.isoformat(), "token": FINNHUB_API_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{FINNHUB_BASE}/company-news", params=params,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                items = await resp.json()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_hours * 3600
    out = []
    for it in items:
        ts = it.get("datetime", 0)
        hl = (it.get("headline") or "").strip()
        if not ts or ts < cutoff or not hl:
            continue
        out.append({"ts_utc": datetime.fromtimestamp(ts, timezone.utc),
                    "headline": hl, "provider": "Finnhub"})
    return out


async def _ibkr_dj_news(ib, symbol: str, max_age_hours: int) -> list[dict]:
    """IBKR Dow Jones historisk news (eet news-kald paa delt ib). Tom liste ved fejl."""
    global _dj_provider_str
    try:
        if _dj_provider_str is None:
            providers = await asyncio.wait_for(ib.reqNewsProvidersAsync(), timeout=10.0)
            eligible = {getattr(p, "code", "") for p in providers}
            use = [c for c in _DJ_HISTORICAL_CANDIDATES if c in eligible and c not in _DJ_SKIP_PROVIDERS]
            _dj_provider_str = "+".join(use)            # "" hvis ingen DJ-provider berettiget
        if not _dj_provider_str:
            return []
        contract = Stock(symbol, "SMART", "USD")
        await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10.0)
        conid = getattr(contract, "conId", None)
        if not conid:
            return []
        items = await asyncio.wait_for(
            ib.reqHistoricalNewsAsync(conid, _dj_provider_str, "", "", 20), timeout=20.0)
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    out = []
    for it in (items or []):
        dt = _parse_ibkr_news_time(getattr(it, "time", ""))
        hl = str(getattr(it, "headline", "") or "").strip()
        if dt is None or not hl or dt < cutoff:
            continue
        out.append({"ts_utc": dt, "headline": hl, "provider": getattr(it, "providerCode", "DJ")})
    return out


async def fetch_catalyst_news(ib, symbol: str, *, max_age_hours: int = 12) -> list[dict]:
    """Flettet, dedup'et liste af nylige overskrifter fra Finnhub + IBKR DJ.
    Hver: {ts_utc: datetime(UTC), headline: str, provider: str}. Nyeste foerst.
    Fejler ALDRIG kalderen (en kilde-fejl -> bare den anden kildes resultater)."""
    sym = symbol.strip().upper()
    finn = await _finnhub_company_news(sym, max_age_hours)
    ibkr = await _ibkr_dj_news(ib, sym, max_age_hours)
    seen, out = set(), []
    for h in finn + ibkr:
        key = (h["ts_utc"].replace(second=0, microsecond=0),
               " ".join(h["headline"].lower().split())[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    out.sort(key=lambda h: h["ts_utc"], reverse=True)
    return out


# === Forsyningsdata (float fra TradingViews screener — IKKE IBKR) ==========
def fetch_supply(symbol: str) -> dict:
    """Float fra TradingViews screener (samme verificerede query-moenster som swings
    data_source.fetch_float — kopieret, ikke importeret: forsyningsdata er day-tradings
    eget datalag). Short interest er tomt paa TV (verificeret) -> None.

    Proever NASDAQ/NYSE/AMEX (en US-aktie ligger kun paa een); foerste hit med udfyldt
    float. Fejler ALDRIG kalderen (net-/lib-problem -> alle None).

    -> {'float_shares','float_pct','short_float_pct','days_to_cover'} (None hvor ukendt).
    """
    out = {"float_shares": None, "float_pct": None, "short_float_pct": None, "days_to_cover": None}
    try:
        from tradingview_screener import Query
    except ImportError:
        return out
    sym = symbol.strip().upper()
    for exch in ("NASDAQ", "NYSE", "AMEX"):
        try:
            _, df = (Query()
                     .select("float_shares_outstanding", "float_shares_percent_current")
                     .set_tickers(f"{exch}:{sym}")
                     .get_scanner_data())
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
        if fl != fl or fl <= 0:          # NaN/ugyldig -> proev naeste boers
            continue
        out["float_shares"] = fl
        pct = row.get("float_shares_percent_current")
        try:
            pct = float(pct)
            out["float_pct"] = None if pct != pct else pct
        except (TypeError, ValueError):
            pass
        return out
    return out


# === Smoke-test ============================================================
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Smoke-test day trading-datakilden mod IBKR")
    ap.add_argument("--symbol", default="AMAT")
    ap.add_argument("--timeframe", default="5 mins")
    ap.add_argument("--duration", default="5 D")
    ap.add_argument("--daily-duration", default="45 D")
    ap.add_argument("--end", default="")
    args = ap.parse_args()

    async def main():
        from ibkr_connect import IBKRConnection
        conn = IBKRConnection(paper_trading=True)
        if not await conn.connect():
            print("FEJL: kunne ikke forbinde til IBKR. Koerer TWS/Gateway paa 7497 (paper) paa DENNE maskine?")
            return
        try:
            ib = conn.ib
            print("=" * 72)
            print(f" Day trading-datakilde smoke-test: {args.symbol}  ({args.timeframe})")
            print("=" * 72)
            bars, spy, daily = await load_intraday_context(
                ib, args.symbol, timeframe=args.timeframe,
                duration=args.duration, daily_duration=args.daily_duration, end=args.end)

            # 1) bars + premarket-bekraeftelse
            print(f"\nbars: {len(bars)}  spy: {len(spy)}  daily: {len(daily)}")
            if bars.empty:
                print("FEJL: ingen day trading-bars returneret.")
                return
            print(f"foerste ET-bar: {bars.index[0]}   sidste: {bars.index[-1]}")
            firsts = {}
            for d, g in bars.groupby(bars.index.date):
                firsts[str(d)] = str(g.index.min().time())
            print("foerste bar-tid pr. dag (skal vise <09:30, fx 04:00 = premarket):")
            for d, t in firsts.items():
                print(f"  {d}: {t}")

            # 2) daily-shift + ikke-NaN
            print("\ndaily.tail(6) (prev_day_* = FORRIGE handelsdag):")
            with pd.option_context("display.width", 200, "display.max_columns", 10):
                print(daily.tail(6).round(2))
            # bekraeft shift: prev_day_close[i] == close[i-1] (via raa daily)
            rawd = await _bars(ib, Stock(args.symbol, "SMART", "USD"), bar_size="1 day",
                               duration=args.daily_duration, what="TRADES", use_rth=True)
            rd = rawd.copy()
            rd.index = pd.Index(_session_dates(rd["date"]))
            rd = rd[~rd.index.duplicated(keep="last")].sort_index()
            chk = pd.DataFrame({"prev_day_close": daily["prev_day_close"], "raa_close_gaars": rd["close"].shift(1)}).dropna()
            ok_shift = bool(np.allclose(chk["prev_day_close"], chk["raa_close_gaars"]))
            print(f"shift-tjek (prev_day_close == raa close[i-1]): {'OK' if ok_shift else 'FEJL'}")
            print(f"adv20/adr20 udfyldt paa sidste dag: "
                  f"{not pd.isna(daily['adv20'].iloc[-1])}/{not pd.isna(daily['adr20'].iloc[-1])}")

            # 3) score seneste bar via den verificerede scorer
            from technical_intraday import compute_technical_intraday, _band
            res = compute_technical_intraday(bars, spy, daily)
            print(f"\nDAG-SCORE (lag_score): "
                  f"{'-' if res['lag_score'] is None else round(res['lag_score'], 1)}  -> {_band(res['lag_score'])}")
            print("gruppe-scorer:")
            for g, v in res["group_scores"].items():
                print(f"  {g:<16}{'-' if v is None else f'{v:+.0f}'}")
            print("faktorer (foerste 3):")
            for r in res["results"][:3]:
                print(f"  {r.name:<18}{r.signal:>+6.0f}  {r.raw}")
            print(f"ekskluderet (mangler data): {[e[0] for e in res['excluded']] or 'ingen'}")
            print("\nSmoke-test faerdig.")
        finally:
            conn.disconnect()

    asyncio.run(main())
