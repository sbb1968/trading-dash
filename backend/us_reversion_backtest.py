#!/usr/bin/env python3
"""
us_reversion_backtest.py
────────────────────────
Backtest + variant-sweep for US-reversion.

Bruger SAMME rule.py og SAMME indicators.py som live-wrapperen
(algo_us_reversion.py), så en parameter der ser god ud her er den samme
parameter der handles i morgen. Kun ordre-håndtering, IBKR og forensik er
udeladt — beslutningerne er identiske.

DEN TO-TIDSRAMMEDE SIMULERING er det eneste ikke-trivielle her. Vi itererer
over 5m-bars og holder en markør ind i 15m-serien, som kun rykkes frem når en
15m-bar er FÆRDIG på den aktuelle 5m-bars tidspunkt. Dermed kan strategien
aldrig se en 15m-bar før den ville have været tilgængelig live — den klassiske
look-ahead-fælde ved blandede tidsrammer.

OMKOSTNING opgives i basispunkter rundtur, som i eureversion_backtest. MES har
en tick på 0,25 point ≈ 0,003% ved 7400 — plus kommission. 2 bp er et
konservativt skøn for rundturen. Læs ALTID resultatet ved 2 bp: forsvinder
edgen mellem 0 og 2 bp, er den et spread-artefakt.

Brug:
    python us_reversion_backtest.py                    # live-varianten alene
    python us_reversion_backtest.py --sweep            # alle varianter
    python us_reversion_backtest.py --variant z2_5     # én bestemt

Placering: C:\\Projects\\trading_dash\\backend\\us_reversion_backtest.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

import pytz

from indicators import macd as macd_of, cmf as cmf_of
from strategies.us_reversion import rule
from strategies.us_reversion.config import (
    SESSION_START_ET, ENTRY_CUTOFF_ET, FORCE_CLOSE_ET, LAST_SESSION_BAR_ET,
    LOOKBACK, CMF_LEN, MACD_FAST, MACD_SLOW, MACD_SIG,
    MIN_WARMUP_TRIG, MIN_WARMUP_BAND,
    VARIANTS, LIVE_VARIANT_KEY, UsReversionVariantConfig,
)

ET = pytz.timezone("America/New_York")

DEFAULT_DATA   = Path("data_harvest/mes_m2k_stitched")
OUTPUT_DIRNAME = "us_reversion_backtest_output"
COST_LEVELS_BP = [0.0, 1.0, 2.0, 3.0]
TRIG_SECONDS   = 300    # 5 min
BAND_SECONDS   = 900    # 15 min


@dataclass
class Bar:
    ts:     datetime
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float


@dataclass
class Trade:
    entry_ts: datetime
    exit_ts:  datetime
    entry:    float
    exit:     float
    reason:   str
    bars_held: int
    entry_z:  float | None
    armed_z:  float | None

    def gross_pct(self) -> float:
        """Long-only, så rå procent uden fortegns-vending."""
        if self.entry <= 0:
            return 0.0
        return (self.exit - self.entry) / self.entry * 100.0

    def net_pct(self, cost_bp: float) -> float:
        return self.gross_pct() - cost_bp * 0.01   # 1 bp = 0,01 %


# ═══════════════════════════════════════════════════════════════
#  Indlæsning
# ═══════════════════════════════════════════════════════════════

def load_bars(path: Path) -> list[Bar]:
    out: list[Bar] = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                out.append(Bar(
                    ts     = datetime.fromisoformat(row["timestamp"]),
                    open   = float(row["open"]),
                    high   = float(row["high"]),
                    low    = float(row["low"]),
                    close  = float(row["close"]),
                    volume = float(row.get("volume", 0) or 0),
                ))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda b: b.ts)


def contiguous(bars: list[Bar], i: int, need: int, seconds: int) -> bool:
    """
    Er de seneste `need` bars op til og med i sammenhængende?

    Vigtigt over kontrakt-rul og weekender: et hul i serien ville ellers lade
    z og CMF blive beregnet hen over en diskontinuitet, og bevægelsen mellem
    to kontrakter er ikke en bevægelse nogen kunne have handlet.
    """
    if i - need < 0:
        return False
    for k in range(i - need + 1, i + 1):
        if (bars[k].ts - bars[k - 1].ts).total_seconds() != seconds:
            return False
    return True


# ═══════════════════════════════════════════════════════════════
#  Simulering
# ═══════════════════════════════════════════════════════════════

def run_backtest(bars5: list[Bar], bars15: list[Bar],
                 cfg: UsReversionVariantConfig) -> list[Trade]:
    """
    Kør US-reversion over de to bar-serier.

    Tilstandsmaskinen er præcis live-wrapperens:
      15m-close under nedre bånd  → ARMERET
      15m-close tilbage i båndet  → afarmeret
      5m: to grønne + MACD op + CMF op (mens ARMERET) → LONG
      exit: stop / upper_z / trail (rule.check_exit) eller sessions-slut
    """
    trades: list[Trade] = []

    # Markør ind i 15m-serien: indekset på den SIDSTE 15m-bar der er færdig.
    # Rykkes kun frem — aldrig tilbage — og aldrig forbi den aktuelle 5m-bars tid.
    j = -1

    armed = False
    armed_z: float | None = None
    z15: float | None = None
    cmf_now: float | None = None
    cmf_prev: float | None = None

    pos_entry: float | None = None
    pos_entry_ts: datetime | None = None
    pos_entry_i: int = 0
    pos_entry_z: float | None = None
    pos_armed_z: float | None = None
    hh_close: float = 0.0

    for i, b5 in enumerate(bars5):
        t = b5.ts.astimezone(ET)
        tod = t.time()

        # ── Ryk 15m-markøren frem til alt der er FÆRDIGT nu ──
        # En 15m-bar med tidsstempel T er færdig når T+15min er passeret. Vi
        # bruger 5m-barens SLUT-tid, fordi det er det tidspunkt beslutningen
        # ville være truffet live.
        b5_end = b5.ts.timestamp() + TRIG_SECONDS
        moved = False
        while j + 1 < len(bars15) and bars15[j + 1].ts.timestamp() + BAND_SECONDS <= b5_end:
            j += 1
            moved = True

        if moved and j >= 0:
            # Genberegn 15m-afledte værdier — kun på sammenhængende historik.
            if contiguous(bars15, j, LOOKBACK, BAND_SECONDS):
                res = rule.compute_z([x.close for x in bars15[j - LOOKBACK + 1: j + 1]])
                z15 = res[0] if res else None
            else:
                z15 = None

            if contiguous(bars15, j, CMF_LEN + 1, BAND_SECONDS):
                rows = [{"high": x.high, "low": x.low, "close": x.close, "volume": x.volume}
                        for x in bars15[max(0, j - CMF_LEN * 2): j + 1]]
                cmf_now  = cmf_of(rows, CMF_LEN)
                cmf_prev = cmf_of(rows[:-1], CMF_LEN)
            else:
                cmf_now = cmf_prev = None

            # Armering opdateres KUN på en ny færdig 15m-bar (som live).
            if z15 is not None and pos_entry is None:
                if rule.is_break_below(z15, cfg.entry_z):
                    if not armed:
                        armed = True
                        armed_z = z15
                elif armed and rule.is_back_inside(z15, cfg.entry_z):
                    armed = False
                    armed_z = None

        # ── Åben position: exit-tjek ──
        if pos_entry is not None:
            hh_close = rule.update_hh(hh_close, b5.close)

            reason = None
            if tod >= LAST_SESSION_BAR_ET or tod >= FORCE_CLOSE_ET:
                reason = "session_end"
            else:
                reason = rule.check_exit(pos_entry, hh_close, b5.close, z15, cfg)

            if reason:
                trades.append(Trade(
                    entry_ts = pos_entry_ts, exit_ts = b5.ts,
                    entry = pos_entry, exit = b5.close, reason = reason,
                    bars_held = i - pos_entry_i,
                    entry_z = pos_entry_z, armed_z = pos_armed_z,
                ))
                pos_entry = None
                armed = False           # nyt brud kræves før næste handel
                armed_z = None
            continue

        # ── Flad: entry-tjek ──
        if not armed:
            continue
        if not (SESSION_START_ET <= tod < ENTRY_CUTOFF_ET):
            continue
        if i < MIN_WARMUP_TRIG:
            continue
        if not contiguous(bars5, i, MIN_WARMUP_TRIG, TRIG_SECONDS):
            continue

        closes5 = [x.close for x in bars5[i - MIN_WARMUP_TRIG + 1: i + 1]]
        m_now  = macd_of(closes5, MACD_FAST, MACD_SLOW, MACD_SIG)
        m_prev = macd_of(closes5[:-1], MACD_FAST, MACD_SLOW, MACD_SIG)

        ok, _ = rule.check_entry(
            bars5     = [{"open": bars5[i - 1].open, "close": bars5[i - 1].close},
                         {"open": b5.open,           "close": b5.close}],
            macd_now  = m_now.macd if m_now else None,
            macd_prev = m_prev.macd if m_prev else None,
            cmf_now   = cmf_now,
            cmf_prev  = cmf_prev,
            cfg       = cfg,
        )
        if not ok:
            continue

        pos_entry    = b5.close
        pos_entry_ts = b5.ts
        pos_entry_i  = i
        pos_entry_z  = z15
        pos_armed_z  = armed_z
        hh_close     = b5.close      # HH starter ved entry-prisen
        armed        = False         # armeringen er brugt op

    return trades


# ═══════════════════════════════════════════════════════════════
#  Statistik
# ═══════════════════════════════════════════════════════════════

def stats(trades: list[Trade], cost_bp: float) -> dict:
    pnls = [t.net_pct(cost_bp) for t in trades]
    n = len(pnls)
    if n == 0:
        return dict(n=0, wr=0.0, avg=0.0, sum=0.0, pf=0.0, worst=0.0)
    wins = [p for p in pnls if p > 0]
    gl = -sum(p for p in pnls if p < 0)
    gw = sum(wins)
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    return dict(n=n, wr=100 * len(wins) / n, avg=sum(pnls) / n,
                sum=sum(pnls), pf=pf, worst=min(pnls))


def fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def split_by_date(trades: list[Trade], frac: float) -> tuple[list[Trade], list[Trade]]:
    """Kronologisk IS/OOS-split på handelsdato (ikke på antal handler)."""
    if not trades:
        return [], []
    days = sorted({t.entry_ts.astimezone(ET).date() for t in trades})
    cut = days[int(len(days) * frac)] if len(days) > 1 else days[0]
    is_ = [t for t in trades if t.entry_ts.astimezone(ET).date() < cut]
    oos = [t for t in trades if t.entry_ts.astimezone(ET).date() >= cut]
    return is_, oos


def exit_mix(trades: list[Trade]) -> str:
    counts: dict[str, int] = {}
    for t in trades:
        counts[t.reason] = counts.get(t.reason, 0) + 1
    return "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))


# ═══════════════════════════════════════════════════════════════
#  Rapport
# ═══════════════════════════════════════════════════════════════

def report_variant(out, key: str, cfg: UsReversionVariantConfig,
                   trades: list[Trade], oos_frac: float, read_bp: float) -> None:
    w = out.write
    days = len({t.entry_ts.astimezone(ET).date() for t in trades}) or 1
    w(f"\n{'─' * 78}\n")
    w(f"  {key}  —  {cfg.name}\n")
    w(f"  z=±{cfg.entry_z}  stigning≥{cfg.rise_pct}%  stop={cfg.stop_pct}%  "
      f"trail={cfg.trail_pct}%  cmf+={cfg.require_cmf_positive}  zexit={cfg.exit_at_upper_z}\n")
    w(f"{'─' * 78}\n")
    if not trades:
        w("     INGEN handler — reglen udløste aldrig på disse data.\n")
        return

    avg_hold = sum(t.bars_held for t in trades) / len(trades)
    w(f"     handler={len(trades)}  {len(trades)/days:.2f}/dag  "
      f"snit-hold={avg_hold:.1f} 5m-bars\n")
    w(f"     exit-mix: {exit_mix(trades)}\n")
    w(f"      {'rundtur':>9}{'n':>6}{'WR%':>6}{'snit%':>9}{'sum%':>8}{'PF':>7}{'værst%':>9}\n")
    for bp in COST_LEVELS_BP:
        s = stats(trades, bp)
        w(f"      {bp:>7.0f}bp{s['n']:>6}{s['wr']:>6.0f}{s['avg']:>9.3f}"
          f"{s['sum']:>8.1f}{fmt_pf(s['pf']):>7}{s['worst']:>9.2f}\n")

    is_, oos = split_by_date(trades, oos_frac)
    si, so = stats(is_, read_bp), stats(oos, read_bp)
    w(f"     OOS @ {read_bp:.0f}bp:  in-sample n={si['n']} sum={si['sum']:+.1f}% "
      f"PF={fmt_pf(si['pf'])}  |  out-of-sample n={so['n']} sum={so['sum']:+.1f}% "
      f"PF={fmt_pf(so['pf'])}\n")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="US-reversion backtest (5m trigger + 15m bånd)")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA))
    ap.add_argument("--symbol", default="MES")
    ap.add_argument("--variant", default=None, choices=list(VARIANTS),
                    help=f"én variant (default: {LIVE_VARIANT_KEY})")
    ap.add_argument("--variants", default=None,
                    help="kommasepareret delmængde, fx 'rise0_12,r12_z150' — "
                         "til at isolere én parameter uden at køre hele gridet")
    ap.add_argument("--sweep", action="store_true", help="kør ALLE varianter")
    ap.add_argument("--oos-split", type=float, default=0.6)
    ap.add_argument("--cost-read-bp", type=float, default=2.0)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir

    p5  = data_dir / f"{args.symbol}_5min.csv"
    p15 = data_dir / f"{args.symbol}_15min.csv"
    for p in (p5, p15):
        if not p.exists():
            print(f"❌ Mangler datafil: {p}")
            return 1

    print(f"Indlæser {args.symbol}: 5m + 15m fra {data_dir} ...")
    bars5, bars15 = load_bars(p5), load_bars(p15)
    if not bars5 or not bars15:
        print("❌ Tomme bar-serier")
        return 1
    print(f"  {len(bars5):,} 5m-bars · {len(bars15):,} 15m-bars "
          f"({bars5[0].ts.date()} → {bars5[-1].ts.date()})\n")

    if args.sweep:
        keys = list(VARIANTS)
    elif args.variants:
        keys = [k.strip() for k in args.variants.split(",") if k.strip()]
        ukendte = [k for k in keys if k not in VARIANTS]
        if ukendte:
            print(f"❌ Ukendte varianter: {ukendte}\n   Gyldige: {sorted(VARIANTS)}")
            return 1
    elif args.variant:
        keys = [args.variant]
    else:
        keys = [LIVE_VARIANT_KEY]

    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / ("sweep.txt" if args.sweep else "summary.txt")

    lines: list[str] = []

    class _W:
        def write(self, s): lines.append(s)
    w = _W()

    w.write("=" * 78 + "\n")
    w.write("  US-REVERSION — long-only mean-reversion, MES, amerikansk session\n")
    w.write("=" * 78 + "\n")
    w.write(f"Data: {data_dir}   symbol: {args.symbol}\n")
    w.write(f"Session: {SESSION_START_ET}–{FORCE_CLOSE_ET} ET · "
            f"entry-cutoff {ENTRY_CUTOFF_ET} · lookback {LOOKBACK} (15m) · "
            f"CMF({CMF_LEN}) 15m · MACD({MACD_FAST},{MACD_SLOW},{MACD_SIG}) 5m\n")
    w.write(f"Periode: {bars5[0].ts.date()} → {bars5[-1].ts.date()}\n")
    w.write("Omkostning vist ved 0/1/2/3 bp rundtur. Forsvinder edge ved ~2 bp = "
            "spread-artefakt.\n")

    summary_rows = []
    for key in keys:
        cfg = VARIANTS[key]
        print(f"  kører {key} ...", flush=True)
        trades = run_backtest(bars5, bars15, cfg)
        report_variant(w, key, cfg, trades, args.oos_split, args.cost_read_bp)
        s = stats(trades, args.cost_read_bp)
        is_, oos = split_by_date(trades, args.oos_split)
        summary_rows.append((key, s, stats(oos, args.cost_read_bp)))

    if len(summary_rows) > 1:
        w.write("\n" + "=" * 78 + "\n")
        w.write(f"  SAMLET OVERSIGT @ {args.cost_read_bp:.0f} bp — sorteret efter OOS PF\n")
        w.write("=" * 78 + "\n")
        w.write(f"  {'variant':<16}{'n':>6}{'WR%':>6}{'sum%':>9}{'PF':>7}"
                f"{'OOS n':>7}{'OOS sum%':>10}{'OOS PF':>8}\n")
        for key, s, so in sorted(summary_rows, key=lambda r: -(r[2]['pf'] if r[2]['pf'] != float('inf') else 999)):
            w.write(f"  {key:<16}{s['n']:>6}{s['wr']:>6.0f}{s['sum']:>9.1f}"
                    f"{fmt_pf(s['pf']):>7}{so['n']:>7}{so['sum']:>10.1f}{fmt_pf(so['pf']):>8}\n")
        w.write("\n  LÆS DEN SÅDAN: en variant tæller kun hvis PF > 1 ved 2 bp OG holder\n")
        w.write("  out-of-sample. Ét godt IS-tal blandt seksten varianter er forventet støj —\n")
        w.write("  det er OOS-kolonnen der afgør, og helst med n stor nok til at betyde noget.\n")

    text = "".join(lines)
    print(text)
    out_path.write_text(text, encoding="utf-8")
    print(f"\nFil: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
