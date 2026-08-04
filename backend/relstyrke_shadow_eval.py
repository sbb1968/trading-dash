#!/usr/bin/env python3
"""
relstyrke_shadow_eval.py — akkumuleret selection-alpha-bevis for Relativ Styrke (OFFLINE)
═════════════════════════════════════════════════════════════════════════════════════════
Spejler trendjoin_shadow_eval.py + trendjoin_forensik.py: en READ-ONLY offline-doemmer der
laeser dagens 'relstyrke_shadow_candidate'-event (source="Relativ Styrke") tilbage og — efter
luk — maaler om toppen af dagens rangliste slog RESTEN af universet.

HVORFOR: Relativ Styrke er long-only, saa dens raa P&L indeholder markeds-/small-cap-beta.
Edgen er IKKE det absolutte top-K-afkast (survivorship-/beta-loeftet) men SELECTION ALPHA =
(snit top-K) - (snit hele universet), pr. dag, netto for realistisk slippage. Det maales HER,
akkumuleret over mange sessioner (spor D's praeregistrerede dom kraever >= 1c-net positiv alpha
+ hit-rate > 0.50 i BAADE in-sample og out-of-sample). Enkelte dage er for faa til en dom.

Live-wrapperen (algo_relstyrke.py) emitterer ved T=09:45 EET event med HELE tvaersnittet
(hvert navns early_rs + rank + percentil) + de valgte top-3. Dette script rekonstruerer hvert
navns handel som backtesten (cross_sectional_rs_backtest.py): entry = naeste bar's OPEN efter T,
exit = EOD (sidste RTH-bar <= 15:51). Saa live-maalingen == backtest-mekanikken 1:1.

READ-ONLY mod handel: ingen ordrer, skriver kun sin egen summary. Egen CLIENT_ID = 49 (maa
IKKE kollidere med backend (client 1), trendjoin_shadow_eval (45) ELLER NKD-probe (46) — stop
backend, ELLER skift --client-id). Historik-pull sker EFTER luk, saa det aldrig konkurrerer med live-feedet.

    python relstyrke_shadow_eval.py --verify     # laes BARE dagens event tilbage (intet IBKR)
    python relstyrke_shadow_eval.py              # fuld eval (kraever TWS; stop backend foerst)
    python relstyrke_shadow_eval.py --day 2026-07-11

Placering: C:\\Projects\\trading_dash\\backend\\relstyrke_shadow_eval.py
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from datetime import date, datetime, time as dtime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

SOURCE = "Relativ Styrke"
HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 49

# FROSNE parametre (SKAL matche algo_relstyrke.py + cross_sectional_rs_backtest.py)
DECISION_ET_DEFAULT = dtime(9, 45)          # T
EOD_FORCE           = dtime(15, 51)         # exit-backstop
TOP_K               = 3
COST_READ_CENTS     = 1.0                   # doms-omkostning: cents/side (round-trip = 2x)
RTH_OPEN            = 9 * 60 + 30
RTH_CLOSE           = 16 * 60


# ── Journal-laesning (read-only) ─────────────────────────────────────────────
def ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def pts(s):
    if not s:
        return None
    for cut in (None, 26, 19):
        try:
            return datetime.fromisoformat(str(s) if cut is None else str(s)[:cut])
        except ValueError:
            continue
    return None


def read_snapshots(db, source, day):
    """Returnerer dagens 'relstyrke_shadow_candidate'-snapshots (typisk EEN pr. dag).
    Hver = payload-dict m. 'universe' (ranked liste), 'selected', 'decision_et', ..."""
    con = ro(db)
    rows = con.execute(
        "SELECT ts_local, ts_utc, event_type, payload_json FROM events "
        "WHERE source=? ORDER BY id", (source,)).fetchall()
    con.close()
    snaps = []
    for r in rows:
        t = pts(r["ts_local"]) or pts(r["ts_utc"])
        if not t or t.date() != day:
            continue
        if r["event_type"] != "relstyrke_shadow_candidate":
            continue
        try:
            pl = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except (json.JSONDecodeError, TypeError):
            pl = {}
        if isinstance(pl, dict) and pl.get("universe") is not None:
            snaps.append(pl)
    return snaps


# ── Bar-hjaelpere ────────────────────────────────────────────────────────────
def _bar_dt_et(b):
    d = b.date
    if isinstance(d, datetime):
        if d.tzinfo is not None and ET is not None:
            return d.astimezone(ET)
        return d.replace(tzinfo=ET) if (ET is not None and d.tzinfo is None) else d
    return d


def _tmin(dt):
    return dt.hour * 60 + dt.minute


def _parse_decision(s) -> dtime:
    try:
        parts = [int(x) for x in str(s).replace(":", " ").split()]
        return dtime(parts[0], parts[1])
    except (ValueError, IndexError):
        return DECISION_ET_DEFAULT


async def fetch_1m(ib, contract, end_dt):
    bars = await ib.reqHistoricalDataAsync(
        contract, endDateTime=end_dt, durationStr="1 D", barSizeSetting="1 min",
        whatToShow="TRADES", useRTH=True, formatDate=1)
    return bars or []


async def reconstruct_rth(ib, sym, day, end_dt):
    """Hent dagens 1-min RTH-bars for sym som [{tmin,o,h,l,c}] sorteret. None hvis mangler."""
    from ib_async import Stock
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(sym, "SMART", "USD")), timeout=15)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        contract = q[0] if (q and getattr(q[0], "conId", 0)) else None
    except Exception:
        contract = None
    if contract is None:
        return None, "ukvalificeret"
    bars1 = await fetch_1m(ib, contract, end_dt)
    rth = []
    for b in bars1:
        dt = _bar_dt_et(b)
        if not isinstance(dt, datetime) or dt.date() != day:
            continue
        tm = _tmin(dt)
        if RTH_OPEN <= tm < RTH_CLOSE:
            rth.append({"tmin": tm, "o": float(b.open), "h": float(b.high),
                        "l": float(b.low), "c": float(b.close)})
    rth.sort(key=lambda x: x["tmin"])
    if len(rth) < 3:
        return None, "for_faa_rth_bars"
    return rth, "ok"


def simulate(rth, T_min):
    """Rekonstruer handlen som backtesten: entry = OPEN paa foerste bar med tmin > T; exit =
    EOD (sidste bar med tmin <= 15:51). Returnerer (gross_pct, entry_px, entry_tmin, exit_tmin)
    eller None."""
    ei = next((i for i, b in enumerate(rth) if b["tmin"] > T_min), None)
    if ei is None:
        return None
    entry = rth[ei]["o"]
    if entry <= 0:
        return None
    eod_min = EOD_FORCE.hour * 60 + EOD_FORCE.minute
    xi = None
    for i in range(ei, len(rth)):
        if rth[i]["tmin"] <= eod_min:
            xi = i
        else:
            break
    if xi is None or xi < ei:
        xi = len(rth) - 1
    exit_px = rth[xi]["c"]
    return (exit_px - entry) / entry * 100.0, entry, rth[ei]["tmin"], rth[xi]["tmin"]


def net_pct(gross_pct, entry_px, cents):
    """Round-trip omkostning cents/side -> pct af entry-pris (spejler backtestens net_pct)."""
    if entry_px <= 0:
        return gross_pct
    cost = (2.0 * cents / 100.0) / entry_px * 100.0
    return gross_pct - cost


def _end_dt(day):
    """endDateTime til IBKR: '' for i dag (=nu), ellers dagens efter-luk i ET."""
    today = datetime.now(ET).date() if ET else date.today()
    if day >= today:
        return ""
    if ET is None:
        return f"{day:%Y%m%d} 20:00:00"
    return datetime(day.year, day.month, day.day, 20, 0, tzinfo=ET)


def _hms(tm):
    return f"{tm // 60:02d}:{tm % 60:02d}"


# ── --verify (intet IBKR) ────────────────────────────────────────────────────
def run_verify(db, source, day, emit):
    snaps = read_snapshots(db, source, day)
    emit(f"\n  --verify · dag {day} · source='{source}' (ingen IBKR-pull)")
    if not snaps:
        emit("\n  (tomt — live-wrapperen har ikke emitteret et shadow-snapshot for dagen endnu)")
        return
    for s in snaps:
        uni = s.get("universe") or []
        sel = s.get("selected") or []
        emit(f"\n  SNAPSHOT @ {s.get('scan_et','?')} ET · beslutning T={s.get('decision_et','?')} · "
             f"score={s.get('score','?')} · {len(uni)} navne · top-{s.get('top_k', TOP_K)}")
        emit(f"  VALGT (top-{s.get('top_k', TOP_K)}): {', '.join(sel) if sel else '(ingen)'}")
        emit(f"\n  {'rank':>4} {'symbol':<7}{'early_rs':>10}{'percentil':>11}   valgt")
        for row in sorted(uni, key=lambda r: r.get("rank", 9999)):
            sym = row.get("symbol", "?")
            mark = "  ✅" if sym in sel else ""
            emit(f"  {row.get('rank','?'):>4} {sym:<7}"
                 f"{row.get('early_rs', 0)*100:>9.2f}%{row.get('percentile', 0):>11.3f}{mark}")


# ── Aggregering + output ─────────────────────────────────────────────────────
def summarize_day(day, uni, sel, results, cents, emit):
    """results: {sym -> (gross_pct, entry_px, entry_tmin, exit_tmin)}. Beregn top-K-snit,
    universe-snit og selection alpha (netto). Returnerer dict til akkumulering."""
    traded = {sym: r for sym, r in results.items() if r is not None}
    if len(traded) < 2:
        emit("     (for faa navne med handel til at maale alpha)")
        return None
    univ_nets = [net_pct(g, e, cents) for (g, e, _et, _xt) in traded.values()]
    univ_ret = statistics.mean(univ_nets)
    topk_names = [s for s in sel if s in traded][:TOP_K]
    if not topk_names:
        emit("     (ingen af de valgte top-K havde en brugbar handel)")
        return None
    topk_nets = [net_pct(*(traded[s][:2]), cents) for s in topk_names]
    topk_ret = statistics.mean(topk_nets)
    alpha = topk_ret - univ_ret
    hit = topk_ret > univ_ret
    emit(f"     top-{len(topk_names)}-snit {topk_ret:+.3f}%  ·  universe-snit ({len(traded)}) "
         f"{univ_ret:+.3f}%  ·  SELECTION ALPHA {alpha:+.3f}%  ·  {'HIT' if hit else 'miss'}")
    return {"day": day, "topk_ret": topk_ret, "univ_ret": univ_ret, "alpha": alpha, "hit": hit,
            "n_univ": len(traded), "n_topk": len(topk_names)}


def _persist_alpha_log(out_dir, day, day_aggs, cents, emit):
    """Upsert dagens realiserede selection-alpha til en AKKUMULERENDE CSV (en raekke pr. snapshot)
    saa vi kan trende Route B-beviset over mange sessioner. Idempotent: erstatter evt. raekker for
    samme dag (saa en genkoersel ikke dobbelt-tegner). Sorteret paa (dag, snapshot)."""
    path = out_dir / "alpha_log.csv"
    header = ["day", "snap", "alpha", "topk_ret", "univ_ret", "hit", "n_univ", "n_topk", "cost_cents"]
    rows = []
    if path.exists():
        try:
            with path.open(newline="", encoding="utf-8") as f:
                rows = [r for r in csv.DictReader(f) if r.get("day") != str(day)]
        except Exception as e:
            emit(f"  (advarsel: kunne ikke laese eksisterende alpha_log.csv: {e})")
    for i, a in enumerate(day_aggs):
        rows.append({"day": str(day), "snap": str(i), "alpha": f"{a['alpha']:.6f}",
                     "topk_ret": f"{a['topk_ret']:.6f}", "univ_ret": f"{a['univ_ret']:.6f}",
                     "hit": str(int(a['hit'])), "n_univ": str(a['n_univ']),
                     "n_topk": str(a['n_topk']), "cost_cents": str(cents)})
    rows.sort(key=lambda r: (str(r["day"]), int(r["snap"])))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    pos = sum(1 for r in rows if float(r["alpha"]) > 0)
    emit(f"\n  Akkumuleret: {path.name} ({len(rows)} dags-raekker; {pos} m. positiv alpha) "
         f"-> analyserbar Route B-tidsserie.")


async def run_full(db, source, day, args, emit):
    from ib_async import IB
    out_dir = Path.cwd() / "relstyrke_shadow_eval_output"
    snaps = read_snapshots(db, source, day)
    emit(f"\n  Dag {day}: {len(snaps)} shadow-snapshot(s)")
    if not snaps:
        emit("  (intet at evaluere — live-wrapperen har ikke emitteret event. Koer efter en session.)")
        return

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde TWS (client-id {args.client_id}): {e}")
        emit("  -> stop backenden (client-id-konflikt) eller vaelg en ledig --client-id.")
        return
    emit("  Forbundet til TWS (read-only historik).\n")
    end_dt = _end_dt(day)

    day_aggs = []
    try:
        for s in snaps:
            uni = s.get("universe") or []
            sel = s.get("selected") or []
            T_min = (_parse_decision(s.get("decision_et")).hour * 60
                     + _parse_decision(s.get("decision_et")).minute)
            emit("=" * 78)
            emit(f"  SNAPSHOT @ {s.get('scan_et','?')} ET · T={s.get('decision_et','?')} · "
                 f"{len(uni)} navne · valgt: {', '.join(sel) if sel else '(ingen)'}")
            emit("=" * 78)
            results: dict = {}
            for row in uni:
                sym = (row.get("symbol") or "").upper()
                if not sym:
                    continue
                rth, why = await reconstruct_rth(ib, sym, day, end_dt)
                if rth is None:
                    results[sym] = None
                    emit(f"   {sym:<7} ingen handel ({why})")
                    continue
                sim = simulate(rth, T_min)
                results[sym] = sim
                if sim is None:
                    emit(f"   {sym:<7} ingen handel (ingen bar efter T)")
                    continue
                g, e, et_, xt_ = sim
                mark = "  ✅valgt" if sym in sel else ""
                emit(f"   {sym:<7} entry ${e:.2f} @ {_hms(et_)} -> exit @ {_hms(xt_)}  "
                     f"gross {g:+.2f}%  net {net_pct(g, e, args.cost_cents):+.2f}%{mark}")
            emit("")
            agg = summarize_day(day, uni, sel, results, args.cost_cents, emit)
            if agg:
                day_aggs.append(agg)
    finally:
        ib.disconnect()

    emit("\n" + "=" * 78)
    emit("  DAGENS SELECTION ALPHA (top-K minus universe-snit, netto)")
    emit("=" * 78)
    if not day_aggs:
        emit("  (ingen brugbare dags-observationer)")
    else:
        for a in day_aggs:
            emit(f"   {a['day']}  alpha {a['alpha']:+.3f}%  (top-{a['n_topk']} {a['topk_ret']:+.3f}% "
                 f"vs univ {a['univ_ret']:+.3f}%, n={a['n_univ']})  {'HIT' if a['hit'] else 'miss'}")
        _persist_alpha_log(out_dir, day, day_aggs, args.cost_cents, emit)

    emit("\n  FORUDREGISTRERET LAESNING (spor D): spor D er leve-testbart KUN hvis selection alpha")
    emit("  (top-K minus universe-snit) er POSITIV netto @>=1c OG hit-rate > 0.50 — i BAADE in-")
    emit("  sample OG out-of-sample, med et robust PLATEAU. EEN dag doemmer intet; dette er en")
    emit("  AKKUMULERET dom over mange live-sessioner (koer scriptet dagligt og saml alpha/hit).")


# ── --selftest (KOERSEL 3): shadow-alpha == backtest-alpha for samme picks (offline) ────────
def run_selftest(args, emit) -> bool:
    """Bevis at shadow-eval's selection-alpha er IDENTISK med backtestens for samme dag/picks/kost.
    Bruger bar_cache (ingen IBKR): rekonstruerer eventet fra backtesten (samme early_rs + top-3),
    koerer shadow-eval's EGEN simulate/net_pct/summarize_day paa samme bars, og sammenligner med
    cross_sectional_rs_backtest.build_day_obs.alpha. Alle dage skal matche (op til afrunding)."""
    import cross_sectional_rs_backtest as bt
    cents = args.cost_cents
    T = dtime(9, 45)
    T_min = T.hour * 60 + T.minute

    emit("\n  KOERSEL 3 — SELFTEST: shadow-alpha vs backtest-alpha (samme picks, offline bar_cache)")
    universe = bt.load_universe()
    if not universe:
        emit("  INGEN universe-filer -> kan ikke selfteste.")
        return False
    all_names = sorted({tk for ticks in universe.values() for tk in ticks})
    data = {tk: bt.load_ticker_days(tk) for tk in all_names}

    def _silent(*_a, **_k):
        pass

    emit(f"     {'dag':>11}{'backtest_alpha':>16}{'shadow_alpha':>14}{'diff':>12}   verdikt")
    n_ok = n_match = 0
    max_diff = 0.0
    shown = 0
    for day in sorted(universe):
        # backtest-siden (kilden)
        obs = bt.build_day_obs(universe, data, day, T, "early_rs", 3, "eod", cents,
                               bt.TARGET_PCT_DEFAULT, bt.STOP_PCT_DEFAULT, bt.TRAIL_LB_DEFAULT)
        bt_alpha = obs.alpha if obs else None

        # shadow-siden: rekonstruer picks + koer shadow's egen simulate/summarize paa samme bars
        scores, results = {}, {}
        for sym in universe[day]:
            bars = data.get(sym, {}).get(day)
            if not bars or len(bars) < 3:
                continue
            ns = bt.compute_name_score(sym, bars, None, T, "early_rs")
            if ns.raw is not None:
                scores[sym] = ns.raw
            rth = [{"tmin": b.dt.hour * 60 + b.dt.minute, "o": b.o, "h": b.h, "l": b.l, "c": b.c}
                   for b in bars]
            results[sym] = simulate(rth, T_min)
        sel = sorted(scores, key=lambda s: (-scores[s], s))[:TOP_K]
        agg = summarize_day(day, [], sel, results, cents, _silent)
        sh_alpha = agg["alpha"] if agg else None

        if bt_alpha is None and sh_alpha is None:
            continue                                   # begge udelader dagen -> enige, ingen dom
        n_ok += 1
        if bt_alpha is None or sh_alpha is None:
            verdict = "AFVIG (én mangler)"
            diff = float("nan")
        else:
            diff = abs(bt_alpha - sh_alpha)
            max_diff = max(max_diff, diff)
            match = diff < 1e-6
            n_match += match
            verdict = "match" if match else "AFVIG"
        if shown < args.days:
            shown += 1
            ba = f"{bt_alpha:+.6f}" if bt_alpha is not None else "na"
            sa = f"{sh_alpha:+.6f}" if sh_alpha is not None else "na"
            dd = f"{diff:.2e}" if diff == diff else "na"     # nan-check
            emit(f"     {str(day):>11}{ba:>16}{sa:>14}{dd:>12}   {verdict}")

    ok = (n_ok > 0 and n_match == n_ok)
    emit("")
    emit(f"  Dage sammenlignet: {n_ok}  ·  match: {n_match}/{n_ok}  ·  max diff: {max_diff:.2e}")
    if ok:
        emit("  -> PASS: shadow-eval maaler PRAECIS samme selection-alpha som backtesten. Route B")
        emit("     dommer paa det tal backtesten bestod. (entry=naeste-open, exit=EOD, net-kost 1:1.)")
    else:
        emit("  -> AFVIG: definitionen (entry-timing / universe-population / slippage) er ude af sync.")
        emit("     Ret shadow-eval til at spejle backtesten 1:1 og genkoer FOER paper taeller.")
    return ok


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Relativ Styrke — shadow selection-alpha-eval (offline, read-only)")
    ap.add_argument("--db", default="trading_dash.db")
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default i dag)")
    ap.add_argument("--verify", action="store_true", help="laes BARE dagens event tilbage (intet IBKR)")
    ap.add_argument("--selftest", action="store_true",
                    help="KOERSEL 3: bevis shadow-alpha == backtest-alpha (offline, intet IBKR)")
    ap.add_argument("--days", type=int, default=8, help="antal dage m. detaljer (--selftest)")
    ap.add_argument("--cost-cents", type=float, default=COST_READ_CENTS, help="doms-omkostning cents/side")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    a = ap.parse_args()
    day = date.fromisoformat(a.day) if a.day else (datetime.now(ET).date() if ET else date.today())

    out_dir = Path.cwd() / "relstyrke_shadow_eval_output"
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    mode = "SELFTEST" if a.selftest else ("VERIFY" if a.verify else "FULD")
    emit("=" * 78)
    emit(f"  RELATIV STYRKE — SHADOW SELECTION-ALPHA-EVAL · dag {day} · {mode}")
    emit("=" * 78)

    if a.selftest:
        run_selftest(a, emit)
    elif a.verify:
        run_verify(a.db, a.source, day, emit)
    else:
        try:
            asyncio.run(run_full(a.db, a.source, day, a, emit))
        except KeyboardInterrupt:
            emit("\n  Afbrudt.")

    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    # Dateret kopi ved en fuld (efter-luk) eval, saa hver sessions detaljer bevares til analyse.
    if not a.verify and not a.selftest:
        (out_dir / f"summary_{day}.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"\n  Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
