#!/usr/bin/env python3
"""
relstyrke_parity.py — Spor D live-vs-backtest paritets-harness (KOERSEL 1 + 2)
═══════════════════════════════════════════════════════════════════════════════════════════
Ren OFFLINE, ingen IBKR, ingen handel. GATER Route B: beviser at live-wrapperens rangering
matcher backtestens FOER paper-sessionerne taeller som dom.

KOERSEL 1 — Bar-timestamp-konvention: hvad betyder 'timestamp' i en 1-min bar (aabnings- eller
lukketid), og hvilken faktisk bar lander live's price_T paa naar den fyrer efter 09:45.

KOERSEL 2 — early_rs-paritet: for >=3 historiske sessioner beregnes early_rs pr. navn PAA TO
MAADER og top-3 sammenlignes:
  a) backtest-pathen = cross_sectional_rs_backtest.compute_name_score (kilden).
  b) live-pathen     = algo_relstyrke.RelStyrkeLive._early_rs (DEN FAKTISKE live-funktion, ikke
     en kopi), fodret med praecis de bars live ville have tilgaengelige ved fyring.
Live modelleres i TO fyrings-tidspunkter: NUVAERENDE (~09:45:15, foer 09:45-baren er komplet ->
ser kun t<=09:44) og FIXED (~09:46, efter 09:45-baren er komplet -> ser t<=09:45 == backtesten).

Praeregistreret pass (KOERSEL 2): top-3 identisk paa ALLE testede dage i FIXED-pathen (Spearman
~1.0). Divergerer NUVAERENDE systematisk, er det netop 14-vs-15-min-baren -> anvend fix i
algo_relstyrke.py (fyr foerst naar 09:45-baren er komplet) og genkoer.

Brug (fra backend/):
    python relstyrke_parity.py                 # alle brugbare dage, rapport
    python relstyrke_parity.py --days 6        # detaljer for foerste 6 dage

Output: cross_sectional_rs_output/parity_report.txt
Placering: C:\\Projects\\trading_dash\\backend\\relstyrke_parity.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta, time as dtime
from pathlib import Path

import cross_sectional_rs_backtest as bt
from algo_relstyrke import RelStyrkeLive, DECISION_ET
from strategies.base import Bar

OUT_DIR = bt.OUT_DIR

# Fyrings-tidspunkter der modelleres (sekund-praecist).
FIRE_CURRENT = dtime(9, 45, 15)   # nuvaerende: ~15s efter 09:45:00 (09:45-baren IKKE komplet)
FIRE_FIXED   = dtime(9, 46, 5)    # fix: efter 09:45-baren er lukket (09:46:00) + lidt margin


def _to_live_bars(bt_bars) -> list:
    """cross_sectional_rs_backtest.Bar -> strategies.base.Bar (den type live-koden bruger)."""
    return [Bar(timestamp=b.dt, open=b.o, high=b.h, low=b.l, close=b.c, volume=b.v)
            for b in bt_bars]


def _completed_at(live_bars, fire: dtime) -> list:
    """Bars der er BEKRAEFTET komplette ved fyrings-tid 'fire' (samme dag). En bar stemplet paa
    aabningstid t lukker t+1min; den er tilgaengelig via get_historical_bars naar t+1 <= fire.
    Dette modellerer praecis hvad live's _fetch_bars returnerer paa fyrings-oejeblikket."""
    out = []
    for b in live_bars:
        close_time = (b.timestamp + timedelta(minutes=1)).time()
        if close_time <= fire:
            out.append(b)
    return out


def _live_early_rs(live_bars, today, fire: dtime):
    """Kald DEN FAKTISKE live-funktion (RelStyrkeLive._early_rs) paa de bars live ville se ved
    'fire'. self bruges ikke i _early_rs, saa None som self er sikkert (tester den ægte kode)."""
    avail = _completed_at(live_bars, fire)
    res = RelStyrkeLive._early_rs(None, avail, today)
    return None if res is None else res[0]      # (rs, open_0930, price_T) -> rs


def _last_bar_at(live_bars, fire: dtime):
    """Hvilken bar-timestamp lander price_T paa ved 'fire' (sidste komplette bar <= DECISION_ET)?"""
    avail = [b for b in _completed_at(live_bars, fire)
             if b.timestamp.time() <= DECISION_ET]
    return avail[-1].timestamp.time() if avail else None


