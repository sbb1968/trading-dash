#!/usr/bin/env python3
"""
velocity_backtest.py — Hastigheds-burst-møllen: pris-velocity persistens-backtest
═════════════════════════════════════════════════════════════════════════════════
Ren offline på bar_cache/ (1-min CSV'er). Ingen IBKR, ingen TradingView.

Spørgsmål: fortsætter en aktie der LIGE har bevæget sig hurtigt nok (acceleration,
ikke akkumuleret %), nok til at dække sent indstig + omkostninger (MOMENTUM-edge →
byg screeneren), eller er "hurtigst lige nu" reelt spike-TOPPEN der vender (FADE)?

Signal (selv-filtrerende, univers-løst): for hver RTH 1-min bar i —
  • velocity = (close[i] − close[i−w]) / close[i−w] · 100   (w = vel_window)
  • vol_mult = gns. volumen/bar i burst-vinduet ÷ trailing baseline (BASELINE_BARS før)
  • likviditetsgulv: trailing gns. dollar-volumen/bar ≥ LIQ_FLOOR (proxy; ingen mcap)
  Burst-event = |velocity| ≥ vel_thr OG vol_mult ≥ vol_thr OG likviditet OK.
  Cooldown pr. ticker så ét langt løb ikke tæller som mange events.

LOOK-AHEAD: velocity-vinduet er BAGUDRETTET (bruger close[i] og close[i−w]); baseline
+ likviditet er trailing (bars FØR i). Kvalifikationen bruger derfor KUN data ≤ i.
Entry ved bar i+L (L≥1) er strengt efter → look-ahead-ren ved ALLE latencies (modsat
katalysator-backtesten, hvis reaktions-vindue pegede fremad). Verificeret i koden.

To hypoteser (begge testes): MOMENTUM (op→long, ned→short) vs FADE (op→short, ned→long).
Exit: hold_window min ELLER RTH-slut, hvad end først. Aldrig over natten.
SHORT antager borrow altid muligt → optimistisk (markeret).

Brug (fra backend/):
    python velocity_backtest.py --limit 5        # verificér event-rate først
    python velocity_backtest.py                  # fuld kørsel, default-config
    python velocity_backtest.py --sweep          # plateau (vel_w × vel_thr × vol_thr × hold)

Output: velocity_backtest_output/summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\velocity_backtest.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

OUTPUT_DIRNAME = "velocity_backtest_output"
BAR_CACHE = Path("bar_cache")   # overstyres af --data-dir i main()

RTH_OPEN  = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)

LATENCIES_MIN  = [1, 2, 5, 15, 30]
COST_LEVELS_BP = [0.0, 1.0, 2.0, 3.0]
COST_READ_BP   = 2.0
OOS_SPLIT      = 0.6

BASELINE_BARS  = 20            # trailing bars til volumen/likviditets-baseline
COOLDOWN_MIN   = 30            # min mellem events for samme ticker (unikke løb)
LIQ_FLOOR      = 50_000.0      # min trailing gns. dollar-volumen/bar
SESSION_GAP_MIN = 30

# Defaults (hoved-config)
VEL_WINDOW_DEFAULT = 2
VEL_THR_DEFAULT    = 2.0
VOL_THR_DEFAULT    = 2.0
HOLD_DEFAULT       = 15

# Sweep-grids
VEL_WINDOWS = [1, 2, 3, 5]
VEL_THRS    = [1.0, 2.0, 3.0]
VOL_THRS    = [2.0, 3.0]
HOLDS       = [5, 15, 30, 60]


@dataclass
class TBars:
    ts:     list
    close:  list
    sstart: list          # indeks for første bar i samme session
    slast:  list          # indeks for sidste bar i samme session
    cum_v:  list          # kumulativ volumen i session (inkl.)
    cum_dv: list          # kumulativ dollar-volumen i session (inkl.)


def load_ticker(ticker: str) -> TBars | None:
    """Flet alle *_1min.csv for ticker, RTH-filtrér, byg session-prefix-sums."""
    rows = {}
    for p in sorted(BAR_CACHE.glob(f"{ticker}_*_1min.csv")):
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    dt = datetime.fromisoformat(r["timestamp"])
                except (ValueError, KeyError):
                    continue
                t = dt.timetz()
                if not (RTH_OPEN <= t.replace(tzinfo=None) < RTH_CLOSE):
                    continue
                try:
                    rows[dt] = (float(r["close"]), float(r["volume"] or 0))
                except (ValueError, KeyError):
                    continue
    if len(rows) < BASELINE_BARS + 50:
        return None
    ts = sorted(rows)
    close = [rows[t][0] for t in ts]
    vol   = [rows[t][1] for t in ts]
    n = len(ts)
    sstart = [0] * n
    for i in range(1, n):
        gap = (ts[i] - ts[i - 1]).total_seconds() / 60.0
        sstart[i] = i if (gap > SESSION_GAP_MIN or ts[i].date() != ts[i - 1].date()) else sstart[i - 1]
    slast = [0] * n
    li = n - 1
    for i in range(n - 1, -1, -1):
        if i == n - 1 or sstart[i] != sstart[i + 1]:
            li = i
        slast[i] = li
    cum_v, cum_dv = [0.0] * n, [0.0] * n
    for i in range(n):
        dv = close[i] * vol[i]
        if sstart[i] == i:
            cum_v[i], cum_dv[i] = vol[i], dv
        else:
            cum_v[i], cum_dv[i] = cum_v[i - 1] + vol[i], cum_dv[i - 1] + dv
    return TBars(ts, close, sstart, slast, cum_v, cum_dv)


def _win_sum(cum, a, b, sstart_b):
    """Sum cum-array over [a..b] inkl., samme session (a > sstart_b garanteret)."""
    return cum[b] - cum[a - 1]


@dataclass
class Event:
    idx: int
    day: object
    direction: str        # "up" | "down"


def detect_events(bars: TBars, w, vel_thr, vol_thr) -> list:
    """Burst-events med bagudrettet velocity + trailing baseline + likviditetsgulv +
    cooldown. Look-ahead-ren: alt afhænger kun af bars ≤ i."""
    events = []
    last_ts = None
    n = len(bars.ts)
    warm = w + BASELINE_BARS
    for i in range(n):
        ss = bars.sstart[i]
        if i - ss < warm:
            continue
        c0 = bars.close[i - w]
        if c0 <= 0:
            continue
        vel = (bars.close[i] - c0) / c0 * 100.0
        if abs(vel) < vel_thr:
            continue
        # burst-volumen (sidste w bars) vs trailing baseline (BASELINE_BARS før vinduet)
        burst_v = _win_sum(bars.cum_v, i - w + 1, i, ss)
        base_a, base_b = i - w - BASELINE_BARS + 1, i - w
        base_v = _win_sum(bars.cum_v, base_a, base_b, ss)
        base_dv = _win_sum(bars.cum_dv, base_a, base_b, ss)
        base_per_bar = base_v / BASELINE_BARS
        liq_per_bar = base_dv / BASELINE_BARS
        if liq_per_bar < LIQ_FLOOR:
            continue
        if base_per_bar <= 0:
            continue
        vol_mult = (burst_v / w) / base_per_bar
        if vol_mult < vol_thr:
            continue
        # cooldown pr. ticker
        if last_ts is not None and (bars.ts[i] - last_ts).total_seconds() < COOLDOWN_MIN * 60:
            continue
        last_ts = bars.ts[i]
        events.append(Event(idx=i, day=bars.ts[i].date(),
                            direction=("up" if vel > 0 else "down")))
    return events


@dataclass
class TradeRes:
    ret_pct: float
    side:    str
    day:     object


def simulate(bars: TBars, ev: Event, L, hold, hyp) -> TradeRes | None:
    i = ev.idx
    ss = bars.sstart[i]
    entry_ts = bars.ts[i] + timedelta(minutes=L)
    e = bisect_left(bars.ts, entry_ts)
    if e >= len(bars.ts) or bars.sstart[e] != ss:
        return None
    entry = bars.close[e]
    if entry <= 0:
        return None
    exit_ts = bars.ts[e] + timedelta(minutes=hold)
    x = bisect_left(bars.ts, exit_ts)
    if x >= len(bars.ts) or bars.sstart[x] != ss:
        x = bars.slast[e]
    exit_px = bars.close[x]
    if hyp == "momentum":
        side = "long" if ev.direction == "up" else "short"
    else:
        side = "short" if ev.direction == "up" else "long"
    raw = (exit_px - entry) / entry * 100.0
    return TradeRes(raw if side == "long" else -raw, side, ev.day)


def stats(rets, cost_bp):
    pnls = [r - cost_bp * 0.01 for r in rets]
    n = len(pnls)
    if n == 0:
        return dict(n=0, wr=0, avg=0, sum=0, pf=0, worst=0)
    wins = [p for p in pnls if p > 0]
    gl = -sum(p for p in pnls if p < 0)
    gw = sum(wins)
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)
    return dict(n=n, wr=100 * len(wins) / n, avg=sum(pnls) / n, sum=sum(pnls),
                pf=pf, worst=min(pnls))


def fmt_pf(pf):
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def oos_split(trades, frac):
    if not trades:
        return [], []
    days = sorted({t.day for t in trades})
    cut = days[min(len(days) - 1, int(len(days) * frac))]
    return [t for t in trades if t.day <= cut], [t for t in trades if t.day > cut]


def load_universe(symbols, emit):
    loaded = []
    for sym in symbols:
        b = load_ticker(sym)
        if b is not None:
            loaded.append((sym, b))
    emit(f"Indlæst: {len(loaded)}/{len(symbols)} navne med brugbar 1-min RTH-dækning.")
    return loaded


def all_symbols() -> list:
    syms = set()
    for p in BAR_CACHE.glob("*_1min.csv"):
        name = p.name
        # {TICKER}_{start}_{end}_1min.csv → TICKER = før første _<dato>
        base = name.split("_1min.csv")[0]
        parts = base.split("_")
        # ticker = alt før første del der ligner en dato (YYYY-..)
        tick = []
        for part in parts:
            if len(part) >= 4 and part[:4].isdigit():
                break
            tick.append(part)
        if tick:
            syms.add("_".join(tick))
    return sorted(syms)


def load_pit_universe(path):
    """Point-in-time univers-JSON {dato: [tickere]} → {date_cls: set}. None hvis path None."""
    if not path:
        return None
    import json
    from datetime import date as _date
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for ds, ticks in raw.items():
        try:
            out[_date.fromisoformat(ds)] = {t.upper() for t in ticks}
        except ValueError:
            continue
    return out


def filter_events_pit(ev_by_t_with_sym, universe):
    """[(sym, bars, [events])] → flad [(sym, bars, event)]; event beholdes hvis universe
    er None ELLER sym ∈ universe[event.day]."""
    flat = []
    for sym, b, evs in ev_by_t_with_sym:
        for ev in evs:
            if universe is None or sym.upper() in universe.get(ev.day, ()):
                flat.append((sym, b, ev))
    return flat


def regroup_by_bars(flat):
    """[(sym, bars, event)] → [(bars, [events])] bevarende rækkefølge."""
    order, seen = [], {}
    for _sym, b, ev in flat:
        key = id(b)
        if key not in seen:
            seen[key] = (b, [])
            order.append(seen[key])
        seen[key][1].append(ev)
    return order


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Hastigheds-burst persistens-backtest (momentum vs fade)")
    ap.add_argument("--vel-window", type=int, default=VEL_WINDOW_DEFAULT)
    ap.add_argument("--vel-threshold", type=float, default=VEL_THR_DEFAULT)
    ap.add_argument("--vol-threshold", type=float, default=VOL_THR_DEFAULT)
    ap.add_argument("--hold-min", type=int, default=HOLD_DEFAULT)
    ap.add_argument("--oos-split", type=float, default=OOS_SPLIT)
    ap.add_argument("--symbols", default=None, help="kommasepareret; default = alle i data-dir")
    ap.add_argument("--limit", type=int, default=None, help="kun første N navne (verifikation)")
    ap.add_argument("--data-dir", default=None,
                    help="mappe med {TICKER}_*_1min.csv (default bar_cache)")
    ap.add_argument("--out-name", default="summary.txt",
                    help="output-filnavn i velocity_backtest_output/ (fx summary_neutral.txt)")
    ap.add_argument("--universe-file", default=None,
                    help="point-in-time univers-JSON {dato: [tickere]}; events for ticker T på "
                         "dag d beholdes kun hvis T ∈ univers[d] (OOP-validering)")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    global BAR_CACHE
    if args.data_dir:
        BAR_CACHE = Path(args.data_dir)

    pit_universe = load_pit_universe(args.universe_file)

    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  HASTIGHEDS-BURST-BACKTEST — pris-velocity persistens (momentum vs fade)")
    emit("=" * 78)

    if not BAR_CACHE.exists():
        emit(f"  {BAR_CACHE} findes ikke.")
        (out_dir / args.out_name).write_text("\n".join(lines), encoding="utf-8")
        return 1

    symbols = ([s.strip().upper() for s in args.symbols.split(",")]
               if args.symbols else all_symbols())
    if args.limit:
        symbols = symbols[:args.limit]
    loaded = load_universe(symbols, emit)
    if not loaded:
        emit("  Ingen brugbare navne.")
        (out_dir / args.out_name).write_text("\n".join(lines), encoding="utf-8")
        return 1

    if args.sweep:
        emit("")
        emit("── PLATEAU-SWEEP (vel_w × vel_thr × vol_thr × hold), PF@2bp + OOS_PF ──")
        emit("   Look-ahead-ren ved alle L (bagudrettet velocity). Kig efter PLATEAU der")
        emit("   holder på tværs af nabo-parametre OG OOS — vælg IKKE højeste enkelt-PF.")
        for w in VEL_WINDOWS:
            for vth in VEL_THRS:
                for volth in VOL_THRS:
                    _evs = [(sym, b, detect_events(b, w, vth, volth)) for sym, b in loaded]
                    ev_by_t = regroup_by_bars(filter_events_pit(_evs, pit_universe))
                    ntot = sum(len(e) for _b, e in ev_by_t)
                    if ntot < 30:
                        continue
                    emit("")
                    emit(f"  vel_w={w}  vel≥{vth}%  vol≥{volth}×   (events: {ntot})")
                    emit(f"     {'hyp':>9}{'lat':>5}{'hold':>6}{'n':>7}{'WR%':>7}{'PF@2bp':>8}{'OOS_PF':>8}")
                    for hold in HOLDS:
                        for hyp in ("momentum", "fade"):
                            # vis kun et repræsentativt latency-udvalg i sweep
                            for L in (2, 5, 15):
                                trades = []
                                for b, evs in ev_by_t:
                                    for ev in evs:
                                        tr = simulate(b, ev, L, hold, hyp)
                                        if tr:
                                            trades.append(tr)
                                s = stats([t.ret_pct for t in trades], COST_READ_BP)
                                _ins, oos = oos_split(trades, args.oos_split)
                                so = stats([t.ret_pct for t in oos], COST_READ_BP)
                                if s["n"] >= 30:
                                    emit(f"     {hyp:>9}{L:>5}{hold:>6}{s['n']:>7}{s['wr']:>7.0f}"
                                         f"{fmt_pf(s['pf']):>8}{fmt_pf(so['pf']):>8}")
        (out_dir / args.out_name).write_text("\n".join(lines), encoding="utf-8")
        emit(f"\nFil: {out_dir / args.out_name}")
        return 0

    # ── Hoved-config ──
    w, vth, volth, hold = args.vel_window, args.vel_threshold, args.vol_threshold, args.hold_min
    emit(f"Config: vel_w={w}min  vel≥{vth}%  vol≥{volth}×  hold={hold}min  "
         f"likv≥${LIQ_FLOOR:,.0f}/min  cooldown={COOLDOWN_MIN}min  OOS={args.oos_split:.0%}  @{COST_READ_BP:.0f}bp")
    emit("Look-ahead-ren ved ALLE latencies (bagudrettet velocity-vindue).")
    emit("")

    ev_by_t = [(sym, b, detect_events(b, w, vth, volth)) for sym, b in loaded]
    flat_ev = filter_events_pit(ev_by_t, pit_universe)
    all_events = [(b, ev) for _s, b, ev in flat_ev]
    if pit_universe is not None:
        emit(f"Point-in-time univers: {len(pit_universe)} dage · "
             f"events efter univers-filter: {len(all_events)}")
    n_ev = len(all_events)
    n_up = sum(1 for _b, e in all_events if e.direction == "up")
    n_names = sum(1 for _s, _b, evs in ev_by_t if evs)
    days = {e.day for _b, e in all_events}
    emit(f"EVENT-STATISTIK: {n_ev} burst-events · {n_names} navne · {len(days)} dage · "
         f"{n_ev/max(len(days),1):.1f}/dag · op={n_up} ned={n_ev-n_up}")
    if n_ev < 50:
        emit("  ⚠ <50 events — for tyndt til robust statistik (løsn tærskler).")
    elif n_ev > 20000:
        emit("  ⚠ >20.000 events — tærskel for løs (strammere giver renere signal).")
    emit("")

    res = {"momentum": {L: [] for L in LATENCIES_MIN}, "fade": {L: [] for L in LATENCIES_MIN}}
    for b, ev in all_events:
        for L in LATENCIES_MIN:
            for hyp in ("momentum", "fade"):
                tr = simulate(b, ev, L, hold, hyp)
                if tr:
                    res[hyp][L].append(tr)

    # A. Hovedtabel
    emit("  A. HOVEDTABEL — hypotese × latency (@2bp; OOS = sidste "
         f"{1-args.oos_split:.0%} af dage)")
    emit(f"     {'hypotese':>9}{'lat':>5}{'n':>7}{'WR%':>7}{'sum%':>10}{'PF@2bp':>8}{'OOS_n':>7}{'OOS_PF':>8}")
    for hyp in ("momentum", "fade"):
        for L in LATENCIES_MIN:
            trades = res[hyp][L]
            s = stats([t.ret_pct for t in trades], COST_READ_BP)
            _ins, oos = oos_split(trades, args.oos_split)
            so = stats([t.ret_pct for t in oos], COST_READ_BP)
            emit(f"     {hyp:>9}{L:>5}{s['n']:>7}{s['wr']:>7.0f}{s['sum']:>10.1f}"
                 f"{fmt_pf(s['pf']):>8}{so['n']:>7}{fmt_pf(so['pf']):>8}")
        emit("")

    # B. Omkostnings-sweep ved bedste latency pr. hypotese
    emit("  B. OMKOSTNINGS-SWEEP (0/1/2/3 bp) — bedste latency pr. hypotese (på sum@2bp)")
    for hyp in ("momentum", "fade"):
        best_L = max(LATENCIES_MIN,
                     key=lambda L: stats([t.ret_pct for t in res[hyp][L]], COST_READ_BP)["sum"])
        emit(f"     {hyp} @ L={best_L}min:")
        emit(f"        {'rundtur':>8}{'n':>7}{'WR%':>7}{'sum%':>10}{'PF':>7}{'værst%':>9}")
        for c in COST_LEVELS_BP:
            s = stats([t.ret_pct for t in res[hyp][best_L]], c)
            emit(f"        {c:>6.0f}bp{s['n']:>7}{s['wr']:>7.0f}{s['sum']:>10.1f}"
                 f"{fmt_pf(s['pf']):>7}{s['worst']:>9.2f}")
    emit("")

    # C. Long vs short
    emit("  C. LONG vs SHORT (@2bp) — SHORT er OPTIMISTISK (borrow antaget altid muligt)")
    emit(f"     {'hypotese':>9}{'lat':>5}{'long_n':>8}{'long_PF':>9}{'short_n':>9}{'short_PF':>10}")
    for hyp in ("momentum", "fade"):
        for L in LATENCIES_MIN:
            longs = [t.ret_pct for t in res[hyp][L] if t.side == "long"]
            shorts = [t.ret_pct for t in res[hyp][L] if t.side == "short"]
            sl, ss2 = stats(longs, COST_READ_BP), stats(shorts, COST_READ_BP)
            emit(f"     {hyp:>9}{L:>5}{sl['n']:>8}{fmt_pf(sl['pf']):>9}"
                 f"{ss2['n']:>9}{fmt_pf(ss2['pf']):>10}")
    emit("")

    # ── DOM ──
    emit("─" * 78)
    emit("  DOM — fortsætter hurtige bevægelser (momentum) eller vender de (fade/spike-top)?")
    emit("─" * 78)
    best = None
    for hyp in ("momentum", "fade"):
        for L in LATENCIES_MIN:
            trades = res[hyp][L]
            s = stats([t.ret_pct for t in trades], COST_READ_BP)
            _ins, oos = oos_split(trades, args.oos_split)
            so = stats([t.ret_pct for t in oos], COST_READ_BP)
            if s["n"] >= 50 and s["pf"] > 1.0 and so["n"] >= 20 and so["pf"] > 1.0:
                score = min(s["pf"], so["pf"] if so["pf"] != float("inf") else s["pf"])
                if best is None or score > best[0]:
                    best = (score, hyp, L, s, so)
    if best is None:
        emit("  INGEN robust edge: ingen hypotese×latency har PF>1 @2bp i BÅDE in-sample OG OOS.")
        emit("  → Hurtige bevægelser fortsætter IKKE pålideligt på dette tidsniveau (efter sent")
        emit("    indstig + omkostninger). Sammen med den DØDE katalysator-momentum er det to")
        emit("    negative intraday-momentum-tests → screener-idéen parkeres på ærligt grundlag.")
    else:
        _sc, hyp, L, s, so = best
        verdict = ("MOMENTUM (hurtige bevægelser FORTSÆTTER)" if hyp == "momentum"
                   else "FADE (hurtigst = spike-TOP, bevægelsen VENDER)")
        emit(f"  Robust edge: {verdict}")
        emit(f"  Bedste: {hyp.upper()} ved L={L}min — PF@2bp={fmt_pf(s['pf'])} "
             f"(n={s['n']}, WR={s['wr']:.0f}%), OOS_PF={fmt_pf(so['pf'])} (n={so['n']}).")
        emit("  → Bekræft PLATEAU med --sweep og long/short-balance (tabel C) før screener bygges,")
        emit("    og brug DISSE parametre (vel_w/tærskler/hold), ikke gættede.")
    emit("")
    emit("  FORBEHOLD: SHORT antager borrow altid muligt. Likviditetsgulv er dollar-volumen-")
    emit("  proxy (ingen mcap). bar_cache-univers valgt på et tidspunkt → mild survivorship.")
    emit("  Events er unikke løb (cooldown); handler kan overlappe (event-study, ikke én-kapital).")

    (out_dir / args.out_name).write_text("\n".join(lines), encoding="utf-8")
    emit(f"\nFil: {out_dir / args.out_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
