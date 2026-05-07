"""
backtest_momentum.py  v3
────────────────────────
Tilføjet to ekstra filtre:
  1. Gap filter:   aktien skal gappe op mindst GAP_MIN% fra forrige lukke
  2. Volume filter: daglig volumen skal overstige DAILY_VOL_MIN

Kør:
    python backtest_momentum.py
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import time as dtime
import warnings
warnings.filterwarnings("ignore")

DATA_DIR = Path(__file__).parent / "data"

# ── Konfigurationer der testes ────────────────────────────────
CONFIGS = [
    # (stop%, target%, vol_mult, max_hour, gap_min%, daily_vol_min, label)
    (0.02, 0.04, 1.5, "10:30", 0.00, 0,         "Tæt (ingen gap-filter)"),
    (0.02, 0.04, 1.5, "10:30", 0.05, 0,         "Gap 5%+"),
    (0.02, 0.04, 1.5, "10:30", 0.10, 0,         "Gap 10%+"),
    (0.02, 0.04, 1.5, "10:30", 0.05, 500_000,   "Gap 5% + Vol 500k"),
    (0.02, 0.04, 1.5, "10:30", 0.10, 500_000,   "Gap 10% + Vol 500k"),
    (0.02, 0.05, 1.5, "11:00", 0.05, 500_000,   "Gap 5% target 5%"),
    (0.02, 0.06, 1.5, "11:00", 0.10, 500_000,   "Gap 10% target 6%"),
    (0.03, 0.06, 1.5, "11:00", 0.05, 1_000_000, "Gap 5% Vol 1M"),
]

# ── RSI ───────────────────────────────────────────────────────
def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def to_et(df):
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.tz_convert("America/New_York")

def _add_trade(trades, ticker, date, entry_ts, exit_ts,
               entry_price, exit_price, reason, capital, gap_pct):
    pnl_pct = (exit_price - entry_price) / entry_price
    trades.append({
        "ticker":       ticker,
        "date":         str(date),
        "entry_time":   entry_ts.strftime("%H:%M"),
        "exit_time":    exit_ts.strftime("%H:%M"),
        "entry_price":  round(entry_price, 4),
        "exit_price":   round(exit_price,  4),
        "exit_reason":  reason,
        "gap_pct":      round(gap_pct * 100, 2),
        "pnl_pct":      round(pnl_pct * 100, 2),
        "pnl_usd":      round(capital * pnl_pct, 2),
        "duration_min": max(1, int((exit_ts - entry_ts).total_seconds() / 60)),
    })

# ── Backtest kerne ────────────────────────────────────────────
def backtest_ticker(ticker, df, stop_pct, target_pct, vol_mult,
                    max_hour, gap_min, daily_vol_min, capital=10_000):
    trades  = []
    df      = df.copy()
    df["rsi"]     = calc_rsi(df["close"])
    df["vol_avg"] = df["volume"].rolling(20, min_periods=5).mean()
    orb_end = dtime(9, 44)
    max_t   = dtime(*[int(x) for x in max_hour.split(":")])

    # Daglige OHLCV til gap-beregning
    daily = df["close"].resample("1D").last().dropna()

    for date, day_df in df.groupby(df.index.date):
        market = day_df.sort_index().between_time("09:30", "16:00")
        if len(market) < 8:
            continue

        # ── Gap-filter ────────────────────────────────────────
        date_ts   = pd.Timestamp(date)
        prev_days = daily[daily.index.date < date]
        if len(prev_days) == 0:
            continue
        prev_close = float(prev_days.iloc[-1])
        first_open = float(market.iloc[0]["open"])
        gap_pct    = (first_open - prev_close) / prev_close
        if gap_pct < gap_min:
            continue

        # ── Daglig volumen-filter ─────────────────────────────
        daily_vol = float(market["volume"].sum())
        if daily_vol < daily_vol_min:
            continue

        # ── Opening Range ─────────────────────────────────────
        orb = market.between_time("09:30", "09:44")
        if orb.empty:
            continue
        orb_high = orb["high"].max()
        avg_vol  = market["volume"].mean()
        if avg_vol == 0 or np.isnan(avg_vol):
            continue

        in_pos = False
        entry_price = entry_ts = stop = target = None

        for ts, c in market.iterrows():
            t = ts.time()
            if in_pos:
                if c["low"] <= stop:
                    _add_trade(trades, ticker, date, entry_ts, ts,
                               entry_price, stop, "stop", capital, gap_pct)
                    in_pos = False
                elif c["high"] >= target:
                    _add_trade(trades, ticker, date, entry_ts, ts,
                               entry_price, target, "target", capital, gap_pct)
                    in_pos = False
                elif t >= max_t:
                    _add_trade(trades, ticker, date, entry_ts, ts,
                               entry_price, c["close"], "tid", capital, gap_pct)
                    in_pos = False
            else:
                if t <= orb_end or t >= max_t:
                    continue
                if (c["close"]  > orb_high and
                    c["volume"] >= avg_vol * vol_mult and
                    pd.notna(c["rsi"]) and c["rsi"] < 80):
                    in_pos      = True
                    entry_price = c["close"]
                    entry_ts    = ts
                    stop        = entry_price * (1 - stop_pct)
                    target      = entry_price * (1 + target_pct)
    return trades

def calc_stats(trades):
    if not trades:
        return None
    df   = pd.DataFrame(trades)
    wins = df[df["pnl_usd"] > 0]
    loss = df[df["pnl_usd"] < 0]
    gp   = wins["pnl_usd"].sum() if len(wins) > 0 else 0
    gl   = abs(loss["pnl_usd"].sum()) if len(loss) > 0 else 0
    eq   = df["pnl_usd"].cumsum()
    return {
        "trades":        len(df),
        "win_rate":      round(len(wins) / len(df) * 100, 1),
        "total_pnl":     round(df["pnl_usd"].sum(), 2),
        "profit_factor": round(gp / gl, 2) if gl > 0 else 9.99,
        "max_drawdown":  round((eq - eq.cummax()).min(), 2),
        "by_reason":     df["exit_reason"].value_counts().to_dict(),
    }

def load_all():
    data = {}
    for f in sorted(DATA_DIR.glob("*_5m_*.csv")):
        ticker = f.stem.split("_")[0]
        if ticker in ("SPY","QQQ","IWM"):
            continue
        try:
            df = pd.read_csv(f, index_col=0)
            df.columns = [c.lower() for c in df.columns]
            needed = {"open","high","low","close","volume"}
            if not needed.issubset(df.columns):
                continue
            for col in needed:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=list(needed))
            if len(df) < 20:
                continue
            df = to_et(df)
            data[ticker] = df
        except Exception as e:
            print(f"  Fejl: {f.name}: {e}")
    return data

def print_equity(trades, label):
    if not trades:
        return
    eq   = pd.DataFrame(trades)["pnl_usd"].cumsum().values
    h, w = 6, 54
    mn, mx = eq.min(), eq.max()
    rng  = mx - mn or 1
    cols = np.array_split(eq, min(w, len(eq)))
    grid = [[" "] * len(cols) for _ in range(h)]
    for xi, col in enumerate(cols):
        val = float(np.mean(col))
        yi  = min(h-1, max(0, int((val - mn) / rng * (h-1))))
        grid[h-1-yi][xi] = "█"
        for row in range(yi):
            grid[h-1-row][xi] = "▓"
    print(f"\n  Equity-kurve ({label}):")
    print(f"  ${mx:>8,.0f} ┐")
    for row in grid:
        print("           │" + "".join(row))
    print(f"  ${mn:>8,.0f} └" + "─" * len(cols))

def main():
    files = list(DATA_DIR.glob("*_5m_*.csv"))
    if not files:
        print("Ingen 5-minut data fundet. Kør: python download_data.py")
        return

    print(f"Indlæser {len(files)} filer...")
    data = load_all()
    print(f"Klar med {len(data)} tickers\n")

    W = 76
    print("═" * W)
    print("  PARAMETER SWEEP v3 — med gap-filter og volumen-filter")
    print("═" * W)
    print(f"  {'Config':28s} {'Trades':>6} {'Win%':>6} {'P&L':>10} {'PF':>5} {'MaxDD':>10}")
    print(f"  {'─'*28} {'─'*6} {'─'*6} {'─'*10} {'─'*5} {'─'*10}")

    results = []
    for stop, target, vol, max_h, gap_min, daily_vol, label in CONFIGS:
        all_trades = []
        for ticker, df in data.items():
            t = backtest_ticker(ticker, df, stop, target, vol,
                                max_h, gap_min, daily_vol)
            all_trades.extend(t)
        s = calc_stats(all_trades)
        if s:
            results.append((label, s, all_trades))
            pf_str = f"{s['profit_factor']:.2f}" if s["profit_factor"] < 9 else " ∞"
            print(f"  {label:28s} {s['trades']:>6} {s['win_rate']:>5.1f}% "
                  f"${s['total_pnl']:>9,.2f} {pf_str:>5} "
                  f"${s['max_drawdown']:>9,.2f}")
        else:
            print(f"  {label:28s}  ingen handler")

    print("═" * W)

    if not results:
        print("Ingen resultater.")
        return

    # Bedste konfiguration — højeste profit factor
    best_label, best_s, best_trades = max(results, key=lambda x: x[1]["profit_factor"])
    print(f"\n  ✓ Bedste: {best_label}")
    print(f"    Trades: {best_s['trades']}  "
          f"Win: {best_s['win_rate']}%  "
          f"P&L: ${best_s['total_pnl']:,.2f}  "
          f"PF: {best_s['profit_factor']:.2f}")
    print(f"    Exit-årsager: {best_s['by_reason']}")

    print_equity(best_trades, best_label)

    # Gem bedste resultater
    if best_trades:
        out = DATA_DIR / "backtest_results_best.csv"
        pd.DataFrame(best_trades).to_csv(out, index=False)
        print(f"\n  Gemt: {out}\n")

if __name__ == "__main__":
    main()
