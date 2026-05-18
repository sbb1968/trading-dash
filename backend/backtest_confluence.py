"""
backtest_confluence.py
──────────────────────
Backtest-motor for Konfluens-strategien.

Henter top-gainer-univers fra IBKR scanner (TOP_PERC_GAIN, STK.US.MAJOR)
ved hvert backtest-løb. Henter historiske 5min bars fra IBKR for hver
ticker over en valgt periode, kører strategien gennem precompute + bar-loop,
og udskriver komplet resultat-tabel.

DATO-FILTER (matcher Pine scriptets useStartDate/useEndDate):
  --date 2026-05-15
      Backtest kun den ene dag (matcher din TradingView-test af fredag).
  --start 2026-05-13 --end 2026-05-17
      Backtest et vindue.
  Uden flag: sidste handelsdag (typisk fredag hvis kørt mandag morgen).

UNIVERSE:
  --top-n 6
      Tag top 6 gainers (matcher din manuelle 6-aktier-test).
  --tickers AAPL,NVDA,TSLA
      Override scanner — brug eksplicit liste.

EKSEMPLER:
  # Reproducer TradingView-test af 15. maj:
  python backtest_confluence.py --date 2026-05-15 --top-n 6 --variant baseline

  # Bredere historik:
  python backtest_confluence.py --start 2026-04-01 --end 2026-05-17 --top-n 10

  # Test specifikke aktier:
  python backtest_confluence.py --date 2026-05-15 --tickers AAPL,NVDA,TSLA,AMD,META,GOOGL

KRÆVER:
  - TWS oppe på port 7497 (paper trading)
  - Aktive market data abonnementer for NYSE, NASDAQ
  - Python 3.14 + ib_async + pandas

Placering: C:\\Projects\\trading-dash\\backend\\backtest_confluence.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import asdict
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

# Default capital til R-beregning (matcher Pine's initial_capital=10000)
INITIAL_CAPITAL = 10_000.0

# Warmup-vindue: antal handelsdage HISTORIK at hente FØR target-perioden.
# Pine's HTF EMA(50) på 15min kræver ~50 × 15min = 12.5 timer = ~2 handelsdage,
# men for at matche Pine fuldt ud (hvor scriptet har set hele charts historie)
# vil vi have en pæn buffer. 20 handelsdage = ~4 uger giver:
#   - HTF EMA(50) fuldt warmed up
#   - RSI / ATR / vol_ma (alle ≤20 perioder) konvergeret
#   - Pivot/swing-state opbygget gennem flere markedsfaser
# OBS: For meget store warmup-vinduer kan IBKR returnere mange tomme dage hvor
# tickeren ikke handlede; det er OK — vi bruger bare det vi får.
WARMUP_TRADING_DAYS = 20

# Output
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


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


async def fetch_top_gainers(ib, top_n: int = UNIVERSE_TOP_N) -> list[str]:
    """
    Hent top-N gainers via TradingView's screener API.

    Vi bruger TV i stedet for IBKR's scanner fordi IBKR's TOP_PERC_GAIN
    returnerer aktier på basis af noget der ikke matcher hvad TV viser.
    TV's API returnerer præcis den liste Iben kan se i sit TradingView
    "US Top Gainers" screener — samme priser, samme rækkefølge, samme
    procent-gain.

    'ib' parameteren bruges ikke længere (TV-screener bruger sin egen API)
    men vi beholder den i signaturen så kalderen ikke skal ændres.

    Falder tilbage til tom liste hvis TV-API'et er nede.
    """
    from strategies.confluence.tv_scanner import fetch_tv_top_gainer_symbols

    tickers = fetch_tv_top_gainer_symbols(top_n=top_n)
    if not tickers:
        logger.warning("TV-screener returnerede ingen tickers — tjek bibliotek og netværk")
    return tickers


def passes_price_filter(bars_for_target_day: list[Bar]) -> tuple[bool, str]:
    """
    Sanity-check af pris-filter EFTER TV-scanner har gjort sit arbejde.

    TV-scanner filtrerer allerede på pris og volumen. Dette tjek er en
    backup hvis target-dagens åbnings-pris afviger markant fra hvad TV
    så ved scanner-tidspunktet (typisk pga. gap-up natten over), eller
    hvis vi kører med --tickers liste der overrider scanner.
    """
    if not bars_for_target_day:
        return False, "ingen bars på target-dag"

    open_price = bars_for_target_day[0].open

    if open_price < UNIVERSE_PRICE_MIN:
        return False, f"åbnings-pris ${open_price:.2f} < ${UNIVERSE_PRICE_MIN:.2f}"
    if open_price > UNIVERSE_PRICE_MAX:
        return False, f"åbnings-pris ${open_price:.2f} > ${UNIVERSE_PRICE_MAX:.2f}"

    return True, ""


async def fetch_5min_bars(
    ib,
    ticker: str,
    start_date: date_cls,
    end_date: date_cls,
) -> list[Bar]:
    """
    Hent 5min bars for én ticker over et periode-vindue fra IBKR.

    Vi bruger reqHistoricalDataAsync med RTH=True (kun handelstid 09:30-16:00).

    BEMÆRK: IBKR's historiske data har maks-grænser. For 5min bars er
    grænsen typisk 2 år tilbage og 30 dage pr. request. Vi tager én dag ad
    gangen for at undgå rate-issues og bevare granularitet.
    """
    from ib_async import Stock

    bars: list[Bar] = []
    contract = Stock(ticker, "SMART", "USD")
    await ib.qualifyContractsAsync(contract)

    # Iterér over hver handelsdag i [start_date, end_date]
    cur = start_date
    while cur <= end_date:
        # Spring weekender over
        if cur.weekday() >= 5:
            cur += timedelta(days=1)
            continue

        # IBKR forventer endDateTime som "YYYYMMDD HH:MM:SS US/Eastern"
        # For at få én dag tager vi bars frem til markedsluk (16:00 ET) på dagen.
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
            logger.warning(f"  {ticker} {cur}: bars-fejl: {e}")
            cur += timedelta(days=1)
            continue

        if not ibkr_bars:
            logger.debug(f"  {ticker} {cur}: 0 bars")
            cur += timedelta(days=1)
            continue

        for ib_bar in ibkr_bars:
            # ib_async returnerer bar.date som datetime (lokaltid US/Eastern)
            ts = ib_bar.date
            if not isinstance(ts, datetime):
                # Hvis bar.date er date (daily bars) — vi skipper
                continue

            # Sikr ET-tidszone
            if ts.tzinfo is None:
                ts = ET.localize(ts)
            else:
                ts = ts.astimezone(ET)

            # Verificér at bar er på dagen vi spurgte om — IBKR kan give
            # forrige dages bars hvis cur er en helligdag
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

    return bars


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
    initial_equity: float = INITIAL_CAPITAL,
) -> list[dict]:
    """
    Kør backtest for én ticker over en kontinuerlig bar-periode.

    KONTINUERLIG INDIKATOR-DRIFT:
    bars indeholder BÅDE warmup (før target_start) OG target-perioden.
    Vi pre-computer alle indikatorer over hele bar-rækken og lader Pine's
    'var'-state akkumulere kontinuerligt. Entry-tjek udløses kun for bars
    inden for [target_start, target_end] — det matcher Pine's
    `inDateRange = time >= startDate AND time <= endDate`.

    Equity opdateres fortløbende så 1%-risk-sizing afspejler vinder/taber-runde.

    Returnerer liste af trade-dicts.
    """
    if not bars:
        return []

    config = VARIANTS[variant_key]
    trades: list[dict] = []
    equity = initial_equity

    # Build ÉN session context med pre-computed indikatorer over HELE perioden
    context = strategy.build_session_context(ticker, bars, config=config)
    if context is None:
        logger.warning(f"  {ticker}: for få bars til at bygge session-context "
                       f"({len(bars)} bars hentet) — springer over")
        return []

    # Load context i entry-engine ÉN gang (ikke pr. dag)
    strategy.entry.load_session_context(context)
    ind_df = context["ind_df"]

    # Tæl warmup-statistik til log
    bars_warmup = sum(1 for b in bars if b.date < target_start)
    bars_target = sum(1 for b in bars if target_start <= b.date <= target_end)
    logger.info(f"  {ticker}: {bars_warmup} warmup bars + {bars_target} target bars")

    # Position-tracking (pyramiding=0: én long position ad gangen)
    position = None
    session_bars = sorted(
        [b for b in bars if SESSION_START <= b.time_et <= SESSION_END],
        key=lambda b: b.timestamp,
    )

    for bar in session_bars:
        # Slå indicator-row op (alle bars har en row siden vi pre-computede over hele perioden)
        try:
            row = ind_df.loc[bar.timestamp]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
        except KeyError:
            row = None

        # ── Hvis åben position: opdatér og tjek exit ────────────
        # Exit-tjek kører UANSET om vi er i target-perioden — hvis position
        # blev åbnet i target-perioden skal den også kunne lukkes senere
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
                trade = _close_trade(position, decision, bar, config, ticker)
                equity += trade["pnl"]
                trades.append(trade)
                position = None
                continue   # ingen ny entry på samme bar som exit

        # ── Ingen position: tjek entry — KUN inden for target-vinduet ──
        if position is None:
            # in_date_range modsvarer Pine's `inDateRange` filter
            in_date_range = target_start <= bar.date <= target_end
            if not in_date_range:
                continue

            signal = strategy.entry.check_entry(ticker, bar, context)
            if signal is not None:
                # 1%-risk sizing
                atr_val_signal = signal.metadata["atr"]
                risk_per_share = config.atr_sl_mult * atr_val_signal
                risk_amount    = equity * (config.risk_percent / 100.0)
                shares = int(risk_amount / max(risk_per_share, MINTICK))
                if shares <= 0:
                    continue

                position = strategy.exit.open_position(signal, shares, variant_key)

    # Defensiv: hvis backtest slutter med åben position, force-close på sidste bar
    if position is not None and session_bars:
        last_bar = session_bars[-1]
        from strategies.base import ExitDecision
        from strategies.confluence.exit import REASON_SESSION_CLOSE
        decision = ExitDecision(exit_price=last_bar.close, reason=REASON_SESSION_CLOSE)
        trade = _close_trade(position, decision, last_bar, config, ticker)
        equity += trade["pnl"]
        trades.append(trade)

    return trades


def _close_trade(
    position,
    decision,
    bar: Bar,
    config: ConfluenceVariantConfig,
    ticker: str,
) -> dict:
    """Beregn trade-resultat inkl. friktion."""
    entry_price = position.entry_price
    exit_price  = decision.exit_price
    shares      = position.shares

    # Friktion: slippage + commission begge sider
    slip_per_side = config.slippage_ticks * MINTICK
    entry_friction = (
        slip_per_side * shares
        + (entry_price * shares) * (config.commission_pct / 100.0)
    )
    exit_friction = (
        slip_per_side * shares
        + (exit_price * shares) * (config.commission_pct / 100.0)
    )

    gross_pnl = (exit_price - entry_price) * shares
    net_pnl   = gross_pnl - entry_friction - exit_friction
    pnl_pct   = (exit_price - entry_price) / entry_price * 100.0

    duration_min = (bar.timestamp - position.entry_time).total_seconds() / 60.0

    return {
        "ticker":        ticker,
        "date":          position.entry_time.date().isoformat(),
        "entry_time":    position.entry_time.strftime("%H:%M:%S"),
        "exit_time":     bar.timestamp.strftime("%H:%M:%S"),
        "entry_price":   round(entry_price, 4),
        "exit_price":    round(exit_price, 4),
        "shares":        shares,
        "side":          position.side,
        "reason":        decision.reason,
        "gross_pnl":     round(gross_pnl, 2),
        "friction":      round(entry_friction + exit_friction, 2),
        "pnl":           round(net_pnl, 2),
        "pnl_pct":       round(pnl_pct, 3),
        "duration_min":  round(duration_min, 1),
        "entry_score":   position.metadata.get("entry_score"),
        "entry_bricks":  position.metadata.get("entry_short"),
        "variant":       position.metadata.get("variant_key"),
    }


# ─────────────────────────────────────────────────────────────
# Statistik
# ─────────────────────────────────────────────────────────────

def calc_stats(trades: list[dict]) -> dict:
    """Beregn samlet statistik for en liste af trades."""
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
            "avg_pnl": 0.0, "profit_factor": 0.0, "max_dd": 0.0,
            "best": 0.0, "worst": 0.0, "avg_duration_min": 0.0,
            "by_reason": {},
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100.0 if pnls else 0.0

    profit_sum = sum(wins)
    loss_sum   = abs(sum(losses))
    pf = profit_sum / loss_sum if loss_sum > 0 else float("inf") if profit_sum > 0 else 0.0

    # Drawdown
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Tæl exit-reasons
    by_reason: dict[str, int] = {}
    for t in trades:
        r = t["reason"]
        by_reason[r] = by_reason.get(r, 0) + 1

    return {
        "trades":          len(trades),
        "win_rate":        round(win_rate, 2),
        "total_pnl":       round(total, 2),
        "avg_pnl":         round(total / len(trades), 2),
        "profit_factor":   round(pf, 2) if pf != float("inf") else float("inf"),
        "max_dd":          round(max_dd, 2),
        "best":            round(max(pnls), 2),
        "worst":           round(min(pnls), 2),
        "avg_duration_min": round(sum(t["duration_min"] for t in trades) / len(trades), 1),
        "by_reason":       by_reason,
    }


# ─────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────

def print_trades_table(trades: list[dict]) -> None:
    if not trades:
        print(f"  {YELLOW}Ingen handler i perioden{RESET}")
        return

    print()
    print(f"  {BOLD}{'Date':10s} {'Time':16s} {'Ticker':6s} "
          f"{'Entry':>8s} {'Exit':>8s} {'Shrs':>5s} "
          f"{'PnL':>9s} {'%':>7s} {'Min':>5s} {'Reason':12s} {'Bricks':6s}{RESET}")
    print(f"  {DIM}{'-' * 110}{RESET}")
    for t in trades:
        color = GREEN if t["pnl"] > 0 else (RED if t["pnl"] < 0 else "")
        print(f"  {t['date']:10s} {t['entry_time']}-{t['exit_time']:7s} "
              f"{t['ticker']:6s} "
              f"${t['entry_price']:>6.2f} ${t['exit_price']:>6.2f} "
              f"{t['shares']:>5d} "
              f"{color}${t['pnl']:>+7.2f}{RESET} "
              f"{color}{t['pnl_pct']:>+6.2f}%{RESET} "
              f"{t['duration_min']:>5.0f} "
              f"{t['reason']:12s} {t.get('entry_bricks') or '':6s}")


def print_summary(stats: dict, label: str = "") -> None:
    if label:
        print(f"\n  {BOLD}{label}:{RESET}")

    pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float('inf') else "∞"
    total_color = GREEN if stats['total_pnl'] > 0 else RED if stats['total_pnl'] < 0 else ""

    print(f"    Trades:         {stats['trades']}")
    print(f"    Win Rate:       {stats['win_rate']:.1f}%")
    print(f"    Total P&L:      {total_color}${stats['total_pnl']:+,.2f}{RESET}")
    print(f"    Avg P&L:        ${stats['avg_pnl']:+,.2f}")
    print(f"    Profit Factor:  {pf_str}")
    print(f"    Max Drawdown:   ${stats['max_dd']:,.2f}")
    print(f"    Best/Worst:     ${stats['best']:+,.2f} / ${stats['worst']:+,.2f}")
    print(f"    Avg Duration:   {stats['avg_duration_min']:.1f} min")
    if stats["by_reason"]:
        print(f"    Exit reasons:   {', '.join(f'{k}={v}' for k, v in stats['by_reason'].items())}")


def export_trades_csv(trades: list[dict], path: Path) -> None:
    if not trades:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
        writer.writeheader()
        writer.writerows(trades)
    logger.info(f"  Trades eksporteret: {path}  ({len(trades)} rækker)")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

async def main_async(args) -> int:
    # ── Bestem dato-vindue ────────────────────────────────────
    if args.date:
        start_date = end_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    elif args.start and args.end:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date   = datetime.strptime(args.end,   "%Y-%m-%d").date()
        if end_date < start_date:
            logger.error("--end er før --start")
            return 2
    elif args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        end_date   = datetime.now(ET).date()
    else:
        # Default: sidste handelsdag
        today = datetime.now(ET).date()
        last_day = today
        while last_day.weekday() >= 5:
            last_day -= timedelta(days=1)
        start_date = end_date = last_day

    # ── Beregn warmup-vindue ────────────────────────────────────
    # Vi henter WARMUP_TRADING_DAYS handelsdage før start_date.
    # Konservativ approksimation: 7 kalenderdage pr. 5 handelsdage.
    warmup_calendar_days = int(WARMUP_TRADING_DAYS * 1.5) + 5
    fetch_start_date = start_date - timedelta(days=warmup_calendar_days)
    # Skub fetch_start_date til mandag hvis det rammer weekend
    while fetch_start_date.weekday() >= 5:
        fetch_start_date -= timedelta(days=1)

    print(f"\n{BOLD}{'=' * 90}{RESET}")
    print(f"{BOLD}  KONFLUENS BACKTEST{RESET}")
    print(f"{BOLD}  Target-periode:  {start_date}  →  {end_date}{RESET}")
    print(f"{BOLD}  Warmup-periode:  {fetch_start_date}  →  {start_date - timedelta(days=1)} "
          f"(~{WARMUP_TRADING_DAYS} handelsdage){RESET}")
    print(f"{BOLD}  Variant:         {args.variant!r}  ({VARIANTS[args.variant].name}){RESET}")
    if not args.tickers:
        print(f"{BOLD}  Univers:         top-{args.top_n} fra TradingView screener, "
              f"filter ${UNIVERSE_PRICE_MIN:.0f}-${UNIVERSE_PRICE_MAX:.0f}, "
              f"vol >{UNIVERSE_MIN_VOLUME:,}{RESET}")
    print(f"{BOLD}{'=' * 90}{RESET}\n")

    # ── Forbind IBKR ──────────────────────────────────────────
    ib = await connect_ibkr()
    try:
        # ── Bestem univers ────────────────────────────────────
        if args.tickers:
            tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
            logger.info(f"Eksplicit ticker-liste: {', '.join(tickers)}")
        else:
            tickers = await fetch_top_gainers(ib, args.top_n)
            if not tickers:
                logger.error("Scanner returnerede 0 tickers — afbryder")
                return 1

        # ── Hent bars for hver ticker (2-faset for performance) ─────
        # Fase 1: Hent KUN target-dagens bars (~1 sek pr. ticker) og tjek pris-filter
        # Fase 2: For tickers der passerer, hent warmup-bars (~30 sek pr. ticker)
        # Tickers der filtreres ud i fase 1 koster os kun ~1 sek hver, ikke 30+
        strategy = ConfluenceStrategy()
        all_trades: list[dict] = []
        per_ticker_stats: dict[str, dict] = {}
        filtered_out: list[tuple[str, str]] = []

        n_total = len(tickers)
        t_overall_start = datetime.now()

        # Hvor lang en gennemsnitlig target-fetch (sek) — bruges til ETA
        avg_target_fetch_sec = 1.5
        # Hvor lang en gennemsnitlig warmup-fetch (sek)
        avg_warmup_fetch_sec = 30.0

        print()
        logger.info(f"{BOLD}Fase 1/2: Tjekker pris-filter for {n_total} tickers...{RESET}")
        eta_phase1 = avg_target_fetch_sec * n_total
        logger.info(f"  Forventet tid: ~{eta_phase1:.0f} sek")
        print()

        # Gem target-bars for de tickers der passerer (så vi ikke henter dem to gange)
        passed_tickers: list[tuple[str, list[Bar], float]] = []  # (ticker, target_bars, open_price)

        for i, ticker in enumerate(tickers, start=1):
            t_start = datetime.now()
            prefix = f"  [{i:2d}/{n_total}] {ticker:6s}"

            try:
                target_bars = await fetch_5min_bars(ib, ticker, start_date, end_date)
            except Exception as e:
                elapsed = (datetime.now() - t_start).total_seconds()
                logger.warning(f"{prefix}  {RED}FEJL{RESET} ({elapsed:.1f}s) — {e}")
                filtered_out.append((ticker, f"bar-hentning fejlede: {e}"))
                continue

            elapsed = (datetime.now() - t_start).total_seconds()

            if not target_bars:
                logger.info(f"{prefix}  {YELLOW}0 bars på target-dag{RESET} ({elapsed:.1f}s)")
                filtered_out.append((ticker, "ingen bars på target-dato"))
                continue

            target_day_bars = [b for b in target_bars if b.date == start_date]
            ok, reason = passes_price_filter(target_day_bars)
            if not ok:
                logger.info(f"{prefix}  {YELLOW}filtreret væk{RESET} ({elapsed:.1f}s) — {reason}")
                filtered_out.append((ticker, reason))
                continue

            open_price = target_day_bars[0].open
            logger.info(f"{prefix}  {GREEN}OK{RESET} ({elapsed:.1f}s) — "
                        f"åbning ${open_price:.2f}")
            passed_tickers.append((ticker, target_bars, open_price))

        # ── Fase 2: Hent warmup + kør backtest for de tickers der passerede ──
        phase1_elapsed = (datetime.now() - t_overall_start).total_seconds()
        n_passed = len(passed_tickers)
        print()
        logger.info(f"{BOLD}Fase 1 færdig: {n_passed}/{n_total} tickers passerede "
                    f"pris-filter ({phase1_elapsed:.0f}s){RESET}")
        print()

        if n_passed == 0:
            logger.warning(f"{YELLOW}Ingen tickers tilbage efter pris-filter — afbryder{RESET}")
            return 0

        eta_phase2 = avg_warmup_fetch_sec * n_passed
        logger.info(f"{BOLD}Fase 2/2: Henter warmup + backtester {n_passed} tickers...{RESET}")
        logger.info(f"  Forventet tid: ~{eta_phase2:.0f} sek ({eta_phase2/60:.1f} min)")
        print()

        warmup_end = start_date - timedelta(days=1)

        for i, (ticker, target_bars, open_price) in enumerate(passed_tickers, start=1):
            t_start = datetime.now()
            prefix = f"  [{i:2d}/{n_passed}] {ticker:6s}"

            # Beregn forventet resterende tid
            t_so_far = (datetime.now() - t_overall_start).total_seconds() - phase1_elapsed
            if i > 1:
                avg_per_ticker = t_so_far / (i - 1)
                eta_remaining = avg_per_ticker * (n_passed - i + 1)
                eta_str = f"ETA ~{eta_remaining:.0f}s"
            else:
                eta_str = f"ETA ~{eta_phase2:.0f}s"

            try:
                warmup_bars = await fetch_5min_bars(ib, ticker, fetch_start_date, warmup_end)
            except Exception as e:
                logger.warning(f"{prefix}  {RED}warmup fejlede{RESET} — {e}")
                filtered_out.append((ticker, f"warmup-fetch fejlede: {e}"))
                continue

            bars = warmup_bars + target_bars
            fetch_elapsed = (datetime.now() - t_start).total_seconds()

            target_bar_count = len(target_bars)
            logger.info(f"{prefix}  {len(warmup_bars):4d} warmup + {target_bar_count:3d} target "
                        f"({fetch_elapsed:.1f}s, {eta_str})")

            # Kør backtest
            t_bt_start = datetime.now()
            trades = backtest_ticker(
                strategy=strategy,
                ticker=ticker,
                bars=bars,
                variant_key=args.variant,
                target_start=start_date,
                target_end=end_date,
                initial_equity=INITIAL_CAPITAL,
            )
            bt_elapsed = (datetime.now() - t_bt_start).total_seconds()

            all_trades.extend(trades)
            per_ticker_stats[ticker] = calc_stats(trades)
            pnl_total = sum(t["pnl"] for t in trades)
            pnl_color = GREEN if pnl_total > 0 else (RED if pnl_total < 0 else "")
            logger.info(f"            → {len(trades)} trades, "
                        f"{pnl_color}P&L ${pnl_total:+.2f}{RESET}  "
                        f"(backtest {bt_elapsed:.2f}s)")

        total_elapsed = (datetime.now() - t_overall_start).total_seconds()
        print()
        logger.info(f"{BOLD}Færdig: total {total_elapsed:.0f}s "
                    f"({total_elapsed/60:.1f} min){RESET}")

        # ── Output ────────────────────────────────────────────
        print()
        print(f"{BOLD}{'=' * 90}{RESET}")
        print(f"{BOLD}  ALLE HANDLER{RESET}")
        print(f"{BOLD}{'=' * 90}{RESET}")
        # Sorter på tid
        all_trades.sort(key=lambda t: (t["date"], t["entry_time"]))
        print_trades_table(all_trades)

        # ── Filtered-out rapport ──────────────────────────────
        if filtered_out:
            print()
            print(f"{BOLD}{'=' * 90}{RESET}")
            print(f"{BOLD}  TICKERS FRAFILTRERET  ({len(filtered_out)} af {len(tickers)}){RESET}")
            print(f"{BOLD}{'=' * 90}{RESET}")
            for ticker, reason in filtered_out:
                print(f"  {YELLOW}{ticker:8s}{RESET}  {reason}")

        print()
        print(f"{BOLD}{'=' * 90}{RESET}")
        print(f"{BOLD}  PR. TICKER  ({len(per_ticker_stats)} aktier handlede){RESET}")
        print(f"{BOLD}{'=' * 90}{RESET}")
        for ticker, st in sorted(per_ticker_stats.items(),
                                 key=lambda kv: kv[1]["total_pnl"], reverse=True):
            print_summary(st, label=ticker)

        print()
        print(f"{BOLD}{'=' * 90}{RESET}")
        print(f"{BOLD}  SAMLET{RESET}")
        print(f"{BOLD}{'=' * 90}{RESET}")
        print_summary(calc_stats(all_trades), label="Total")

        # ── Eksporter CSV ─────────────────────────────────────
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = DATA_DIR / f"backtest_confluence_{args.variant}_{timestamp}.csv"
        export_trades_csv(all_trades, csv_path)

        return 0
    finally:
        ib.disconnect()
        logger.info("Frakoblet IBKR")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backtest Konfluens-strategi mod IBKR top gainers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Eksempler:\n"
            "  # Reproducer TradingView-test af 15. maj 2026:\n"
            "  python backtest_confluence.py --date 2026-05-15\n\n"
            "  # Vindue:\n"
            "  python backtest_confluence.py --start 2026-04-01 --end 2026-05-17\n\n"
            "  # Eksplicit univers:\n"
            "  python backtest_confluence.py --date 2026-05-15 --tickers AAPL,NVDA,TSLA\n\n"
            f"  Pris-filter: ${UNIVERSE_PRICE_MIN:.0f}-${UNIVERSE_PRICE_MAX:.0f} (på dagens åbnings-pris)\n"
            f"  Default univers: top {UNIVERSE_TOP_N} fra IBKR scanner\n"
        ),
    )

    # Dato-styring
    parser.add_argument("--date",  type=str, help="Én enkelt dato (YYYY-MM-DD)")
    parser.add_argument("--start", type=str, help="Startdato (YYYY-MM-DD)")
    parser.add_argument("--end",   type=str, help="Slutdato (YYYY-MM-DD)")

    # Universe
    parser.add_argument("--top-n",   type=int, default=UNIVERSE_TOP_N,
                        help=f"Antal top gainers fra IBKR scanner (default {UNIVERSE_TOP_N})")
    parser.add_argument("--tickers", type=str,
                        help="Eksplicit komma-separeret ticker-liste (override scanner + pris-filter)")

    # Strategi
    parser.add_argument("--variant", type=str, default=LIVE_VARIANT_KEY,
                        choices=list(VARIANTS.keys()),
                        help=f"Variant at backteste (default {LIVE_VARIANT_KEY!r})")

    args = parser.parse_args()

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("\nAfbrudt af bruger")
        return 130


if __name__ == "__main__":
    sys.exit(main())
