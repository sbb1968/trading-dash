"""
backtest_confluence_intrabar.py
────────────────────────────────
Intra-bar backtest for Konfluens-strategien.

Forskellig fra backtest_confluence.py:
- Henter BAADE 5-min bars (til warmup + indikator-context) og
  1-min bars (til intra-bar evaluering af "snapshot prices")
- Simulerer hvordan LIVE evaluerer: for hver 1-min bar i target-perioden,
  byg en delvis 5-min bar med snapshot-pris som close, og kald check_entry
- Saa langt tidstaettere paa live's faktiske adfaerd end bar-close backtest

FASE 1 (denne version): Bare data-hentning. Verificerer at vi kan hente
baade 5-min og 1-min bars korrekt for et givet ticker/datoer.

Placering: C:\\projects\\trading_dash\\backend\\backtest_confluence_intrabar.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, time as dtime, date as date_cls
from pathlib import Path
from typing import Optional

import pytz

# Python 3.14 event loop fix
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from strategies.base import Bar

ET = pytz.timezone("America/New_York")
IBKR_HOST       = "127.0.0.1"
IBKR_PORT       = 7497
IBKR_CLIENT_ID  = 16  # Anden end backtest_confluence (12), live algo, etc.
CONNECT_TIMEOUT = 15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtest_intrabar")
logging.getLogger("ib_async").setLevel(logging.WARNING)


async def connect_ibkr():
    from ib_async import IB
    ib = IB()
    logger.info(f"Forbinder til IBKR {IBKR_HOST}:{IBKR_PORT} (client_id={IBKR_CLIENT_ID})...")
    await ib.connectAsync(IBKR_HOST, IBKR_PORT,
                          clientId=IBKR_CLIENT_ID, timeout=CONNECT_TIMEOUT)
    logger.info("Forbundet til IBKR")
    return ib


async def fetch_bars(
    ib,
    ticker: str,
    start_date: date_cls,
    end_date: date_cls,
    bar_size: str,  # "5 mins" or "1 min"
) -> list[Bar]:
    """
    Hent bars for én ticker over et periode-vindue.
    bar_size: "5 mins" or "1 min" (skal matche IBKR's API-syntax)
    """
    from ib_async import Stock

    bars: list[Bar] = []
    contract = Stock(ticker, "SMART", "USD")
    await ib.qualifyContractsAsync(contract)

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
                barSizeSetting = bar_size,
                whatToShow     = "TRADES",
                useRTH         = True,
                formatDate     = 1,
            )
        except Exception as e:
            logger.warning(f"  {ticker} {cur} ({bar_size}): bars-fejl: {e}")
            cur += timedelta(days=1)
            continue

        if not ibkr_bars:
            cur += timedelta(days=1)
            continue

        for ib_bar in ibkr_bars:
            ts = ib_bar.date
            if not isinstance(ts, datetime):
                continue

            if ts.tzinfo is None:
                ts = ET.localize(ts)
            else:
                ts = ts.astimezone(ET)

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
# Intra-bar hjaelpefunktioner
# ─────────────────────────────────────────────────────────────

def get_5min_bar_start(ts: datetime) -> datetime:
    """
    Returnér tidspunktet for start af den 5-min bar som ts ligger inden i.
    Eksempel: ts = 09:37:00 -> 09:35:00 (start af 09:35-09:40 bar)
    """
    minute_in_5m = ts.minute % 5
    return ts.replace(minute=ts.minute - minute_in_5m, second=0, microsecond=0)


def build_partial_5min_bar(bars_1m_in_5m: list[Bar]) -> Bar:
    """
    Konstruér en (muligvis delvis) 5-min bar fra de 1-min bars der hoerer til.

    Eksempel: hvis 1-min bars er [09:35, 09:36, 09:37] (tre stk), saa returner
    en bar med open = 09:35's open, high/low = max/min af alle, close = 09:37's
    close (seneste), volume = sum.

    Hvis listen indeholder 5 stk (en hel 5-min periode), er resultatet identisk
    med en faerdig 5-min bar.
    """
    if not bars_1m_in_5m:
        raise ValueError("Tom 1-min bar liste")

    sorted_bars = sorted(bars_1m_in_5m, key=lambda b: b.timestamp)
    return Bar(
        timestamp=sorted_bars[0].timestamp,  # start af 5-min perioden
        open=sorted_bars[0].open,
        high=max(b.high for b in sorted_bars),
        low=min(b.low for b in sorted_bars),
        close=sorted_bars[-1].close,
        volume=sum(b.volume for b in sorted_bars),
    )


def group_1min_by_5min(bars_1m: list[Bar]) -> dict[datetime, list[Bar]]:
    """
    Grupér 1-min bars efter hvilken 5-min periode de tilhører.
    Returnerer dict: 5-min start tidspunkt -> liste af 1-min bars i den periode.
    """
    groups: dict[datetime, list[Bar]] = {}
    for b in bars_1m:
        bucket = get_5min_bar_start(b.timestamp)
        groups.setdefault(bucket, []).append(b)
    return groups

# ─────────────────────────────────────────────────────────────
# Fase 1: Bare verificér data-hentning
# ─────────────────────────────────────────────────────────────
async def main_async(args) -> int:
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    warmup_start = target_date - timedelta(days=30)
    while warmup_start.weekday() >= 5:
        warmup_start -= timedelta(days=1)

    logger.info(f"Target: {target_date}, warmup fra: {warmup_start}")
    logger.info(f"Ticker: {args.ticker}")

    from strategies.confluence import ConfluenceStrategy
    from strategies.confluence.config import VARIANTS, MINTICK

    ib = await connect_ibkr()
    try:
        logger.info("Henter 5-min bars (warmup + target)...")
        bars_5m = await fetch_bars(ib, args.ticker, warmup_start, target_date, "5 mins")
        logger.info(f"  5-min bars: {len(bars_5m)} total")
        bars_5m_target = [b for b in bars_5m if b.timestamp.date() == target_date]
        logger.info(f"  5-min bars paa target-dato: {len(bars_5m_target)}")

        logger.info("Henter 1-min bars (kun target-dato)...")
        bars_1m = await fetch_bars(ib, args.ticker, target_date, target_date, "1 min")
        logger.info(f"  1-min bars paa target-dato: {len(bars_1m)}")

        if args.verify:
            verify_intrabar_helpers(bars_1m)
            return 0

        # ── Setup ───────────────────────────────────────────
        strategy = ConfluenceStrategy()
        config = VARIANTS[args.variant]
        logger.info(f"Variant: {args.variant} ({config.name})")

        bars_history: list[Bar] = [b for b in bars_5m if b.timestamp.date() < target_date]
        logger.info(f"Warmup historie (foer target): {len(bars_history)} 5-min bars")

        groups_1m = group_1min_by_5min(bars_1m)
        five_min_starts = sorted(groups_1m.keys())

        # ── State ────────────────────────────────────────────
        INITIAL_CAPITAL = 10_000.0
        equity = INITIAL_CAPITAL
        position = None  # None or Position
        trades: list[dict] = []

        # ── Intra-bar bar-loop ───────────────────────────────
        import pandas as pd
        for fm_start in five_min_starts:
            bars_in_5m = sorted(groups_1m[fm_start], key=lambda b: b.timestamp)

            # Hybrid evaluering der matcher live's adfaerd:
            # - Entry: intra-bar (alle 1-min snapshots) for at fange snapshot-pris
            # - Exit: kun ved faerdig 5-min bar (matcher live's _last_bar_processed)
            for n_partial in range(1, len(bars_in_5m) + 1):
                partial = build_partial_5min_bar(bars_in_5m[:n_partial])
                is_final_bar = (n_partial == len(bars_in_5m))
                snapshot_time = bars_in_5m[n_partial - 1].timestamp + timedelta(minutes=1)

                # Byg context med delvis bar tilfoejet
                hist_with_partial = bars_history + [partial]
                try:
                    context = strategy.build_session_context(args.ticker, hist_with_partial, config=config)
                    if context is None:
                        continue
                    strategy.entry.load_session_context(context)
                except Exception as e:
                    logger.warning(f"  {partial.timestamp.strftime('%H:%M:%S')}+{n_partial}m: context-fejl: {e}")
                    continue

                ind_df = context["ind_df"]
                try:
                    row = ind_df.loc[partial.timestamp]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[0]
                except KeyError:
                    row = None

                # ── HVIS POSITION: opdatér exit-state og tjek exit ─
                # KUN ved faerdig 5-min bar (matcher live's adfaerd)
                if position is not None and is_final_bar:
                    ema_fast       = float(row["ema_fast"])       if row is not None and not pd.isna(row["ema_fast"])       else None
                    last_swing_low = float(row["last_swing_low"]) if row is not None and not pd.isna(row["last_swing_low"]) else None
                    atr_val        = float(row["atr"])            if row is not None and not pd.isna(row["atr"])            else None

                    strategy.exit.update(
                        position=position,
                        high_seen=partial.close,
                        variant_key=args.variant,
                        low_seen=partial.low,
                        ema_fast=ema_fast,
                        last_swing_low=last_swing_low,
                        atr_val=atr_val,
                    )
                    decision = strategy.exit.check_exit_bar(
                        position, partial, args.variant, indicator_row=row
                    )
                    if decision is not None:
                        # Luk position
                        entry_price = position.entry_price
                        exit_price = decision.exit_price
                        shares = position.shares

                        slip_per_side = config.slippage_ticks * MINTICK
                        entry_fric = slip_per_side * shares + (entry_price * shares) * (config.commission_pct / 100.0)
                        exit_fric  = slip_per_side * shares + (exit_price * shares)  * (config.commission_pct / 100.0)
                        gross = (exit_price - entry_price) * shares
                        net = gross - entry_fric - exit_fric
                        pnl_pct = (exit_price - entry_price) / entry_price * 100.0
                        duration = (partial.timestamp - position.entry_time).total_seconds() / 60.0

                        trade = {
                            "ticker": args.ticker,
                            "entry_time": position.entry_time.strftime("%H:%M:%S"),
                            "exit_time":  snapshot_time.strftime("%H:%M:%S"),
                            "entry_price": round(entry_price, 4),
                            "exit_price":  round(exit_price, 4),
                            "shares": shares,
                            "reason": decision.reason,
                            "pnl":     round(net, 2),
                            "pnl_pct": round(pnl_pct, 3),
                            "duration_min": round(duration, 1),
                            "bricks": position.metadata.get("entry_short", ""),
                        }
                        trades.append(trade)
                        equity += net
                        color = "GROEN" if net > 0 else "ROED"
                        if not args.quiet:
                            logger.info(f"  EXIT {snapshot_time.strftime('%H:%M:%S')}: "
                                        f"{decision.reason} @ ${exit_price:.4f} "
                                        f"pnl=${net:+.2f} ({pnl_pct:+.2f}%) {color}")
                        position = None
                        # Skip resten af denne 1-min loop (ingen ny entry samme tidsstep)
                        continue

                # ── HVIS INGEN POSITION: tjek entry ─────────────
                if position is None:
                    signal = strategy.entry.check_entry(args.ticker, partial, context)
                    if signal is not None:
                        atr_val_signal = signal.metadata.get("atr")
                        if atr_val_signal is None or atr_val_signal <= 0:
                            continue
                        risk_per_share = config.atr_sl_mult * atr_val_signal
                        risk_amount = equity * (config.risk_percent / 100.0)
                        shares = int(risk_amount / max(risk_per_share, MINTICK))
                        if shares <= 0:
                            continue
                        if not args.quiet:
                            logger.info(f"  ENTRY {snapshot_time.strftime('%H:%M:%S')}: "
                                        f"@ ${signal.entry_price:.4f} shares={shares} "
                                        f"bricks={signal.metadata.get('entry_short', '?')}")
                        position = strategy.exit.open_position(signal, shares, args.variant)
                        

            # Efter 5-min periode: tilfoej faerdig bar til permanent historie
            full_5m = build_partial_5min_bar(bars_in_5m)
            bars_history.append(full_5m)

        # ── Eventuel aaben position: force-close ved sidste 1-min ─
        if position is not None:
            last_bar = bars_1m[-1]
            from strategies.base import ExitDecision
            exit_price = last_bar.close
            entry_price = position.entry_price
            shares = position.shares
            slip_per_side = config.slippage_ticks * MINTICK
            entry_fric = slip_per_side * shares + (entry_price * shares) * (config.commission_pct / 100.0)
            exit_fric  = slip_per_side * shares + (exit_price * shares)  * (config.commission_pct / 100.0)
            gross = (exit_price - entry_price) * shares
            net = gross - entry_fric - exit_fric
            pnl_pct = (exit_price - entry_price) / entry_price * 100.0
            trades.append({
                "ticker": args.ticker, "entry_time": position.entry_time.strftime("%H:%M:%S"),
                "exit_time": last_bar.timestamp.strftime("%H:%M:%S"),
                "entry_price": round(entry_price, 4), "exit_price": round(exit_price, 4),
                "shares": shares, "reason": "session_close_eod",
                "pnl": round(net, 2), "pnl_pct": round(pnl_pct, 3),
                "duration_min": round((last_bar.timestamp - position.entry_time).total_seconds() / 60.0, 1),
                "bricks": position.metadata.get("entry_short", ""),
            })
            equity += net
            position = None

        # ── Output ────────────────────────────────────────────
        print()
        print("=" * 90)
        print(f"  INTRA-BAR BACKTEST RESULTAT: {len(trades)} trades")
        print("=" * 90)
        if trades:
            print(f"\n  {'Entry':>10s} {'Exit':>10s} {'EntryPx':>8s} {'ExitPx':>8s} {'Shrs':>5s} "
                  f"{'PnL':>8s} {'%':>7s} {'Min':>5s} {'Reason':14s} {'Bricks':6s}")
            print("  " + "-" * 95)
            for t in trades:
                print(f"  {t['entry_time']:>10s} {t['exit_time']:>10s} "
                      f"${t['entry_price']:>6.4f} ${t['exit_price']:>6.4f} "
                      f"{t['shares']:>5d} ${t['pnl']:>+6.2f} "
                      f"{t['pnl_pct']:>+6.2f}% {t['duration_min']:>5.1f} "
                      f"{t['reason']:14s} {t['bricks']:6s}")

            total_pnl = sum(t['pnl'] for t in trades)
            wins = sum(1 for t in trades if t['pnl'] > 0)
            print(f"\n  Total P&L: ${total_pnl:+.2f}  ({wins}/{len(trades)} winners)")
            print(f"  Equity slut: ${equity:.2f} (start ${INITIAL_CAPITAL:.2f})")
        else:
            print("  Ingen trades")

        return 0
    finally:
        ib.disconnect()

def main() -> int:
    parser = argparse.ArgumentParser(description="Intra-bar backtest (fase 1: data)")
    parser.add_argument("--date", required=True, help="Target-dato (YYYY-MM-DD)")
    parser.add_argument("--ticker", required=True, help="Single ticker, fx VCIG")
    # ← INDSÆT HER:
    parser.add_argument("--verify", action="store_true", help="Koer intra-bar hjaelpefunktion verifikation")
    parser.add_argument("--variant", default="baseline", help="Variant (default baseline)")
    parser.add_argument("--quiet", action="store_true", help="Skjul per-trade logging, vis kun resultat")
    args = parser.parse_args()

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.warning("Afbrudt af bruger")
        return 130


# ─────────────────────────────────────────────────────────────
# Skridt 2a verifikation (kald med --verify)
# ─────────────────────────────────────────────────────────────

def verify_intrabar_helpers(bars_1m: list[Bar]) -> None:
    """Verificér hjaelpefunktionerne paa et udsnit af 1-min bars."""
    print("\nSkridt 2a verifikation: build_partial_5min_bar")
    print("-" * 70)

    # Find alle 1-min bars i 09:35-09:40 5-min perioden
    five_min_start = bars_1m[0].timestamp.replace(hour=9, minute=35, second=0, microsecond=0)
    bars_in_5m = [b for b in bars_1m if get_5min_bar_start(b.timestamp) == five_min_start]

    print(f"\n1-min bars i 09:35-09:40 5-min perioden: {len(bars_in_5m)}")
    for b in bars_in_5m:
        print(f"  {b.timestamp.strftime('%H:%M:%S')}: O={b.open} H={b.high} L={b.low} C={b.close} V={b.volume}")

    # Konstruer delvis 5-min bar trinvis
    print("\nDelvise 5-min bars trinvis:")
    for i in range(1, len(bars_in_5m) + 1):
        partial = build_partial_5min_bar(bars_in_5m[:i])
        n_1m = i
        print(f"  Efter {n_1m} 1-min bar(s): "
              f"O={partial.open:.4f} H={partial.high:.4f} L={partial.low:.4f} "
              f"C={partial.close:.4f} V={partial.volume:.0f}")

if __name__ == "__main__":
    sys.exit(main())