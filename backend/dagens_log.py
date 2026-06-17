"""
dagens_log.py — fuldt overblik over dagens journal-events + dyb per-handel-forensik.

Brug:
    python dagens_log.py                       # alle strategier, i dag
    python dagens_log.py Konfluens             # filtrer på én strategi
    python dagens_log.py Konfluens 2026-05-27  # specifik dato
    python dagens_log.py --forensics           # + dyb per-handel-forensik i terminalen
    python dagens_log.py 2026-06-12 --forensics

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


def section(title: str) -> None:
    print(f"\n{'='*72}\n  {title}\n{'='*72}")


# ─────────────────────────────────────────────────────────────────
# Indlæsning
# ─────────────────────────────────────────────────────────────────
def load_events(con, date_filter, strategy_filter):
    where = ["ts_local LIKE ?"]
    params = [f"{date_filter}%"]
    if strategy_filter:
        where.append("source LIKE ?")
        params.append(f"%{strategy_filter}%")
    sql = (f"SELECT id, ts_local, instance_id, source, event_type, symbol, payload_json "
           f"FROM events WHERE {' AND '.join(where)} ORDER BY id ASC")
    return con.execute(sql, params).fetchall()


def load_trades(con, date_filter, strategy_filter):
    """Dagens handler fra den kanoniske trades-tabel (entry-dag = handelsdag)."""
    where = ["entry_time_et LIKE ?"]
    params = [f"{date_filter}%"]
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
def print_overview(rows, trades, date_filter, strategy_filter):
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["event_type"]].append(r)

    section(f"DAGENS LOG — {date_filter}"
            + (f" — {strategy_filter}" if strategy_filter else ""))
    print(f"Maskine: {rows[0]['instance_id']}   Events i alt: {len(rows)}")

    # 1. Livscyklus
    section("1. STRATEGI-LIVSCYKLUS")
    lifecycle = (by_type.get("strategy_started", []) + by_type.get("strategy_stopped", [])
                 + by_type.get("strategy_emergency_stop", []))
    for ev in lifecycle:
        kind = ev["event_type"].replace("strategy_", "").upper()
        print(f"  {t(ev['ts_local'])}  {ev['source']:<15} {kind}")
    if not lifecycle:
        print("  (ingen livscyklus-events)")

    # 2. Univers
    section("2. UNIVERS (Lag A)")
    for ev in by_type.get("universe_selected", []):
        d = p(ev)
        tickers = d.get("tickers", [])
        print(f"  {t(ev['ts_local'])}  {ev['source']}: {len(tickers)} af {d.get('raw_count','?')} rå")
        print(f"     {', '.join(tickers)}")
    if not by_type.get("universe_selected"):
        print("  (ingen universe-events)")

    # 3. Handler — fra trades-tabellen (kanonisk)
    section("3. HANDLER")
    closed = [tr for tr in trades if tr["exit_time_et"]]
    open_only = [tr for tr in trades if not tr["exit_time_et"]]
    print(f"  Handler i alt: {len(trades)}   Lukkede: {len(closed)}   Åbne: {len(open_only)}")
    total = wins = losses = 0
    for tr in trades:
        pnl = tr["pnl"] if tr["pnl"] is not None else 0.0
        total += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        et = t(tr["entry_time_et"])
        xt = t(tr["exit_time_et"]) if tr["exit_time_et"] else "ÅBEN"
        print(f"  {et}→{xt}  {tr['symbol']:<6} {tr['side']:<5} "
              f"P&L ${pnl:>+8.2f}  ({tr['exit_reason'] or '—'})  [{tr['source']}]")
    if trades:
        wr = f"   Win rate: {wins/(wins+losses)*100:.1f}%" if (wins + losses) else ""
        print(f"\n  TOTAL: ${total:+.2f}   Wins: {wins}   Losses: {losses}{wr}")
    else:
        print("  (ingen handler)")

    # 4. Trade forensics — kort oversigt
    section("4. TRADE FORENSICS (Lag C)")
    forensics = by_type.get("trade_forensics", [])
    if forensics:
        print(f"  {len(forensics)} forensics-events. Fuld per-handel-dump:")
        print(f"     python dagens_log.py {strategy_filter or ''} {date_filter} --forensics".replace("  ", " "))
        for ev in forensics[:5]:
            d = p(ev)
            phase = d.get("phase", "?")
            print(f"  {t(ev['ts_local'])}  {ev['symbol']:<6} {phase}")
        if len(forensics) > 5:
            print(f"  ... og {len(forensics)-5} mere")
    else:
        print("  (ingen forensics)")

    # 5. Afvisninger
    section("5. ENTRY-AFVISNINGER (Lag B)")
    rejects = by_type.get("entry_rejected", [])
    if rejects:
        agg = defaultdict(int)
        for ev in rejects:
            d = p(ev)
            sym = ev["symbol"] or d.get("ticker", "?")
            reason = d.get("detail") or d.get("reason") or "?"
            agg[(sym, reason[:60])] += 1
        print(f"  {len(rejects)} afvisninger på {len(agg)} unikke kombinationer:")
        for (sym, reason), n in sorted(agg.items(), key=lambda x: -x[1])[:20]:
            print(f"    {sym:<6} ×{n:<4} {reason}")
        if len(agg) > 20:
            print(f"    ... og {len(agg)-20} flere kombinationer")
    else:
        print("  (ingen afvisninger)")

    # 6. Ordrer
    section("6. ORDRER")
    errors = by_type.get("ibkr_order_error", [])
    print(f"  Godkendte: {len(by_type.get('order_approved', []))}   "
          f"Afviste: {len(by_type.get('order_rejected', []))}   "
          f"Placeret hos IBKR: {len(by_type.get('ibkr_order_placed', []))}   "
          f"IBKR-fejl: {len(errors)}")
    for ev in errors:
        d = p(ev)
        msg = d.get("error") or d.get("message", "?")
        print(f"    FEJL  {t(ev['ts_local'])}  {ev['symbol'] or '?'}: {msg[:100]}")

    # 7. System-events
    section("7. SYSTEM-EVENTS")
    sys_events = (by_type.get("emergency_stop", []) + by_type.get("daily_limit_reached", [])
                  + by_type.get("ibkr_connect_attempt", []))
    if sys_events:
        for ev in sys_events:
            d = p(ev)
            msg = d.get("message") or d.get("reason") or json.dumps(d)[:80]
            print(f"  {t(ev['ts_local'])}  {ev['event_type']:<25} {msg}")
    else:
        print("  (ingen system-events)")

    # 8. Diagnostik
    section("8. DIAGNOSTIK (Lag C)")
    diags = by_type.get("daily_diagnostics", [])
    if diags:
        for ev in diags:
            d = p(ev)
            print(f"  {t(ev['ts_local'])}  {ev['source']}  (stop: {d.get('shutdown_reason','?')})")
            print(f"     Univers: {d.get('universe_size',0)} aktier")
            print(f"     Evalueringer: {d.get('evaluations',0)}   Scorede bars: {d.get('scored_bars',0)}")
            print(f"     Entries: {d.get('entries',0)}   Handler: {d.get('trades',0)}")
            peak = d.get("peak_score")
            if peak is not None:
                print(f"     Peak score: {peak}/6")
            mbc = d.get("missing_by_condition")
            if mbc:
                print("     Pr. betingelse (antal bars hvor den manglede):")
                for cond, n in mbc.items():
                    print(f"        {cond:<14} {n}")
            if d.get("universe_size", 0) > 0 and d.get("evaluations", 0) == 0:
                print(f"     ⚠ ADVARSEL: univers men 0 evalueringer → mistanke om bar-feed-problem.")
    else:
        print("  (ingen diagnostik)")

    # 9. Heartbeat
    section("9. HEARTBEAT (periodiske snapshots)")
    beats = by_type.get("diagnostics_heartbeat", [])
    if beats:
        print(f"  {len(beats)} heartbeats. Seneste 8:")
        for ev in beats[-8:]:
            d = p(ev)
            print(f"  {t(ev['ts_local'])}  evals={d.get('evaluations',0):<5} "
                  f"scored={d.get('scored_bars',0):<5} entries={d.get('entries',0):<3} "
                  f"pos={d.get('open_positions','?')}")
    else:
        print("  (ingen heartbeats)")


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


def print_forensics(con, trades):
    section("DYB PER-HANDEL-FORENSIK")
    if not trades:
        print("  (ingen handler at analysere)")
        return

    for i, tr in enumerate(trades, 1):
        entry, exit_ = match_snapshots(con, tr)
        pnl = tr["pnl"] if tr["pnl"] is not None else 0.0
        verdict = "VINDER" if pnl > 0 else ("TABER" if pnl < 0 else "FLAD")
        print(f"\n{'─'*72}")
        print(f"  HANDEL {i}/{len(trades)}  {tr['symbol']}  [{tr['source']}"
              + (f" · {tr['variant']}" if tr['variant'] else "") + "]")
        print(f"{'─'*72}")
        print(f"  {verdict}   P&L ${pnl:+.2f}"
              + (f"  ({tr['pnl_pct']:+.2f}%)" if tr['pnl_pct'] is not None else "")
              + f"   exit: {tr['exit_reason'] or '—'}")

        # Tider
        print(f"  Entry: {t(tr['entry_time_et'])} ET / {dansk(tr['entry_time_utc']) or '—'} dansk"
              f"   @ ${tr['entry_price']}   {tr['side']} {tr['shares']} stk")
        if tr["exit_time_et"]:
            dur = tr["duration_sec"]
            durtxt = f"{dur//60}m{dur%60}s" if dur is not None else "—"
            print(f"  Exit:  {t(tr['exit_time_et'])} ET / {dansk(tr['exit_time_utc']) or '—'} dansk"
                  f"   @ ${tr['exit_price']}   hold-tid {durtxt}")
        else:
            print("  Exit:  ÅBEN")

        # Risiko / strategi-payload
        risk_bits = []
        if tr["capital_used"] is not None:
            risk_bits.append(f"kapital ${tr['capital_used']:.0f}")
        if tr["current_stop"] is not None:
            risk_bits.append(f"stop ${tr['current_stop']:.2f}")
        if tr["current_target"] is not None:
            risk_bits.append(f"target ${tr['current_target']:.2f}")
        if risk_bits:
            print("  Risiko: " + "   ".join(risk_bits))
        tp = p(tr)
        if tp:
            print("  Strategi-data: " + "   ".join(f"{k}={v}" for k, v in tp.items()))

        # Entry-snapshot — strategi-bevidst (K2 / Europa-reversion / BuyTheDip)
        if entry:
            print("  ── Entry-snapshot ──")
            _setup = entry.get("setup")                       # Konfluens 2 — KUN hvis reel
            if isinstance(_setup, dict) and (_setup.get("entry_score") is not None     # score/bricks (den
                                             or _setup.get("entry_bricks") is not None):  # delte builder laver
                print(f"     [K2] score/bricks {_g(entry,'setup','entry_score', default='—')} / "  # altid en tom
                      f"{_g(entry,'setup','entry_bricks', default='—')}")                            # 'setup' for alle)
                print(f"          rel.vol {_g(entry,'setup','rel_vol_last_bar', default='—')}   "
                      f"ATR {_g(entry,'setup','atr', default='—')}   "
                      f"risk/aktie {_g(entry,'setup','risk_per_share', default='—')}   "
                      f"init-stop {_g(entry,'setup','initial_stop', default='—')}")
            if isinstance(entry.get("reversion"), dict):      # Europa-reversion
                print(f"     [EUREV] z {_g(entry,'reversion','entry_z', default='—')}   "
                      f"mean {_g(entry,'reversion','mean', default='—')}   "
                      f"std {_g(entry,'reversion','std', default='—')}")
                print(f"            bånd {_g(entry,'reversion','lower_band', default='—')}.."
                      f"{_g(entry,'reversion','upper_band', default='—')}   "
                      f"stop {_g(entry,'reversion','stop_price', default='—')} "
                      f"({_g(entry,'reversion','stop_distance_pts', default='—')} pt)   "
                      f"{_g(entry,'reversion','contracts', default='—')} kontrakt(er)")
            if isinstance(entry.get("buythedip"), dict):      # BuyTheDip
                print(f"     [BTD] dip-dybde {_g(entry,'buythedip','dip_depth', default='—')}%   "
                      f"ref-high {_g(entry,'buythedip','ref_high', default='—')}   "
                      f"dip-low/stop {_g(entry,'buythedip','dip_low', default='—')}   "
                      f"target {_g(entry,'buythedip','target', default='—')}")
            # Indikatorer (alle strategier)
            print(f"     RSI14 {_g(entry,'indicators','rsi_14', default='—')}   "
                  f"MACD {_g(entry,'indicators','macd', default='—')}/{_g(entry,'indicators','macd_signal', default='—')}   "
                  f"VWAP-dist {_g(entry,'indicators','vwap_distance_pct', default='—')}%")
            # Tape (kun hvis faktisk opsamlet — tom for futures/BTD i paper)
            if _g(entry,'tape','trade_count') is not None:
                print(f"     Tape: aggressor {_g(entry,'tape','aggressor_ratio', default='—')}   "
                      f"{_g(entry,'tape','trade_count', default='—')} trades   "
                      f"største {_g(entry,'tape','largest_trade_size', default='—')} "
                      f"({_g(entry,'tape','largest_trade_direction', default='—')})")
        else:
            print("  ── Entry-snapshot: ingen (ikke bygget for denne strategi/handel) ──")

        # Exit-snapshot — strategi-bevidst
        if exit_:
            print("  ── Exit-snapshot ──")
            print(f"     MFE {_g(exit_,'trade_metrics','max_favorable_excursion', default='—')}   "
                  f"MAE {_g(exit_,'trade_metrics','max_adverse_excursion', default='—')}   "
                  f"bars {_g(exit_,'trade_metrics','duration_bars', default='—')}")
            if isinstance(exit_.get("reversion"), dict):      # Europa-reversion
                print(f"     [EUREV] exit-z {_g(exit_,'reversion','exit_z', default='—')} "
                      f"(≈0 = vendt til middel)")
            if isinstance(exit_.get("buythedip"), dict):      # BuyTheDip
                print(f"     [BTD] exit-grund {_g(exit_,'buythedip','reason', default='—')}")
            print(f"     RSI14 {_g(exit_,'indicators','rsi_14', default='—')}   "
                  f"MACD-hist {_g(exit_,'indicators','macd_hist', default='—')}   "
                  f"VWAP-dist {_g(exit_,'indicators','vwap_distance_pct', default='—')}%")
        elif tr["exit_time_et"]:
            print("  ── Exit-snapshot: ingen ──")


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
    date_filter = date.today().isoformat()
    for a in ns.args:
        if len(a) == 10 and a.count("-") == 2:
            date_filter = a
        else:
            strategy_filter = a

    con = ro_connect(DB_PATH)
    events = load_events(con, date_filter, strategy_filter)
    trades = load_trades(con, date_filter, strategy_filter)

    if not events and not trades:
        print(f"Ingen events eller handler fundet for {date_filter}"
              + (f" / {strategy_filter}" if strategy_filter else ""))
        return 0

    if events:
        print_overview(events, trades, date_filter, strategy_filter)
    else:
        section(f"DAGENS LOG — {date_filter}")
        print("  (ingen events — kun trades-rækker fundet)")

    if ns.forensics:
        print_forensics(con, trades)

    return 0


if __name__ == "__main__":
    sys.exit(main())