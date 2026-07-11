"""
dagens_log.py — fuldt overblik over dagens journal-events + dyb per-handel-forensik.

Brug:
    python dagens_log.py                            # alle strategier, i dag
    python dagens_log.py Konfluens                  # filtrer på én strategi
    python dagens_log.py Konfluens 2026-05-27       # specifik dato
    python dagens_log.py --forensics                # + dyb per-handel-forensik
    python dagens_log.py 2026-06-12 --forensics     # én dag
    python dagens_log.py 2026-06-01 2026-06-12 --forensics   # dato-INTERVAL (inklusivt)

Output er MARKDOWN (overskrifter + punkter — ikke monospace-kolonner): bygges af
build_report_md() saa terminal og "Dagens log"-vinduet deler nøjagtig samme rapport.
Strengt READ-ONLY (sqlite mode=ro) — sikker at køre ved siden af en kørende strategi.

Overblik (altid) viser i rækkefølge:
  1. Strategi-livscyklus (start/stop/emergency)
  2. Univers (Lag A): hvad blev valgt
  3. Handler: åbnede og lukkede positioner med P&L
  4. Lag C: trade forensics — kort oversigt (fuld dump med --forensics)
  5. Lag B: afvisninger — hvorfor entries IKKE blev taget
  6. Ordrer: godkendte, afviste, IBKR-fejl
  7. System-events: emergency stops, daily limit, IBKR connect-problemer
  8. Diagnostik (daily_diagnostics)
  9. Heartbeat (periodiske snapshots)

--forensics: for HVER handel i trades-tabellen dumpes ALT vi gemmer — hele
trades-rækken (priser, tider ET+dansk, P&L, risiko, stop) + det matchede
entry- og exit-snapshot (indikatorer, setup, tape, depth, MFE/MAE) side om side.
Forensics matches pr. (source, symbol, nærmeste tid).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime

try:
    import pytz
    _CPH = pytz.timezone("Europe/Copenhagen")
except Exception:                       # pragma: no cover
    _CPH = None

DB_PATH = "trading_dash.db"

# Match-tolerance når et forensics-snapshot kobles til en handel (sekunder).
# Snapshottets time_et er bar-tidspunktet; handlens entry/exit ligger typisk
# < 60 s væk. 600 s giver margen uden at ramme en NABO-handel i samme symbol.
SNAPSHOT_MATCH_TOLERANCE_SEC = 600


# ─────────────────────────────────────────────────────────────────
# Forbindelse + små helpers
# ─────────────────────────────────────────────────────────────────
def ro_connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def t(ts: str | None) -> str:
    """'2026-05-27T11:31:28.5+02:00' -> '11:31:28'."""
    if not ts:
        return "—"
    return ts.split("T")[1][:8] if "T" in ts else ts


def p(row) -> dict:
    try:
        return json.loads(row["payload_json"]) if row["payload_json"] else {}
    except Exception:
        return {}


def dansk(utc_iso: str | None, full: bool = False) -> str | None:
    """Dansk lokaltid fra et UTC-ISO-tidsstempel (+00:00). DST-korrekt via pytz."""
    if not utc_iso or _CPH is None:
        return None
    try:
        d = datetime.fromisoformat(utc_iso).astimezone(_CPH)
    except (ValueError, TypeError):
        return None
    return d.strftime("%Y-%m-%d %H:%M:%S") if full else d.strftime("%H:%M:%S")


def _et_naive(s: str | None) -> datetime | None:
    """Parse et ET-tidsstempel (ISO med offset, eller 'YYYY-MM-DD HH:MM:SS') til
    naiv ET-vægur-tid, så trades og forensics kan sammenlignes på samme akse."""
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s).replace(tzinfo=None)
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────
# Indlæsning — dato-INTERVAL (inklusivt), substr-prefiks BETWEEN
# ─────────────────────────────────────────────────────────────────
def load_events(con, d_from, d_to, strategy_filter):
    where = ["substr(ts_local, 1, 10) BETWEEN ? AND ?"]
    params = [d_from, d_to]
    if strategy_filter:
        where.append("source LIKE ?")
        params.append(f"%{strategy_filter}%")
    sql = (f"SELECT id, ts_local, instance_id, source, event_type, symbol, payload_json "
           f"FROM events WHERE {' AND '.join(where)} ORDER BY id ASC")
    return con.execute(sql, params).fetchall()


def load_trades(con, d_from, d_to, strategy_filter):
    """Handler i dato-intervallet fra den kanoniske trades-tabel (entry-dag = handelsdag)."""
    where = ["substr(entry_time_et, 1, 10) BETWEEN ? AND ?"]
    params = [d_from, d_to]
    if strategy_filter:
        where.append("source LIKE ?")
        params.append(f"%{strategy_filter}%")
    sql = (f"SELECT * FROM trades WHERE {' AND '.join(where)} "
           f"ORDER BY entry_time_et ASC")
    return con.execute(sql, params).fetchall()


def load_forensics_for(con, source, symbol):
    """Alle entry/exit-snapshots for (source, symbol) — bruges til matching."""
    rows = con.execute(
        "SELECT payload_json FROM events WHERE event_type='trade_forensics' "
        "AND source=? AND symbol=?", (source, symbol)).fetchall()
    entry, exit_ = [], []
    for r in rows:
        try:
            d = json.loads(r["payload_json"])
        except Exception:
            continue
        ts = _et_naive(d.get("time_et"))
        if ts is None:
            continue
        (entry if d.get("phase") == "entry" else exit_).append((ts, d))
    return entry, exit_


def _nearest(cands, target):
    """Nærmeste snapshot til target-tid indenfor tolerancen, ellers None."""
    if not cands or target is None:
        return None
    best, best_dt = None, None
    for ts, d in cands:
        delta = abs((ts - target).total_seconds())
        if delta <= SNAPSHOT_MATCH_TOLERANCE_SEC and (best_dt is None or delta < best_dt):
            best, best_dt = d, delta
    return best


def match_snapshots(con, tr) -> tuple[dict | None, dict | None]:
    entry_c, exit_c = load_forensics_for(con, tr["source"], tr["symbol"])
    entry = _nearest(entry_c, _et_naive(tr["entry_time_et"]))
    exit_ = _nearest(exit_c, _et_naive(tr["exit_time_et"]))
    return entry, exit_


# ─────────────────────────────────────────────────────────────────
# Overblik (sektion 1–9)
# ─────────────────────────────────────────────────────────────────
def _overview_md(rows, trades, strategy_filter, forensics):
    """Bygger overblikket (sektion 1-9) som markdown-linjer (ingen monospace)."""
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["event_type"]].append(r)
    out = []

    # 1. Livscyklus
    out.append("## 1. Strategi-livscyklus")
    lifecycle = (by_type.get("strategy_started", []) + by_type.get("strategy_stopped", [])
                 + by_type.get("strategy_emergency_stop", []))
    if lifecycle:
        for ev in lifecycle:
            kind = ev["event_type"].replace("strategy_", "").upper()
            out.append(f"- {t(ev['ts_local'])} · **{ev['source']}** {kind}")
    else:
        out.append("_(ingen livscyklus-events)_")
    out.append("")

    # 2. Univers
    out.append("## 2. Univers (Lag A)")
    universe = by_type.get("universe_selected", [])
    if universe:
        for ev in universe:
            d = p(ev)
            tickers = d.get("tickers", [])
            rows = d.get("rows") or []
            pool = d.get("pool_size")
            head = (f"{len(tickers)} af {pool} i puljen" if pool is not None
                    else f"{len(tickers)} af {d.get('raw_count','?')} raa")
            out.append(f"- {t(ev['ts_local'])} · **{ev['source']}**: {head}")
            if rows:
                # Del 1-instrumentering: vis bevægelses-metrikkerne pr. ticker, så vi
                # kan se fordelingen af ATR-1D blandt navne der handler godt vs. dør i
                # stoppet. Sorteret som universet (change DESC).
                for r in rows:
                    out.append(
                        f"  - {str(r.get('symbol','?')):<6} ${r.get('price',0):>7.2f}"
                        f"  chg {r.get('change',0):+.1f}%"
                        f"  ATR-1D {r.get('atrp_1d',0):.1f}%"
                        f"  ATR-1W {r.get('atrp_1w',0):.1f}%"
                        f"  RVOL {r.get('rvol',0):.1f}"
                    )
            elif tickers:
                out.append(f"  - {', '.join(tickers)}")
    else:
        out.append("_(ingen universe-events)_")
    out.append("")

    # 3. Handler — fra trades-tabellen (kanonisk)
    out.append("## 3. Handler")
    closed = [tr for tr in trades if tr["exit_time_et"]]
    open_only = [tr for tr in trades if not tr["exit_time_et"]]
    out.append(f"Handler i alt: **{len(trades)}** · lukkede: {len(closed)} · aabne: {len(open_only)}")
    out.append("")
    total = wins = losses = 0
    for tr in trades:
        pnl = tr["pnl"] if tr["pnl"] is not None else 0.0
        total += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        et_dk, et_et = dansk(tr["entry_time_utc"]) or "—", t(tr["entry_time_et"])
        if tr["exit_time_et"]:
            xt_dk, xt_et = dansk(tr["exit_time_utc"]) or "—", t(tr["exit_time_et"])
            exit_part = f"-> exit ${tr['exit_price']} ({xt_dk} dansk / {xt_et} ET)"
        else:
            exit_part = "-> **ÅBEN**"
        dur = tr["duration_sec"]
        durtxt = f" · {dur//60} min" if dur is not None else ""
        pnlpct = f" ({tr['pnl_pct']:+.2f} %)" if tr["pnl_pct"] is not None else ""
        out.append(f"- **{tr['symbol']}** · {tr['side']} · entry ${tr['entry_price']} ({et_dk} dansk / {et_et} ET) "
                   f"{exit_part} · P&L **${pnl:+.2f}**{pnlpct} · {tr['exit_reason'] or '—'}{durtxt} · [{tr['source']}]")
    if trades:
        wr = f" · win rate {wins/(wins+losses)*100:.1f} %" if (wins + losses) else ""
        out.append("")
        out.append(f"**TOTAL: ${total:+.2f}** · wins {wins} · losses {losses}{wr}")
    else:
        out.append("_(ingen handler)_")
    out.append("")

    # 4. Trade forensics — kort oversigt
    out.append("## 4. Trade forensics (Lag C)")
    forensics_ev = by_type.get("trade_forensics", [])
    if forensics_ev:
        note = "fuldt per-handel-dump nedenfor." if forensics else "koer med --forensics for fuldt dump."
        out.append(f"{len(forensics_ev)} forensics-events — {note}")
        for ev in forensics_ev[:5]:
            d = p(ev)
            out.append(f"- {t(ev['ts_local'])} · **{ev['symbol']}** {d.get('phase','?')}")
        if len(forensics_ev) > 5:
            out.append(f"- ... og {len(forensics_ev)-5} mere")
    else:
        out.append("_(ingen forensics)_")
    out.append("")

    # 5. Afvisninger
    out.append("## 5. Entry-afvisninger (Lag B)")
    rejects = by_type.get("entry_rejected", [])
    if rejects:
        agg = defaultdict(int)
        for ev in rejects:
            d = p(ev)
            sym = ev["symbol"] or d.get("ticker", "?")
            reason = d.get("detail") or d.get("reason") or "?"
            agg[(sym, reason[:60])] += 1
        out.append(f"{len(rejects)} afvisninger paa {len(agg)} unikke kombinationer:")
        for (sym, reason), n in sorted(agg.items(), key=lambda x: -x[1])[:20]:
            out.append(f"- **{sym}** ×{n} · {reason}")
        if len(agg) > 20:
            out.append(f"- ... og {len(agg)-20} flere kombinationer")
    else:
        out.append("_(ingen afvisninger)_")
    out.append("")

    # 6. Ordrer
    out.append("## 6. Ordrer")
    errors = by_type.get("ibkr_order_error", [])
    out.append(f"Godkendte: {len(by_type.get('order_approved', []))} · "
               f"afviste: {len(by_type.get('order_rejected', []))} · "
               f"placeret hos IBKR: {len(by_type.get('ibkr_order_placed', []))} · "
               f"IBKR-fejl: {len(errors)}")
    for ev in errors:
        d = p(ev)
        msg = d.get("error") or d.get("message", "?")
        out.append(f"- **FEJL** {t(ev['ts_local'])} · {ev['symbol'] or '?'}: {msg[:100]}")
    out.append("")

    # 7. System-events
    out.append("## 7. System-events")
    sys_events = (by_type.get("emergency_stop", []) + by_type.get("daily_limit_reached", [])
                  + by_type.get("ibkr_connect_attempt", []))
    if sys_events:
        for ev in sys_events:
            d = p(ev)
            msg = d.get("message") or d.get("reason") or json.dumps(d)[:80]
            out.append(f"- {t(ev['ts_local'])} · **{ev['event_type']}** {msg}")
    else:
        out.append("_(ingen system-events)_")
    out.append("")

    # 8. Diagnostik
    out.append("## 8. Diagnostik (Lag C)")
    diags = by_type.get("daily_diagnostics", [])
    if diags:
        for ev in diags:
            d = p(ev)
            out.append(f"- {t(ev['ts_local'])} · **{ev['source']}** (stop: {d.get('shutdown_reason','?')})")
            out.append(f"  - Univers: {d.get('universe_size',0)} aktier")
            out.append(f"  - Evalueringer: {d.get('evaluations',0)} · scorede bars: {d.get('scored_bars',0)}")
            out.append(f"  - Entries: {d.get('entries',0)} · handler: {d.get('trades',0)}")
            peak = d.get("peak_score")
            if peak is not None:
                # Score = kontekst-hits over E/K/R/T → maks 4 (V/B/G er obligatoriske
                # impuls-brikker og tæller ikke med). Bricks-strengen er 7 tegn, men
                # skalaen er 4.
                out.append(f"  - Peak score: {peak}/4")
            mbc = d.get("missing_by_condition")
            if mbc:
                out.append("  - Pr. betingelse (antal bars hvor den manglede):")
                for cond, n in mbc.items():
                    out.append(f"    - {cond}: {n}")
            if d.get("universe_size", 0) > 0 and d.get("evaluations", 0) == 0:
                out.append("  - ⚠ **ADVARSEL:** univers men 0 evalueringer -> mistanke om bar-feed-problem.")
    else:
        out.append("_(ingen diagnostik)_")
    out.append("")

    # 9. Heartbeat
    out.append("## 9. Heartbeat (periodiske snapshots)")
    beats = by_type.get("diagnostics_heartbeat", [])
    if beats:
        out.append(f"{len(beats)} heartbeats. Seneste 8:")
        for ev in beats[-8:]:
            d = p(ev)
            out.append(f"- {t(ev['ts_local'])} · evals={d.get('evaluations',0)} "
                       f"scored={d.get('scored_bars',0)} entries={d.get('entries',0)} "
                       f"pos={d.get('open_positions','?')}")
    else:
        out.append("_(ingen heartbeats)_")
    return out


# ─────────────────────────────────────────────────────────────────
# Dyb per-handel-forensik (--forensics)
# ─────────────────────────────────────────────────────────────────
def _g(d, *path, default=None):
    """Sikker nested-get: _g(snap, 'indicators', 'rsi_14')."""
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _forensics_md(con, trades):
    """Bygger det dybe per-handel-dump som markdown — ALLE felter bevaret 1:1."""
    out = ["## Dyb per-handel-forensik"]
    if not trades:
        out.append("_(ingen handler at analysere)_")
        return out

    for i, tr in enumerate(trades, 1):
        entry, exit_ = match_snapshots(con, tr)
        pnl = tr["pnl"] if tr["pnl"] is not None else 0.0
        verdict = "VINDER" if pnl > 0 else ("TABER" if pnl < 0 else "FLAD")
        variant = f" · {tr['variant']}" if tr["variant"] else ""
        out.append("")
        out.append(f"### {tr['symbol']} — {tr['side']} ({i}/{len(trades)})")
        out.append(f"- **{verdict}** · P&L **${pnl:+.2f}**"
                   + (f" ({tr['pnl_pct']:+.2f} %)" if tr["pnl_pct"] is not None else "")
                   + f" · exit: {tr['exit_reason'] or '—'} · [{tr['source']}{variant}]")

        # Tider — dansk FOERST, ET i parentes
        out.append(f"- Entry: {dansk(tr['entry_time_utc']) or '—'} dansk ({t(tr['entry_time_et'])} ET) "
                   f"@ ${tr['entry_price']} · {tr['side']} {tr['shares']} stk")
        if tr["exit_time_et"]:
            dur = tr["duration_sec"]
            durtxt = f"{dur//60}m{dur%60}s" if dur is not None else "—"
            out.append(f"- Exit: {dansk(tr['exit_time_utc']) or '—'} dansk ({t(tr['exit_time_et'])} ET) "
                       f"@ ${tr['exit_price']} · hold-tid {durtxt}")
        else:
            out.append("- Exit: **ÅBEN**")

        # Risiko / strategi-payload
        risk_bits = []
        if tr["capital_used"] is not None:
            risk_bits.append(f"kapital ${tr['capital_used']:.0f}")
        if tr["current_stop"] is not None:
            risk_bits.append(f"stop ${tr['current_stop']:.2f}")
        if tr["current_target"] is not None:
            risk_bits.append(f"target ${tr['current_target']:.2f}")
        if risk_bits:
            out.append("- Risiko: " + " · ".join(risk_bits))
        tp = p(tr)
        if tp:
            out.append("- Strategi-data: " + " · ".join(f"{k}={v}" for k, v in tp.items()))

        # Entry-snapshot — strategi-bevidst (K2 / Europa-reversion / BuyTheDip)
        if entry:
            out.append("- **Entry-snapshot**")
            _setup = entry.get("setup")                       # Konfluens 2 — KUN hvis reel
            if isinstance(_setup, dict) and (_setup.get("entry_score") is not None
                                             or _setup.get("entry_bricks") is not None):
                out.append(f"  - [K2] score/bricks {_g(entry,'setup','entry_score', default='—')} / "
                           f"{_g(entry,'setup','entry_bricks', default='—')}")
                out.append(f"  - rel.vol {_g(entry,'setup','rel_vol_last_bar', default='—')} · "
                           f"ATR {_g(entry,'setup','atr', default='—')} · "
                           f"risk/aktie {_g(entry,'setup','risk_per_share', default='—')} · "
                           f"init-stop {_g(entry,'setup','initial_stop', default='—')}")
            if isinstance(entry.get("reversion"), dict):      # Europa-reversion
                out.append(f"  - [EUREV] z {_g(entry,'reversion','entry_z', default='—')} · "
                           f"mean {_g(entry,'reversion','mean', default='—')} · "
                           f"std {_g(entry,'reversion','std', default='—')}")
                out.append(f"  - baand {_g(entry,'reversion','lower_band', default='—')}.."
                           f"{_g(entry,'reversion','upper_band', default='—')} · "
                           f"stop {_g(entry,'reversion','stop_price', default='—')} "
                           f"({_g(entry,'reversion','stop_distance_pts', default='—')} pt) · "
                           f"{_g(entry,'reversion','contracts', default='—')} kontrakt(er)")
            if isinstance(entry.get("buythedip"), dict):      # BuyTheDip
                out.append(f"  - [BTD] dip-dybde {_g(entry,'buythedip','dip_depth', default='—')}% · "
                           f"ref-high {_g(entry,'buythedip','ref_high', default='—')} · "
                           f"dip-low/stop {_g(entry,'buythedip','dip_low', default='—')} · "
                           f"target {_g(entry,'buythedip','target', default='—')}")
            # Indikatorer (alle strategier)
            out.append(f"  - RSI14 {_g(entry,'indicators','rsi_14', default='—')} · "
                       f"MACD {_g(entry,'indicators','macd', default='—')}/{_g(entry,'indicators','macd_signal', default='—')} · "
                       f"VWAP-dist {_g(entry,'indicators','vwap_distance_pct', default='—')}%")
            # Tape (kun hvis faktisk opsamlet — tom for futures/BTD i paper)
            if _g(entry, "tape", "trade_count") is not None:
                out.append(f"  - Tape: aggressor {_g(entry,'tape','aggressor_ratio', default='—')} · "
                           f"{_g(entry,'tape','trade_count', default='—')} trades · "
                           f"stoerste {_g(entry,'tape','largest_trade_size', default='—')} "
                           f"({_g(entry,'tape','largest_trade_direction', default='—')})")
        else:
            out.append("- _Entry-snapshot: ingen (ikke bygget for denne strategi/handel)_")

        # Exit-snapshot — strategi-bevidst
        if exit_:
            out.append("- **Exit-snapshot**")
            out.append(f"  - MFE {_g(exit_,'trade_metrics','max_favorable_excursion', default='—')} · "
                       f"MAE {_g(exit_,'trade_metrics','max_adverse_excursion', default='—')} · "
                       f"bars {_g(exit_,'trade_metrics','duration_bars', default='—')}")
            if isinstance(exit_.get("reversion"), dict):      # Europa-reversion
                out.append(f"  - [EUREV] exit-z {_g(exit_,'reversion','exit_z', default='—')} (≈0 = vendt til middel)")
            if isinstance(exit_.get("buythedip"), dict):      # BuyTheDip
                out.append(f"  - [BTD] exit-grund {_g(exit_,'buythedip','reason', default='—')}")
            out.append(f"  - RSI14 {_g(exit_,'indicators','rsi_14', default='—')} · "
                       f"MACD-hist {_g(exit_,'indicators','macd_hist', default='—')} · "
                       f"VWAP-dist {_g(exit_,'indicators','vwap_distance_pct', default='—')}%")
        elif tr["exit_time_et"]:
            out.append("- _Exit-snapshot: ingen_")
    return out


# ─────────────────────────────────────────────────────────────────
# Markdown-rapport — EN kilde for baade terminal og vindue
# ─────────────────────────────────────────────────────────────────
def build_report_md(con, d_from, d_to, strategy_filter=None, forensics=True) -> str:
    """Hele dagens-log-rapporten som markdown (overskrifter + punkter, ingen
    monospace). Renderer paent i vinduet OG limes direkte ind i Claude."""
    rows = load_events(con, d_from, d_to, strategy_filter)
    trades = load_trades(con, d_from, d_to, strategy_filter)
    label = d_from if d_from == d_to else f"{d_from} -> {d_to}"

    if not rows and not trades:
        return f"_Ingen handler eller events fundet for {label}._"

    nu = datetime.now().strftime("%Y-%m-%d %H:%M")
    machine = rows[0]["instance_id"] if rows else "—"
    ctx_strat = strategy_filter or "Alle strategier"
    out = [
        f"# Dagens log — {label}",
        f"{ctx_strat} · {len(trades)} handler · {len(rows)} events · maskine {machine} · genereret {nu}",
        "",
    ]
    out += _overview_md(rows, trades, strategy_filter, forensics)
    if forensics:
        out.append("")
        out += _forensics_md(con, trades)
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="Dagens journal-overblik + per-handel-forensik (read-only)")
    ap.add_argument("args", nargs="*", help="valgfrit: strateginavn og/eller dato (YYYY-MM-DD)")
    ap.add_argument("--forensics", action="store_true", help="dyb per-handel-forensik i terminalen")
    ns = ap.parse_args()

    strategy_filter = None
    dates = []
    for a in ns.args:
        if len(a) == 10 and a.count("-") == 2:
            dates.append(a)
        else:
            strategy_filter = a
    today = date.today().isoformat()
    if not dates:
        d_from = d_to = today
    elif len(dates) == 1:
        d_from = d_to = dates[0]
    else:
        d_from, d_to = sorted(dates[:2])

    con = ro_connect(DB_PATH)
    try:
        print(build_report_md(con, d_from, d_to, strategy_filter, ns.forensics))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())