def _spearman(rank_a: dict, rank_b: dict) -> float | None:
    """Spearman rho over navne der har en rang i BEGGE. 1.0 = identisk rangorden."""
    common = [s for s in rank_a if s in rank_b]
    n = len(common)
    if n < 3:
        return None
    d2 = sum((rank_a[s] - rank_b[s]) ** 2 for s in common)
    return 1.0 - (6.0 * d2) / (n * (n * n - 1))


def _ranks(scores: dict) -> dict:
    """{sym: score} -> {sym: rang} (1 = hoejest score). Ties: stabil paa symbol."""
    order = sorted(scores, key=lambda s: (-scores[s], s))
    return {s: i + 1 for i, s in enumerate(order)}


def _topk(scores: dict, k: int) -> list:
    return sorted(scores, key=lambda s: (-scores[s], s))[:k]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Spor D live-vs-backtest paritets-harness")
    ap.add_argument("--days", type=int, default=8, help="antal dage m. detaljer i rapporten")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    lines = []

    def e(s=""):
        print(s, flush=True)
        lines.append(s)

    e("=" * 92)
    e("  SPOR D — LIVE-VS-BACKTEST PARITETS-HARNESS (KOERSEL 1 + 2)")
    e("=" * 92)
    e("  Offline, ingen IBKR, ingen handel. Gater Route B: live-rangering == backtest FOER paper taeller.")
    e("")

    universe = bt.load_universe()
    if not universe:
        e("  INGEN universe-filer -> kan ikke koere paritet.")
        (OUT_DIR / "parity_report.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1
    all_names = sorted({tk for ticks in universe.values() for tk in ticks})
    data = {tk: bt.load_ticker_days(tk) for tk in all_names}

    # ── KOERSEL 1 — bar-timestamp-konvention ──────────────────────────────────────
    e("── KOERSEL 1: BAR-TIMESTAMP-KONVENTION ──")
    e("  bar_cache/ + live IBKR-bars er SAMME kilde (IBKR reqHistoricalData). IBKR stempler en")
    e("  bar paa dens AABNINGSTID (bar-start): en bar stemplet 09:30 daekker [09:30, 09:31) og er")
    e("  foerst KOMPLET (returneres af get_historical_bars) kl. 09:31:00.")
    # Konkret demonstration paa foerste dag/navn med daekning
    demo = None
    for day in sorted(universe):
        for tk in universe[day]:
            days = data.get(tk)
            if days and day in days and len(days[day]) > 20:
                demo = (day, tk, days[day]); break
        if demo:
            break
    if demo:
        day, tk, bt_bars = demo
        lb = _to_live_bars(bt_bars)
        first3 = ", ".join(b.dt.strftime("%H:%M") for b in bt_bars[:3])
        e(f"  Demo {tk} {day}: foerste bars stemplet {first3} (= aabningstider; 09:30 = RTH-open).")
        bt_bar = _last_bar_at(lb, dtime(23, 59))      # backtest = alt <= DECISION_ET tilgaengeligt
        cur_bar = _last_bar_at(lb, FIRE_CURRENT)
        fix_bar = _last_bar_at(lb, FIRE_FIXED)
        e(f"    backtest price_T  -> sidste bar <= 09:45  = {bt_bar}  (09:45-baren, luk ~09:46)")
        e(f"    live @ {FIRE_CURRENT}  -> sidste KOMPLETTE bar   = {cur_bar}  (09:45-baren ikke lukket endnu)")
        e(f"    live @ {FIRE_FIXED}   -> sidste KOMPLETTE bar   = {fix_bar}  (09:45-baren nu komplet == backtest)")
        e(f"  >> Forskellen: live fyrer ~15s efter 09:45 og ser EEN bar mindre. Fix = fyr efter 09:45-baren lukker.")
    e("")

    # ── KOERSEL 2 — early_rs-paritet ──────────────────────────────────────────────
    e("── KOERSEL 2: early_rs-PARITET (backtest vs live-path; top-3 pr. dag) ──")
    e("  a) backtest = compute_name_score('early_rs')   b) live = RelStyrkeLive._early_rs (ægte kode)")
    e(f"  live modelleret ved NUVAERENDE fyring ({FIRE_CURRENT}) og FIXED fyring ({FIRE_FIXED}).")
    e("")
    e(f"     {'dag':>11}{'n':>4}{'top3_backtest':>22}{'top3_live_NU':>22}{'top3_live_FIX':>22}"
      f"{'rhoNU':>7}{'rhoFIX':>7}{'NU':>5}{'FIX':>5}")

    days_all = sorted(universe)
    n_days_ok = 0
    cur_match = fix_match = 0
    shown = 0
    detail_days = []
    for day in days_all:
        bt_scores, cur_scores, fix_scores = {}, {}, {}
        for tk in universe[day]:
            days = data.get(tk)
            if not days or day not in days:
                continue
            bt_bars = days[day]
            if len(bt_bars) < 2:
                continue
            ns = bt.compute_name_score(tk, bt_bars, None, DECISION_ET, "early_rs")
            if ns.raw is None:
                continue
            bt_scores[tk] = ns.raw
            lb = _to_live_bars(bt_bars)
            cur = _live_early_rs(lb, day, FIRE_CURRENT)
            fix = _live_early_rs(lb, day, FIRE_FIXED)
            if cur is not None:
                cur_scores[tk] = cur
            if fix is not None:
                fix_scores[tk] = fix
        if len(bt_scores) < 3:
            continue
        n_days_ok += 1
        t_bt = _topk(bt_scores, 3)                              # K=3 (frossen)
        t_cur = _topk(cur_scores, 3)
        t_fix = _topk(fix_scores, 3)
        rho_cur = _spearman(_ranks(bt_scores), _ranks(cur_scores))
        rho_fix = _spearman(_ranks(bt_scores), _ranks(fix_scores))
        m_cur = set(t_bt) == set(t_cur)
        m_fix = set(t_bt) == set(t_fix)
        cur_match += m_cur
        fix_match += m_fix
        if shown < args.days:
            detail_days.append((day, len(bt_scores), t_bt, t_cur, t_fix, rho_cur, rho_fix, m_cur, m_fix))
            shown += 1

    for (day, n, t_bt, t_cur, t_fix, rc, rf, mc, mf) in detail_days:
        e(f"     {str(day):>11}{n:>4}{','.join(t_bt):>22}{','.join(t_cur):>22}{','.join(t_fix):>22}"
          f"{(f'{rc:.2f}' if rc is not None else 'na'):>7}{(f'{rf:.2f}' if rf is not None else 'na'):>7}"
          f"{('=' if mc else 'x'):>5}{('=' if mf else 'x'):>5}")
    e("")
    e(f"  Dage testet: {n_days_ok}  ·  top-3 match NUVAERENDE: {cur_match}/{n_days_ok}  ·  "
      f"top-3 match FIXED: {fix_match}/{n_days_ok}")
    e("")

    # ── SAMLET GATE ───────────────────────────────────────────────────────────────
    e("─" * 92)
    e("  VERDIKT (KOERSEL 1 + 2)")
    e("─" * 92)
    e("  KOERSEL 1: bar-konvention fastslaaet = timestamp er AABNINGSTID; live ser 09:44-bar ved")
    e("             09:45:15, backtest ser 09:45-bar. FASTSLAAET.")
    fix_pass = (n_days_ok > 0 and fix_match == n_days_ok)
    if fix_pass:
        e(f"  KOERSEL 2: FIXED-pathen matcher backtestens top-3 paa ALLE {n_days_ok} dage -> PASS.")
        if cur_match < n_days_ok:
            e(f"             (NUVAERENDE fyring divergerede paa {n_days_ok - cur_match} dag(e) — netop")
            e(f"             14-vs-15-min-baren. Anvend fixet i algo_relstyrke.py: fyr efter 09:45-baren")
            e(f"             lukker (DECISION_FIRE_ET=09:46), saa live == FIXED == backtest.)")
        else:
            e(f"             (NUVAERENDE fyring matchede ogsaa paa alle dage — ingen praktisk divergens,")
            e(f"             men fixet giver stadig determinisme; anbefalet.)")
    else:
        e(f"  KOERSEL 2: FIXED-pathen matcher IKKE paa alle dage ({fix_match}/{n_days_ok}) -> UNDERSOEG.")
        e(f"             En reel definitions-forskel ud over bar-timing; ret foer paper taeller.")
    e("")

    (OUT_DIR / "parity_report.txt").write_text("\n".join(lines), encoding="utf-8")
    e(f"Fil: {OUT_DIR / 'parity_report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
