#!/usr/bin/env python3
"""
catalyst_backtest.py — Katalysator-møllen Trin 2, leverance 2: latency-backtest
═══════════════════════════════════════════════════════════════════════════════
Ren offline på de høstede CSV'er (catalyst_harvest.py). Ingen IBKR.

Hypotese-test: en KATALYSATOR = en Dow Jones-overskrift der falder sammen med en
unormal initial markedsreaktion (selv-filtrerende — markedet bestemmer hvilke
nyheder der er katalysatorer; vi gætter IKKE via nøgleord). For hver katalysator
simuleres entry ved LATENCY L ∈ {1,5,15,30,60} min, og BEGGE hypoteser testes:
  • MOMENTUM — handl I bevægelsens retning (pos→long, neg→short)
  • FADE     — handl IMOD bevægelsen (pos→short, neg→long)

Look-ahead-sikker: kvalifikationen (reaktion) måles på NYHEDS-baren alene (move +
volumen i nyheds-minuttet), så beslutningen kun bruger data ≤ entry — også ved L=1.

Exit: entry + hold_min minutter ELLER RTH-sessionsslut, hvad end først. Holder
ALDRIG over natten (intraday-princip som EUREVERSION).

Begge retninger testes; SHORT-resultater antager borrow altid muligt → OPTIMISTISKE
(markeret). Overnight- vs intraday-katalysatorer rapporteres separat (natlige nyheder
absorberes ved åbning og opfører sig anderledes).

Brug (fra backend/):
    python catalyst_backtest.py
    python catalyst_backtest.py --move-threshold 1.0 --vol-mult 2.0 --hold-min 30
    python catalyst_backtest.py --sweep          # plateau-tjek (move × vol × hold)

Output: catalyst_backtest_output/summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\catalyst_backtest.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

OUTPUT_DIRNAME = "catalyst_backtest_output"
NEWS_DIR = Path("catalyst_data") / "news"
BARS_DIR = Path("catalyst_data") / "bars"

LATENCIES_MIN   = [1, 5, 15, 30, 60]
COST_LEVELS_BP  = [0.0, 1.0, 2.0, 3.0]
COST_READ_BP    = 2.0          # aflæsnings-omkostning for hoved-dommen
OOS_SPLIT       = 0.6          # in-sample først, OOS sidst (på handelsdag)
BASELINE_BARS   = 20           # bars før nyheden til volumen-baseline
SESSION_GAP_MIN = 30           # gap > dette mellem 1-min bars = ny session
HOLD_DEFAULT    = 30
MOVE_DEFAULT    = 1.0          # % move over reaktions-vinduet
VOL_DEFAULT     = 2.0          # × baseline-volumen (pr. bar i vinduet)
REACTION_DEFAULT = 5           # min: reaktions-vindue til katalysator-kvalifikation
OVERNIGHT_GAP_MIN = 10         # nyhed→første-bar-gap > dette = overnight-katalysator


@dataclass
class Bars:
    ts:     list          # datetime, sorteret
    open:   list
    high:   list
    low:    list
    close:  list
    volume: list
    sess:   list          # session-id pr. bar
    sess_last: list       # indeks for sidste bar i samme session
    med_vol: float        # global median 1-min volumen (fallback-baseline)


def _parse(ts_str):
    return datetime.fromisoformat(ts_str)


def load_bars(path: Path) -> Bars | None:
    ts, o, h, l, c, v = [], [], [], [], [], []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts.append(_parse(row["ts_utc"]))
                o.append(float(row["open"])); h.append(float(row["high"]))
                l.append(float(row["low"]));  c.append(float(row["close"]))
                v.append(float(row["volume"] or 0))
            except (ValueError, KeyError):
                continue
    if len(ts) < 50:
        return None
    # Session-id via gap-detektion; sess_last via baglæns-scan.
    sess = [0] * len(ts)
    sid = 0
    for i in range(1, len(ts)):
        if (ts[i] - ts[i - 1]).total_seconds() > SESSION_GAP_MIN * 60:
            sid += 1
        sess[i] = sid
    sess_last = [0] * len(ts)
    last_idx = len(ts) - 1
    for i in range(len(ts) - 1, -1, -1):
        if i == len(ts) - 1 or sess[i] != sess[i + 1]:
            last_idx = i
        sess_last[i] = last_idx
    svol = sorted(x for x in v if x > 0)
    med = svol[len(svol) // 2] if svol else 0.0
    return Bars(ts, o, h, l, c, v, sess, sess_last, med)


def load_news(path: Path) -> list:
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out.append((_parse(row["ts_utc"]), row.get("provider", ""),
                            row.get("headline", "")))
            except (ValueError, KeyError):
                continue
    out.sort(key=lambda x: x[0])
    return out


@dataclass
class Catalyst:
    ticker:    str
    news_ts:   datetime
    news_idx:  int
    move_pct:  float      # signeret: + = op, − = ned
    vol_ratio: float
    overnight: bool
    direction: str        # "pos" | "neg"
    day:       object      # handelsdag (date) til OOS-split


def first_bar_at_or_after(bars: Bars, ts: datetime) -> int | None:
    i = bisect_left(bars.ts, ts)
    return i if i < len(bars.ts) else None


def identify_catalysts(ticker, bars: Bars, news, move_thr, vol_mult, reaction_min) -> list:
    """Selv-filtrerende: en nyhed kvalificerer hvis |move| ≥ move_thr (%) over de
    første `reaction_min` minutter OG gennemsnits-volumen pr. bar i vinduet ≥
    vol_mult × baseline. Reaktion måles fra nyheds-barens open til close ~reaction_min
    min senere (samme session). Dedup pr. (news_idx) så flere overskrifter i samme
    minut kun giver én katalysator.

    LOOK-AHEAD: kvalifikationen bruger et vindue på reaction_min min. Entries ved
    latency L < reaction_min sker INDE i vinduet → behandl lave L som ØVRE grænse
    (markeres i output). Entries ved L ≥ reaction_min er look-ahead-rene."""
    cats = []
    seen_idx = set()
    for news_ts, _prov, _hl in news:
        idx = first_bar_at_or_after(bars, news_ts)
        if idx is None:
            continue
        if idx in seen_idx:
            continue
        gap_min = (bars.ts[idx] - news_ts).total_seconds() / 60.0
        overnight = gap_min > OVERNIGHT_GAP_MIN
        op = bars.open[idx]
        if op <= 0:
            continue
        # Reaktions-vindue: bars fra idx til ~reaction_min min senere, samme session.
        win_end_ts = bars.ts[idx] + timedelta(minutes=reaction_min)
        r_idx = idx
        k = idx
        while k < len(bars.ts) and bars.sess[k] == bars.sess[idx] and bars.ts[k] <= win_end_ts:
            r_idx = k
            k += 1
        move_pct = (bars.close[r_idx] - op) / op * 100.0
        win_vols = [bars.volume[j] for j in range(idx, r_idx + 1) if bars.volume[j] > 0]
        win_vol_per_bar = (sum(win_vols) / len(win_vols)) if win_vols else 0.0
        # Baseline-volumen pr. bar: median af op til BASELINE_BARS bars FØR i samme
        # session; fallback global median hvis for få (fx ved åbnings-bar). Ingen
        # fremtidig info ud over selve reaktions-vinduet.
        base_lo = idx - BASELINE_BARS
        prior = [bars.volume[j] for j in range(max(0, base_lo), idx)
                 if j >= 0 and bars.sess[j] == bars.sess[idx] and bars.volume[j] > 0]
        if len(prior) >= 5:
            base = sorted(prior)[len(prior) // 2]
        else:
            base = bars.med_vol
        vol_ratio = (win_vol_per_bar / base) if base > 0 else 0.0
        if abs(move_pct) < move_thr or vol_ratio < vol_mult:
            continue
        seen_idx.add(idx)
        cats.append(Catalyst(
            ticker=ticker, news_ts=news_ts, news_idx=idx, move_pct=move_pct,
            vol_ratio=vol_ratio, overnight=overnight,
            direction=("pos" if move_pct > 0 else "neg"),
            day=bars.ts[idx].date()))
    return cats


@dataclass
class TradeRes:
    ret_pct: float        # brutto retnings-justeret %
    side:    str          # "long" | "short"
    overnight: bool
    day:     object


def simulate(bars: Bars, cat: Catalyst, latency_min: int, hold_min: int,
             hypothesis: str) -> TradeRes | None:
    """Entry ved nyheds-bar + latency_min (tids-baseret opslag, samme session).
    Exit ved entry + hold_min ELLER sessionsslut. Retning afhænger af hypotese."""
    sess = bars.sess[cat.news_idx]
    entry_ts = bars.ts[cat.news_idx] + timedelta(minutes=latency_min)
    e_idx = first_bar_at_or_after(bars, entry_ts)
    if e_idx is None or bars.sess[e_idx] != sess:
        return None                      # entry faldt uden for samme session → drop
    entry = bars.close[e_idx]
    if entry <= 0:
        return None
    exit_ts = bars.ts[e_idx] + timedelta(minutes=hold_min)
    x_idx = first_bar_at_or_after(bars, exit_ts)
    if x_idx is None or bars.sess[x_idx] != sess:
        x_idx = bars.sess_last[e_idx]    # sessionsslut-tvangsluk
    exit_px = bars.close[x_idx]

    # Retning: MOMENTUM = med bevægelsen; FADE = imod.
    if hypothesis == "momentum":
        side = "long" if cat.direction == "pos" else "short"
    else:
        side = "short" if cat.direction == "pos" else "long"
    raw = (exit_px - entry) / entry * 100.0
    ret = raw if side == "long" else -raw
    return TradeRes(ret_pct=ret, side=side, overnight=cat.overnight, day=cat.day)


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
    """Split på handelsdag (in-sample først, OOS sidst). trades: liste m. .day."""
    if not trades:
        return [], []
    days = sorted({t.day for t in trades})
    cut = days[min(len(days) - 1, int(len(days) * frac))]
    return [t for t in trades if t.day <= cut], [t for t in trades if t.day > cut]


# ─────────────────────────────────────────────────────────────────────────────
def build_universe_catalysts(move_thr, vol_mult, reaction_min, emit_cov=None):
    """Indlæs alle (news, bars)-par der findes, returnér samlet katalysator-liste."""
    all_cats = []
    coverage = []
    if not NEWS_DIR.exists():
        return all_cats, coverage
    for news_path in sorted(NEWS_DIR.glob("*_news.csv")):
        ticker = news_path.name.replace("_news.csv", "")
        bars_path = BARS_DIR / f"{ticker}_1min.csv"
        if not bars_path.exists():
            coverage.append((ticker, 0, 0, "ingen bars"))
            continue
        bars = load_bars(bars_path)
        news = load_news(news_path)
        if bars is None:
            coverage.append((ticker, len(news), 0, "for få bars"))
            continue
        cats = identify_catalysts(ticker, bars, news, move_thr, vol_mult, reaction_min)
        all_cats.append((ticker, bars, cats))
        coverage.append((ticker, len(news), len(cats), ""))
    return all_cats, coverage


def run_config(all_cats, hold_min):
    """Returnér dict: hypothesis → latency → liste af TradeRes."""
    out = {"momentum": {L: [] for L in LATENCIES_MIN},
           "fade":     {L: [] for L in LATENCIES_MIN}}
    for _ticker, bars, cats in all_cats:
        for cat in cats:
            for L in LATENCIES_MIN:
                for hyp in ("momentum", "fade"):
                    tr = simulate(bars, cat, L, hold_min, hyp)
                    if tr is not None:
                        out[hyp][L].append(tr)
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Katalysator latency-backtest (momentum + fade)")
    ap.add_argument("--move-threshold", type=float, default=MOVE_DEFAULT,
                    help="min |move %%| i nyheds-minuttet for at kvalificere")
    ap.add_argument("--vol-mult", type=float, default=VOL_DEFAULT,
                    help="min volumen × baseline for at kvalificere")
    ap.add_argument("--reaction-min", type=int, default=REACTION_DEFAULT,
                    help="reaktions-vindue (min) til katalysator-kvalifikation")
    ap.add_argument("--hold-min", type=int, default=HOLD_DEFAULT)
    ap.add_argument("--oos-split", type=float, default=OOS_SPLIT)
    ap.add_argument("--sweep", action="store_true",
                    help="plateau-tjek: move × vol × hold (PF@2bp), anti-overfit")
    args = ap.parse_args()

    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  KATALYSATOR-BACKTEST — latency-knap, momentum + fade (køber intet)")
    emit("=" * 78)

    if args.sweep:
        emit("── PLATEAU-SWEEP (move × vol × hold), PF@2bp ved bedste latency pr. combo ──")
        emit("   Vælg IKKE højeste PF — kig efter et plateau der holder (anti-overfit).")
        for mv in (0.5, 1.0, 2.0):
            for vm in (2.0, 3.0):
                cats, _cov = build_universe_catalysts(mv, vm, args.reaction_min)
                ncat = sum(len(c) for _t, _b, c in cats)
                emit("")
                emit(f"  move≥{mv}%  vol≥{vm}×   (katalysatorer: {ncat})")
                emit(f"     {'hyp':>9}{'lat':>5}{'hold':>6}{'n':>6}{'WR%':>7}{'sum%':>9}{'PF@2bp':>8}")
                for hold in (15, 30, 60):
                    res = run_config(cats, hold)
                    for hyp in ("momentum", "fade"):
                        for L in LATENCIES_MIN:
                            rets = [t.ret_pct for t in res[hyp][L]]
                            s = stats(rets, COST_READ_BP)
                            if s["n"] >= 10:
                                emit(f"     {hyp:>9}{L:>5}{hold:>6}{s['n']:>6}{s['wr']:>7.0f}"
                                     f"{s['sum']:>9.1f}{fmt_pf(s['pf']):>8}")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        emit(f"\nFil: {out_dir / 'summary.txt'}")
        return 0

    # ── Hoved-konfiguration ──
    emit(f"Konfig: move≥{args.move_threshold}% over {args.reaction_min}min  vol≥{args.vol_mult}×  "
         f"hold={args.hold_min}min  OOS-split={args.oos_split:.0%}  aflæsning ved {COST_READ_BP:.0f}bp")
    emit(f"Look-ahead: latency L<{args.reaction_min}min bruger fremadrettet kvalifikations-"
         f"vindue → behandl L<{args.reaction_min} som ØVRE grænse.")
    all_cats, coverage = build_universe_catalysts(args.move_threshold, args.vol_mult, args.reaction_min)
    if not all_cats:
        emit("  Ingen data — kør catalyst_harvest.py først.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    total_news = sum(c[1] for c in coverage)
    total_cats = sum(len(c) for _t, _b, c in all_cats)
    emit(f"Dækning: {len(all_cats)} navne · {total_news:,} nyheder · {total_cats:,} katalysatorer "
         f"({100*total_cats/total_news:.1f}% kvalificerede)" if total_news else "")
    on = sum(1 for _t, _b, cs in all_cats for c in cs if c.overnight)
    emit(f"         overnight={on}  intraday={total_cats - on}")
    emit("")

    res = run_config(all_cats, args.hold_min)

    # A. Hovedtabel: hypotese × latency (@2bp + OOS)
    emit("  A. HOVEDTABEL — hypotese × latency (sum/PF ved 2bp; OOS = sidste "
         f"{1-args.oos_split:.0%} af dage)")
    emit(f"     {'hypotese':>9}{'lat(min)':>9}{'n':>6}{'WR%':>7}{'sum%':>9}{'PF@2bp':>8}"
         f"{'OOS_n':>7}{'OOS_PF':>8}")
    for hyp in ("momentum", "fade"):
        for L in LATENCIES_MIN:
            trades = res[hyp][L]
            rets = [t.ret_pct for t in trades]
            s = stats(rets, COST_READ_BP)
            ins, oos = oos_split(trades, args.oos_split)
            so = stats([t.ret_pct for t in oos], COST_READ_BP)
            emit(f"     {hyp:>9}{L:>9}{s['n']:>6}{s['wr']:>7.0f}{s['sum']:>9.1f}"
                 f"{fmt_pf(s['pf']):>8}{so['n']:>7}{fmt_pf(so['pf']):>8}")
        emit("")

    # B. Omkostnings-sensitivitet for hver hypotese ved bedste latency
    emit("  B. OMKOSTNINGS-SWEEP (0/1/2/3 bp) — bedste latency pr. hypotese (på sum@2bp)")
    for hyp in ("momentum", "fade"):
        best_L = max(LATENCIES_MIN,
                     key=lambda L: stats([t.ret_pct for t in res[hyp][L]], COST_READ_BP)["sum"])
        emit(f"     {hyp} @ L={best_L}min:")
        emit(f"        {'rundtur':>8}{'n':>6}{'WR%':>7}{'sum%':>9}{'PF':>7}{'værst%':>9}")
        for c in COST_LEVELS_BP:
            s = stats([t.ret_pct for t in res[hyp][best_L]], c)
            emit(f"        {c:>6.0f}bp{s['n']:>6}{s['wr']:>7.0f}{s['sum']:>9.1f}"
                 f"{fmt_pf(s['pf']):>7}{s['worst']:>9.2f}")
    emit("")

    # C. Long vs short separat (short = optimistisk, borrow antaget muligt)
    emit("  C. LONG vs SHORT separat (@2bp) — SHORT er OPTIMISTISK (borrow antaget altid muligt)")
    emit(f"     {'hypotese':>9}{'lat':>5}{'long_n':>8}{'long_PF':>9}{'short_n':>9}{'short_PF':>10}")
    for hyp in ("momentum", "fade"):
        for L in LATENCIES_MIN:
            longs = [t.ret_pct for t in res[hyp][L] if t.side == "long"]
            shorts = [t.ret_pct for t in res[hyp][L] if t.side == "short"]
            sl, ss = stats(longs, COST_READ_BP), stats(shorts, COST_READ_BP)
            emit(f"     {hyp:>9}{L:>5}{sl['n']:>8}{fmt_pf(sl['pf']):>9}"
                 f"{ss['n']:>9}{fmt_pf(ss['pf']):>10}")
    emit("")

    # D. Overnight vs intraday separat
    emit("  D. OVERNIGHT vs INTRADAY katalysator (@2bp) — natlige nyheder absorberes ved åbning")
    emit(f"     {'hypotese':>9}{'lat':>5}{'intra_n':>8}{'intra_PF':>9}{'on_n':>6}{'on_PF':>8}")
    for hyp in ("momentum", "fade"):
        for L in LATENCIES_MIN:
            intra = [t.ret_pct for t in res[hyp][L] if not t.overnight]
            ons = [t.ret_pct for t in res[hyp][L] if t.overnight]
            si, so = stats(intra, COST_READ_BP), stats(ons, COST_READ_BP)
            emit(f"     {hyp:>9}{L:>5}{si['n']:>8}{fmt_pf(si['pf']):>9}"
                 f"{so['n']:>6}{fmt_pf(so['pf']):>8}")
    emit("")

    # ── DOM ──
    emit("─" * 78)
    emit("  DOM — findes en edge?")
    emit("─" * 78)
    # KUN look-ahead-RENE latencies (L ≥ reaktions-vindue) tæller i dommen. L<reaktion
    # bruger fremadrettet kvalifikation → kunstigt høj (typisk momentum) og må ALDRIG
    # drive en edge-konklusion eller en Benzinga-beslutning.
    clean_lat = [L for L in LATENCIES_MIN if L >= args.reaction_min]
    emit(f"  (Kun look-ahead-rene latencies vurderet: L ∈ {clean_lat}. "
         f"L<{args.reaction_min} udeladt — kontamineret af fremadrettet kvalifikation.)")
    best = None
    for hyp in ("momentum", "fade"):
        for L in clean_lat:
            trades = res[hyp][L]
            s = stats([t.ret_pct for t in trades], COST_READ_BP)
            ins, oos = oos_split(trades, args.oos_split)
            so = stats([t.ret_pct for t in oos], COST_READ_BP)
            if s["n"] >= 20 and s["pf"] > 1.0 and so["n"] >= 8 and so["pf"] > 1.0:
                score = min(s["pf"], so["pf"] if so["pf"] != float("inf") else s["pf"])
                if best is None or score > best[0]:
                    best = (score, hyp, L, s, so)
    if best is None:
        emit("  INGEN edge: ingen hypotese×latency har PF>1 ved 2bp med holdbar OOS.")
        emit("  → Katalysator har ingen påviselig edge på dette datagrundlag. Parkér sporet,")
        emit("    eller udvid univers/periode og gentag før en Benzinga-beslutning.")
    else:
        _sc, hyp, L, s, so = best
        emit(f"  Bedste: {hyp.upper()} ved latency L={L} min — PF@2bp={fmt_pf(s['pf'])} "
             f"(n={s['n']}, WR={s['wr']:.0f}%), OOS_PF={fmt_pf(so['pf'])} (n={so['n']}).")
        emit("")
        emit("  LATENCY-LÆSNING (kernen i Benzinga-beslutningen):")
        if L <= 5:
            emit(f"  • Edgen lever ved L={L} min → KRÆVER lav-latency feed. Benzinga (~$35/md")
            emit("    via API) er relevant, og PF-tallet ovenfor begrunder udgiften.")
        elif L >= 30:
            emit(f"  • Edgen lever ved L={L} min → nuværende (forsinkede) Dow Jones-feed er")
            emit("    hurtigt nok. INGEN Benzinga nødvendig.")
        else:
            emit(f"  • Edgen lever ved L={L} min → mellem-latency; test om den overlever ved")
            emit("    L=30/60 (DJ nok) før en Benzinga-beslutning.")
    emit("")
    emit("  FORBEHOLD: SHORT-tal antager borrow altid muligt (optimistiske). Univers er")
    emit("  hardkodet nutidige navne → mild survivorship-bias. move/vol er sweep-parametre —")
    emit("  bekræft et PLATEAU (--sweep), vælg ikke bare højeste PF. Realtid IKKE målt her.")
    emit("")

    # Dækningstabel sidst (kompakt)
    emit("  Dækning pr. navn (nyheder → katalysatorer):")
    for ticker, n_news, n_cat, note in coverage:
        emit(f"     {ticker:<8}{n_news:>6} → {n_cat:>4}   {note}")

    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"\nFil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
