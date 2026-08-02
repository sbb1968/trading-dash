"""
backtest_confluence2.py
───────────────────────
Backtest for Konfluens 2 (impuls-strategi) på 1-min bars.

To univers-tilstande:
  --universe journal   (DEFAULT) — det FAKTISKE daglige univers fra journalen
                       (event_type 'universe_selected', source Konfluens).
                       Ingen look-ahead/survivorship. Tester kun dage algoen kørte.
  --universe tickers --tickers VCIG,RPD,...   — eksplicit liste (kontrolleret test)

Sweeper alle K2-varianter (eller én via --variant) og printer en
sammenligningstabel, så vi kan se hvilken exit-arketype / target-multiplum
der klarer sig bedst over MANGE dage — ikke kun VCIG-trenddagen.

VIGTIGT: 1-min bars. IBKR-grænse er typisk ~stykkevis; vi henter én dag ad
gangen pr. ticker. Et stort vindue × mange aktier = mange kald (tager tid).

Kør lokalt fra backend/ med TWS oppe (port 7497):
    python backtest_confluence2.py --universe tickers --tickers VCIG --date 2026-05-29
    python backtest_confluence2.py --universe journal --start 2026-05-20 --end 2026-05-29
    python backtest_confluence2.py --universe journal --variant D_target_2r
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, time as dtime, date as date_cls
from pathlib import Path

import pandas as pd
import pytz

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock
from strategies.base import Bar
from strategies.confluence2 import Confluence2Strategy, VARIANTS, LIVE_VARIANT_KEY

ET = pytz.timezone("America/New_York")
SESS_START, SESS_END = dtime(9, 30), dtime(16, 0)

DB_PATH    = Path(__file__).parent / "trading_dash.db"
DATA_DIR   = Path(__file__).parent / "data"; DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR  = Path(__file__).parent / "bar_cache"; CACHE_DIR.mkdir(exist_ok=True)
PORT, CLIENT_ID, TIMEOUT = 7497, 15, 15
MAX_POSITION_SIZE = 2500.0
ENTRY_FILL_NEXT_OPEN = True   # True = realistisk: fyld på næste bars open. False = gammel model: signal-barens close
WARMUP_DAYS = 5   # 1-min: 5 handelsdage warmup er rigeligt til EMA20/RSI/ATR

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("backtest_confluence2")
logging.getLogger("ib_async").setLevel(logging.WARNING)
GREEN, RED, BOLD, RESET = "\033[92m", "\033[91m", "\033[1m", "\033[0m"


# ── Bar-cache (gemmer hentede bars på disk, så vi kun henter én gang) ──
import csv as _csv

class IBKRConnectionError(Exception):
    """Rejses når TWS-sessionen er optaget af en anden IP — al data fejler."""

def _cache_path(ticker: str, start: date_cls, end: date_cls) -> Path:
    return CACHE_DIR / f"{ticker}_{start}_{end}_1min.csv"

def _load_cache(ticker: str, start: date_cls, end: date_cls) -> list[Bar] | None:
    p = _cache_path(ticker, start, end)
    if not p.exists():
        return None
    bars = []
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


# ── 1-min bar-hentning (én dag ad gangen, med cache) ───────────
async def fetch_1m_range(ib: IB, ticker: str, start: date_cls, end: date_cls) -> list[Bar]:
    # 1) Prøv cache først
    cached = _load_cache(ticker, start, end)
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
    bars, cur, conn_errors = [], start, 0
    while cur <= end:
        if cur.weekday() >= 5:
            cur += timedelta(days=1); continue
        end_dt = ET.localize(datetime(cur.year, cur.month, cur.day, 16, 0))
        try:
            raw = await ib.reqHistoricalDataAsync(
                contract, endDateTime=end_dt, durationStr="1 D",
                barSizeSetting="1 min", whatToShow="TRADES", useRTH=True, formatDate=2)
        except Exception as e:
            msg = str(e)
            if "different IP address" in msg or "session is connected" in msg:
                conn_errors += 1
                if conn_errors >= 2:
                    raise IBKRConnectionError(
                        "TWS-sessionen er forbundet fra en anden IP. Luk IBKR Mobile-app "
                        "og Client Portal i browseren, genstart TWS, og prøv igen.")
            logger.debug(f"  {ticker} {cur}: {e}"); cur += timedelta(days=1); continue
        for b in raw or []:
            ts = b.date
            if not isinstance(ts, datetime):
                continue
            ts = ET.localize(ts) if ts.tzinfo is None else ts.astimezone(ET)
            if ts.date() != cur:
                continue
            if SESS_START <= ts.time() <= SESS_END:
                bars.append(Bar(timestamp=ts, open=float(b.open), high=float(b.high),
                                low=float(b.low), close=float(b.close),
                                volume=float(b.volume or 0.0)))
        cur += timedelta(days=1)
    bars.sort(key=lambda x: x.timestamp)
    # 2) Gem i cache (også hvis tom — undgår at gen-forsøge døde tickers,
    #    men kun hvis vi faktisk fik kontakt; tomme pga. conn-fejl gemmes ikke)
    if bars or conn_errors == 0:
        _save_cache(ticker, start, end, bars)
    return bars


# ── Journal-univers ────────────────────────────────────────────
def read_daily_universes(start, end) -> dict[date_cls, list[str]]:
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
    out = {}
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


# ── Backtest af én ticker over én dag ──────────────────────────
def backtest_day(strategy, ticker, bars, day, variant_key) -> list[dict]:
    cfg = VARIANTS[variant_key]
    ctx = strategy.build_session_context(ticker, bars, config=cfg)
    if ctx is None:
        return []
    strategy.entry.load_session_context(ctx)
    strategy.exit.load_session_context(ctx)
    ind = ctx["ind_df"]

    trades, pos, entry_meta, pending_sig = [], None, None, None
    day_bars = [b for b in bars if b.date == day and SESS_START <= b.time_et <= SESS_END]
    for b in day_bars:
        try:
            row = ind.loc[b.timestamp]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
        except KeyError:
            continue

        # Realistisk fill: et signal fra forrige bar fyldes på DENNE bars open.
        if pos is None and pending_sig is not None:
            _cfg = VARIANTS[variant_key]
            _imp_close = pending_sig.metadata.get("impulse_close", pending_sig.entry_price)
            if getattr(_cfg, "confirm_next_bar", False) and b.open < _imp_close:
                pending_sig = None          # ingen følge-igennem på næste bars open → drop signalet
            else:
                pending_sig.entry_price = b.open
                pending_sig.entry_time  = b.timestamp
                shares = int(MAX_POSITION_SIZE / pending_sig.entry_price) if pending_sig.entry_price > 0 else 0
                if shares > 0:
                    pos = strategy.exit.open_position(pending_sig, shares, variant_key)
                    entry_meta = {"time": b.time_et, "price": pending_sig.entry_price,
                                  "bricks": pending_sig.metadata.get("bricks"), "shares": shares,
                                  "impulse_low": pending_sig.metadata.get("impulse_low"),
                                  "score": pending_sig.metadata.get("score")}
                pending_sig = None
            # INGEN continue: fill-baren exit-tjekkes også (falder igennem til
            # else-grenen nedenfor) — den kan selv ramme stoppet, som en rigtig fill.

        if pos is None:
            sig = strategy.entry.check_entry(ticker, b, ctx)
            if sig:
                if ENTRY_FILL_NEXT_OPEN:
                    pending_sig = sig          # åbn IKKE nu — vent på næste bars open
                else:
                    shares = int(MAX_POSITION_SIZE / sig.entry_price) if sig.entry_price > 0 else 0
                    if shares <= 0:
                        continue
                    pos = strategy.exit.open_position(sig, shares, variant_key)
                    entry_meta = {"time": b.time_et, "price": sig.entry_price,
                                  "bricks": sig.metadata.get("bricks"), "shares": shares,
                                  "impulse_low": sig.metadata.get("impulse_low"),
                                  "score": sig.metadata.get("score")}
        else:
            strategy.exit.update(pos, b.high, variant_key, low_seen=b.low)
            dec = strategy.exit.check_exit_bar(pos, b, variant_key, indicator_row=row)
            if dec:
                gross_pnl = (dec.exit_price - pos.entry_price) * pos.shares
                trades.append({
                    "date": str(day), "ticker": ticker,
                    "entry_time": entry_meta["time"].strftime("%H:%M"),
                    "exit_time": b.time_et.strftime("%H:%M"),
                    "entry": pos.entry_price, "exit": dec.exit_price,
                    "shares": pos.shares, "pnl": round(gross_pnl, 2),
                    "pct": round((dec.exit_price / pos.entry_price - 1) * 100, 2),
                    "reason": dec.reason, "bricks": entry_meta["bricks"],
                    "impulse_low": entry_meta["impulse_low"],
                    "score": entry_meta["score"],
                })
                pos, entry_meta = None, None
    return trades


# ── Omkostningsmodel: IBKR Pro Fixed + slippage ──────────────────
COMMISSION_PER_SHARE = 0.005   # IBKR Pro Fixed: $0,005/aktie
COMMISSION_MIN       = 1.00    # min $1 pr. ordre

def trade_cost(shares: int, slippage_per_share: float) -> float:
    """
    Samlede omkostninger for ÉN rundtur (køb + salg):
      - kommission: max($0,005 × aktier, $1) for HVER side (køb + salg)
      - slippage:   slippage_per_share × aktier for HVER side
    """
    commission = 2 * max(COMMISSION_PER_SHARE * shares, COMMISSION_MIN)
    slippage   = 2 * slippage_per_share * shares
    return commission + slippage


def stats(trades, slippage_per_share: float = 0.0) -> dict:
    """Beregn statistik. slippage_per_share=0 → brutto; >0 → netto efter omkostninger."""
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "pnl": 0.0, "pf": 0.0, "avg": 0.0}
    net_pnls = []
    for t in trades:
        cost = trade_cost(t["shares"], slippage_per_share)
        net_pnls.append(t["pnl"] - cost)
    wins = [p for p in net_pnls if p > 0]; losses = [p for p in net_pnls if p < 0]
    gross_w, gross_l = sum(wins), abs(sum(losses))
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf")
    return {"trades": len(trades), "win_rate": len(wins) / len(net_pnls) * 100,
            "pnl": sum(net_pnls), "pf": pf, "avg": sum(net_pnls) / len(net_pnls)}

# ── Porteføljesimulator: equity-sizing + max samtidige positioner ──
START_EQUITY       = 10_000.0
RISK_PCT           = 0.01          # 1% af løbende equity pr. trade
MAX_CONCURRENT     = 3
MAX_TOTAL_EXPOSURE = 25_000.0      # loft på samlet åben eksponering


def simulate_portfolio(trades, selection="fifo", slippage_per_share=0.0):
    """Afvikl handler som portefølje: løbende equity (1% risk/R sizing),
    max 3 samtidige positioner, lofter. selection: 'fifo' eller 'priority'
    (højest score blandt SAMME-minut-signaler først). Returnerer stats-dict."""
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
    open_positions = []
    taken = 0
    net_pnls = []
    equity_curve = []

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
        low = t.get("impulse_low")
        if low is None or entry <= low:
            continue
        R = entry - low
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
        open_positions.append({"_x": t["_x"], "exposure": shares * entry,
                               "net_pnl": net})
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

def print_stats(label, s):
    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
    c = GREEN if s["pnl"] > 0 else RED if s["pnl"] < 0 else ""
    print(f"  {label:<28} {s['trades']:>4} trades | WR {s['win_rate']:>4.0f}% | "
          f"P&L {c}${s['pnl']:>+9.2f}{RESET} | PF {pf:>5} | avg ${s['avg']:>+7.2f}")


async def run(args):
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None
    end   = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else None
    if args.date:
        start = end = datetime.strptime(args.date, "%Y-%m-%d").date()

    # Byg {dag: [tickers]}
    if args.universe == "tickers":
        if not args.tickers:
            logger.error("--universe tickers kræver --tickers")
            return 2
        tks = [t.strip().upper() for t in args.tickers.split(",")]
        if not (start and end):
            logger.error("--universe tickers kræver --date eller --start/--end")
            return 2
        days = {}
        d = start
        while d <= end:
            if d.weekday() < 5:
                days[d] = tks
            d += timedelta(days=1)
    elif args.universe == "file":
        if not args.universe_file:
            logger.error("--universe file kræver --universe-file")
            return 2
        raw = json.loads(Path(args.universe_file).read_text())
        days = {}
        for dstr, tickers in raw.items():
            d = datetime.strptime(dstr, "%Y-%m-%d").date()
            if start and d < start:
                continue
            if end and d > end:
                continue
            days[d] = list(tickers)
        days = dict(sorted(days.items()))
        if not days:
            logger.warning("Ingen dage i univers-filen (efter dato-filter)."); return 0
    else:
        days = read_daily_universes(start, end)
        if not days:
            logger.warning("Ingen 'universe_selected'-events i vinduet."); return 0

    variant_keys = [args.variant] if args.variant else list(VARIANTS.keys())
    all_tickers = sorted({t for ts in days.values() for t in ts})
    fetch_start = min(days) - timedelta(days=int(WARMUP_DAYS * 1.6) + 4)
    while fetch_start.weekday() >= 5:
        fetch_start -= timedelta(days=1)
    fetch_end = max(days)

    print(f"\n{BOLD}{'='*88}{RESET}")
    print(f"{BOLD}  KONFLUENS 2 BACKTEST (1-min impuls){RESET}")
    print(f"{BOLD}  Univers-kilde:   {args.universe}{RESET}")
    print(f"{BOLD}  Dage:            {min(days)} → {max(days)}  ({len(days)} dage){RESET}")
    print(f"{BOLD}  Unikke aktier:   {len(all_tickers)}{RESET}")
    print(f"{BOLD}  Varianter:       {', '.join(variant_keys)}{RESET}")
    print(f"{BOLD}{'='*88}{RESET}\n")

    ib = IB()
    logger.info(f"Forbinder IBKR 127.0.0.1:{PORT} (clientId={CLIENT_ID})...")
    await ib.connectAsync("127.0.0.1", PORT, clientId=CLIENT_ID, timeout=TIMEOUT)
    logger.info("✓ Forbundet")
    strategy = Confluence2Strategy()

    try:
        # ── Hent 1-min bars (cache pr. ticker over hele spændet) ──
        logger.info(f"{BOLD}Henter 1-min bars for {len(all_tickers)} aktier "
                    f"({fetch_start} → {fetch_end})...{RESET}")
        logger.info(f"  (cache: {CACHE_DIR} — allerede-hentede aktier genbruges øjeblikkeligt)")
        cache = {}
        n_cached, n_fetched = 0, 0
        for i, t in enumerate(all_tickers, 1):
            was_cached = _cache_path(t, fetch_start, fetch_end).exists()
            try:
                bars = await fetch_1m_range(ib, t, fetch_start, fetch_end)
            except IBKRConnectionError as e:
                logger.error(f"\n{RED}✗ IBKR-FORBINDELSESPROBLEM{RESET}")
                logger.error(f"  {e}")
                logger.error(f"  Allerede hentede aktier er gemt i cache og genbruges næste gang.")
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
        results_by_variant = {}
        for vk in variant_keys:
            all_trades = []
            for day, tickers in days.items():
                wstart = day - timedelta(days=int(WARMUP_DAYS * 1.6) + 4)
                for t in tickers:
                    bars = [b for b in cache.get(t, []) if wstart <= b.date <= day]
                    if not bars:
                        continue
                    all_trades.extend(backtest_day(strategy, t, bars, day, vk))
            results_by_variant[vk] = all_trades

        # ── Sammenligningstabel med slippage-følsomhed ──
        slippage_levels = [0.0, 0.01, 0.02]
        print(f"\n{BOLD}{'='*92}{RESET}")
        print(f"{BOLD}  VARIANT-SAMMENLIGNING — netto efter IBKR Pro Fixed ($0,005/aktie) + slippage{RESET}")
        print(f"{BOLD}{'='*92}{RESET}")
        header = f"  {'variant':<16} {'trades':>7}"
        for sl in slippage_levels:
            header += f" | {'P&L@' + str(int(sl*100)) + '¢':>11} {'PF':>5}"
        print(header)
        print(f"  {'-'*16} {'-'*7}" + (" | " + "-"*11 + " " + "-"*5) * len(slippage_levels))
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
        # ── Portefølje-realistisk: equity-sizing + max 3 samtidige ──
        print(f"\n{BOLD}{'='*92}{RESET}")
        print(f"{BOLD}  PORTEFØLJE-SIMULATION — 1% risk/equity, max 3 samtidige, $10k start{RESET}")
        print(f"{BOLD}  (afslører A's REELLE edge når positions-loftet rammer){RESET}")
        print(f"{BOLD}{'='*92}{RESET}")
        for vk in variant_keys:
            trades = results_by_variant[vk]
            print(f"\n  {BOLD}{vk}{RESET} ({len(trades)} rå-signaler)")
            print(f"    {'regel':<10} {'slip':>4} {'taget':>6} {'afvist':>7} "
                  f"{'P&L':>11} {'PF':>6} {'WR':>5} {'maxDD':>10} {'slut-equity':>12}")
            for sel in ["fifo", "priority"]:
                for sl in slippage_levels:           # 0 / 1 / 2¢ — beslutning på 2¢
                    s = simulate_portfolio(trades, sel, sl)
                    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
                    c = GREEN if s["pnl"] > 0 else RED
                    print(f"    {sel:<10} {int(round(sl*100)):>3}¢ {s['taken']:>6} {s['skipped']:>7} "
                          f"{c}${s['pnl']:>+9.0f}{RESET} {pf:>6} {s['win_rate']:>4.0f}% "
                          f"${s['max_dd']:>+8.0f} ${s['final_equity']:>10.0f}")
        print(f"\n  Læsning: 'taget' vs 'afvist' viser hvor ofte loftet på 3 positioner")
        print(f"  binder (uafhængigt af slippage). BESLUTNING træffes på 2¢-rækken; 0/1¢")
        print(f"  viser robustheds-marginen. maxDD er største fald fra equity-top undervejs.")

        # ── Exit-mix pr. variant (2026-06-15): andel stop vs session-luk. Afslører
        # om et bredt ATR-gulv gør K2 til en "hold-til-sessionsluk"-strategi
        # (stoppet binder ikke længere). Rå-signaler (samme grundlag som per-trade-tabel). ──
        from collections import Counter
        print(f"\n{BOLD}{'='*92}{RESET}")
        print(f"{BOLD}  EXIT-MIX pr. variant — fordeling af exit-årsager (rå-signaler){RESET}")
        print(f"{BOLD}{'='*92}{RESET}")
        # trail_pct faar sin EGEN kolonne. Uden den forsvandt den nye trailing
        # take-profit ned i "andet", og hele pointen med T_trail_*-sweepet er
        # netop at se hvor mange hold-til-luk den konverterer til gevinstsikring.
        print(f"  {'variant':<16} {'trades':>7} | {'stop':>13} {'session_close':>15} "
              f"{'trail_pct':>13} {'andet':>13}")
        print(f"  {'-'*16} {'-'*7} | {'-'*13} {'-'*15} {'-'*13} {'-'*13}")
        for vk in variant_keys:
            trades = results_by_variant[vk]
            n = len(trades) or 1
            cnt = Counter(t["reason"] for t in trades)
            stop, sess = cnt.get("stop", 0), cnt.get("session_close", 0)
            trail = cnt.get("trail_pct", 0)
            other = len(trades) - stop - sess - trail
            print(f"  {vk:<16} {len(trades):>7} | "
                  f"{stop:>6} ({100*stop/n:>3.0f}%) {sess:>6} ({100*sess/n:>3.0f}%) "
                  f"{trail:>6} ({100*trail/n:>3.0f}%) {other:>6} ({100*other/n:>3.0f}%)")

        # ── Detaljer for live-variant (eller den ene valgte) ──
        focus = args.variant or LIVE_VARIANT_KEY
        ftrades = results_by_variant.get(focus, [])
        if ftrades and len(ftrades) <= 80:
            print(f"\n{BOLD}  HANDLER — {focus} (pnl er BRUTTO; se tabel ovenfor for netto){RESET}")
            print(f"  {'dato':<11} {'tid':>11} {'tkr':<6} {'entry':>7} {'exit':>7} "
                  f"{'pnl':>9} {'%':>7} {'reason':<13} bricks")
            for t in ftrades:
                c = GREEN if t["pnl"] > 0 else RED
                print(f"  {t['date']:<11} {t['entry_time']}-{t['exit_time']} {t['ticker']:<6} "
                      f"${t['entry']:>6.3f} ${t['exit']:>6.3f} {c}{t['pnl']:>+8.2f}{RESET} "
                      f"{t['pct']:>+6.1f}% {t['reason']:<13} {t['bricks']}")

        # ── CSV ──
        if ftrades:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = DATA_DIR / f"backtest_confluence2_{focus}_{ts}.csv"
            pd.DataFrame(ftrades).to_csv(path, index=False)
            logger.info(f"  {focus}: {len(ftrades)} handler → {path}")

    finally:
        ib.disconnect()
        logger.info("Frakoblet IBKR")
    return 0


def main():
    p = argparse.ArgumentParser(description="Backtest Konfluens 2 (1-min impuls)")
    p.add_argument("--universe", choices=["journal", "tickers", "file"], default="journal")
    p.add_argument("--tickers", type=str, help="Kommasepareret liste (for --universe tickers)")
    p.add_argument("--universe-file", type=str, help="JSON-fil fra build_historical_universe.py (for --universe file)")
    p.add_argument("--date", type=str)
    p.add_argument("--start", type=str)
    p.add_argument("--end", type=str)
    p.add_argument("--variant", type=str, choices=list(VARIANTS.keys()),
                   help="Kør kun én variant (default: sweep alle)")
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.warning("Afbrudt"); return 130


if __name__ == "__main__":
    raise SystemExit(main())
