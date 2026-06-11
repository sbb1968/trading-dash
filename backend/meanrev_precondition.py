#!/usr/bin/env python3
"""
meanrev_precondition.py
═══════════════════════
Måler FORUDSÆTNINGEN for en intraday mean-reversion-strategi på de høstede
15-min data — FØR vi designer nogen regel. Spørgsmålet er det MODSATTE af
continuation-testen: vender index-futures tilbage mod gennemsnittet, eller ej?

To skala-frie/robuste mål, pr. SESSION (asiatisk/europæisk/amerikansk) og
pr. instrument, alt VOLUMEN-RENSET fra start (tynde bars narrer — det så vi
med futures-continuation, og de asiatiske/europæiske sessioner er tynde):

  1. LAG-1 AUTOKORRELATION af 15-min returns.
     < 0 = mean-reverting (GODT for os) · ~0 = møntkast · > 0 = trending.
  2. STRÆK → TILBAGEVENDEN. Når prisen er strukket k·σ fra et glidende
     gennemsnit, hvad er det GENNEMSNITLIGE næste-bar-afkast i retning MOD
     gennemsnittet? Positivt og stigende med stræk = ægte mean-reversion.

Læser CSV'erne fra data_harvest/ (timestamp,open,high,low,close,volume), kun
15-min-filerne (*_15min.csv). Rent offline, kun stdlib. Ingen IBKR.

Sessioner (ET): asiatisk 18–02, europæisk 02–08, amerikansk 09–16.
(≈ dansk tid: asiatisk 00–08, europæisk 08–14, amerikansk 15–22.)

Brug (på Sørens workstation, hvor data_harvest/ er kopieret hen):
    python meanrev_precondition.py
    python meanrev_precondition.py --data-dir data_harvest --min-vol-pct 50
    python meanrev_precondition.py --only ES,NQ,RTY

Output i ./meanrev_precondition_output/: summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\meanrev_precondition.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median, pstdev

OUTPUT_DIRNAME = "meanrev_precondition_output"
BAR_SECONDS = 900  # 15 min

# session -> ET-timer (start inkl., slut ekskl.); wrap håndteres for asiatisk
SESSIONS = {
    "asiatisk":   list(range(18, 24)) + list(range(0, 2)),   # 18–02 ET
    "europaeisk": [2, 3, 4, 5, 6, 7],                         # 02–08 ET
    "amerikansk": [9, 10, 11, 12, 13, 14, 15],                # 09–16 ET
}
MA_LOOKBACK = 20          # bars i glidende gennemsnit (≈5 timer på 15-min)
STRETCH_BUCKETS = [0.5, 1.0, 1.5, 2.0]  # z-score-tærskler


@dataclass
class Bar:
    ts: datetime
    close: float
    volume: float


def session_of(hour):
    for name, hours in SESSIONS.items():
        if hour in hours:
            return name
    return None


def load_15min(path):
    out = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            ts_raw = row["timestamp"]
            if "T" not in ts_raw:          # daily-fil — spring over
                return []
            try:
                ts = datetime.fromisoformat(ts_raw)
                out.append(Bar(ts, float(row["close"]), float(row.get("volume", 0) or 0)))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda b: b.ts)


def pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def build_returns(bars, min_vol):
    """[(ts, ret)] kun for sammenhængende 15-min-par over volumen-tærsklen."""
    out, prev = [], None
    for b in bars:
        if min_vol is not None and b.volume < min_vol:
            prev = None
            continue
        if prev is not None and (b.ts - prev.ts).total_seconds() == BAR_SECONDS and prev.close > 0:
            out.append((b.ts, (b.close - prev.close) / prev.close))
        prev = b
    return out


def autocorr_by_session(rets):
    """Lag-1 autokorrelation pr. session (par bucketes på første bars session)."""
    xs = defaultdict(list)
    ys = defaultdict(list)
    for i in range(len(rets) - 1):
        ts0, r0 = rets[i]
        ts1, r1 = rets[i + 1]
        if (ts1 - ts0).total_seconds() != BAR_SECONDS:
            continue
        s = session_of(ts0.hour)
        if s:
            xs[s].append(r0)
            ys[s].append(r1)
    return {s: (len(xs[s]), pearson(xs[s], ys[s])) for s in xs}


def stretch_revert_by_session(bars, min_vol):
    """Når |z| >= tærskel, gennemsnitligt næste-bar-afkast i retning MOD gennemsnittet.
    z = (close - MA) / std(close, lookback). Returnér {session: {tærskel: (n, mean_revert_%)}}."""
    # behold kun likvide bars, men kræv sammenhæng til MA-vinduet
    out = {s: {k: [0, 0.0] for k in STRETCH_BUCKETS} for s in SESSIONS}
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    for i in range(MA_LOOKBACK, len(bars) - 1):
        # vindue + nuværende + næste skal være likvide og sammenhængende
        window = bars[i - MA_LOOKBACK:i]
        if min_vol is not None and (bars[i].volume < min_vol or bars[i + 1].volume < min_vol):
            continue
        if (bars[i + 1].ts - bars[i].ts).total_seconds() != BAR_SECONDS:
            continue
        wc = [b.close for b in window]
        ma = sum(wc) / len(wc)
        sd = pstdev(wc)
        if sd <= 0 or bars[i].close <= 0:
            continue
        z = (bars[i].close - ma) / sd
        nxt = (bars[i + 1].close - bars[i].close) / bars[i].close * 100.0
        # afkast i retning MOD gennemsnittet: hvis strukket OP (z>0), reversion = ned = -nxt
        revert = -nxt if z > 0 else nxt
        s = session_of(bars[i].ts.hour)
        if not s:
            continue
        for k in STRETCH_BUCKETS:
            if abs(z) >= k:
                out[s][k][0] += 1
                out[s][k][1] += revert
    return out


def main():
    ap = argparse.ArgumentParser(description="Mean-reversion forudsætningstest (15-min, pr. session)")
    ap.add_argument("--data-dir", default="data_harvest")
    ap.add_argument("--only", default=None, help="kommasepareret delmængde, fx ES,NQ,RTY")
    ap.add_argument("--min-vol-pct", type=float, default=50.0, help="behold bars med volumen >= percentil")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    only = set(s.strip().upper() for s in args.only.split(",")) if args.only else None
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  MEAN-REVERSION FORUDSÆTNINGSTEST — 15-min, pr. session, volumen-renset")
    emit("=" * 78)
    emit(f"Data: {data_dir}   vol-filter: >= {args.min_vol_pct:.0f}-percentil")
    emit("Søger NEGATIV autokorr + POSITIV stræk-tilbagevenden = mean-reversion-edge.")
    emit("Sessioner (ET): asiatisk 18–02 · europæisk 02–08 · amerikansk 09–16")
    emit("")

    if not data_dir.exists():
        emit(f"Mappen {data_dir} findes ikke. Kopiér data_harvest/ hertil først.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    files = sorted(data_dir.glob("*_15min.csv"))
    if not files:
        emit("Ingen *_15min.csv filer fundet i data-mappen.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # opsummerings-akkumulator pr. (session) på tværs af index-futures
    verdict_rows = []

    for path in files:
        label = path.name[:-len("_15min.csv")]
        if only and label.upper() not in only:
            continue
        bars = load_15min(path)
        if len(bars) < MA_LOOKBACK + 50:
            emit(f"── {label}: for få bars ({len(bars)}) — springer over\n")
            continue

        # volumen-tærskel (percentil af positiv volumen)
        pos = sorted(b.volume for b in bars if b.volume > 0)
        thr = pos[min(len(pos) - 1, int(len(pos) * args.min_vol_pct / 100))] if pos else None
        # hvis instrumentet stort set ingen volumen har (fx FX MIDPOINT), kør uden filter
        if thr is None or thr <= 0:
            thr = None

        rets = build_returns(bars, thr)
        ac = autocorr_by_session(rets)
        sr = stretch_revert_by_session(bars, thr)

        emit("─" * 78)
        emit(f"  {label}   (bars={len(bars)}, vol-tærskel={'ingen' if thr is None else int(thr)})")
        emit("─" * 78)
        emit(f"     {'session':>11}{'n-par':>8}{'autokorr':>10}   |  stræk→tilbagevenden (snit % næste bar)")
        for s in ("asiatisk", "europaeisk", "amerikansk"):
            n, a = ac.get(s, (0, None))
            astr = f"{a:+.3f}" if a is not None else "  —  "
            # stræk-tal ved z>=1.0 og z>=2.0
            def cell(k):
                cnt, tot = sr[s][k]
                return f"{(tot/cnt):+.3f}%(n={cnt})" if cnt else "—"
            emit(f"     {s:>11}{n:>8}{astr:>10}   |  z≥1.0: {cell(1.0):<18} z≥2.0: {cell(2.0)}")
            verdict_rows.append((label, s, a, sr[s]))
        emit("")

    # ── samlet dom ──
    emit("─" * 78)
    emit("  DOM — er der en intraday mean-reversion-forudsætning?")
    emit("─" * 78)
    emit("  GODT tegn: autokorr klart < 0 OG stræk-tilbagevenden positiv+stigende med z.")
    emit("  Møntkast: autokorr ~0 og stræk-tilbagevenden ~0 (som FX/continuation).")
    emit("")
    # pr. session: tæl instrumenter med negativ autokorr og positiv z≥2 tilbagevenden
    by_sess = defaultdict(lambda: {"neg_ac": 0, "pos_rev": 0, "tot": 0})
    for label, s, a, srs in verdict_rows:
        d = by_sess[s]
        d["tot"] += 1
        if a is not None and a < -0.02:
            d["neg_ac"] += 1
        cnt, tot = srs[2.0]
        if cnt >= 20 and tot / cnt > 0:
            d["pos_rev"] += 1
    for s in ("asiatisk", "europaeisk", "amerikansk"):
        d = by_sess.get(s, {"neg_ac": 0, "pos_rev": 0, "tot": 0})
        emit(f"  {s:>11}: {d['neg_ac']}/{d['tot']} instrumenter m. negativ autokorr, "
             f"{d['pos_rev']}/{d['tot']} m. positiv stræk→tilbagevenden (z≥2)")
    emit("")
    emit("  → Stærkeste (session, instrument) = dér vi designer den konkrete regel.")
    emit("  → Hvis alt er ~0: index-futures mean-reverter ikke intraday → genovervej klasse.")
    emit("  (Forudsætning, ikke P&L — kun en backtest med tick-omkostninger afgør penge.)")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    emit("→ Send mig summary.txt.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())