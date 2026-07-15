#!/usr/bin/env python3
"""
cross_sectional_rs_backtest.py — Tvaersnitlig relativ styrke / dispersions-backtest (spor D)
═════════════════════════════════════════════════════════════════════════════════════════════
Ren OFFLINE: kun stdlib. Laeser eksisterende bar_cache/ (1-min CSV, ET, RTH) + point-in-time
universe (historical_universe_midcap_*.json). Ingen IBKR, ingen handler paa live, ingen netvaerk.

HVORFOR (spor D): Regime-fingeraftrykket (Trin 0, commit 9d13fb7) gav ET entydigt verdikt for
recent-vinduet: RELATIV VAERDI / STOCK-PICKING. Hoej navne-dispersion (3.88% dagligt) + lav/
negativ index-trend-persistens (-0.09). Edgen ligger i at VAELGE MELLEM navne, ikke i at ride
indexet (spor A/ES-RTY-spread er praeregistreret LUKKET: random walk paa baade dag og 15-min).

Tese (eksplicit, tvaersnitlig — anderledes end K2/TrendJoin's absolutte pr-navn-gates):
    "Toppen af dagens rangliste (relativ styrke) slaar RESTEN af universet."
Vi maaler det DIREKTE som SELECTION ALPHA = (gns. afkast top-K) - (gns. afkast hele universet),
pr. dag, netto for omkostning. Slaar top-K ikke universe-snittet, er der ingen selektions-edge
(saa er vi bare long beta paa et survivorship-loeftet univers).

SELECTION ALPHA NEUTRALISERER SURVIVORSHIP (metodisk kerne): universe-filerne er valgt fra navne
vi VED loeb (mild survivorship). Det loefter BAADE top-K OG benchmark. Ved at doemme paa SPAENDET
(top-K minus universe-snit) traekker vi det faelles loeft ud — differencen isolerer selektionen.

────────────────────────────────────────────────────────────────────────────────────────────
PRAEREGISTRERET DOM (skrevet FOER resultater — staar ogsaa i summary.txt' top):
  Spor D er "vaerd at leve-teste" HVIS OG KUN HVIS, for den valgte score/T/K/exit:
    1. selection alpha (top-K minus universe-snit) POSITIV netto ved >= 1c i BAADE maj OG apr, OG
    2. hit-rate (andel dage top-K > universe-snit) > 0.50 i BAADE maj OG apr, OG
    3. robust PLATEAU (positiv alpha over en REGION af T x K x score x exit — ikke ét punkt; ses i --sweep).
  Ellers: spor D forkastes paa aerligt grundlag. Alt er beskrivende; INGEN edge-paastand a priori.
  (maj = in-sample/udvikling, apr = out-of-sample-bekraeftelse. 2026-03-30..31 = kort sanity, ikke dom.)
────────────────────────────────────────────────────────────────────────────────────────────

HAERDNING (--months all): RE-VALIDERING af den FROSNE plateau-config paa STOERRE stikproeve —
IKKE et nyt sweep. Vi tager den allerede valgte config (early_rs/above_vwap, eod, T in {0945,1000},
K in {1,2,3}) og spoerger: overlever selection-alphaen paa maaneder den ALDRIG er valgt paa?
Et nyt sweep paa nye maaneder ville geninvitere overfitting og er FORBUDT her. (Roerer IKKE
universe-mismatchet — stadig midcap-universet; kun paper afgoer mismatchet. Sporene er komplementaere.)

PRAEREGISTRERET HAERDNINGS-DOM (skrevet FOER de nye maaneder koeres; alle 4 skal holde):
  1. selection alpha (median, netto @1c) POSITIV i FLERTALLET af de nye maaneder, OG
  2. hit-rate > 0.50 i FLERTALLET af de nye maaneder, OG
  3. above_vwap KORROBORERER fortsat (samme fortegn i samme T/K-region), OG
  4. plateauet forbliver en sammenhaengende REGION (early_rs-eod-cellerne, ikke skrumpet til ét punkt).
  Bestaas -> plateauet er haerdet. Fejler -> smaastikproeve-artefakt; STOP live-udrulning, re-evaluer.
  De fire parametre (T/K/score/exit) aendres ALDRIG paa live/nye data — en re-tune kraever nyt sweep.

Brug (fra backend/):
    python cross_sectional_rs_backtest.py                              # defaults (early_rs, T=0945, K=3, eod)
    python cross_sectional_rs_backtest.py --score early_rs --time 0945 --k 3 --exit eod
    python cross_sectional_rs_backtest.py --sweep                      # T x K x score x exit plateau
    python cross_sectional_rs_backtest.py --hedge m2k                  # markeds-neutral variant (short M2K-ben)
    python cross_sectional_rs_backtest.py --months all                 # HAERDNING: frossen config, per maaned

Output: cross_sectional_rs_output/{summary.txt, trades_{score}_{time}_k{K}.csv}  (mappe i .gitignore)
Placering: C:\\Projects\\trading_dash\\backend\\cross_sectional_rs_backtest.py
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import statistics as st
import sys
from dataclasses import dataclass
from datetime import datetime, date, time as dtime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
BAR_CACHE = BACKEND / "bar_cache"
STITCHED = BACKEND / "data_harvest" / "mes_m2k_stitched"   # M2K = Russell 2000 micro (small-cap-beta)
OUT_DIR = BACKEND / "cross_sectional_rs_output"

RTH_OPEN, RTH_CLOSE = dtime(9, 30), dtime(16, 0)
EOD_FORCE = dtime(15, 51)          # baseline force-close (aldrig over natten)

# Sweep-grids (spec afsnit 4-5)
SCORES = ["early_rs", "gap", "gap_x_rvol", "above_vwap"]
TIMES = [dtime(9, 35), dtime(9, 45), dtime(10, 0), dtime(10, 15)]
KS = [1, 2, 3, 5, "decile"]
EXITS = ["eod", "1100", "1200", "ts", "trail"]

# ── HAERDNING (--months all): FROSSEN config fra det bestaaede plateau. RE-VALIDERING, IKKE
#    re-optimering. Vi sweeper IKKE igen; vi koerer PRAECIS disse celler paa nye maaneder. ──
HARDEN_SCORES = ["early_rs", "above_vwap"]        # primaer + korroboration (samme retning ventes)
HARDEN_TS = [dtime(9, 45), dtime(10, 0)]          # plateauets T-region
HARDEN_KS = [1, 2, 3]                             # plateauets K-region
HARDEN_EXIT = "eod"
HARDEN_PRIMARY = ("early_rs", dtime(9, 45), 3)    # overskrifts-celle (score, T, K)
# Discovery-vinduet configen blev VALGT paa (2026-03..05). Alt andet = out-of-sample-maaneder.
OLD_MONTHS = {"2026-03", "2026-04", "2026-05"}

# Omkostning: cents pr. navn pr. side. round-trip pct = 2*cents/entry_px*100 (pris-afhaengig).
COST_CENTS_LEVELS = [0.0, 1.0, 2.0]
COST_READ_CENTS = 1.0              # doms-omkostning (>= 1c, jf. spec afsnit 8)

# Defaults for exit-parametre
TARGET_PCT_DEFAULT = 5.0
STOP_PCT_DEFAULT = 3.0
TRAIL_LB_DEFAULT = 3               # swing-low lookback (bars) for trailing exit

NOTES: list[str] = []


# ═══════════════════════════════════════════════════════════════════
# Loaders (ren stdlib)
# ═══════════════════════════════════════════════════════════════════
def _num(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


@dataclass
class Bar:
    dt: datetime
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def t(self) -> dtime:
        return self.dt.timetz().replace(tzinfo=None)


def load_ticker_days(ticker: str) -> dict:
    """Flet alle {TICKER}_*_1min.csv, RTH-filtrer, deduper paa ts, gruppér pr. ET-dato.
    Returnerer {date -> [Bar,...] sorteret}. Tom hvis ingen brugbar daekning."""
    rows: dict[datetime, Bar] = {}
    for p in sorted(BAR_CACHE.glob(f"{ticker}_*_1min.csv")):
        try:
            if p.stat().st_size < 64:          # tomme 38-byte-filer
                continue
        except OSError:
            continue
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    dt = datetime.fromisoformat(r["timestamp"])
                except (ValueError, KeyError):
                    continue
                tt = dt.timetz().replace(tzinfo=None)
                if not (RTH_OPEN <= tt < RTH_CLOSE):
                    continue
                o, h, l, c = (_num(r.get(k)) for k in ("open", "high", "low", "close"))
                if None in (o, h, l, c) or o <= 0 or c <= 0:
                    continue
                rows[dt] = Bar(dt, o, h, l, c, _num(r.get("volume")) or 0.0)
    days: dict = {}
    for dt in sorted(rows):
        days.setdefault(dt.date(), []).append(rows[dt])
    return days


def load_universe(pattern="historical_universe_midcap_*.json") -> dict:
    """date -> [tickers] (point-in-time). Merget fra alle midcap-filer."""
    uni: dict = {}
    files = sorted(glob.glob(str(BACKEND / pattern)))
    if not files:
        NOTES.append("universe: INGEN historical_universe_midcap_*.json fundet -> ingen backtest mulig.")
    for fp in files:
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for ds, ticks in d.items():
            try:
                dd = date.fromisoformat(ds)
            except ValueError:
                continue
            uni.setdefault(dd, [])
            for t in ticks:
                tu = str(t).upper()
                if tu not in uni[dd]:
                    uni[dd].append(tu)
    return uni


def load_m2k_hedge() -> dict:
    """M2K 5-min stitched -> {date -> [(dt, close)] sorteret}. Til beta-hedge (afsnit 7)."""
    out: dict = {}
    p = STITCHED / "M2K_5min.csv"
    if not p.exists():
        NOTES.append("hedge: M2K_5min.csv mangler i mes_m2k_stitched/ -> --hedge deaktiveret.")
        return out
    with p.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(r["timestamp"])
                c = _num(r["close"])
            except (ValueError, KeyError):
                continue
            if c is None or c <= 0:
                continue
            out.setdefault(dt.date(), []).append((dt, c))
    for d in out:
        out[d].sort(key=lambda x: x[0])
    return out


# ═══════════════════════════════════════════════════════════════════
# Score pr. (dag, navn) ved beslutningstid T — look-ahead-ren (kun bars <= T)
# ═══════════════════════════════════════════════════════════════════
@dataclass
class NameScore:
    ticker: str
    raw: float | None          # rang-scoren (percentileres tvaersnitligt i dagens univers)
    valid: bool                # har brugbart entry+exit (kan indgaa i benchmark)


def _bars_upto(bars, T):
    return [b for b in bars if b.t <= T]


def compute_name_score(ticker, bars, prior_close, T, variant):
    """Beregn rang-score for ét navn ved T. Look-ahead-assert: ingen bar > T bruges."""
    upto = _bars_upto(bars, T)
    if len(upto) < 2:
        return NameScore(ticker, None, False)
    assert max(b.t for b in upto) <= T, "look-ahead: score bruger bar efter T"
    open0930 = bars[0].o
    c_T = upto[-1].c
    if open0930 <= 0:
        return NameScore(ticker, None, False)

    raw = None
    if variant == "early_rs":
        raw = (c_T - open0930) / open0930
    elif variant == "gap":
        if prior_close and prior_close > 0:
            raw = (open0930 - prior_close) / prior_close
    elif variant == "gap_x_rvol":
        if prior_close and prior_close > 0:
            gap = (open0930 - prior_close) / prior_close
            cum_v = sum(b.v for b in upto)
            rvol = cum_v / max(len(upto), 1)     # avg vol/bar-proxy (spec afsnit 4)
            raw = gap * rvol
    elif variant == "above_vwap":
        num = sum(((b.h + b.l + b.c) / 3.0) * b.v for b in upto)
        den = sum(b.v for b in upto)
        if den > 0:
            vwap = num / den
            if vwap > 0:
                raw = (c_T - vwap) / vwap
    return NameScore(ticker, raw, True)


# ═══════════════════════════════════════════════════════════════════
# Simulering af én handel (LONG-only) — entry = naeste bar's open (konservativt), exit = bar-luk
# ═══════════════════════════════════════════════════════════════════
@dataclass
class Trade:
    ticker: str
    day: date
    gross_pct: float
    entry_px: float
    entry_dt: datetime
    exit_dt: datetime
    reason: str


def _entry_index(bars, T):
    for i, b in enumerate(bars):
        if b.t > T:
            return i
    return None


def _last_idx_le(bars, clock, lo):
    """sidste index >= lo hvis bar-tid <= clock, ellers None."""
    j = None
    for i in range(lo, len(bars)):
        if bars[i].t <= clock:
            j = i
        else:
            break
    return j


def simulate_name(ticker, day, bars, T, exit_mode, target_pct, stop_pct, trail_lb):
    """LONG-only. entry = open paa foerste bar med tid > T. exit iflg. exit_mode paa bar-luk
    (eller target/stop-niveau). Aldrig over natten (senest EOD_FORCE)."""
    ei = _entry_index(bars, T)
    if ei is None:
        return None
    entry = bars[ei].o
    entry_dt = bars[ei].dt
    if entry <= 0:
        return None
    assert entry_dt.timetz().replace(tzinfo=None) > T, "look-ahead: entry-bar ikke efter T"

    eod_idx = _last_idx_le(bars, EOD_FORCE, ei)
    if eod_idx is None or eod_idx < ei:
        eod_idx = len(bars) - 1

    def done(px, x_idx, reason):
        return Trade(ticker, day, (px - entry) / entry * 100.0, entry, entry_dt,
                     bars[x_idx].dt, reason)

    if exit_mode in ("1100", "1200"):
        clock = dtime(11, 0) if exit_mode == "1100" else dtime(12, 0)
        xi = _last_idx_le(bars, clock, ei)
        if xi is None or xi < ei:
            xi = eod_idx                       # T efter midday-klok -> fald til EOD
        return done(bars[xi].c, xi, exit_mode)

    if exit_mode == "ts":
        tgt = entry * (1 + target_pct / 100.0)
        stp = entry * (1 - stop_pct / 100.0)
        for i in range(ei, eod_idx + 1):
            if bars[i].l <= stp:               # konservativt: stop tjekkes FOER target i samme bar
                return done(stp, i, "stop")
            if bars[i].h >= tgt:
                return done(tgt, i, "target")
        return done(bars[eod_idx].c, eod_idx, "eod")

    if exit_mode == "trail":
        r_price = entry * stop_pct / 100.0
        trail_stop = entry - r_price
        activated = False
        for i in range(ei, eod_idx + 1):
            if not activated and bars[i].h >= entry + r_price:   # +1R -> aktiver trailing
                activated = True
            if activated:
                lo_win = min(bars[j].l for j in range(max(ei, i - trail_lb + 1), i + 1))
                trail_stop = max(trail_stop, lo_win)
            if bars[i].l <= trail_stop:
                return done(trail_stop, i, "trail" if activated else "stop")
        return done(bars[eod_idx].c, eod_idx, "eod")

    # default: eod
    return done(bars[eod_idx].c, eod_idx, "eod")


# ═══════════════════════════════════════════════════════════════════
# Omkostning + stats
# ═══════════════════════════════════════════════════════════════════
def net_pct(tr: Trade, cents: float) -> float:
    """Round-trip omkostning i cents/side -> pct af entry-pris.
    cents er CENT ($0.01): round-trip dollars = 2*cents/100; pct = det / entry_px * 100."""
    cost = (2.0 * cents / 100.0) / tr.entry_px * 100.0
    return tr.gross_pct - cost


def pf(rets):
    gw = sum(r for r in rets if r > 0)
    gl = -sum(r for r in rets if r < 0)
    if gl <= 0:
        return float("inf") if gw > 0 else 0.0
    return gw / gl


def fmt_pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def period_of(d: date) -> str:
    return {3: "mar", 4: "apr", 5: "maj"}.get(d.month, "andet")


# ═══════════════════════════════════════════════════════════════════
# Kerne: byg daglige selection-alpha-observationer for en config
# ═══════════════════════════════════════════════════════════════════
@dataclass
class DayObs:
    day: date
    period: str
    n_univ: int
    topk_ret: float            # netto @ read-cost
    univ_ret: float
    botk_ret: float
    alpha: float               # topk_ret - univ_ret
    hit: bool
    topk_trades: list          # [Trade] (til PF)


def resolve_k(k, n):
    if k == "decile":
        return max(1, math.ceil(n / 10))
    return min(int(k), n)


def build_day_obs(universe, data, day, T, variant, k, exit_mode, cents,
                  target_pct, stop_pct, trail_lb):
    """Én dag -> DayObs (eller None). Point-in-time: kun navne i universe[day] kan vaelges."""
    names = universe.get(day, [])
    scored, trades = [], {}
    for tk in names:
        days = data.get(tk)
        if not days or day not in days:
            continue
        bars = days[day]
        if len(bars) < 3:
            continue
        # prior close = seneste tilgaengelige session-luk FOER day (til gap)
        prior_close = None
        prev = [d for d in days if d < day]
        if prev:
            prior_close = days[max(prev)][-1].c
        ns = compute_name_score(tk, bars, prior_close, T, variant)
        tr = simulate_name(tk, day, bars, T, exit_mode, target_pct, stop_pct, trail_lb)
        if tr is None:
            continue
        trades[tk] = tr
        if ns.raw is not None:
            scored.append((ns.raw, tk))
    if len(trades) < 2:
        return None
    univ_rets = [net_pct(tr, cents) for tr in trades.values()]
    univ_ret = st.mean(univ_rets)
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    kk = resolve_k(k, len(scored))
    top = [tk for _r, tk in scored[:kk]]
    bot = [tk for _r, tk in scored[-kk:]]
    topk_trades = [trades[tk] for tk in top if tk in trades]
    botk_trades = [trades[tk] for tk in bot if tk in trades]
    if not topk_trades:
        return None
    topk_ret = st.mean([net_pct(tr, cents) for tr in topk_trades])
    botk_ret = st.mean([net_pct(tr, cents) for tr in botk_trades]) if botk_trades else 0.0
    alpha = topk_ret - univ_ret
    return DayObs(day, period_of(day), len(trades), topk_ret, univ_ret, botk_ret,
                  alpha, topk_ret > univ_ret, topk_trades)


def build_all_obs(universe, data, T, variant, k, exit_mode, cents,
                  target_pct, stop_pct, trail_lb):
    obs = []
    for day in sorted(universe):
        o = build_day_obs(universe, data, day, T, variant, k, exit_mode, cents,
                          target_pct, stop_pct, trail_lb)
        if o is not None:
            obs.append(o)
    return obs


# ═══════════════════════════════════════════════════════════════════
# Aggregering pr. periode
# ═══════════════════════════════════════════════════════════════════
@dataclass
class PeriodAgg:
    period: str
    n_days: int
    n_trades: int
    topk_abs: float
    univ_abs: float
    alpha_med: float
    alpha_mean: float
    hit_rate: float
    pf_topk: float
    disp: float                # top-K minus bund-K (diagnostik)


def aggregate(obs, period=None):
    sub = [o for o in obs if period is None or o.period == period]
    if not sub:
        return None
    alphas = [o.alpha for o in sub]
    all_topk = [tr for o in sub for tr in o.topk_trades]
    n_trades = len(all_topk)
    return PeriodAgg(
        period=period or "alle",
        n_days=len(sub),
        n_trades=n_trades,
        topk_abs=st.mean([o.topk_ret for o in sub]),
        univ_abs=st.mean([o.univ_ret for o in sub]),
        alpha_med=st.median(alphas),
        alpha_mean=st.mean(alphas),
        hit_rate=sum(1 for o in sub if o.hit) / len(sub),
        pf_topk=pf([net_pct(tr, COST_READ_CENTS) for tr in all_topk]),
        disp=st.mean([o.topk_ret - o.botk_ret for o in sub]),
    )


# ═══════════════════════════════════════════════════════════════════
# Hedge (afsnit 7): short notional-matchet M2K over dagens vindue
# ═══════════════════════════════════════════════════════════════════
def m2k_window_ret(m2k, day, T, exit_mode):
    """M2K %-afkast over [entry-klok≈T, exit-klok]. Nearest-bar. None hvis ingen daekning."""
    bars = m2k.get(day)
    if not bars:
        return None
    exit_clock = {"1100": dtime(11, 0), "1200": dtime(12, 0)}.get(exit_mode, EOD_FORCE)
    entry_c = next((c for dt, c in bars if dt.timetz().replace(tzinfo=None) >= T), None)
    exit_c = None
    for dt, c in bars:
        if dt.timetz().replace(tzinfo=None) <= exit_clock:
            exit_c = c
    if entry_c is None or exit_c is None or entry_c <= 0:
        return None
    return (exit_c - entry_c) / entry_c * 100.0


def hedged_period(obs, m2k, T, exit_mode, cents, m2k_cost_bp=1.0, period=None):
    """Aggregér daglig (topk_ret - m2k_ret) — short M2K-ben, markeds-neutral."""
    sub = [o for o in obs if period is None or o.period == period]
    rets = []
    for o in sub:
        mret = m2k_window_ret(m2k, o.day, T, exit_mode)
        if mret is None:
            continue
        rets.append(o.topk_ret - mret - m2k_cost_bp * 0.01)   # short M2K -> traek dens afkast
    if not rets:
        return None
    return dict(n=len(rets), mean=st.mean(rets), med=st.median(rets),
                hit=sum(1 for r in rets if r > 0) / len(rets))


# ═══════════════════════════════════════════════════════════════════
# Haerdning (--months all): frossen config, per-maaned re-validering
# ═══════════════════════════════════════════════════════════════════
def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _month_stats(obs, month):
    """Selection-alpha-stats for én maaned ud af en obs-liste (bygget ved en given cents)."""
    sub = [o for o in obs if month_key(o.day) == month]
    if not sub:
        return None
    al = sorted(o.alpha for o in sub)
    return dict(
        n=len(sub),
        med=st.median(al),
        mean=st.mean(al),
        hit=sum(1 for o in sub if o.hit) / len(sub),
        no_best=st.mean(al[:-1]) if len(al) > 1 else al[0],
        no_worst=st.mean(al[1:]) if len(al) > 1 else al[0],
    )


def _region_grid(universe, data, months_set, variant, cents=COST_READ_CENTS):
    """Median selection-alpha (netto @cents) pr. (T,K) i den frosne region, over months_set.
    Returnerer ({(T,K): med}, antal_positive, antal_celler)."""
    cells, pos, tot = {}, 0, 0
    for T in HARDEN_TS:
        for K in HARDEN_KS:
            obs = build_all_obs(universe, data, T, variant, K, HARDEN_EXIT, cents,
                                TARGET_PCT_DEFAULT, STOP_PCT_DEFAULT, TRAIL_LB_DEFAULT)
            sub = [o for o in obs if month_key(o.day) in months_set]
            med = st.median([o.alpha for o in sub]) if sub else None
            cells[(T, K)] = med
            if med is not None:
                tot += 1
                pos += (med > 0)
    return cells, pos, tot


def run_hardening(universe, data, e):
    """--months all: RE-VALIDER den frosne config paa alle tilgaengelige maaneder."""
    e("── HAERDNINGS-RE-VALIDERING (frossen config; RE-VALIDERING, IKKE re-optimering) ──")
    sc0, T0, K0 = HARDEN_PRIMARY
    e(f"   Frossen: score={{early_rs,above_vwap}}  exit={HARDEN_EXIT}  T in "
      f"{[t.strftime('%H%M') for t in HARDEN_TS]}  K in {HARDEN_KS}. INTET nyt sweep.")
    months = sorted({month_key(d) for d in universe})
    old = sorted(m for m in months if m in OLD_MONTHS)
    new = [m for m in months if m not in OLD_MONTHS]
    e(f"   Maaneder tilgaengelige: {months}")
    e(f"   Discovery (config valgt her): {old or '(ingen)'}   |   NYE OOS-maaneder: {new or '(INGEN)'}")
    if not new:
        e("   >> INGEN nye maaneder endnu. Koer build_historical_universe.py for andre maaneder")
        e("      (fx 2026-01/02 bagud, 2026-06 frem) + hent bars, og gentag --months all.")
    e("")

    # A. Primaer-celle pr. maaned (early_rs/0945/K3/eod), @1c og @2c + outlier-tjek
    obs1 = build_all_obs(universe, data, T0, sc0, K0, HARDEN_EXIT, 1.0,
                         TARGET_PCT_DEFAULT, STOP_PCT_DEFAULT, TRAIL_LB_DEFAULT)
    obs2 = build_all_obs(universe, data, T0, sc0, K0, HARDEN_EXIT, 2.0,
                         TARGET_PCT_DEFAULT, STOP_PCT_DEFAULT, TRAIL_LB_DEFAULT)
    e(f"  A. PRIMAER CELLE {sc0}/{T0.strftime('%H%M')}/K{K0}/eod — selection alpha pr. maaned")
    e(f"     {'maaned':>8}{'dage':>6}{'med@1c':>9}{'med@2c':>9}{'snit@1c':>9}{'hit':>7}"
      f"{'u.bedste':>10}{'u.vaerste':>11}{'type':>7}")
    for m in months:
        a1, a2 = _month_stats(obs1, m), _month_stats(obs2, m)
        if not a1:
            continue
        typ = "OOS" if m not in OLD_MONTHS else "disc"
        e(f"     {m:>8}{a1['n']:>6}{a1['med']:>+9.3f}{a2['med']:>+9.3f}{a1['mean']:>+9.3f}"
          f"{a1['hit']:>7.2f}{a1['no_best']:>+10.3f}{a1['no_worst']:>+11.3f}{typ:>7}")
    e("     [med = median daglig alpha; u.bedste/u.vaerste = snit uden hhv. bedste/vaerste dag (outlier-tjek).]")
    e("")

    # B. Frossen region — celler m. positiv median-alpha @1c (nye vs discovery), begge scores
    e("  B. FROSSEN REGION (eod) — median-alpha @1c pr. (T,K); tegn: + positiv, - negativ")
    for grp_name, mset in (("NYE OOS-maaneder", set(new)), ("Discovery-maaneder", set(old))):
        if not mset:
            continue
        e(f"     [{grp_name}]")
        for variant in HARDEN_SCORES:
            cells, pos, tot = _region_grid(universe, data, mset, variant)
            row = []
            for T in HARDEN_TS:
                for K in HARDEN_KS:
                    med = cells[(T, K)]
                    tag = "  na" if med is None else f"{med:>+6.2f}"
                    row.append(f"{T.strftime('%H%M')}/K{K}={tag}")
                    _ = tag
            e(f"       {variant:>11}: " + "  ".join(row) + f"   [{pos}/{tot} positive]")
    e("")

    # C. Haerdnings-dom (praeregistreret, 4 betingelser) — doemmes paa NYE maaneder
    e("  C. HAERDNINGS-DOM (praeregistreret; doemt paa NYE OOS-maaneder)")
    if not new:
        e("     Kan ikke afgoeres endnu: ingen nye maaneder. Byg dem og gentag.")
        return
    new_stats = [_month_stats(obs1, m) for m in new]
    new_stats = [s for s in new_stats if s]
    n_new = len(new_stats)
    c1_pos = sum(1 for s in new_stats if s['med'] > 0)
    c2_hit = sum(1 for s in new_stats if s['hit'] > 0.50)
    c1 = c1_pos > n_new / 2
    c2 = c2_hit > n_new / 2
    _c, ev_pos, ev_tot = _region_grid(universe, data, set(new), "early_rs")
    _c2, av_pos, av_tot = _region_grid(universe, data, set(new), "above_vwap")
    c3 = av_tot > 0 and av_pos > av_tot / 2                     # above_vwap korroborerer (flertal positive)
    c4 = ev_tot > 0 and ev_pos >= max(1, int(0.6 * ev_tot))    # region intakt (>=60% af early_rs-celler positive)
    e(f"     1. alpha-median @1c positiv i flertal af nye maaneder : {'JA' if c1 else 'NEJ'} ({c1_pos}/{n_new})")
    e(f"     2. hit-rate > 0.50 i flertal af nye maaneder          : {'JA' if c2 else 'NEJ'} ({c2_hit}/{n_new})")
    e(f"     3. above_vwap korroborerer (flertal celler positive)  : {'JA' if c3 else 'NEJ'} ({av_pos}/{av_tot})")
    e(f"     4. early_rs-region intakt (>=60% celler positive)     : {'JA' if c4 else 'NEJ'} ({ev_pos}/{ev_tot})")
    if c1 and c2 and c3 and c4:
        e("     -> HAERDNING BESTAAET: plateauet overlever paa nye maaneder. Live-wrapperens")
        e("        Route B-paper-test faar staerkere rygdaekning. Parametre uaendret.")
    else:
        e("     -> HAERDNING FEJLER (>=1 betingelse): selection-alphaen var sandsynligvis")
        e("        et smaastikproeve-artefakt. STOP live-udrulning og re-evaluer (tilbage til fingeraftrykket).")


# ═══════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════
PREREG = [
    "PRAEREGISTRERET DOM (skrevet FOER resultater; alle 3 skal holde for den valgte config):",
    "  1. selection alpha (top-K minus universe-snit) POSITIV netto ved >= 1c i BAADE maj OG apr",
    "  2. hit-rate (andel dage top-K > universe-snit) > 0.50 i BAADE maj OG apr",
    "  3. robust PLATEAU: positiv alpha over en REGION af T x K x score x exit (ses i --sweep), ikke ét punkt",
    "  Ellers -> spor D forkastes. maj = in-sample, apr = OOS. mar (2026-03-30..31) = kort sanity, ikke dom.",
    "  Alt beskrivende. Edge = SELECTION ALPHA (top-K minus universe-snit); absolut top-K er survivorship-loeftet.",
]


def _fmt_agg(a: PeriodAgg):
    return (f"  {a.period:>6}{a.n_days:>7}{a.n_trades:>8}{a.topk_abs:>10.2f}{a.univ_abs:>10.2f}"
            f"{a.alpha_med:>11.3f}{a.alpha_mean:>11.3f}{a.hit_rate:>9.2f}{fmt_pf(a.pf_topk):>8}{a.disp:>9.2f}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Tvaersnitlig relativ styrke / dispersions-backtest (spor D)")
    ap.add_argument("--score", choices=SCORES, default="early_rs")
    ap.add_argument("--time", default="0945", help="beslutningstid HHMM: 0935/0945/1000/1015")
    ap.add_argument("--k", default="3", help="antal navne: 1/2/3/5 eller 'decile'")
    ap.add_argument("--exit", choices=EXITS, default="eod")
    ap.add_argument("--target", type=float, default=TARGET_PCT_DEFAULT, help="target%% (exit=ts)")
    ap.add_argument("--stop", type=float, default=STOP_PCT_DEFAULT, help="stop%% (exit=ts/trail)")
    ap.add_argument("--trail-lb", type=int, default=TRAIL_LB_DEFAULT, help="swing-low lookback (exit=trail)")
    ap.add_argument("--cost-cents", type=float, default=COST_READ_CENTS, help="doms-omkostning cents/side")
    ap.add_argument("--hedge", choices=["none", "m2k"], default="none")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--months", default=None,
                    help="'all' = HAERDNING: re-valider frossen config per maaned (intet nyt sweep)")
    args = ap.parse_args()

    def parse_time(s):
        s = s.replace(":", "").zfill(4)
        return dtime(int(s[:2]), int(s[2:]))

    T = parse_time(args.time)
    k = args.k if args.k == "decile" else int(args.k)

    OUT_DIR.mkdir(exist_ok=True)
    lines = []

    def e(s=""):
        print(s, flush=True)
        lines.append(s)

    e("=" * 92)
    e("  TVAERSNITLIG RELATIV STYRKE / DISPERSIONS-BACKTEST (spor D)")
    e("=" * 92)
    for ln in PREREG:
        e(ln)
    e("")

    universe = load_universe()
    if not universe:
        e("  INGEN universe-filer -> kan ikke backteste.")
        for n in NOTES:
            e("  NOTE: " + n)
        (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    all_names = sorted({tk for ticks in universe.values() for tk in ticks})
    e(f"Univers: {len(universe)} handelsdage, {len(all_names)} unikke navne "
      f"({min(universe)} .. {max(universe)}).")
    e("Indlaeser 1-min bars (bar_cache/) ...")
    data = {tk: load_ticker_days(tk) for tk in all_names}
    covered = sum(1 for tk in all_names if data.get(tk))
    e(f"Bar-daekning: {covered}/{len(all_names)} navne med brugbare RTH-bars.")
    e("")

    # Forbehold (spec afsnit 2 + 9) — gentages altid.
    e("FORBEHOLD (skal laeses med resultatet):")
    e("  - Universe-mismatch: MIDCAP-filerne er IKKE det live $3-500 small-cap-gapper-univers.")
    e("    Backtesten maaler MEKANIKKEN; live koerer paa scannerens univers, paper er dommeren (Route B).")
    e("  - Survivorship: universe-navne er valgt fra navne vi VED loeb. Vi doemmer paa SPAENDET")
    e("    (top-K minus universe-snit) netop for at traekke det faelles survivorship-loeft ud.")
    e("  - Entry = naeste bar's OPEN efter T (konservativt); exit = bar-luk (eller target/stop-niveau).")
    e("  - Long-only (small-cap borrow-veto). Absolut top-K-afkast er survivorship-loeftet: kun spaendet taeller.")
    e("")

    m2k = load_m2k_hedge() if args.hedge == "m2k" else {}

    # ── HAERDNING (--months all): frossen config, per-maaned re-validering ──
    if args.months == "all":
        e("PRAEREGISTRERET HAERDNINGS-DOM (skrevet FOER nye maaneder; alle 4 skal holde):")
        e("  1. selection alpha (median @1c) positiv i FLERTAL af nye maaneder")
        e("  2. hit-rate > 0.50 i FLERTAL af nye maaneder")
        e("  3. above_vwap korroborerer (samme fortegn i samme T/K-region)")
        e("  4. plateauet forbliver en sammenhaengende REGION (early_rs-eod-celler)")
        e("  RE-VALIDERING, ikke re-optimering: de fire parametre aendres ALDRIG. Intet nyt sweep.")
        e("")
        run_hardening(universe, data, e)
        for n in NOTES:
            e("  NOTE: " + n)
        (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        e(f"\nFil: {OUT_DIR / 'summary.txt'}")
        return 0

    # ── SWEEP ──
    if args.sweep:
        e("── PLATEAU-SWEEP: selection alpha (median, netto @1c) + hit-rate, maj (IS) og apr (OOS) ──")
        e("   Kig efter en REGION med positiv alpha OG hit>0.50 i BEGGE — ikke ét enkelt punkt.")
        e(f"   {'score':>12}{'T':>7}{'K':>7}{'exit':>7}"
          f"{'maj_a':>9}{'maj_hit':>9}{'maj_d':>7}{'apr_a':>9}{'apr_hit':>9}{'apr_d':>7}")
        for variant in SCORES:
            for Tt in TIMES:
                for kk in KS:
                    for xm in ("eod", "1100", "ts", "trail"):
                        obs = build_all_obs(universe, data, Tt, variant, kk, xm,
                                            COST_READ_CENTS, args.target, args.stop, args.trail_lb)
                        maj = aggregate(obs, "maj")
                        apr = aggregate(obs, "apr")
                        if not maj or not apr or maj.n_days < 5 or apr.n_days < 5:
                            continue
                        ks = kk if kk == "decile" else str(kk)
                        e(f"   {variant:>12}{Tt.strftime('%H%M'):>7}{ks:>7}{xm:>7}"
                          f"{maj.alpha_med:>9.3f}{maj.hit_rate:>9.2f}{maj.n_days:>7}"
                          f"{apr.alpha_med:>9.3f}{apr.hit_rate:>9.2f}{apr.n_days:>7}")
        for n in NOTES:
            e("  NOTE: " + n)
        (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        e(f"\nFil: {OUT_DIR / 'summary.txt'}")
        return 0

    # ── ENKELT-CONFIG ──
    ks = k if k == "decile" else str(k)
    e(f"CONFIG: score={args.score}  T={T.strftime('%H:%M')}  K={ks}  exit={args.exit}"
      + (f" (target={args.target}% stop={args.stop}%)" if args.exit == "ts"
         else f" (stop={args.stop}% lb={args.trail_lb})" if args.exit == "trail" else "")
      + f"  doms-kost={args.cost_cents}c/side")
    e("")

    obs = build_all_obs(universe, data, T, args.score, k, args.exit,
                        args.cost_cents, args.target, args.stop, args.trail_lb)
    if not obs:
        e("  Ingen brugbare dags-observationer (for tynd bar-daekning).")
        (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    e("  SELECTION ALPHA pr. periode (top-K minus universe-snit; afkast i %, netto @doms-kost)")
    e(f"  {'periode':>6}{'dage':>7}{'handl':>8}{'topK_abs':>10}{'univ_abs':>10}"
      f"{'alpha_med':>11}{'alpha_snit':>11}{'hit':>9}{'PF_top':>8}{'top-bund':>9}")
    for per in ("maj", "apr", "mar", "alle"):
        a = aggregate(obs, None if per == "alle" else per)
        if a:
            e(_fmt_agg(a))
    e("  [topK_abs = survivorship-loeftet, IKKE trovaerdig absolut edge. alpha = det der taeller.]")
    e("")

    # Skriv trades-CSV for den valgte config
    csv_name = f"trades_{args.score}_{T.strftime('%H%M')}_k{ks}.csv"
    with (OUT_DIR / csv_name).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "period", "ticker", "entry_dt", "exit_dt", "entry_px",
                    "gross_pct", "net_pct_1c", "reason", "day_alpha", "day_hit"])
        for o in obs:
            for tr in o.topk_trades:
                w.writerow([o.day, o.period, tr.ticker, tr.entry_dt.isoformat(),
                            tr.exit_dt.isoformat(), f"{tr.entry_px:.4f}",
                            f"{tr.gross_pct:.4f}", f"{net_pct(tr, 1.0):.4f}", tr.reason,
                            f"{o.alpha:.4f}", int(o.hit)])
    e(f"  Handels-CSV: {OUT_DIR / csv_name}")
    e("")

    # ── HEDGE (valgfri) ──
    if args.hedge == "m2k" and m2k:
        e("  BETA-HEDGE (short M2K-ben, notional-matchet; markeds-neutral dispersion)")
        e("  Daglig (topK_ret - M2K_ret), netto. Sammenlign m. u-hedget topK_abs ovenfor.")
        e(f"  {'periode':>6}{'dage':>7}{'hedged_snit':>13}{'hedged_med':>12}{'hit':>8}")
        for per in ("maj", "apr", "alle"):
            h = hedged_period(obs, m2k, T, args.exit, args.cost_cents,
                              period=None if per == "alle" else per)
            if h:
                e(f"  {per:>6}{h['n']:>7}{h['mean']:>13.3f}{h['med']:>12.3f}{h['hit']:>8.2f}")
        e("  [Hedge aendrer IKKE selektionen; kun ren markeds-eksponering fjernes. M2K = small-cap-beta.]")
        e("")

    # ── DOM (praeregistreret) ──
    e("─" * 92)
    e("  DOM (praeregistreret regel — evalueret paa denne config)")
    e("─" * 92)
    maj, apr = aggregate(obs, "maj"), aggregate(obs, "apr")
    if not maj or not apr:
        e("  Utilstraekkelig maj/apr-daekning til at afgoere dommen.")
    else:
        c1 = maj.alpha_med > 0 and apr.alpha_med > 0        # positiv alpha (median) @doms-kost i begge
        c2 = maj.hit_rate > 0.50 and apr.hit_rate > 0.50
        e(f"  1. alpha positiv @>=1c i BAADE maj OG apr : {'JA' if c1 else 'NEJ'} "
          f"(maj_med={maj.alpha_med:+.3f}, apr_med={apr.alpha_med:+.3f})")
        e(f"  2. hit-rate > 0.50 i BAADE maj OG apr      : {'JA' if c2 else 'NEJ'} "
          f"(maj={maj.hit_rate:.2f}, apr={apr.hit_rate:.2f})")
        e(f"  3. robust PLATEAU (T x K x score x exit)    : koer --sweep og laes af (ikke afgjort her)")
        if c1 and c2:
            e("  -> BETINGELSE 1+2 OPFYLDT for denne config. Bekraeft PLATEAU med --sweep FOER")
            e("     spor D erklaeres leve-testbar. Er plateauet der ogsaa -> skriv live-wrapper-spec.")
        else:
            e("  -> BETINGELSE 1 og/eller 2 FEJLER for denne config. Ingen selektions-edge her.")
            e("     Tjek --sweep for om en ANDEN region holder; ellers forkastes spor D aerligt.")
    e("")
    for n in NOTES:
        e("  NOTE: " + n)

    (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    e(f"\nFil: {OUT_DIR / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
