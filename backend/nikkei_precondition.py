#!/usr/bin/env python3
"""
nikkei_precondition.py — DISCOVERY-scan: findes der en edge i den ASIATISKE session?
═════════════════════════════════════════════════════════════════════════════════════
Mirror af meanrev_precondition's disciplin, MEN tesen er ikke pinnet — vi AFDAEKKER
strategien. Maaler de signaturer der ADSKILLER kandidat-teserne (momentum vs reversion,
og HVORNAAR i sessionen), saa outputtet PEGER paa tesen. INGEN strategikode skrives foer
dataet siger hvad edgen er.

Input:  data_harvest/NIKKEI_1min.csv  (timestamp,open,high,low,close,volume; ISO ET)
Output: ./nikkei_precondition_output/summary.txt

Rent OFFLINE: kun stdlib (csv/datetime/zoneinfo/statistics/math), ingen IBKR. ALT
volumen-renset fra start (tynde bars narrer — laeringen fra futures-continuation).
Tider i JST (instrumentets hjemme-klok) + dansk; bucketing pr. JST-tid.

Fire maalinger (de adskiller teserne):
  A. Autokorr pr. sessions-FASE (5-min afkast; aabning/midt/sent) — trend vs revert.
  B. Aabnings-range (OR 15/30 min): braek -> kontinuation (momentum) vs fade.
  C. Overnight-gap: stoerrelse, fill-rate (fade) vs kontinuation (ride).
  D. Aktivitets-/vol-profil pr. 30-min JST-bucket (hvornaar er instrumentet aktivt).

GRAENSE: maaler en SIGNATUR, beviser ikke en tradeable edge — omkostninger paalaegges
IKKE her (det er backtestens job bagefter). Precondition = "findes der noget vaerd at
bygge en regel for".

Brug (fra backend/, efter nikkei_harvest_1min.py har skrevet CSV'en):
    python nikkei_precondition.py

Placering: C:\\Projects\\trading_dash\\backend\\nikkei_precondition.py
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DK  = ZoneInfo("Europe/Copenhagen")

IN_CSV  = Path("data_harvest") / "NIKKEI_1min.csv"
OUT_DIR = Path("nikkei_precondition_output")

# OSE-Nikkei day-session ~08:45-15:15 JST. Vi bruger 09:00-15:15 (spec: ~09:00-15:00) som
# "dag-sessionen". Faser: AABNING = foerste 30 min, SENT = sidste 30 min, MIDT = resten.
DAY_START   = time(9, 0)
DAY_END     = time(15, 15)
OPEN_END    = time(9, 30)
LATE_START  = time(14, 45)
MIN_DAY_BARS = 60            # min rene 1-min bars i dag-vinduet for at taelle som handelsdag
VOL_FLOOR_FRAC = 0.10        # dropper bars med volumen < frac * median (volumen-rensning)


def fmt_ac(ac, n):
    if ac is None or n < 20:
        return f"n/a (n={n})"
    se = 1.0 / math.sqrt(n)
    sig = abs(ac) > 2 * se
    if not sig:
        tag = "stoej (~0)"
    elif ac > 0:
        tag = "TREND (momentum)"
    else:
        tag = "REVERT"
    return f"{ac:+.4f} +-{se:.4f} [{tag}] (n={n})"


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def lag1(seqs):
    """lag-1 autokorr over flere sammenhaengende sekvenser (pairs krydser ALDRIG en sekvens-
    graense -> intet overnight-gab i beregningen). Returnerer (ac, n_par)."""
    xs, ys = [], []
    for s in seqs:
        for i in range(len(s) - 1):
            xs.append(s[i])
            ys.append(s[i + 1])
    return pearson(xs, ys), len(xs)


def load_bars(path):
    """ISO-ET -> liste af {dt_jst, o,h,l,c,v} sorteret paa tid. Rens NaN/0-pris."""
    out = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(r["timestamp"]).astimezone(JST)
                o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
                v = int(float(r["volume"])) if r.get("volume") else 0
            except (ValueError, KeyError, TypeError):
                continue
            if c <= 0 or h <= 0:
                continue
            out.append({"dt": dt, "o": o, "h": h, "l": l, "c": c, "v": v})
    out.sort(key=lambda b: b["dt"])
    return out


def in_day(t: time) -> bool:
    return DAY_START <= t < DAY_END


def session_days(bars, min_vol):
    """{jst_date: [bars i dag-vinduet, volumen-renset]} for dage med nok bars."""
    days = {}
    for b in bars:
        t = b["dt"].timetz().replace(tzinfo=None)
        if not in_day(t):
            continue
        if b["v"] < min_vol:
            continue
        days.setdefault(b["dt"].date(), []).append(b)
    return {d: sorted(bs, key=lambda b: b["dt"]) for d, bs in days.items()
            if len(bs) >= MIN_DAY_BARS}


def to_5min(day_bars):
    """Aggreger 1-min -> 5-min (close-to-close-robusthed til autokorr). OHLC pr. 5-min-bucket."""
    buckets = {}
    for b in day_bars:
        key = b["dt"].replace(minute=(b["dt"].minute // 5) * 5, second=0, microsecond=0)
        g = buckets.get(key)
        if g is None:
            buckets[key] = {"dt": key, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            g["h"] = max(g["h"], b["h"])
            g["l"] = min(g["l"], b["l"])
            g["c"] = b["c"]
    return [buckets[k] for k in sorted(buckets)]


def phase_of(t: time) -> str:
    if t < OPEN_END:
        return "AABNING"
    if t >= LATE_START:
        return "SENT"
    return "MIDT"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    inp = IN_CSV if IN_CSV.is_absolute() else (Path.cwd() / IN_CSV)
    out_dir = OUT_DIR if OUT_DIR.is_absolute() else (Path.cwd() / OUT_DIR)
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  OSE-NIKKEI — PRECONDITION DISCOVERY-SCAN (offline, omkostninger IKKE paalagt)")
    emit("=" * 78)
    if not inp.exists():
        emit(f"  FEJL: {inp} findes ikke. Koer nikkei_harvest_1min.py foerst.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    bars = load_bars(inp)
    if len(bars) < 500:
        emit(f"  FOR FAA BARS ({len(bars)}). Host mere foerst.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # Volumen-baseline (median over dag-vindue-bars) -> rensnings-gulv.
    day_vols = [b["v"] for b in bars if in_day(b["dt"].timetz().replace(tzinfo=None)) and b["v"] > 0]
    med_vol = statistics.median(day_vols) if day_vols else 0
    min_vol = max(1, med_vol * VOL_FLOOR_FRAC)

    first, last = bars[0]["dt"], bars[-1]["dt"]
    emit(f"  Bars: {len(bars)} (1-min)  ·  {first:%Y-%m-%d} -> {last:%Y-%m-%d} JST")
    emit(f"  Dag-session: {DAY_START:%H:%M}-{DAY_END:%H:%M} JST "
         f"(= {datetime.combine(first.date(), DAY_START, JST).astimezone(DK):%H:%M}-"
         f"{datetime.combine(first.date(), DAY_END, JST).astimezone(DK):%H:%M} dansk paa denne dato)")
    emit(f"  Volumen-rensning: median dag-volumen={med_vol:.0f} -> dropper bars < {min_vol:.0f}")

    days = session_days(bars, min_vol)
    emit(f"  Handelsdage med nok rene bars: {len(days)}")
    emit("")

    # ── A. Autokorr pr. fase (5-min afkast) ──
    emit("─" * 78)
    emit("  A. AUTOKORRELATION pr. sessions-fase  (5-min afkast; lag-1)")
    emit("─" * 78)
    seqs = {"AABNING": [], "MIDT": [], "SENT": [], "ALLE": []}
    for d, dbars in days.items():
        bars5 = to_5min(dbars)
        if len(bars5) < 4:
            continue
        rets = [(bars5[i]["c"] - bars5[i - 1]["c"]) / bars5[i - 1]["c"] for i in range(1, len(bars5))]
        # afkast i index i hoerer til 5-min-bar i+1's fase; tag paa bar-tid.
        per_phase = {"AABNING": [], "MIDT": [], "SENT": []}
        for i, r in enumerate(rets):
            ph = phase_of(bars5[i + 1]["dt"].timetz().replace(tzinfo=None))
            per_phase[ph].append(r)
        for ph in ("AABNING", "MIDT", "SENT"):
            if per_phase[ph]:
                seqs[ph].append(per_phase[ph])
        seqs["ALLE"].append(rets)
    for ph in ("AABNING", "MIDT", "SENT", "ALLE"):
        ac, n = lag1(seqs[ph])
        emit(f"   {ph:<8} {fmt_ac(ac, n)}")
    emit("   (<0 = revert · >0 = trend · |ac|<2*SE = ikke adskilleligt fra stoej)")
    emit("")

    # ── B. Aabnings-range: braek -> kontinuation vs fade ──
    emit("─" * 78)
    emit("  B. AABNINGS-RANGE  (OR = high/low af foerste N min; braek -> kontinuation?)")
    emit("─" * 78)
    for N in (15, 30):
        or_end = time((DAY_START.hour * 60 + DAY_START.minute + N) // 60,
                      (DAY_START.hour * 60 + DAY_START.minute + N) % 60)
        n_days = n_break = 0
        cont_up, cont_dn = [], []
        open_moves = []
        for d, dbars in days.items():
            orb = [b for b in dbars if b["dt"].timetz().replace(tzinfo=None) < or_end]
            rest = [b for b in dbars if b["dt"].timetz().replace(tzinfo=None) >= or_end]
            if len(orb) < 3 or len(rest) < 5:
                continue
            n_days += 1
            or_hi = max(b["h"] for b in orb)
            or_lo = min(b["l"] for b in orb)
            day_open = orb[0]["o"]
            close = rest[-1]["c"]
            ext = max((or_hi - day_open), (day_open - or_lo))
            if day_open > 0:
                open_moves.append(ext / day_open)
            brk = None  # (dir, price)
            for b in rest:
                if b["h"] >= or_hi:
                    brk = ("up", or_hi); break
                if b["l"] <= or_lo:
                    brk = ("dn", or_lo); break
            if brk is None:
                continue
            n_break += 1
            direction, price = brk
            if price <= 0:
                continue
            if direction == "up":
                cont_up.append((close - price) / price)
            else:
                cont_dn.append((price - close) / price)
        allc = cont_up + cont_dn
        med_move = statistics.median(open_moves) * 100 if open_moves else 0.0
        emit(f"   OR={N:>2} min:  {n_days} dage · braek paa {100*n_break/max(n_days,1):.0f}% af dage "
             f"· aabnings-bevaegelse median {med_move:.2f}%")
        if allc:
            mu = statistics.mean(allc) * 100
            tag = "MOMENTUM (braek fortsaetter)" if mu > 0 else "FADE (braek reverter)"
            up_s = f"{statistics.mean(cont_up)*100:+.2f}%" if cont_up else "n/a"
            dn_s = f"{statistics.mean(cont_dn)*100:+.2f}%" if cont_dn else "n/a"
            emit(f"             kontinuation braek->close: {mu:+.2f}% [{tag}]  "
                 f"(op {up_s} n={len(cont_up)} · ned {dn_s} n={len(cont_dn)})")
    emit("")

    # ── C. Overnight-gap: fill (fade) vs kontinuation (ride) ──
    emit("─" * 78)
    emit("  C. OVERNIGHT-GAP  (dag-open vs forrige dag-close)")
    emit("─" * 78)
    sd = sorted(days.items())
    gaps, fills, conts = [], 0, []
    n_gap = 0
    for i in range(1, len(sd)):
        prev_close = sd[i - 1][1][-1]["c"]
        today = sd[i][1]
        day_open = today[0]["o"]
        if prev_close <= 0:
            continue
        gap = (day_open - prev_close) / prev_close
        if abs(gap) < 1e-9:
            continue
        n_gap += 1
        gaps.append(abs(gap))
        # fill: naar prisen i sessionen tilbage til forrige close
        if gap > 0:
            filled = any(b["l"] <= prev_close for b in today)
        else:
            filled = any(b["h"] >= prev_close for b in today)
        if filled:
            fills += 1
        # kontinuation: fortsaetter gap-retningen open->close?
        close = today[-1]["c"]
        conts.append(((close - day_open) / day_open) * (1 if gap > 0 else -1))
    if n_gap:
        med_gap = statistics.median(gaps) * 100
        fill_rate = 100 * fills / n_gap
        cont_mu = statistics.mean(conts) * 100
        emit(f"   {n_gap} gaps · median |gap| {med_gap:.2f}% · fill-rate {fill_rate:.0f}% "
             f"(vender til forrige close)")
        ctag = "RIDE (gap fortsaetter)" if cont_mu > 0 else "FADE (gap reverter intradag)"
        emit(f"   open->close i gap-retning: {cont_mu:+.2f}% [{ctag}]")
        gap_signal = ("fade" if (fill_rate >= 55 and cont_mu < 0) else
                      "ride" if (fill_rate <= 45 and cont_mu > 0) else "blandet")
    else:
        emit("   (ingen gaps fundet)")
        gap_signal = "n/a"
    emit("")

    # ── D. Aktivitets-/vol-profil pr. 30-min JST-bucket (hele doegnet) ──
    emit("─" * 78)
    emit("  D. AKTIVITETS-PROFIL pr. 30-min JST-bucket  (|afkast| + volumen; hele doegnet)")
    emit("─" * 78)
    buck_absret, buck_vol = {}, {}
    prev = None
    for b in bars:
        if prev is not None and prev["c"] > 0:
            key = (b["dt"].hour, (b["dt"].minute // 30) * 30)
            buck_absret.setdefault(key, []).append(abs((b["c"] - prev["c"]) / prev["c"]))
            buck_vol.setdefault(key, []).append(b["v"])
        prev = b
    if buck_absret:
        max_act = max(statistics.mean(v) for v in buck_absret.values())
        for key in sorted(buck_absret):
            h, m = key
            act_frac = statistics.mean(buck_absret[key])
            act = act_frac * 100
            vol = statistics.mean(buck_vol.get(key, [0]))
            jst_lbl = f"{h:02d}:{m:02d}"
            dk_lbl = datetime.combine(last.date(), time(h, m), JST).astimezone(DK).strftime("%H:%M")
            day_tag = " [DAG]" if in_day(time(h, m)) else ""
            barlen = int(40 * act_frac / max_act) if max_act > 0 else 0
            emit(f"   {jst_lbl} JST ({dk_lbl} dk){day_tag:<6} |afk| {act:.3f}%  vol {vol:>6.0f}  {'#'*barlen}")
    emit("")

    # ── HVAD DATAET PEGER PAA ──
    emit("=" * 78)
    emit("  HVAD DATAET PEGER PAA")
    emit("=" * 78)
    ac_open, n_open = lag1(seqs["AABNING"])
    ac_mid, n_mid = lag1(seqs["MIDT"])

    def sig(ac, n):
        if ac is None or n < 20 or abs(ac) <= 2 / math.sqrt(n):
            return "stoej"
        return "trend" if ac > 0 else "revert"

    s_open, s_mid = sig(ac_open, n_open), sig(ac_mid, n_mid)
    pointed = False
    if s_open == "trend":
        emit("  - AABNING trender (momentum) -> kandidat: aabnings-momentum i de foerste 30 min.")
        pointed = True
    elif s_open == "revert":
        emit("  - AABNING reverter -> kandidat: fade aabnings-impulsen.")
        pointed = True
    if s_mid == "revert":
        emit("  - MIDT-session reverter -> kandidat: mean-reversion mid-session.")
        pointed = True
    elif s_mid == "trend":
        emit("  - MIDT-session trender -> kandidat: momentum-fortsaettelse mid-session.")
        pointed = True
    if gap_signal == "fade":
        emit("  - Gaps FADER (fill-rate hoej, kontinuation negativ) -> kandidat: fade gappet.")
        pointed = True
    elif gap_signal == "ride":
        emit("  - Gaps RIDER (lav fill, positiv kontinuation) -> kandidat: ride gappet.")
        pointed = True
    if not pointed:
        emit("  - INGEN maaling viser en klar signatur ud over stoej.")
        emit("    Det er OGSAA et svar: Nikkei-dag-sessionen baerer (a priori) ingen aaben edge.")
        emit("    Naeste: proev nat-sessionen (se D — hvor er aktiviteten?), et andet asiatisk")
        emit("    instrument, eller drop sporet.")
    else:
        emit("")
        emit("  NB: dette er en SIGNATUR, ikke en bevist edge. Naeste skridt = backtest med")
        emit("      omkostninger paalagt (OSE mini-spaend ~5 bp rundtur i Tokyo-timer).")
    emit("")

    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"  Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
