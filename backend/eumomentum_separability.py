#!/usr/bin/env python3
"""
eumomentum_separability.py — EUMOMENTUM, Trin 1: separabilitets-studie.

KOEBER INTET. BYGGER INGEN STRATEGI. Rent offline, kun stdlib.

Svarer paa ét spoergsmaal: naar MES/M2K er straekket til |z| >= 2.0 i den europaeiske
session — findes der da en variabel, kendt VED BAR-LUK, som adskiller de straek der
FORTSAETTER (til |z| >= 3.5) fra dem der REVERTERER (til |z| <= 0.5)?

z beregnes via den DELTE regel (strategies.europa_reversion.rule.compute_z) — IKKE
reimplementeret. Sessionsgating + contiguous spejler eureversion_backtest.py 1:1.

Output: ./eumomentum_separability_output/{events.csv, summary.txt}. summary.txt afsluttes
med "DOM: <GROEN|GUL|ROED> — <kriterium>".

Koersel:
    python eumomentum_separability.py
    python eumomentum_separability.py --only MES
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
import sys
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ── DELT SANDHEDSKILDE — ikke til forhandling ────────────────────────────────
from strategies.europa_reversion.rule import compute_z
from strategies.europa_reversion.config import (
    LOOKBACK, ENTRY_Z, EXIT_Z, STOP_Z,
    SESSION_START_ET, SESSION_END_ET, FORCE_CLOSE_ET, LAST_SESSION_BAR_ET,
    BAR_MINUTES, INSTRUMENTS, MULTIPLIER,
)

BAR_SECONDS = BAR_MINUTES * 60           # 900 (spejler backtestens BAR_SECONDS)
CONF_Z = 1.5                             # F7: "det andet instrument straekket" ved |z| >= 1.5
US_HOURS = set(range(9, 16))             # amerikansk session (ET-timer) — til F8-gap
COST_LEVELS_BP = [0.0, 1.0, 2.0, 3.0]    # rundtur i basispunkter (som eureversion_backtest)
OUTPUT_DIRNAME = "eumomentum_separability_output"


@dataclass
class Bar:
    ts: datetime          # tz-aware (ET-offset i CSV'en)
    o: float
    h: float
    l: float
    c: float
    v: float


# ── Loader — EGEN, fuld OHLCV (roerer ikke backtestens close-only loader) ─────
def load_ohlcv(path: Path) -> list[Bar]:
    out: list[Bar] = []
    if not path.exists():
        return out
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            ts_raw = row.get("timestamp", "")
            if "T" not in ts_raw:
                return []
            try:
                out.append(Bar(
                    datetime.fromisoformat(ts_raw),
                    float(row["open"]), float(row["high"]), float(row["low"]),
                    float(row["close"]), float(row.get("volume", 0) or 0),
                ))
            except (ValueError, KeyError):
                continue
    return sorted(out, key=lambda b: b.ts)


# ── Session + contiguous (spejler eureversion_backtest.py) ───────────────────
def contiguous(bars: list[Bar], i: int, lookback: int) -> bool:
    """Er de seneste lookback+1 bars (i-lookback..i) alle 15-min sammenhaengende?"""
    if i - lookback < 0:
        return False
    for k in range(i - lookback + 1, i + 1):
        if (bars[k].ts - bars[k - 1].ts).total_seconds() != BAR_SECONDS:
            return False
    return True


def in_eu_session(ts: datetime) -> bool:
    """Bar i den europaeiske session: SESSION_START_ET <= tid <= LAST_SESSION_BAR_ET (ET)."""
    t = ts.time()
    return SESSION_START_ET <= t <= LAST_SESSION_BAR_ET


# ── Indikator-serier (kun fortids-bars; point-in-time) ───────────────────────
def ema_series(vals: list[float], span: int) -> list[float]:
    k = 2.0 / (span + 1.0)
    out: list[float] = []
    prev = vals[0] if vals else 0.0
    for i, v in enumerate(vals):
        prev = v if i == 0 else v * k + prev * (1 - k)
        out.append(prev)
    return out


def atr_series(bars: list[Bar], period: int = 14) -> list[float]:
    """ATR(period) pr. bar. atr[i] afhaenger kun af bars <= i (ingen look-ahead)."""
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.h - b.l)
        else:
            pc = bars[i - 1].c
            trs.append(max(b.h - b.l, abs(b.h - pc), abs(b.l - pc)))
    out: list[float] = []
    for i in range(len(bars)):
        lo = max(0, i - period + 1)
        out.append(st.fmean(trs[lo:i + 1]))
    return out


def z_at(bars: list[Bar], i: int, lookback: int):
    """(z_close, sd, ma) via den DELTE compute_z, eller None. ma udledes af z=(c-ma)/sd."""
    if not contiguous(bars, i, lookback):
        return None
    closes = [b.c for b in bars[i - lookback + 1:i + 1]]
    res = compute_z(closes)
    if res is None:
        return None
    z, sd = res
    ma = bars[i].c - z * sd
    return z, sd, ma


# ── Event-detektion ──────────────────────────────────────────────────────────
@dataclass
class Event:
    inst: str
    i: int                # bar-index
    ts: datetime
    z: float
    close: float
    side: str             # "up" (z>=+2) | "down" (z<=-2)


def detect_events(bars: list[Bar]) -> list[Event]:
    """Bar hvor |z|>=ENTRY_Z, forrige bars |z|<ENTRY_Z (kun KRYDSET), i session,
    LOOKBACK sammenhaengende. Spejler live: aabnes kun ved flad position."""
    events: list[Event] = []
    prev_abs = 0.0
    prev_ok = False
    for i in range(len(bars)):
        za = z_at(bars, i, LOOKBACK)
        cur_abs = abs(za[0]) if za else 0.0
        if za and in_eu_session(bars[i].ts) and cur_abs >= ENTRY_Z and not (prev_ok and prev_abs >= ENTRY_Z):
            z = za[0]
            events.append(Event("", i, bars[i].ts, z, bars[i].c, "up" if z >= 0 else "down"))
        prev_abs, prev_ok = cur_abs, bool(za)
    return events


# ── Labeling (extension/reversion/timeout) + konservativ tie-break + MFE/MAE ──
@dataclass
class Label:
    outcome: str          # "EXTENSION" | "REVERSION" | "TIMEOUT"
    tie_break: bool
    exit_close: float
    mfe_pts: float
    mae_pts: float


def label_event(bars: list[Bar], ev: Event) -> Label:
    entry = ev.close
    up = ev.side == "up"
    mfe = 0.0            # max favorable (momentum-retning) i prispoint
    mae = 0.0            # max adverse (mod momentum) i prispoint
    j = ev.i + 1
    while j < len(bars):
        b = bars[j]
        if not (b.ts.date() == ev.ts.date() and in_eu_session(b.ts) and contiguous(bars, j, LOOKBACK)):
            break
        # MFE/MAE for en momentum-handel entret ved event-close.
        if up:
            mfe = max(mfe, b.h - entry); mae = min(mae, b.l - entry)
        else:
            mfe = max(mfe, entry - b.l); mae = min(mae, entry - b.h)
        za = z_at(bars, j, LOOKBACK)
        if za is None:
            break
        z, sd, ma = za
        if sd <= 0:
            j += 1; continue
        z_hi = (b.h - ma) / sd
        z_lo = (b.l - ma) / sd
        if up:
            ext_implied = z_hi >= STOP_Z            # straek fortsaetter opad
            rev_implied = z_lo <= EXIT_Z            # tilbage mod middel
        else:
            ext_implied = z_lo <= -STOP_Z           # straek fortsaetter nedad
            rev_implied = z_hi >= -EXIT_Z
        if ext_implied and rev_implied:
            return Label("REVERSION", True, b.c, mfe, mae)   # tvivl -> momentums ulempe
        if ext_implied:
            return Label("EXTENSION", False, b.c, mfe, mae)
        if rev_implied:
            return Label("REVERSION", False, b.c, mfe, mae)
        j += 1
    # Ingen taerskel naaet foer sessions-slut/tvangsluk.
    exit_close = bars[j - 1].c if j - 1 > ev.i else entry
    return Label("TIMEOUT", False, exit_close, mfe, mae)


# ── Faktor-katalog (LAAST). Alle paa bars <= event-baren. Hard look-ahead-assert. ──
def compute_factors(bars, ev, ema20, ema50, atr14, slot_index, other_zmap) -> dict:
    i = ev.i
    up = ev.side == "up"
    f: dict = {}

    # Hard look-ahead-assert: intet vindue maa roere en bar med ts > event_ts.
    window = bars[max(0, i - 99):i + 1]
    assert max(b.ts for b in window) <= ev.ts, \
        f"LOOK-AHEAD: faktor saa bar {max(b.ts for b in window)} > event {ev.ts}"

    # F1 — trend-medvind: sign(EMA20 - EMA50) == straek-retning.
    trend_up = ema20[i] >= ema50[i]
    f["F1_trend_aligned"] = int(trend_up == up)

    # F2 — range-udvidelse: ATR14(event) / median ATR14 over sidste 100 bars.
    a_win = atr14[max(0, i - 99):i + 1]
    med_atr = st.median(a_win) if a_win else 0.0
    f["F2_atr_ratio"] = (atr14[i] / med_atr) if med_atr > 0 else 1.0

    # F3 — RVOL slot-normaliseret: event-vol / median vol for SAMME 15-min slot, kun tidligere.
    slot = ev.ts.strftime("%H:%M")
    tslist, vlist = slot_index.get(slot, ([], []))
    pos = bisect_left(tslist, ev.ts)                 # kun bars STRENGT foer event
    prior_v = vlist[:pos]
    assert not tslist[:pos] or tslist[pos - 1] < ev.ts, "LOOK-AHEAD: RVOL-slot inkluderede event-bar"
    med_v = st.median(prior_v) if prior_v else 0.0
    f["F3_rvol"] = (bars[i].v / med_v) if med_v > 0 else 1.0

    # F4 — bar-struktur: luk i straekkets ende (naer 1.0 = fortsaettelse).
    rng = bars[i].h - bars[i].l
    if rng > 0:
        f["F4_bar_struct"] = (bars[i].c - bars[i].l) / rng if up else (bars[i].h - bars[i].c) / rng
    else:
        f["F4_bar_struct"] = 0.5

    # F5 — impuls-persistens: antal af sidste 3 bars der lukkede i straekkets retning.
    p_win = bars[max(0, i - 2):i + 1]
    assert max(b.ts for b in p_win) <= ev.ts, "LOOK-AHEAD: F5"
    f["F5_persistence"] = sum(1 for b in p_win if (b.c > b.o) == up)

    # F6 — tidspunkt: ET-time (08-09 dansk = 02-03 ET, ...).
    f["F6_hour_et"] = ev.ts.hour

    # F7 — tvaer-instrument-konfluens: det ANDET instrument straekket samme vej (|z|>=1.5) samme bar.
    oz = other_zmap.get(ev.ts)
    if oz is None:
        f["F7_confluence"] = 0
    else:
        f["F7_confluence"] = int(abs(oz) >= CONF_Z and (oz >= 0) == up)

    # F8 — aabnings-gap (bp): session-aabningsbarens open vs forrige dags sidste US-close.
    f["F8_abs_gap_bp"] = ev._gap_bp
    return f


# ── Analyse-hjaelpere ────────────────────────────────────────────────────────
def ext_rate(rows: list[dict]) -> float:
    return sum(1 for r in rows if r["label"] == "EXTENSION") / len(rows) if rows else 0.0


def pctile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    idx = min(len(s) - 1, int(q * len(s)))
    return s[idx]


def fmt_rate(rows: list[dict]) -> str:
    if not rows:
        return "n=0"
    n = len(rows)
    e = sum(1 for r in rows if r["label"] == "EXTENSION")
    rv = sum(1 for r in rows if r["label"] == "REVERSION")
    to = n - e - rv
    return f"n={n:>4}  ext {e/n*100:4.1f}%  rev {rv/n*100:4.1f}%  timeout {to/n*100:4.1f}%"


def naive_pf(rows: list[dict], cost_bp: float):
    """PF/win/avg/total for naiv momentum-handel: entry=event-close, exit=label-barens close.
    net_pct = gross_pct - cost_bp*0.01 (1 bp = 0.01 %)."""
    gp, gl, wins, nets = 0.0, 0.0, 0, []
    for r in rows:
        entry = r["close"]; ex = r["exit_close"]
        if entry <= 0:
            continue
        raw = (ex - entry) / entry * 100.0
        gross = raw if r["side"] == "up" else -raw     # momentum foelger straekket
        net = gross - cost_bp * 0.01
        nets.append(net)
        if net > 0:
            gp += net; wins += 1
        else:
            gl += -net
    n = len(nets)
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    return {"pf": pf, "win": wins / n * 100 if n else 0.0,
            "avg": sum(nets) / n if n else 0.0, "total": sum(nets), "n": n}


# ── Kandidat-signalbuckets (til lift + dom) ──────────────────────────────────
def build_candidates(is_rows: list[dict]):
    """Definér hver faktors 'signal-bucket' + praedikat (event->bool). Kontinuerte
    taerskler pinnes paa IN-SAMPLE (ingen OOS-laek). Returnerer liste af
    (navn, praedikat)."""
    def col(key): return [r["f"][key] for r in is_rows]
    thr = {
        "F2": pctile(col("F2_atr_ratio"), 0.75),
        "F3": pctile(col("F3_rvol"), 0.75),
        "F4": pctile(col("F4_bar_struct"), 0.75),
        "F8": pctile(col("F8_abs_gap_bp"), 0.75),
        "F3_med": st.median(col("F3_rvol")) if is_rows else 0.0,
        "F4_med": st.median(col("F4_bar_struct")) if is_rows else 0.0,
    }
    # Bedste time (F6): time med hoejest in-sample ext-rate (n>=30).
    by_hour: dict = {}
    for r in is_rows:
        by_hour.setdefault(r["f"]["F6_hour_et"], []).append(r)
    best_hour, best_hr_rate = None, -1.0
    for h, rr in by_hour.items():
        if len(rr) >= 30 and ext_rate(rr) > best_hr_rate:
            best_hour, best_hr_rate = h, ext_rate(rr)

    cands = [
        ("F1 trend-medvind (aligned)",           lambda r: r["f"]["F1_trend_aligned"] == 1),
        ("F2 range-udvidelse (oevre kvartil)",   lambda r: r["f"]["F2_atr_ratio"] >= thr["F2"]),
        ("F3 RVOL (oevre kvartil)",              lambda r: r["f"]["F3_rvol"] >= thr["F3"]),
        ("F4 bar-struktur (oevre kvartil)",      lambda r: r["f"]["F4_bar_struct"] >= thr["F4"]),
        ("F5 impuls-persistens (=3)",            lambda r: r["f"]["F5_persistence"] == 3),
        (f"F6 bedste time ({best_hour}:00 ET)" if best_hour is not None else "F6 (ingen)",
                                                 lambda r: r["f"]["F6_hour_et"] == best_hour),
        ("F7 tvaer-instrument-konfluens",        lambda r: r["f"]["F7_confluence"] == 1),
        ("F8 aabnings-gap (oevre kvartil |bp|)", lambda r: r["f"]["F8_abs_gap_bp"] >= thr["F8"]),
        # Pre-registrerede 2-faktor-par (KUN disse tre).
        ("F7 x F1 (konfluens & medvind)",        lambda r: r["f"]["F7_confluence"] == 1 and r["f"]["F1_trend_aligned"] == 1),
        ("F7 x F3 (konfluens & hoej RVOL)",      lambda r: r["f"]["F7_confluence"] == 1 and r["f"]["F3_rvol"] >= thr["F3_med"]),
        ("F1 x F4 (medvind & bar-struktur)",     lambda r: r["f"]["F1_trend_aligned"] == 1 and r["f"]["F4_bar_struct"] >= thr["F4_med"]),
    ]
    return cands


def cand_stats(rows: list[dict], pred, p0: float):
    sub = [r for r in rows if pred(r)]
    n = len(sub)
    rate = ext_rate(sub) if n else 0.0
    return {"n": n, "rate": rate, "lift": rate - p0}


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="EUMOMENTUM Trin 1 — separabilitets-studie (offline)")
    ap.add_argument("--data-dir", default="data_harvest")
    ap.add_argument("--only", default=None, help="kun ét instrument (fx MES); F7 laeser stadig det andet")
    a = ap.parse_args()

    data_dir = Path(a.data_dir)
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)

    L: list[str] = []
    def emit(s=""):
        print(s); L.append(s)

    emit("=" * 78)
    emit("  EUMOMENTUM — Trin 1: separabilitets-studie")
    emit(f"  Data: {data_dir}   |z|>={ENTRY_Z} kryds, ext |z|>={STOP_Z}, rev |z|<={EXIT_Z}")
    emit("=" * 78)

    # Indlaes BEGGE instrumenter (F7 kraever det andet), byg z-maps.
    bars_by: dict[str, list[Bar]] = {}
    zmap_by: dict[str, dict] = {}
    for inst in INSTRUMENTS:
        bars = load_ohlcv(data_dir / f"{inst}_15min.csv")
        bars_by[inst] = bars
        zm = {}
        for i in range(len(bars)):
            za = z_at(bars, i, LOOKBACK)
            if za:
                zm[bars[i].ts] = za[0]
        zmap_by[inst] = zm
        emit(f"  {inst}: {len(bars)} bars indlaest, {len(zm)} med z")

    targets = [a.only] if a.only else list(INSTRUMENTS)
    all_rows: list[dict] = []

    for inst in targets:
        bars = bars_by[inst]
        if not bars:
            emit(f"  {inst}: ingen data — sprunget over"); continue
        closes = [b.c for b in bars]
        ema20 = ema_series(closes, 20)
        ema50 = ema_series(closes, 50)
        atr14 = atr_series(bars, 14)
        # Slot-index til RVOL (F3): slot -> (sorterede ts, vol) for point-in-time median.
        slot_index: dict = {}
        for b in bars:
            slot = b.ts.strftime("%H:%M")
            slot_index.setdefault(slot, ([], []))
            slot_index[slot][0].append(b.ts); slot_index[slot][1].append(b.v)
        # Gap pr. dag (F8): forrige dags sidste US-close vs dagens session-aabnings-open.
        gap_bp_by_date: dict = {}
        session_open_by_date: dict = {}
        last_us_close = None
        for b in bars:
            d = b.ts.date()
            if in_eu_session(b.ts) and d not in session_open_by_date:
                session_open_by_date[d] = b.o
                if last_us_close and last_us_close > 0:
                    gap_bp_by_date[d] = (b.o - last_us_close) / last_us_close * 10000.0
                else:
                    gap_bp_by_date[d] = 0.0
            if b.ts.hour in US_HOURS:
                last_us_close = b.c

        other = [x for x in INSTRUMENTS if x != inst]
        other_zmap = zmap_by[other[0]] if other else {}

        events = detect_events(bars)
        emit(f"  {inst}: {len(events)} events (|z|>={ENTRY_Z}-kryds i session)")
        for ev in events:
            ev.inst = inst
            ev._gap_bp = abs(gap_bp_by_date.get(ev.ts.date(), 0.0))
            lab = label_event(bars, ev)
            fac = compute_factors(bars, ev, ema20, ema50, atr14, slot_index, other_zmap)
            all_rows.append({
                "inst": inst, "ts": ev.ts, "date": ev.ts.date(), "z": ev.z, "side": ev.side,
                "close": ev.close, "label": lab.outcome, "tie_break": lab.tie_break,
                "exit_close": lab.exit_close,
                "mfe_pts": lab.mfe_pts, "mae_pts": lab.mae_pts,
                "mfe_usd": lab.mfe_pts * MULTIPLIER.get(inst, 5.0),
                "mae_usd": lab.mae_pts * MULTIPLIER.get(inst, 5.0),
                "f": fac,
            })

    if not all_rows:
        emit("\nIngen events — kan ikke danne dom.")
        (out_dir / "summary.txt").write_text("\n".join(L), encoding="utf-8")
        return 1

    # ── Base rates ──────────────────────────────────────────────────────────
    all_rows.sort(key=lambda r: (r["date"], r["ts"]))
    n = len(all_rows)
    p0 = ext_rate(all_rows)
    n_tie = sum(1 for r in all_rows if r["tie_break"])
    tie_pct = n_tie / n * 100
    emit("")
    emit("── Base rates (alle events) ─────────────────────────────────────────")
    emit(f"  {fmt_rate(all_rows)}")
    emit(f"  p0 (ubetinget ekstensions-rate) = {p0*100:.1f} %")
    emit(f"  median MFE = ${st.median([r['mfe_usd'] for r in all_rows]):.0f}/kontrakt  |  "
         f"median MAE = ${st.median([r['mae_usd'] for r in all_rows]):.0f}/kontrakt")
    emit(f"  tie-break (begge taerskler i samme bar -> REVERSION): {n_tie}/{n} = {tie_pct:.1f} %"
         + ("   [>10 % -> automatisk GUL: 15-min for grovt]" if tie_pct > 10 else ""))

    # ── In-sample / OOS split (kronologisk pr. handelsdag) ──────────────────
    dates = sorted({r["date"] for r in all_rows})
    cut = dates[int(len(dates) * 0.70)] if dates else None
    is_rows = [r for r in all_rows if r["date"] < cut]
    oos_rows = [r for r in all_rows if r["date"] >= cut]
    p0_is = ext_rate(is_rows)
    p0_oos = ext_rate(oos_rows)
    emit("")
    emit(f"── In-sample (foerste 70 % dage, <{cut}): {len(is_rows)} events, p0={p0_is*100:.1f} % ──")
    emit(f"── OOS (sidste 30 %, >={cut}): {len(oos_rows)} events, p0={p0_oos*100:.1f} % ──")

    # ── Faktor-tabel (single + par), rangeret paa in-sample-lift ────────────
    cands = build_candidates(is_rows)
    scored = []
    for name, pred in cands:
        s_is = cand_stats(is_rows, pred, p0_is)
        s_oos = cand_stats(oos_rows, pred, p0_oos)
        scored.append({"name": name, "is": s_is, "oos": s_oos})
    scored.sort(key=lambda c: c["is"]["lift"], reverse=True)

    emit("")
    emit("── Faktorer & par (signal-bucket), rangeret paa in-sample-lift ──────")
    emit(f"  {'faktor':<40}{'IS n':>6}{'IS ext':>8}{'IS lift':>9}{'OOS n':>7}{'OOS lift':>10}")
    for c in scored:
        i, o = c["is"], c["oos"]
        emit(f"  {c['name']:<40}{i['n']:>6}{i['rate']*100:>7.1f}%{i['lift']*100:>+8.1f}"
             f"{o['n']:>7}{o['lift']*100:>+9.1f}")

    # ── Regime-split (hoej/lav realiseret vol dagen foer) ───────────────────
    # Realiseret vol pr. dag = stdev af 20 seneste close-to-close %-aendringer pr. instrument.
    emit("")
    emit("── Regime-split (top-faktor, hoej vs lav realiseret vol) ────────────")
    day_vol: dict = {}
    for inst in (targets):
        bars = bars_by.get(inst, [])
        for i in range(20, len(bars)):
            rets = [(bars[k].c / bars[k-1].c - 1) for k in range(i-19, i+1) if bars[k-1].c > 0]
            if rets:
                day_vol[(inst, bars[i].ts.date())] = st.pstdev(rets)
    vols = [v for v in day_vol.values()]
    vmed = st.median(vols) if vols else 0.0
    top = scored[0]
    top_pred = dict(cands)[top["name"]]
    hi = [r for r in all_rows if day_vol.get((r["inst"], r["date"]), 0.0) >= vmed]
    lo = [r for r in all_rows if day_vol.get((r["inst"], r["date"]), 0.0) < vmed]
    for lbl, rr in [("hoej-vol", hi), ("lav-vol", lo)]:
        sub = [r for r in rr if top_pred(r)]
        base = ext_rate(rr)
        emit(f"  {top['name']} i {lbl}: signal {fmt_rate(sub)}  (regime-p0 {base*100:.1f}%, "
             f"lift {(ext_rate(sub)-base)*100:+.1f} pp)")

    # ── Omkostnings-sweep (naiv momentum, alle events) ──────────────────────
    emit("")
    emit("── Omkostnings-sweep — naiv momentum (alle events) ──────────────────")
    emit(f"  {'bp':>4}{'PF':>8}{'win%':>8}{'avg%':>9}{'total%':>10}")
    for bp in COST_LEVELS_BP:
        m = naive_pf(all_rows, bp)
        emit(f"  {bp:>4.0f}{m['pf']:>8.2f}{m['win']:>7.1f}{m['avg']:>+9.3f}{m['total']:>+10.2f}")

    # ── DOM (§2) ─────────────────────────────────────────────────────────────
    best = scored[0]
    # Sekundaer gate: PF@2bp for de KONFLUENTE events (top-faktorens signal-bucket).
    conf_rows = [r for r in all_rows if top_pred(r)]
    pf2 = naive_pf(conf_rows, 2.0)["pf"] if conf_rows else 0.0
    max_abs_lift = max(abs(c["is"]["lift"]) for c in scored) if scored else 0.0

    # §8: kun TOP-faktoren (hoejest in-sample-lift) evalueres paa OOS — én gang.
    top_holds_oos = best["oos"]["lift"] > 0
    verdict, why = "ROED", ""
    if tie_pct > 10:
        verdict = "GUL"
        why = f"tie-break {tie_pct:.1f}% > 10% (15-min for grovt; hoest 1-min)"
    elif (best["is"]["lift"] >= 0.15 and best["is"]["rate"] >= 0.60
          and best["is"]["n"] >= 50 and top_holds_oos and pf2 >= 1.25):
        verdict = "GROEN"
        why = (f"{best['name']}: IS-lift {best['is']['lift']*100:+.1f}pp, rate {best['is']['rate']*100:.1f}%, "
               f"n={best['is']['n']}, OOS-lift {best['oos']['lift']*100:+.1f}pp, PF@2bp {pf2:.2f}")
    elif best["is"]["lift"] >= 0.08:
        # §2 GUL: et loeft (>=8pp) findes, men fejler paa n / OOS / PF.
        verdict = "GUL"
        reasons = []
        if best["is"]["n"] < 50: reasons.append(f"n={best['is']['n']}<50")
        if not top_holds_oos: reasons.append(f"OOS-lift {best['oos']['lift']*100:+.1f}pp<=0")
        if pf2 < 1.25: reasons.append(f"PF@2bp {pf2:.2f}<1.25")
        why = f"{best['name']}: IS-lift {best['is']['lift']*100:+.1f}pp men " + (", ".join(reasons) or "delvist")
    elif max_abs_lift <= 0.05:
        # §2 ROED: ingen faktor rykker mere end +-5pp fra p0.
        verdict = "ROED"
        why = f"ingen faktor rykker >+-5pp (max |lift| {max_abs_lift*100:.1f}pp) -> ekstension ikke separerbar"
    elif not top_holds_oos:
        # §2 ROED: loeftet kollapser i OOS (top-faktoren gaar negativ).
        verdict = "ROED"
        why = (f"top-faktor {best['name']} IS-lift {best['is']['lift']*100:+.1f}pp KOLLAPSER i OOS "
               f"({best['oos']['lift']*100:+.1f}pp) — loeft reproducerer ikke -> ikke separerbar")
    else:
        # 5-8pp loeft der OVERLEVER OOS svagt: for lille til at bygge, for stort til at aflive.
        verdict = "GUL"
        why = (f"{best['name']}: IS-lift {best['is']['lift']*100:+.1f}pp (5-8pp), holder svagt i OOS "
               f"({best['oos']['lift']*100:+.1f}pp) -> hoest dybere / 1-min")

    emit("")
    emit("=" * 78)
    emit(f"  Sekundaer gate (PF@2bp paa top-faktorens konfluente events): {pf2:.2f}  (>=1.25 kraevet)")
    emit("=" * 78)
    emit(f"DOM: {verdict} — {why}")

    # ── Skriv output ────────────────────────────────────────────────────────
    (out_dir / "summary.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    fkeys = ["F1_trend_aligned", "F2_atr_ratio", "F3_rvol", "F4_bar_struct",
             "F5_persistence", "F6_hour_et", "F7_confluence", "F8_abs_gap_bp"]
    with (out_dir / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["inst", "ts", "z", "side", "close", "label", "tie_break",
                    "mfe_usd", "mae_usd"] + fkeys)
        for r in all_rows:
            w.writerow([r["inst"], r["ts"].isoformat(), f"{r['z']:.3f}", r["side"],
                        r["close"], r["label"], int(r["tie_break"]),
                        f"{r['mfe_usd']:.1f}", f"{r['mae_usd']:.1f}"]
                       + [r["f"][k] for k in fkeys])
    print(f"\nFiler: {out_dir/'summary.txt'} + {out_dir/'events.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
