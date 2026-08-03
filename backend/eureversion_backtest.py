#!/usr/bin/env python3
"""
eureversion_backtest.py
═══════════════════════
Den validerede backtest for EUREVERSION-strategien (algo_europa_reversion.py).
Backtester den konkrete mean-reversion-regel som forudsætningstesten åbnede for:
index/rente/metal-futures der mean-reverter i den EUROPÆISKE session (02–08 ET
≈ 08–14 dansk tid). Det her er make-or-break — det afgør om reversionen er ÆGTE
(overlever spænd) eller bare bid-ask-bounce (dør når vi betaler spændet).

Regel (defaults — IKKE optimeret; sweep kommer kun hvis defaults er lovende):
  • z = (close − MA20) / std20, beregnet på sammenhængende 15-min bars.
  • ENTRY (kun i sessionen, kun på likvide bars): |z| ≥ entry_z (2.0).
      z ≥ +entry_z → SHORT (vædder på fald mod gennemsnittet)
      z ≤ −entry_z → LONG  (vædder på stigning mod gennemsnittet)
  • EXIT (hvad end først): tilbage til gennemsnittet (|z| ≤ exit_z, 0.5) ·
      stop hvis stræk fortsætter (|z| ≥ stop_z, 3.5) · tvangsluk ved sessions-slut
      (intraday — holder ALDRIG over sessionen).
  • Én position pr. instrument ad gangen.

OMKOSTNINGS-SENSITIVITET er kernen: P&L vises ved 0/1/2/3 bp rundtur. For
index-micros (MES/MNQ) er realistisk rundtur ~1.5–2 bp (1 tick spænd + kommission).
HVIS edgen forsvinder ved 2 bp = bounce-artefakt. HVIS den holder = ægte.

Plus OUT-OF-SAMPLE-split (in-sample først, OOS sidst) og handler/dag.

Rent offline på de høstede 15-min CSV'er. Kun stdlib. Ingen IBKR.

Brug (på Sørens workstation):
    python eureversion_backtest.py
    python eureversion_backtest.py --only MES
    python eureversion_backtest.py --session european --entry-z 2.0 --exit-z 0.5 --stop-z 3.5
    python eureversion_backtest.py --sweep            # let parametersweep (efter defaults)

Output i ./eureversion_backtest_output/: summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\eureversion_backtest.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
# Den validerede z-regel bor nu i pakken (delt med live-wrapperen
# algo_europa_reversion.py, så backtest og live aldrig divergerer). Denne
# backtest delegerer al beslutningslogik hertil.
from strategies.europa_reversion.rule import (
    compute_z as _rule_compute_z,
    entry_side as _rule_entry_side,
    exit_reason as _rule_exit_reason,
)
# Låste live-parametre — config.py er sandhedskilden. Exit-z-sweepet pinner ALT
# undtagen exit_z hertil, så et resultat kan overføres direkte til live-reglen.
from strategies.europa_reversion.config import (
    LOOKBACK as CFG_LOOKBACK,
    ENTRY_Z as CFG_ENTRY_Z,
    STOP_Z as CFG_STOP_Z,
    INSTRUMENTS as CFG_INSTRUMENTS,
)

OUTPUT_DIRNAME = "eureversion_backtest_output"
BAR_SECONDS = 900  # 15 min

SESSIONS = {
    "asiatisk":   list(range(18, 24)) + list(range(0, 2)),
    "europaeisk": [2, 3, 4, 5, 6, 7],
    "amerikansk": [9, 10, 11, 12, 13, 14, 15],
}
DEFAULT_INSTR = ["MES", "MNQ", "M2K"]
COST_LEVELS_BP = [0.0, 1.0, 2.0, 3.0]   # rundtur i basispunkter


@dataclass
class Bar:
    ts: datetime
    close: float
    volume: float


@dataclass
class Trade:
    entry_ts: datetime
    exit_ts: datetime
    side: str          # "long" | "short"
    entry: float
    exit: float
    reason: str
    bars_held: int

    def gross_pct(self):
        if self.entry <= 0:
            return 0.0
        raw = (self.exit - self.entry) / self.entry * 100.0
        return raw if self.side == "long" else -raw

    def net_pct(self, cost_bp):
        return self.gross_pct() - cost_bp * 0.01   # 1 bp = 0.01 %


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
            if "T" not in ts_raw:
                return []
            try:
                out.append(Bar(datetime.fromisoformat(ts_raw), float(row["close"]),
                               float(row.get("volume", 0) or 0)))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda b: b.ts)


def contiguous(bars, i, lookback):
    """Er de seneste lookback+1 bars (i−lookback..i) alle 15-min sammenhængende?"""
    if i - lookback < 0:
        return False
    for k in range(i - lookback + 1, i + 1):
        if (bars[k].ts - bars[k - 1].ts).total_seconds() != BAR_SECONDS:
            return False
    return True


def zscore(bars, i, lookback):
    """z over de seneste lookback closes — delegerer til den delte regel
    (strategies.europa_reversion.rule.compute_z). Returnerer float eller None."""
    closes = [b.close for b in bars[i - lookback + 1:i + 1]]
    res = _rule_compute_z(closes)
    return res[0] if res else None


def last_in_session(bars, i, session):
    if i + 1 >= len(bars):
        return True
    nxt = bars[i + 1]
    if session_of(nxt.ts.hour) != session:
        return True
    if (nxt.ts - bars[i].ts).total_seconds() != BAR_SECONDS:
        return True
    return False


def efficiency_ratio(bars, i, lookback):
    """Kaufman efficiency ratio over lookback: |netto bevaegelse| / |samlet sti|.

    1,0 = ren trend (hver bar i samme retning) → fortsaettelse er sandsynlig.
    0,0 = ren stoej (prisen ender hvor den startede) → mean reversion virker.

    Bruges som TREND-VAGT: en z-udstraekning kan lige saa godt vaere begyndelsen
    paa en trend som en overdrivelse der skal tilbage. ER skelner de to uden at
    kraeve andet end closes.
    """
    seg = bars[i - lookback + 1: i + 1]
    if len(seg) < 2:
        return None
    netto = abs(seg[-1].close - seg[0].close)
    sti = sum(abs(seg[k].close - seg[k - 1].close) for k in range(1, len(seg)))
    if sti <= 0:
        return None
    return netto / sti


def _vendt_tilbage(bars, i, side) -> bool:
    """Lukkede bar i tilbage MOD middelvaerdien i forhold til bar i-1?

    short = prisen er straakket OP, saa en vending er en LAVERE luk.
    long  = prisen er straakket NED, saa en vending er en HOEJERE luk.
    """
    if i < 1:
        return False
    if side == "short":
        return bars[i].close < bars[i - 1].close
    return bars[i].close > bars[i - 1].close


def run_backtest(bars, session, lookback, entry_z, exit_z, stop_z, min_vol, entry_hours=None,
                 confirm=None, confirm_max_wait=4, max_er=None):
    """Returnér liste af Trade. Intraday: tvangsluk ved sessions-slut.
    entry_hours: valgfri maengde af ET-timer hvor entries tillades (default None = hele
    sessionen; additivt, aendrer ikke standard-adfaerd).

    ENTRY-BEKRAEFTELSE (tilfoejet 3/8-2026, default None = uaendret adfaerd):
      confirm=None         — entry straks naar |z| >= entry_z (den oprindelige regel)
      confirm="same_bar"   — kraev at SELVE udstraekningsbaren lukkede tilbage mod middel
      confirm="z_contract" — kraev at |z| er MINDRE end forrige bars |z| (straekket skrumper)
      confirm="armed"      — |z| >= entry_z ARMERER; entry sker foerst paa en SENERE bar
                             der lukker tilbage mod middel (max confirm_max_wait bars;
                             armeringen annulleres hvis |z| falder under exit_z imens)

    max_er: trend-vagt. Spring entry over hvis efficiency ratio > denne vaerdi
      (høj ER = trend, ikke overdrivelse). None = ingen vagt.
    """
    trades = []
    pos = None  # dict: side, entry, entry_ts, entry_i
    armed = None  # dict: side, i  — kun brugt af confirm="armed"
    n = len(bars)
    for i in range(lookback, n):
        bar = bars[i]
        # ── styr åben position ──
        if pos is not None:
            z = zscore(bars, i, lookback) if contiguous(bars, i, lookback) else None
            exit_now, reason = False, ""
            if z is not None:
                r = _rule_exit_reason(pos["side"], z, exit_z, stop_z)
                if r:
                    exit_now, reason = True, r
            if not exit_now and last_in_session(bars, i, session):
                exit_now, reason = True, "session_end"
            if exit_now:
                trades.append(Trade(pos["entry_ts"], bar.ts, pos["side"], pos["entry"],
                                    bar.close, reason, i - pos["entry_i"]))
                pos = None
        # ── ny entry (kun hvis flad) ──
        if pos is not None:
            armed = None          # en aaben position ophaever enhver armering
        elif session_of(bar.ts.hour) == session \
                and (entry_hours is None or bar.ts.hour in entry_hours):
            if (min_vol is None or bar.volume >= min_vol) and contiguous(bars, i, lookback):
                z = zscore(bars, i, lookback)
                if z is not None and not last_in_session(bars, i, session):
                    side = _rule_entry_side(z, entry_z)

                    # Trend-vagt: er udstraekningen en overdrivelse eller en trend?
                    if side is not None and max_er is not None:
                        er = efficiency_ratio(bars, i, lookback)
                        if er is not None and er > max_er:
                            side = None

                    tag = None
                    if confirm is None:
                        tag = side
                    elif confirm == "same_bar":
                        if side is not None and _vendt_tilbage(bars, i, side):
                            tag = side
                    elif confirm == "z_contract":
                        z_prev = zscore(bars, i - 1, lookback) \
                            if contiguous(bars, i - 1, lookback) else None
                        if side is not None and z_prev is not None and abs(z) < abs(z_prev):
                            tag = side
                    elif confirm == "armed":
                        if side is not None and (armed is None or armed["side"] != side):
                            armed = {"side": side, "i": i}
                        if armed is not None:
                            if i - armed["i"] > confirm_max_wait or abs(z) < exit_z:
                                armed = None            # for sent, eller straekket er vaek
                            elif i > armed["i"] and _vendt_tilbage(bars, i, armed["side"]):
                                tag = armed["side"]
                                armed = None
                    else:
                        raise ValueError(f"ukendt confirm: {confirm!r}")

                    if tag in ("short", "long"):
                        pos = {"side": tag, "entry": bar.close, "entry_ts": bar.ts, "entry_i": i}
    return trades


def stats(trades, cost_bp):
    pnls = [t.net_pct(cost_bp) for t in trades]
    n = len(pnls)
    if n == 0:
        return dict(n=0, wr=0, avg=0, sum=0, pf=0, worst=0)
    wins = [p for p in pnls if p > 0]
    gl = -sum(p for p in pnls if p < 0)
    gw = sum(wins)
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)
    return dict(n=n, wr=100 * len(wins) / n, avg=sum(pnls) / n, sum=sum(pnls), pf=pf, worst=min(pnls))


def fmt_pf(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def exit_z_grid(lo, hi, step):
    """Float-sikker liste af exit_z-værdier i [lo, hi] med given step (2 decimaler)."""
    if step <= 0:
        return [round(lo, 2)]
    n = int(round((hi - lo) / step))
    return [round(lo + k * step, 2) for k in range(n + 1)]


def date_span_split(trades, frac):
    if not trades:
        return [], []
    dates = sorted(t.entry_ts.date() for t in trades)
    cut = dates[min(len(dates) - 1, int(len(dates) * frac))]
    insample = [t for t in trades if t.entry_ts.date() <= cut]
    oos = [t for t in trades if t.entry_ts.date() > cut]
    return insample, oos


def main():
    # Tving UTF-8 på stdout — headerne emitter ≥/≤/— der ellers crasher en
    # cp1252-konsol (fx når output pipes). Guard: ældre/uddelte streams mangler
    # reconfigure.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Mean-reversion backtest (session-gated, intraday)")
    ap.add_argument("--data-dir", default="data_harvest")
    ap.add_argument("--only", default=None, help="kommasepareret; default index-micros MES,MNQ,M2K")
    ap.add_argument("--session", default="europaeisk", choices=list(SESSIONS.keys()))
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--entry-z", type=float, default=2.0)
    ap.add_argument("--exit-z", type=float, default=0.5)
    ap.add_argument("--stop-z", type=float, default=3.5)
    ap.add_argument("--min-vol-pct", type=float, default=50.0)
    ap.add_argument("--oos-split", type=float, default=0.6)
    ap.add_argument("--cost-read-bp", type=float, default=2.0, help="omkostning til OOS-aflæsning")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--exit-z-sweep", action="store_true",
                    help="isoleret exit_z-sweep, pinnet til live-config "
                         "(lookback/entry_z/stop_z/instrumenter fra config.py)")
    ap.add_argument("--exit-z-min", type=float, default=-0.5)
    ap.add_argument("--exit-z-max", type=float, default=0.5)
    ap.add_argument("--exit-z-step", type=float, default=0.1)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    if args.only:
        instruments = [s.strip().upper() for s in args.only.split(",")]
    elif args.exit_z_sweep:
        instruments = list(CFG_INSTRUMENTS)   # pinnet til live (MES, M2K — IKKE MNQ)
    else:
        instruments = list(DEFAULT_INSTR)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  MEAN-REVERSION BACKTEST — z-score, session-gated, intraday")
    emit("=" * 78)
    emit(f"Data: {data_dir}   session: {args.session}   instrumenter: {', '.join(instruments)}")
    if args.exit_z_sweep:
        emit(f"Regel: entry |z|≥{CFG_ENTRY_Z}  stop |z|≥{CFG_STOP_Z}  lookback={CFG_LOOKBACK}  "
             f"(pinnet til live-config; exit_z sweepes)")
    else:
        emit(f"Regel: entry |z|≥{args.entry_z}  exit |z|≤{args.exit_z}  stop |z|≥{args.stop_z}  "
             f"lookback={args.lookback}  (DEFAULTS, ikke optimeret)")
    emit("Omkostning vist ved 0/1/2/3 bp rundtur. Forsvinder edge ved ~2 bp = bounce-artefakt.")
    emit("")

    if not data_dir.exists():
        emit(f"Mappen {data_dir} findes ikke.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    if args.sweep:
        emit("── PARAMETERSWEEP (entry_z × exit_z × lookback), aflæst ved "
             f"{args.cost_read_bp:.0f} bp rundtur ──")
        for label in instruments:
            p = data_dir / f"{label}_15min.csv"
            if not p.exists():
                continue
            bars = load_15min(p)
            pos = sorted(b.volume for b in bars if b.volume > 0)
            thr = pos[min(len(pos) - 1, int(len(pos) * args.min_vol_pct / 100))] if pos else None
            if thr is not None and thr <= 0:
                thr = None
            emit(f"\n  {label}:")
            emit(f"     {'entry_z':>8}{'exit_z':>8}{'lookbk':>8}{'n':>6}{'WR%':>7}{'sum%':>9}{'PF':>7}")
            for ez in (1.5, 2.0, 2.5):
                for xz in (0.0, 0.5, 1.0):
                    for lb in (15, 20, 30):
                        tr = run_backtest(bars, args.session, lb, ez, xz, args.stop_z, thr)
                        s = stats(tr, args.cost_read_bp)
                        emit(f"     {ez:>8.1f}{xz:>8.1f}{lb:>8}{s['n']:>6}{s['wr']:>7.0f}"
                             f"{s['sum']:>9.1f}{fmt_pf(s['pf']):>7}")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        emit(f"\nFil: {out_dir / 'summary.txt'}")
        return 0

    if args.exit_z_sweep:
        lb, ez, sz = CFG_LOOKBACK, CFG_ENTRY_Z, CFG_STOP_Z
        cost = args.cost_read_bp
        xs = exit_z_grid(args.exit_z_min, args.exit_z_max, args.exit_z_step)

        emit("── EXIT-Z-SWEEP (isoleret — kun exit_z varierer) ──")
        emit(f"   Pinnet: lookback={lb}  entry_z={ez}  stop_z={sz}  "
             f"instrumenter={', '.join(instruments)}")
        emit(f"   exit_z: {xs[0]:+.2f} … {xs[-1]:+.2f}  (step {args.exit_z_step:.2f}, {len(xs)} værdier)")
        emit(f"   Aflæst ved {cost:.0f} bp rundtur (sum0% = 0 bp).  "
             f"Lavere exit_z = hold længere; 0.00 = luk ved middel.")
        emit("")

        # indlæs bars + vol-tærskel pr. instrument én gang
        loaded = []  # (label, bars, thr)
        for label in instruments:
            p = data_dir / f"{label}_15min.csv"
            if not p.exists():
                emit(f"   {label}: ingen fil ({p.name}) — springer over")
                continue
            bars = load_15min(p)
            if len(bars) < lb + 50:
                emit(f"   {label}: for få bars ({len(bars)}) — springer over")
                continue
            vols = sorted(b.volume for b in bars if b.volume > 0)
            thr = vols[min(len(vols) - 1, int(len(vols) * args.min_vol_pct / 100))] if vols else None
            if thr is not None and thr <= 0:
                thr = None
            loaded.append((label, bars, thr))

        if not loaded:
            emit("   Ingen brugbare instrument-filer.")
            (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
            return 1

        # kør hvert exit_z én gang, saml portefølje-trades på tværs af instrumenter
        rows = []  # (xz, trades)
        for xz in xs:
            tr = []
            for _label, bars, thr in loaded:
                tr.extend(run_backtest(bars, args.session, lb, ez, xz, sz, thr))
            rows.append((xz, tr))

        emit("  A. Headline (samlet portefølje)")
        emit(f"     {'exit_z':>7}{'n':>6}{'WR%':>7}{'sum0%':>9}{'sum%':>9}{'PF':>7}{'værst%':>9}")
        for xz, tr in rows:
            s0, s = stats(tr, 0.0), stats(tr, cost)
            emit(f"     {xz:>+7.2f}{s['n']:>6}{s['wr']:>7.0f}{s0['sum']:>9.1f}"
                 f"{s['sum']:>9.1f}{fmt_pf(s['pf']):>7}{s['worst']:>9.2f}")
        emit("")

        emit(f"  B. Robusthed: in-sample vs OOS (split {args.oos_split:.0%}, ved {cost:.0f} bp)")
        emit(f"     {'exit_z':>7}{'IS_n':>6}{'IS_sum%':>9}{'IS_PF':>7}"
             f"{'OOS_n':>7}{'OOS_sum%':>10}{'OOS_PF':>8}")
        for xz, tr in rows:
            ins, oos = date_span_split(tr, args.oos_split)
            si, so = stats(ins, cost), stats(oos, cost)
            emit(f"     {xz:>+7.2f}{si['n']:>6}{si['sum']:>9.1f}{fmt_pf(si['pf']):>7}"
                 f"{so['n']:>7}{so['sum']:>10.1f}{fmt_pf(so['pf']):>8}")
        emit("")

        emit("  C. Exit-mix (andel af handler)")
        emit(f"     {'exit_z':>7}{'revert%':>9}{'stop%':>8}{'sess%':>8}")
        for xz, tr in rows:
            n = len(tr) or 1
            rc = defaultdict(int)
            for t in tr:
                rc[t.reason] += 1
            emit(f"     {xz:>+7.2f}{100*rc['revert']/n:>9.0f}"
                 f"{100*rc['stop']/n:>8.0f}{100*rc['session_end']/n:>8.0f}")
        emit("")

        emit("  LÆSNING (anti-overfit):")
        emit("   • Vælg IKKE bare den højeste sum/PF i tabel A.")
        emit("   • Kig efter et PLATEAU der holder i BÅDE in-sample og OOS (tabel B).")
        emit("   • Driver exit-mix mod overvejende session_end ved lav exit_z (tabel C),")
        emit("     er 'gevinsten' bare at vente på tvangsluk — ikke en ægte target-forbedring.")
        emit("   • Bekræft en kandidat med fuldt cost-sweep:  --exit-z <værdi>")
        emit("")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        emit(f"Fil: {out_dir / 'summary.txt'}")
        return 0

    portfolio = []  # (label, trades, thr)
    for label in instruments:
        p = data_dir / f"{label}_15min.csv"
        if not p.exists():
            emit(f"── {label}: ingen fil ({p.name}) — springer over\n")
            continue
        bars = load_15min(p)
        if len(bars) < args.lookback + 50:
            emit(f"── {label}: for få bars ({len(bars)})\n")
            continue
        pos = sorted(b.volume for b in bars if b.volume > 0)
        thr = pos[min(len(pos) - 1, int(len(pos) * args.min_vol_pct / 100))] if pos else None
        if thr is not None and thr <= 0:
            thr = None
        trades = run_backtest(bars, args.session, args.lookback, args.entry_z,
                              args.exit_z, args.stop_z, thr)
        portfolio.append((label, trades, thr))

        days = len({t.entry_ts.date() for t in trades}) or 1
        avg_hold = sum(t.bars_held for t in trades) / len(trades) if trades else 0
        reasons = defaultdict(int)
        for t in trades:
            reasons[t.reason] += 1
        emit("─" * 78)
        emit(f"  {label}   (handler={len(trades)}, {len(trades)/days:.1f}/dag, "
             f"snit-hold={avg_hold:.1f} bars, vol-tærskel={'ingen' if thr is None else int(thr)})")
        emit("─" * 78)
        emit(f"     exit-mix: " + "  ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])))
        emit(f"     {'rundtur':>8}{'n':>6}{'WR%':>7}{'snit%':>9}{'sum%':>9}{'PF':>7}{'værst%':>9}")
        for c in COST_LEVELS_BP:
            s = stats(trades, c)
            emit(f"     {c:>6.0f}bp{s['n']:>6}{s['wr']:>7.0f}{s['avg']:>9.3f}"
                 f"{s['sum']:>9.1f}{fmt_pf(s['pf']):>7}{s['worst']:>9.2f}")
        # OOS-split ved aflæsnings-omkostning
        ins, oos = date_span_split(trades, args.oos_split)
        si, so = stats(ins, args.cost_read_bp), stats(oos, args.cost_read_bp)
        emit(f"     OOS @ {args.cost_read_bp:.0f}bp:  in-sample n={si['n']} sum={si['sum']:+.1f}% "
             f"PF={fmt_pf(si['pf'])}  |  out-of-sample n={so['n']} sum={so['sum']:+.1f}% PF={fmt_pf(so['pf'])}")
        emit("")

    # samlet portefølje-dom
    all_trades = [t for _, tr, _ in portfolio for t in tr]
    if all_trades:
        emit("─" * 78)
        emit("  SAMLET (alle instrumenter)")
        emit("─" * 78)
        emit(f"     {'rundtur':>8}{'n':>6}{'WR%':>7}{'snit%':>9}{'sum%':>9}{'PF':>7}")
        for c in COST_LEVELS_BP:
            s = stats(all_trades, c)
            emit(f"     {c:>6.0f}bp{s['n']:>6}{s['wr']:>7.0f}{s['avg']:>9.3f}{s['sum']:>9.1f}{fmt_pf(s['pf']):>7}")
        emit("")

    emit("─" * 78)
    emit("  DOM")
    emit("─" * 78)
    emit("  ÆGTE edge: PF > 1 ved ~2 bp rundtur OG holder i out-of-sample.")
    emit("  BOUNCE-artefakt: PF kollapser mod 1 mellem 0 og 2 bp (spændet æder reversionen).")
    emit("  Overlever den 2 bp + OOS → design er bekræftet; så sweeper vi + regime-splitter.")
    emit("  (Defaults er ikke optimeret — et positivt resultat her er konservativt.)")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    emit("→ Send mig summary.txt.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())