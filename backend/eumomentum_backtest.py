#!/usr/bin/env python3
r"""
eumomentum_backtest.py — EUmomentum LONG-only strategi-backtest (fra bunden).
════════════════════════════════════════════════════════════════════════════════════════
Ny strategi (IKKE relateret til eumomentum_separability/model/filter — de er analyser).
LONG-only paa MES + M2K, ~24t session. Baand som EUreversion: mean=SMA(LOOKBACK) paa closes,
baand = mean ± BAND_Z·pstdev (default 30, 2.0).

ENTRY (dip-og-reclaim):
  1. En candle lukker NED under mean (down-cross)         -> armér setup, reference = mean.
  2. Falder en senere candle-close under NEDRE baand      -> reference skifter til nedre baand.
  3. Bekraeftelse inden for CONFIRM_WINDOW bars: candle 1 LUKKER over reference, candle 2
     har HELE KROPPEN (open OG close) over reference       -> koeb LONG paa candle 2's close.
  (Vi koeber ogsaa selvom prisen er under mean, hvis nedre-baand-triggeren har vaeret der.)

EXIT (hvad end foerst):
  - 2 roede candles i traek, ELLER
  - 1 roed candle hvis krop > EXIT_SIGMA·σ (default 0.5σ = ¼ af mean->baand-afstanden), ELLER
  - TVANGSLUK paa sidste bar foer en session-pause (daglig CME-halt / weekend / kontrakt-roll)
    — en LONG holdes aldrig natten over eller paa tvaers af pausen.

RELATIV VOLUMEN (kun M2K): candles med RVOL_slot < RVOL_MIN (0.3) IGNORERES fuldstaendigt —
i trigger, bekraeftelse OG exit-taelling (undtagen tvangsluk, der altid fyrer). RVOL_slot =
bar-volumen / point-in-time expanding median for samme tids-slot (ingen look-ahead, warm-up 20).

Data: data_harvest/mes_m2k_stitched/{SYM}_{tf}min.csv (kontinuerlig; roll-gaps = session-pauser,
haandteres af contiguity + tvangsluk). Foerste backtest: 2026-01-01..2026-06-30. Sweep 5/10/15-min.
KOEBER INTET. Rent offline.

Koersel:
    python eumomentum_backtest.py
    python eumomentum_backtest.py --timeframes 5,10,15 --start 2026-01-01 --end 2026-06-30
    python eumomentum_backtest.py --confirm-window 8 --trades-csv ut.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from bisect import insort
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytz

ET = pytz.timezone("America/New_York")

# ── Laaste defaults (som EUreversion + spec) ──────────────────────
LOOKBACK       = 30
BAND_Z         = 2.0
RVOL_MIN       = 0.3
RVOL_WARMUP    = 20
CONFIRM_WINDOW = 10          # bekraeftelse skal ske inden for saa mange (valide) bars efter trigger
EXIT_SIGMA     = 0.5         # 1 roed krop > 0.5σ -> exit
COST_BP        = 2.0
RVOL_FILTER    = {"M2K"}     # kun M2K volumen-filtreres (spec: "undtagelse for M2K")
DATA_DIR       = "data_harvest/mes_m2k_stitched"
SYMBOLS        = ["MES", "M2K"]


@dataclass
class Bar:
    ts: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class Trade:
    inst: str
    entry_ts: datetime
    exit_ts: datetime
    entry: float
    exit: float
    reason: str
    bars_held: int

    def net_pct(self, cost_bp: float) -> float:
        if self.entry <= 0:
            return 0.0
        gross = (self.exit - self.entry) / self.entry * 100.0   # LONG
        return gross - cost_bp * 0.01


# ═══════════════════════════════════════════════════════════════════
# Data + afledte serier
# ═══════════════════════════════════════════════════════════════════
def load_bars(path: Path) -> list[Bar]:
    out = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            ts = r.get("timestamp", "")
            if "T" not in ts:
                return []
            try:
                out.append(Bar(datetime.fromisoformat(ts), float(r["open"]), float(r["high"]),
                               float(r["low"]), float(r["close"]), float(r.get("volume", 0) or 0)))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda b: b.ts)


def _mean_sd(vals) -> tuple[float, float]:
    n = len(vals); m = sum(vals) / n
    var = sum((x - m) * (x - m) for x in vals) / n
    return m, var ** 0.5


def bands(bars: list[Bar], lookback: int, band_z: float, tf_sec: int):
    """Pr. bar: (mean, sd, upper, lower) hvis de sidste `lookback` bars er sammenhaengende,
    ellers None. Samme beregning som EUreversion (aritmetisk mean + population-std)."""
    n = len(bars)
    m = [None] * n; s = [None] * n; up = [None] * n; lo = [None] * n
    for i in range(lookback - 1, n):
        ok = True
        for k in range(i - lookback + 2, i + 1):
            if (bars[k].ts - bars[k - 1].ts).total_seconds() != tf_sec:
                ok = False; break
        if not ok:
            continue
        cl = [bars[k].c for k in range(i - lookback + 1, i + 1)]
        mm, ss = _mean_sd(cl)
        m[i] = mm; s[i] = ss; up[i] = mm + band_z * ss; lo[i] = mm - band_z * ss
    return m, s, up, lo


def rvol_series(bars: list[Bar], warmup: int):
    """RVOL_slot = vol / point-in-time expanding median for samme tids-slot (ET HH:MM).
    Kun tidligere bars indgaar (ingen look-ahead). None indtil `warmup` tidligere pr. slot."""
    n = len(bars)
    rv = [None] * n
    seen: dict[str, list[float]] = defaultdict(list)   # sorterede volumener SET indtil nu pr. slot
    for i, b in enumerate(bars):
        slot = b.ts.astimezone(ET).strftime("%H:%M")
        lst = seen[slot]
        if len(lst) >= warmup:
            k = len(lst)
            med = lst[k // 2] if k % 2 else (lst[k // 2 - 1] + lst[k // 2]) / 2.0
            rv[i] = (b.v / med) if med > 0 else None
        insort(lst, b.v)
    return rv


# ═══════════════════════════════════════════════════════════════════
# Kerne — tilstandsmaskine (LONG-only)
# ═══════════════════════════════════════════════════════════════════
def run_backtest(inst: str, bars: list[Bar], tf_sec: int, cfg) -> list[Trade]:
    n = len(bars)
    m, s, up, lo = bands(bars, cfg.lookback, cfg.band_z, tf_sec)
    do_rvol = inst in RVOL_FILTER
    rv = rvol_series(bars, cfg.rvol_warmup) if do_rvol else [None] * n

    trades: list[Trade] = []
    pos = None                    # (entry, entry_ts, entry_i)
    armed = False; mode = "mean"; bars_since = 0; pending_first = False
    red_streak = 0; peak = 0.0                            # peak = hoejeste close siden entry (trailing)
    prev_valid_close = None; prev_valid_above = False   # til mean-down-cross-detektion
    prev_valid_below_lb = False                          # til nedre-baand-kryds-detektion

    for i in range(n):
        b = bars[i]
        mi = m[i]
        gap_after = (i == n - 1) or ((bars[i + 1].ts - b.ts).total_seconds() != tf_sec)
        valid = (not do_rvol) or (rv[i] is None) or (rv[i] >= cfg.rvol_min)   # None=warmup -> valid

        # ── I POSITION: exit-haandtering ──
        if pos is not None:
            exit_now, reason = False, ""
            if cfg.trail_sigma > 0:                       # TRAILING STOP: exit ved close <= peak - K·σ
                if valid and mi is not None:
                    if b.c > peak:
                        peak = b.c
                    if b.c <= peak - cfg.trail_sigma * s[i]:
                        exit_now, reason = True, "trail"
            elif valid and mi is not None:                # original: 2-roed / big-red
                is_red = b.c < b.o
                if cfg.big_red and is_red and (b.o - b.c) > cfg.exit_sigma * s[i]:
                    exit_now, reason = True, "big_red"
                elif is_red:
                    red_streak += 1
                    if red_streak >= 2:
                        exit_now, reason = True, "two_red"
                else:
                    red_streak = 0
            if not exit_now and gap_after:               # tvangsluk foer pause (uanset RVOL)
                exit_now, reason = True, "force_close"
            if exit_now:
                trades.append(Trade(inst, pos[1], b.ts, pos[0], b.c, reason, i - pos[2]))
                pos = None; red_streak = 0
            continue

        # ── FLAT: entry-tilstandsmaskine ──
        if mi is None:                    # ingen baand (efter pause / warmup) -> nulstil kontekst
            armed = False; pending_first = False
            prev_valid_close = None; prev_valid_below_lb = False
            continue
        if not valid:                     # ignorér candlen fuldstaendigt
            continue
        if gap_after:                     # sidste bar foer pause: intet at armere/bekraefte over gap
            armed = False; pending_first = False
            prev_valid_close = b.c; prev_valid_above = (b.c >= mi); prev_valid_below_lb = (b.c < lo[i])
            continue

        # Nedre-baand-kryds: OEJEBLIKKELIG entry hvis lb_immediate (ingen bekraeftelse).
        lb_cross = (prev_valid_close is not None) and (not prev_valid_below_lb) and (b.c < lo[i])
        entered = False
        if cfg.lb_immediate and lb_cross:
            pos = (b.c, b.ts, i)             # KOEB LONG straks paa nedre-baand-krydset
            armed = False; pending_first = False; red_streak = 0; peak = b.c
            entered = True

        if not entered:
            if not armed:
                # mean-down-cross: forrige VALIDE candle laa >= sit mean, denne lukker < mean
                if prev_valid_close is not None and prev_valid_above and b.c < mi:
                    armed = True; mode = "mean"; bars_since = 0; pending_first = False
            else:
                bars_since += 1
                if bars_since > cfg.confirm_window:
                    armed = False; pending_first = False
                else:
                    # original reference-skift til nedre baand gaelder KUN naar lb ikke er umiddelbar
                    if (not cfg.lb_immediate) and b.c < lo[i]:
                        mode = "lower_band"
                    ref = mi if (cfg.lb_immediate or mode == "mean") else lo[i]
                    close_above = b.c > ref
                    body_above = (b.o > ref) and (b.c > ref)
                    if pending_first and body_above:
                        pos = (b.c, b.ts, i)     # KOEB LONG paa candle 2's close (mean-bekraeftelse)
                        armed = False; pending_first = False; red_streak = 0; peak = b.c
                    else:
                        pending_first = close_above

        prev_valid_close = b.c; prev_valid_above = (b.c >= mi); prev_valid_below_lb = (b.c < lo[i])

    return trades


# ═══════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════
def stats(trades: list[Trade], cost_bp: float) -> dict:
    p = [t.net_pct(cost_bp) for t in trades]
    n = len(p)
    if n == 0:
        return dict(n=0, wr=0.0, avg=0.0, sum=0.0, pf=0.0, worst=0.0)
    wins = [x for x in p if x > 0]
    gl = -sum(x for x in p if x < 0)
    pf = (sum(wins) / gl) if gl > 0 else (float("inf") if wins else 0.0)
    return dict(n=n, wr=100 * len(wins) / n, avg=sum(p) / n, sum=sum(p), pf=pf, worst=min(p))


def reason_breakdown(trades: list[Trade]) -> str:
    c = defaultdict(int)
    for t in trades:
        c[t.reason] += 1
    return "  ".join(f"{k}:{v}" for k, v in sorted(c.items()))


# ═══════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="EUmomentum LONG-only backtest (MES/M2K).")
    ap.add_argument("--data-dir", default=DATA_DIR)
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--timeframes", default="5,10,15", help="min, fx 5,10,15 (sweep)")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--band-z", type=float, default=BAND_Z)
    ap.add_argument("--rvol-min", type=float, default=RVOL_MIN)
    ap.add_argument("--rvol-warmup", type=int, default=RVOL_WARMUP)
    ap.add_argument("--confirm-window", type=int, default=CONFIRM_WINDOW)
    ap.add_argument("--exit-sigma", type=float, default=EXIT_SIGMA)
    ap.add_argument("--cost-bp", type=float, default=COST_BP)
    ap.add_argument("--lb-immediate", action="store_true",
                    help="nedre-baand-kryds = OEJEBLIKKELIG entry (ingen bekraeftelse)")
    ap.add_argument("--no-big-red", action="store_true", help="slaa ½σ big-red-exit fra")
    ap.add_argument("--trail-sigma", type=float, default=0.0,
                    help="trailing stop: exit ved close <= peak - K·σ (0 = brug 2-roed/big-red)")
    ap.add_argument("--trades-csv", default=None, help="skriv alle handler til CSV")
    a = ap.parse_args()
    a.big_red = not a.no_big_red
    a.lookback = a.lookback; a.confirm_window = a.confirm_window
    a.band_z = a.band_z; a.rvol_min = a.rvol_min; a.rvol_warmup = a.rvol_warmup
    a.exit_sigma = a.exit_sigma

    data_dir = Path(a.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    tfs = [int(x) for x in a.timeframes.split(",") if x.strip()]
    start = datetime.strptime(a.start, "%Y-%m-%d").date()
    end = datetime.strptime(a.end, "%Y-%m-%d").date()

    print("=" * 82)
    print("  EUmomentum — LONG-only backtest (MES/M2K)")
    lb_txt = "nedre-baand=OEJEBLIKKELIG entry" if a.lb_immediate else "nedre-baand=bekraeftelse"
    if a.trail_sigma > 0:
        exit_txt = f"TRAILING peak-{a.trail_sigma}σ / tvangsluk"
    else:
        exit_txt = "2-roed" + ("/krop>%sσ" % a.exit_sigma if a.big_red else " (big-red FRA)") + "/tvangsluk"
    print(f"  Baand: mean=SMA({a.lookback}) ±{a.band_z}σ · mean-bekraeft<= {a.confirm_window} bars · "
          f"{lb_txt} · exit {exit_txt} · RVOL<{a.rvol_min} ignoreres ({','.join(sorted(RVOL_FILTER))})")
    print(f"  Periode: {start} .. {end}   cost {a.cost_bp}bp   sweep {tfs} min")
    print("=" * 82)

    all_trades_csv = []
    for tf in tfs:
        tf_sec = tf * 60
        print(f"\n── {tf}-min ─────────────────────────────────────────────────────────────────────")
        print(f"  {'inst':6}{'n':>5}{'win%':>7}{'avg%':>8}{'sum%':>9}{'PF':>7}{'worst%':>8}   exit-fordeling")
        combined = []
        for sym in symbols:
            p = data_dir / f"{sym}_{tf}min.csv"
            bars = load_bars(p) if p.exists() else []
            if not bars:
                print(f"  {sym:6} (ingen data i {p.name})"); continue
            trades = run_backtest(sym, bars, tf_sec, a)
            trades = [t for t in trades if start <= t.entry_ts.astimezone(ET).date() <= end]
            combined += trades
            all_trades_csv += [(tf, t) for t in trades]
            st = stats(trades, a.cost_bp)
            pf = "inf" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
            print(f"  {sym:6}{st['n']:>5}{st['wr']:>6.1f}{st['avg']:>+8.3f}{st['sum']:>+9.1f}"
                  f"{pf:>7}{st['worst']:>+8.2f}   {reason_breakdown(trades)}")
        st = stats(combined, a.cost_bp)
        pf = "inf" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
        print(f"  {'BEGGE':6}{st['n']:>5}{st['wr']:>6.1f}{st['avg']:>+8.3f}{st['sum']:>+9.1f}"
              f"{pf:>7}{st['worst']:>+8.2f}")

    if a.trades_csv and all_trades_csv:
        with open(a.trades_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["tf_min", "inst", "entry_ts", "exit_ts", "entry", "exit", "reason",
                        "bars_held", "net_pct_2bp"])
            for tf, t in all_trades_csv:
                w.writerow([tf, t.inst, t.entry_ts.isoformat(), t.exit_ts.isoformat(),
                            t.entry, t.exit, t.reason, t.bars_held, round(t.net_pct(a.cost_bp), 4)])
        print(f"\n  Skrev {len(all_trades_csv)} handler -> {a.trades_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
