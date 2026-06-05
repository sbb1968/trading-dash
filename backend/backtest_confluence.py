"""
backtest_confluence.py
──────────────────────
Backtest-motor for Konfluens-strategien (K1) — opgraderet til K2-niveau, så
K1 kan valideres med SAMME metodologi som backtest_confluence2.py:

  - Point-in-time univers-fil (--universe file) — ingen survivorship/look-ahead
  - ALLE varianter i én kørsel (uden --variant) eller én fokuseret (--variant X)
  - Bar-cache på disk (5-min) så gentagne kørsler er øjeblikkelige
  - Slippage-følsom omkostningsmodel (IBKR Pro Fixed $0,005/aktie + 0¢/1¢/2¢)
  - Portefølje-simulation (1% risk/equity, max 3 samtidige) — samme som K2

METODOLOGI (matcher K2):
  Raw-trades sizes med FAST notional ($2.500) og bærer BRUTTO-P&L; omkostninger
  og slippage lægges på i stats()/trade_cost() på tabel-tid. Risk-sizing (1% af
  løbende equity / R) sker KUN i portefølje-sim'en, hvor R = entry − stop_price.
  K1's stop_price er det INITIELLE ATR-stop fra exit-engine (state.initial_stop),
  veldefineret for alle 6 varianter.

  VIGTIGT — afsluttede bars: Backtesten evaluerer på AFSLUTTEDE 5-min bars (den
  korrekte model). Den laves IKKE om til intra-bar.

UNIVERS-MODES:
  --universe scanner  (DEFAULT) — top-N gainers fra TradingView screener, anvendt
                      på hver handelsdag i [start,end]. Bevarer dagens adfærd.
  --universe tickers --tickers AAPL,NVDA  — eksplicit liste på hver dag i vinduet
  --universe file --universe-file historical_universe_2026-05-01_2026-05-29.json
                      — point-in-time per-dag univers (samme filer som K2 brugte)
  --universe journal  — det faktiske daglige univers fra journalen (source=Konfluens)

DATO-FILTER:
  --date 2026-05-15            én dag
  --start 2026-04-01 --end 2026-04-30   vindue (for file/journal: filtrerer dage)
  Uden flag: sidste handelsdag (kun relevant for scanner/tickers).

EKSEMPLER:
  # Maj in-sample, alle varianter:
  python backtest_confluence.py --universe file --universe-file historical_universe_2026-05-01_2026-05-29.json
  # April out-of-sample, kun baseline:
  python backtest_confluence.py --universe file --universe-file historical_universe_2026-04-01_2026-04-30.json --variant baseline
  # Scanner-mode (som før), én dag:
  python backtest_confluence.py --date 2026-05-15 --top-n 6

KRÆVER:
  - TWS oppe på port 7497 (paper trading)
  - Aktive market data abonnementer for NYSE, NASDAQ
  - Python 3.14 + ib_async + pandas

Placering: C:\\Projects\\trading-dash\\backend\\backtest_confluence.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv as _csv
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, time as dtime, date as date_cls
from pathlib import Path
from typing import Optional

import pandas as pd
import pytz

# ── Python 3.14 event loop fix ────────────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ── Imports fra projektet ─────────────────────────────────────
from strategies.base import Bar
from strategies.confluence import (
    ConfluenceStrategy,
    VARIANTS,
    LIVE_VARIANT_KEY,
    ConfluenceVariantConfig,
)
from strategies.confluence.config import (
    MINTICK,
    SESSION_START_HHMM,
    SESSION_END_HHMM,
    UNIVERSE_PRICE_MIN,
    UNIVERSE_PRICE_MAX,
    UNIVERSE_MIN_VOLUME,
    UNIVERSE_TOP_N,
)


# ── Konfiguration ────────────────────────────────────────────
ET = pytz.timezone("America/New_York")
SESSION_START = dtime(*SESSION_START_HHMM)
SESSION_END   = dtime(*SESSION_END_HHMM)

IBKR_HOST       = "127.0.0.1"
IBKR_PORT       = 7497   # Paper trading
IBKR_CLIENT_ID  = 12     # Anden end live algo (10) og fetch_universe (11)
CONNECT_TIMEOUT = 15

# Warmup-vindue: antal handelsdage HISTORIK at hente FØR hver target-dag.
# HTF EMA(50) på 15min + RSI/ATR/vol_ma konvergeret. 20 handelsdage ≈ 35
# kalenderdage (konservativ approksimation, 7 kalender pr. 5 handelsdage).
WARMUP_TRADING_DAYS = 20
WARMUP_CALENDAR_DAYS = int(WARMUP_TRADING_DAYS * 1.5) + 5

# ── Portefølje-/omkostnings-konstanter (match K2 eksakt) ──────
MAX_POSITION_SIZE  = 2_500.0    # fast notional pr. raw-trade
START_EQUITY       = 10_000.0
RISK_PCT           = 0.01       # 1% af løbende equity pr. trade i sim
MAX_CONCURRENT     = 3
MAX_TOTAL_EXPOSURE = 25_000.0
COMMISSION_PER_SHARE = 0.005    # IBKR Pro Fixed: $0,005/aktie
COMMISSION_MIN       = 1.00     # min $1 pr. ordre

# Output + cache
DATA_DIR  = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR = Path(__file__).parent / "bar_cache"
CACHE_DIR.mkdir(exist_ok=True)
DB_PATH   = Path(__file__).parent / "trading_dash.db"


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtest_confluence")
logging.getLogger("ib_async").setLevel(logging.WARNING)


# ── ANSI-farver til terminal ──────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

try:
    import colorama
    colorama.just_fix_windows_console()
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────
# IBKR-forbindelse + scanner
# ─────────────────────────────────────────────────────────────

class IBKRConnectionError(Exception):
    """Rejses når TWS-sessionen er optaget af en anden IP — al data fejler."""


async def connect_ibkr():
    """Opret IBKR-forbindelse — kræver TWS oppe."""
    from ib_async import IB
    ib = IB()
    logger.info(f"Forbinder til IBKR {IBKR_HOST}:{IBKR_PORT} (client_id={IBKR_CLIENT_ID})...")
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT,
                              clientId=IBKR_CLIENT_ID, timeout=CONNECT_TIMEOUT)
    except Exception as e:
        logger.error(f"Kunne ikke forbinde til IBKR: {e}")
        logger.error("Tjek at TWS kører og at port 7497 er åben")
        raise
    logger.info("✓ Forbundet til IBKR")
    return ib


def fetch_top_gainers(top_n: int = UNIVERSE_TOP_N) -> list[str]:
    """
    Hent top-N gainers via TradingView's screener API.

    TV's API returnerer præcis den liste Iben kan se i sit TradingView
    "US Top Gainers" screener — samme priser, rækkefølge og procent-gain.
    Falder tilbage til tom liste hvis TV-API'et er nede.
    """
    from strategies.confluence.tv_scanner import fetch_tv_top_gainer_symbols

    tickers = fetch_tv_top_gainer_symbols(top_n=top_n)
    if not tickers:
        logger.warning("TV-screener returnerede ingen tickers — tjek bibliotek og netværk")
    return tickers


def passes_price_filter(bars_for_target_day: list[Bar]) -> tuple[bool, str]:
    """
    Sanity-check af pris-filter på dagens åbnings-pris (kun scanner-mode).

    file/journal/tickers-universer er allerede pris-filtreret ved opbygning.
    """
    if not bars_for_target_day:
        return False, "ingen bars på target-dag"

    open_price = bars_for_target_day[0].open
    if open_price < UNIVERSE_PRICE_MIN:
        return False, f"åbnings-pris ${open_price:.2f} < ${UNIVERSE_PRICE_MIN:.2f}"
    if open_price > UNIVERSE_PRICE_MAX:
        return False, f"åbnings-pris ${open_price:.2f} > ${UNIVERSE_PRICE_MAX:.2f}"
    return True, ""


# ─────────────────────────────────────────────────────────────
# Bar-cache (5-min) + hentning
# ─────────────────────────────────────────────────────────────
# 38-BYTE-FALDGRUBEN (jf. UNIVERS_backtest_dokumentation.md): cachen gemmer
# også TOMME resultater, så døde tickers ikke gen-forsøges. Men hvis hentningen
# afbrydes af en FORBINDELSESFEJL, må vi IKKE gemme en tom fil (38 bytes: kun
# header) — den ville fejlagtigt læses som "ingen data" for evigt. Vi gemmer
# derfor kun tom cache når der ikke var forbindelsesfejl (conn_errors == 0).
# Oprydning ved mistanke: slet 38-byte filer i bar_cache/ og genhent.

def _cache_path(ticker: str, start: date_cls, end: date_cls) -> Path:
    return CACHE_DIR / f"{ticker}_{start}_{end}_5min.csv"


def _load_cache(ticker: str, start: date_cls, end: date_cls) -> Optional[list[Bar]]:
    p = _cache_path(ticker, start, end)
    if not p.exists():
        return None
    bars: list[Bar] = []
    with p.open(newline="") as f:
        for row in _csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            bars.append(Bar(timestamp=ts, open=float(row["open"]), high=float(row["high"]),
                            low=float(row["low"]), close=float(row["close"]),
                            volume=float(row["volume"])))
    return bars


def _save_cache(ticker: str, start: date_cls, end: date_cls, bars: list[Bar]) -> None:
    p = _cache_path(ticker, start, end)
    with p.open("w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume])


async def fetch_5min_bars(ib, ticker: str, start_date: date_cls, end_date: date_cls) -> list[Bar]:
    """
    Hent 5-min bars for én ticker over [start_date, end_date] fra IBKR, med cache.

    Én dag ad gangen (RTH=True, 09:30-16:00 ET) for at undgå rate-issues.
    Resultatet caches på disk; næste kørsel genbruger det øjeblikkeligt.
    Rejser IBKRConnectionError hvis TWS-sessionen er optaget af en anden IP.
    """
    from ib_async import Stock

    # 1) Cache først
    cached = _load_cache(ticker, start_date, end_date)
    if cached is not None:
        return cached

    contract = Stock(ticker, "SMART", "USD")
    try:
        await ib.qualifyContractsAsync(contract)
    except Exception:
        contract = Stock(ticker, "SMART", "USD", primaryExchange="NASDAQ")
        try:
            await ib.qualifyContractsAsync(contract)
        except Exception as e:
            logger.warning(f"  {ticker}: qualify fejlede: {e}")
            return []

    bars: list[Bar] = []
    conn_errors = 0
    cur = start_date
    while cur <= end_date:
        if cur.weekday() >= 5:
            cur += timedelta(days=1)
            continue

        end_dt_et = ET.localize(datetime(cur.year, cur.month, cur.day, 16, 0))
        end_str   = end_dt_et.strftime("%Y%m%d %H:%M:%S US/Eastern")

        try:
            ibkr_bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime    = end_str,
                durationStr    = "1 D",
                barSizeSetting = "5 mins",
                whatToShow     = "TRADES",
                useRTH         = True,
                formatDate     = 1,
            )
        except Exception as e:
            msg = str(e)
            if "different IP address" in msg or "session is connected" in msg:
                conn_errors += 1
                if conn_errors >= 2:
                    raise IBKRConnectionError(
                        "TWS-sessionen er forbundet fra en anden IP. Luk IBKR Mobile-app "
                        "og Client Portal i browseren, genstart TWS, og prøv igen.")
            logger.debug(f"  {ticker} {cur}: bars-fejl: {e}")
            cur += timedelta(days=1)
            continue

        for ib_bar in ibkr_bars or []:
            ts = ib_bar.date
            if not isinstance(ts, datetime):
                continue
            ts = ET.localize(ts) if ts.tzinfo is None else ts.astimezone(ET)
            if ts.date() != cur:
                continue
            bars.append(Bar(
                timestamp=ts,
                open=float(ib_bar.open),
                high=float(ib_bar.high),
                low=float(ib_bar.low),
                close=float(ib_bar.close),
                volume=float(ib_bar.volume) if ib_bar.volume else 0.0,
            ))
        cur += timedelta(days=1)

    bars.sort(key=lambda x: x.timestamp)
    # Gem i cache — også hvis tom, MEN kun hvis vi faktisk fik kontakt
    # (undgår 38-byte tom-cache efter en forbindelsesfejl).
    if bars or conn_errors == 0:
        _save_cache(ticker, start_date, end_date, bars)
    return bars


# ─────────────────────────────────────────────────────────────
# Journal-univers (det faktiske daglige univers)
# ─────────────────────────────────────────────────────────────

def read_daily_universes(start: Optional[date_cls], end: Optional[date_cls]) -> dict[date_cls, list[str]]:
    """Læs {dag: [tickers]} fra journalens 'universe_selected'-events (source=Konfluens)."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Fandt ikke {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT ts_local, payload_json FROM events "
            "WHERE event_type='universe_selected' AND source='Konfluens' ORDER BY ts_local ASC"
        ).fetchall()
    finally:
        conn.close()
    out: dict[date_cls, list[str]] = {}
    for r in rows:
        try:
            d = datetime.fromisoformat(r["ts_local"]).date()
        except (ValueError, TypeError):
            continue
        if (start and d < start) or (end and d > end):
            continue
        try:
            p = json.loads(r["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if p.get("tickers"):
            out[d] = list(p["tickers"])
    return dict(sorted(out.items()))


# ─────────────────────────────────────────────────────────────
# Backtest-kerne — bar-loop pr. ticker pr. dag
# ─────────────────────────────────────────────────────────────

def backtest_ticker(
    strategy: ConfluenceStrategy,
    ticker: str,
    bars: list[Bar],
    variant_key: str,
    target_start: date_cls,
    target_end: date_cls,
) -> list[dict]:
    """
    Kør backtest for én ticker over en kontinuerlig bar-periode.

    bars indeholder BÅDE warmup (før target_start) OG target-perioden. Vi
    pre-computer indikatorer over hele rækken (kontinuerlig 'var'-drift) og
    udløser entry KUN for bars i [target_start, target_end]. Exit-tjek kører
    uanset (positioner åbnet i target skal kunne lukkes).

    SIZING: fast notional (MAX_POSITION_SIZE) — som K2. Portefølje-sim'en står
    for den rigtige risk-sizing. Returnerer liste af BRUTTO trade-dicts.
    """
    if not bars:
        return []

    config = VARIANTS[variant_key]
    trades: list[dict] = []

    # Build ÉN session context med pre-computed indikatorer over HELE perioden
    context = strategy.build_session_context(ticker, bars, config=config)
    if context is None:
        return []

    strategy.entry.load_session_context(context)
    ind_df = context["ind_df"]

    position = None
    session_bars = sorted(
        [b for b in bars if SESSION_START <= b.time_et <= SESSION_END],
        key=lambda b: b.timestamp,
    )

    for bar in session_bars:
        try:
            row = ind_df.loc[bar.timestamp]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
        except KeyError:
            row = None

        # ── Hvis åben position: opdatér og tjek exit ────────────
        if position is not None:
            ema_fast       = float(row["ema_fast"])        if row is not None and not pd.isna(row["ema_fast"])        else None
            last_swing_low = float(row["last_swing_low"])  if row is not None and not pd.isna(row["last_swing_low"])  else None
            atr_val        = float(row["atr"])             if row is not None and not pd.isna(row["atr"])             else None

            strategy.exit.update(
                position=position,
                high_seen=bar.close,
                variant_key=variant_key,
                low_seen=bar.low,
                ema_fast=ema_fast,
                last_swing_low=last_swing_low,
                atr_val=atr_val,
            )
            decision = strategy.exit.check_exit_bar(
                position, bar, variant_key, indicator_row=row
            )
            if decision is not None:
                trades.append(_close_trade(position, decision, bar, ticker))
                position = None
                continue   # ingen ny entry på samme bar som exit

        # ── Ingen position: tjek entry — KUN inden for target-vinduet ──
        if position is None:
            if not (target_start <= bar.date <= target_end):
                continue

            signal = strategy.entry.check_entry(ticker, bar, context)
            if signal is not None:
                if signal.entry_price <= 0:
                    continue
                shares = int(MAX_POSITION_SIZE / signal.entry_price)
                if shares <= 0:
                    continue
                position = strategy.exit.open_position(signal, shares, variant_key)

    # Defensiv: åben position ved periodeslut → force-close på sidste bar
    if position is not None and session_bars:
        last_bar = session_bars[-1]
        from strategies.base import ExitDecision
        from strategies.confluence.exit import REASON_SESSION_CLOSE
        decision = ExitDecision(exit_price=last_bar.close, reason=REASON_SESSION_CLOSE)
        trades.append(_close_trade(position, decision, last_bar, ticker))

    return trades


def _close_trade(position, decision, bar: Bar, ticker: str) -> dict:
    """
    Byg BRUTTO trade-dict med K2-kompatible nøgler + K1's stop_price/score.

    pnl er BRUTTO (ingen friktion) — omkostninger lægges på i stats()/trade_cost()
    på tabel-tid. stop_price = det initielle ATR-stop (state.initial_stop), som
    portefølje-sim'en bruger til R = entry − stop_price.
    """
    entry_price = position.entry_price
    exit_price  = decision.exit_price
    shares      = position.shares

    gross_pnl = (exit_price - entry_price) * shares
    pct       = (exit_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
    stop_price = getattr(position.state, "initial_stop", None)

    return {
        "date":        position.entry_time.date().isoformat(),
        "ticker":      ticker,
        "entry_time":  position.entry_time.strftime("%H:%M"),
        "exit_time":   bar.timestamp.strftime("%H:%M"),
        "entry":       round(entry_price, 4),
        "exit":        round(exit_price, 4),
        "shares":      shares,
        "pnl":         round(gross_pnl, 2),       # BRUTTO
        "pct":         round(pct, 2),
        "reason":      decision.reason,
        "stop_price":  round(stop_price, 4) if stop_price is not None else None,
        "score":       position.metadata.get("entry_score"),
        "bricks":      position.metadata.get("entry_short"),
        "variant":     position.metadata.get("variant_key"),
    }


# ─────────────────────────────────────────────────────────────
# Omkostningsmodel: IBKR Pro Fixed + slippage  (kopieret fra K2)
# ─────────────────────────────────────────────────────────────

def trade_cost(shares: int, slippage_per_share: float) -> float:
    """
    Samlede omkostninger for ÉN rundtur (køb + salg):
      - kommission: max($0,005 × aktier, $1) for HVER side
      - slippage:   slippage_per_share × aktier for HVER side
    """
    commission = 2 * max(COMMISSION_PER_SHARE * shares, COMMISSION_MIN)
    slippage   = 2 * slippage_per_share * shares
    return commission + slippage


def stats(trades: list[dict], slippage_per_share: float = 0.0) -> dict:
    """Statistik. slippage_per_share=0 → brutto; >0 → netto efter omkostninger."""
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "pnl": 0.0, "pf": 0.0, "avg": 0.0}
    net_pnls = []
    for t in trades:
        cost = trade_cost(t["shares"], slippage_per_share)
        net_pnls.append(t["pnl"] - cost)
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]
    gross_w, gross_l = sum(wins), abs(sum(losses))
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf")
    return {"trades": len(trades), "win_rate": len(wins) / len(net_pnls) * 100,
            "pnl": sum(net_pnls), "pf": pf, "avg": sum(net_pnls) / len(net_pnls)}


def print_stats(label: str, s: dict) -> None:
    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
    c = GREEN if s["pnl"] > 0 else RED if s["pnl"] < 0 else ""
    print(f"  {label:<28} {s['trades']:>4} trades | WR {s['win_rate']:>4.0f}% | "
          f"P&L {c}${s['pnl']:>+9.2f}{RESET} | PF {pf:>5} | avg ${s['avg']:>+7.2f}")


# ─────────────────────────────────────────────────────────────
# Porteføljesimulator: equity-sizing + max samtidige positioner
# ─────────────────────────────────────────────────────────────
# Eneste forskel fra K2: R = entry − stop_price (K1's variant-afhængige ATR-stop),
# hvor K2 brugte R = entry − impulse_low.

def simulate_portfolio(trades: list[dict], selection: str = "fifo",
                       slippage_per_share: float = 0.0) -> dict:
    """Afvikl handler som portefølje: løbende equity (1% risk/R sizing), max 3
    samtidige positioner, lofter. selection: 'fifo' eller 'priority' (højest
    score blandt SAMME-minut-signaler først). Returnerer stats-dict."""
    evs = []
    for t in trades:
        ymd = t["date"]
        e_dt = datetime.strptime(f"{ymd} {t['entry_time']}", "%Y-%m-%d %H:%M")
        x_dt = datetime.strptime(f"{ymd} {t['exit_time']}", "%Y-%m-%d %H:%M")
        evs.append({**t, "_e": e_dt, "_x": x_dt})

    if selection == "priority":
        evs.sort(key=lambda t: (t["_e"], -(t.get("score") or 0)))
    else:
        evs.sort(key=lambda t: t["_e"])

    equity = START_EQUITY
    open_positions: list[dict] = []
    taken = 0
    net_pnls: list[float] = []
    equity_curve: list[float] = []

    for t in evs:
        still_open = []
        for op in open_positions:
            if op["_x"] <= t["_e"]:
                equity += op["net_pnl"]
                equity_curve.append(equity)
            else:
                still_open.append(op)
        open_positions = still_open

        if len(open_positions) >= MAX_CONCURRENT:
            continue

        entry = t["entry"]
        stop = t.get("stop_price")
        if stop is None or entry <= stop:
            continue
        R = entry - stop
        shares = int((equity * RISK_PCT) / R)
        if shares <= 0:
            continue
        if shares * entry > MAX_POSITION_SIZE:
            shares = int(MAX_POSITION_SIZE / entry)
        cur_exp = sum(op["exposure"] for op in open_positions)
        if cur_exp + shares * entry > MAX_TOTAL_EXPOSURE:
            shares = int((MAX_TOTAL_EXPOSURE - cur_exp) / entry)
        if shares <= 0:
            continue

        gross = (t["exit"] - entry) * shares
        net = gross - trade_cost(shares, slippage_per_share)
        open_positions.append({"_x": t["_x"], "exposure": shares * entry, "net_pnl": net})
        taken += 1
        net_pnls.append(net)

    for op in sorted(open_positions, key=lambda o: o["_x"]):
        equity += op["net_pnl"]
        equity_curve.append(equity)

    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    peak, maxdd = START_EQUITY, 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
    return {"taken": taken, "skipped": len(trades) - taken,
            "final_equity": equity, "pnl": equity - START_EQUITY,
            "win_rate": (len(wins) / taken * 100) if taken else 0,
            "pf": pf, "max_dd": maxdd}


# ─────────────────────────────────────────────────────────────
# Univers-bygning
# ─────────────────────────────────────────────────────────────

def _weekday_range(start: date_cls, end: date_cls) -> list[date_cls]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def build_days(args, start: Optional[date_cls], end: Optional[date_cls]) -> dict[date_cls, list[str]]:
    """Byg {dag: [tickers]} afhængig af univers-mode."""
    if args.universe == "file":
        if not args.universe_file:
            logger.error("--universe file kræver --universe-file")
            return {}
        raw = json.loads(Path(args.universe_file).read_text())
        days: dict[date_cls, list[str]] = {}
        for dstr, tickers in raw.items():
            d = datetime.strptime(dstr, "%Y-%m-%d").date()
            if (start and d < start) or (end and d > end):
                continue
            days[d] = list(tickers)
        return dict(sorted(days.items()))

    if args.universe == "journal":
        return read_daily_universes(start, end)

    # scanner / tickers kræver et dato-vindue
    if not (start and end):
        logger.error(f"--universe {args.universe} kræver --date eller --start/--end")
        return {}
    trading_days = _weekday_range(start, end)
    if not trading_days:
        return {}

    if args.universe == "tickers":
        if not args.tickers:
            logger.error("--universe tickers kræver --tickers")
            return {}
        tks = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        return {d: list(tks) for d in trading_days}

    # scanner (default)
    tks = fetch_top_gainers(args.top_n)
    if not tks:
        logger.error("Scanner returnerede 0 tickers")
        return {}
    return {d: list(tks) for d in trading_days}


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

async def main_async(args) -> int:
    # ── Bestem dato-vindue ────────────────────────────────────
    start = end = None
    if args.date:
        start = end = datetime.strptime(args.date, "%Y-%m-%d").date()
    elif args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end   = datetime.strptime(args.end,   "%Y-%m-%d").date()
        if end < start:
            logger.error("--end er før --start")
            return 2
    elif args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end   = datetime.now(ET).date()
    elif args.universe in ("scanner", "tickers"):
        # Default: sidste handelsdag (kun relevant for scanner/tickers)
        last_day = datetime.now(ET).date()
        while last_day.weekday() >= 5:
            last_day -= timedelta(days=1)
        start = end = last_day

    # ── Byg univers {dag: [tickers]} ──────────────────────────
    days = build_days(args, start, end)
    if not days:
        logger.warning("Ingen dage/tickers i univers — afbryder")
        return 0

    variant_keys = [args.variant] if args.variant else list(VARIANTS.keys())
    all_tickers = sorted({t for ts in days.values() for t in ts})
    fetch_start = min(days) - timedelta(days=WARMUP_CALENDAR_DAYS)
    while fetch_start.weekday() >= 5:
        fetch_start -= timedelta(days=1)
    fetch_end = max(days)

    print(f"\n{BOLD}{'=' * 92}{RESET}")
    print(f"{BOLD}  KONFLUENS (K1) BACKTEST — 5-min, afsluttede bars{RESET}")
    print(f"{BOLD}  Univers-kilde:   {args.universe}{RESET}")
    print(f"{BOLD}  Dage:            {min(days)} → {max(days)}  ({len(days)} dage){RESET}")
    print(f"{BOLD}  Unikke aktier:   {len(all_tickers)}{RESET}")
    print(f"{BOLD}  Varianter:       {', '.join(variant_keys)}{RESET}")
    print(f"{BOLD}{'=' * 92}{RESET}\n")

    # ── Forbind IBKR ──────────────────────────────────────────
    ib = await connect_ibkr()
    strategy = ConfluenceStrategy()
    try:
        # ── Hent 5-min bars (cache pr. ticker over hele spændet) ──
        logger.info(f"{BOLD}Henter 5-min bars for {len(all_tickers)} aktier "
                    f"({fetch_start} → {fetch_end})...{RESET}")
        logger.info(f"  (cache: {CACHE_DIR} — allerede-hentede aktier genbruges øjeblikkeligt)")
        cache: dict[str, list[Bar]] = {}
        n_cached, n_fetched = 0, 0
        for i, t in enumerate(all_tickers, 1):
            was_cached = _cache_path(t, fetch_start, fetch_end).exists()
            try:
                bars = await fetch_5min_bars(ib, t, fetch_start, fetch_end)
            except IBKRConnectionError as e:
                logger.error(f"\n{RED}✗ IBKR-FORBINDELSESPROBLEM{RESET}")
                logger.error(f"  {e}")
                logger.error("  Allerede hentede aktier er gemt i cache og genbruges næste gang.")
                return 3
            cache[t] = bars
            if was_cached:
                n_cached += 1
            else:
                n_fetched += 1
            tag = "cache" if was_cached else "IBKR"
            logger.info(f"  [{i:3d}/{len(all_tickers)}] {t:6s}  {len(bars):5d} bars  ({tag})")
        logger.info(f"  → {n_cached} fra cache, {n_fetched} hentet fra IBKR")

        # ── Sweep varianter ──
        results_by_variant: dict[str, list[dict]] = {}
        for vk in variant_keys:
            all_trades: list[dict] = []
            for day, tickers in days.items():
                wstart = day - timedelta(days=WARMUP_CALENDAR_DAYS)
                for t in tickers:
                    tbars = [b for b in cache.get(t, []) if wstart <= b.date <= day]
                    if not tbars:
                        continue
                    if args.universe == "scanner":
                        day_bars = [b for b in tbars if b.date == day]
                        ok, _reason = passes_price_filter(day_bars)
                        if not ok:
                            continue
                    all_trades.extend(backtest_ticker(strategy, t, tbars, vk, day, day))
            results_by_variant[vk] = all_trades
            logger.info(f"  variant {vk:<14} → {len(all_trades)} rå-signaler")

        # ── Tabel 1: variant-sammenligning med slippage-følsomhed ──
        slippage_levels = [0.0, 0.01, 0.02]
        print(f"\n{BOLD}{'=' * 92}{RESET}")
        print(f"{BOLD}  VARIANT-SAMMENLIGNING — netto efter IBKR Pro Fixed ($0,005/aktie) + slippage{RESET}")
        print(f"{BOLD}{'=' * 92}{RESET}")
        header = f"  {'variant':<16} {'trades':>7}"
        for sl in slippage_levels:
            header += f" | {'P&L@' + str(int(sl * 100)) + '¢':>11} {'PF':>5}"
        print(header)
        print(f"  {'-' * 16} {'-' * 7}" + (" | " + "-" * 11 + " " + "-" * 5) * len(slippage_levels))
        for vk in variant_keys:
            trades = results_by_variant[vk]
            line = f"  {vk:<16} {len(trades):>7}"
            for sl in slippage_levels:
                s = stats(trades, sl)
                pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
                c = GREEN if s["pnl"] > 0 else RED
                line += f" | {c}${s['pnl']:>+9.0f}{RESET} {pf:>5}"
            print(line)
        print(f"\n  Læsning: kolonnerne er stigende slippage (0¢ = brutto, 1¢, 2¢ pr. aktie pr. side).")
        print(f"  Ved hvilket slippage-niveau falder PF under 1,0? Det er strategiens margin mod virkeligheden.")

        # ── Tabel 2: portefølje-simulation ──
        print(f"\n{BOLD}{'=' * 92}{RESET}")
        print(f"{BOLD}  PORTEFØLJE-SIMULATION — 1% risk/equity, max 3 samtidige, $10k start{RESET}")
        print(f"{BOLD}  (R = entry − initielt ATR-stop; afslører reel edge når positions-loftet rammer){RESET}")
        print(f"{BOLD}{'=' * 92}{RESET}")
        for vk in variant_keys:
            trades = results_by_variant[vk]
            print(f"\n  {BOLD}{vk}{RESET} ({len(trades)} rå-signaler)")
            print(f"    {'regel':<10} {'taget':>6} {'afvist':>7} "
                  f"{'P&L@1¢':>11} {'PF':>6} {'WR':>5} {'maxDD':>10} {'slut-equity':>12}")
            for sel in ["fifo", "priority"]:
                s = simulate_portfolio(trades, sel, 0.01)
                pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
                c = GREEN if s["pnl"] > 0 else RED
                print(f"    {sel:<10} {s['taken']:>6} {s['skipped']:>7} "
                      f"{c}${s['pnl']:>+9.0f}{RESET} {pf:>6} {s['win_rate']:>4.0f}% "
                      f"${s['max_dd']:>+8.0f} ${s['final_equity']:>10.0f}")
        print(f"\n  Læsning: 'taget' vs 'afvist' viser hvor ofte loftet på 3 positioner")
        print(f"  binder. Stor forskel mellem fifo og priority = edgen er følsom for")
        print(f"  signalvalg. maxDD er største fald fra equity-top undervejs.")

        # ── Detaljer for fokus-variant (den valgte, ellers live-variant) ──
        focus = args.variant or LIVE_VARIANT_KEY
        ftrades = results_by_variant.get(focus, [])
        ftrades = sorted(ftrades, key=lambda t: (t["date"], t["entry_time"]))
        if ftrades and len(ftrades) <= 80:
            print(f"\n{BOLD}  HANDLER — {focus} (pnl er BRUTTO; se tabel ovenfor for netto){RESET}")
            print(f"  {'dato':<11} {'tid':>11} {'tkr':<6} {'entry':>7} {'exit':>7} "
                  f"{'stop':>7} {'pnl':>9} {'%':>7} {'reason':<13} bricks")
            for t in ftrades:
                c = GREEN if t["pnl"] > 0 else RED
                stop_s = f"${t['stop_price']:>6.3f}" if t["stop_price"] is not None else "    —  "
                print(f"  {t['date']:<11} {t['entry_time']}-{t['exit_time']} {t['ticker']:<6} "
                      f"${t['entry']:>6.3f} ${t['exit']:>6.3f} {stop_s} "
                      f"{c}{t['pnl']:>+8.2f}{RESET} {t['pct']:>+6.1f}% "
                      f"{t['reason']:<13} {t['bricks'] or ''}")

        # ── CSV ──
        if ftrades:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = DATA_DIR / f"backtest_confluence_{focus}_{ts}.csv"
            pd.DataFrame(ftrades).to_csv(path, index=False)
            logger.info(f"  {focus}: {len(ftrades)} handler → {path}")

        return 0
    finally:
        ib.disconnect()
        logger.info("Frakoblet IBKR")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backtest Konfluens-strategi (K1) — K2-metodologi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Eksempler:\n"
            "  # Maj in-sample, alle varianter:\n"
            "  python backtest_confluence.py --universe file --universe-file historical_universe_2026-05-01_2026-05-29.json\n\n"
            "  # April out-of-sample, kun baseline:\n"
            "  python backtest_confluence.py --universe file --universe-file historical_universe_2026-04-01_2026-04-30.json --variant baseline\n\n"
            "  # Scanner-mode, én dag:\n"
            "  python backtest_confluence.py --date 2026-05-15 --top-n 6\n"
        ),
    )

    # Univers
    parser.add_argument("--universe", choices=["scanner", "tickers", "file", "journal"],
                        default="scanner",
                        help="Univers-kilde (default: scanner = TV top-gainers)")
    parser.add_argument("--universe-file", type=str,
                        help="JSON-fil fra build_historical_universe.py (for --universe file)")
    parser.add_argument("--top-n",   type=int, default=UNIVERSE_TOP_N,
                        help=f"Antal top gainers fra TV-scanner (default {UNIVERSE_TOP_N})")
    parser.add_argument("--tickers", type=str,
                        help="Eksplicit komma-separeret ticker-liste (for --universe tickers)")

    # Dato-styring
    parser.add_argument("--date",  type=str, help="Én enkelt dato (YYYY-MM-DD)")
    parser.add_argument("--start", type=str, help="Startdato (YYYY-MM-DD)")
    parser.add_argument("--end",   type=str, help="Slutdato (YYYY-MM-DD)")

    # Strategi
    parser.add_argument("--variant", type=str, default=None,
                        choices=list(VARIANTS.keys()),
                        help="Kør kun én variant (default: sweep alle 6)")

    args = parser.parse_args()

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("\nAfbrudt af bruger")
        return 130


if __name__ == "__main__":
    sys.exit(main())
