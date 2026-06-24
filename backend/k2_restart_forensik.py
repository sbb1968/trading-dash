#!/usr/bin/env python3
"""k2_restart_forensik.py — afdaek om en backend-genstart midt i sessionen draebte K2
og efterlod aabne positioner uden force-close (READ-ONLY).

Viser, for en given dag:
  1. BACKEND-GENSTARTER  (event_type='system_startup') med ET-tid
  2. K2's LIFECYCLE       (alle non-bar_evaluation K2-events: start/status/handler/stop)
  3. K2's SIDSTE AKTIVITET (sidste bar_evaluation) vs force-close 15:45 ET
  4. K2-events EFTER seneste genstart (0 = K2 kom aldrig tilbage)
  5. AABNE K2-positioner   (trades.status='open') = de forældreloese

Strengt read-only (sqlite mode=ro) — sikkert ved siden af koerende K2.
    python k2_restart_forensik.py
    python k2_restart_forensik.py --day 2026-06-23
"""
from __future__ import annotations
import argparse, sqlite3, sys
from datetime import date, datetime, timezone, time as dtime
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
try:
    import pytz
    ET = pytz.timezone("America/New_York")
except Exception:
    ET = None

FORCE_CLOSE_ET = dtime(15, 45)   # K2 force_close_hhmm default


def et_dt(ts_utc):
    if ts_utc and ET is not None:
        try:
            dt = datetime.fromisoformat(str(ts_utc).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(ET)
        except ValueError:
            pass
    return None


def hm(ts_utc, ts_local=None):
    dt = et_dt(ts_utc)
    return dt.strftime("%H:%M:%S ET") if dt else (str(ts_local or ts_utc) or "")[:19]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="trading_dash.db")
    ap.add_argument("--source", default="Konfluens 2")
    ap.add_argument("--day", default=None)
    a = ap.parse_args()
    day = a.day or date.today().isoformat()
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row

    def q(sql, params=()):
        try:
            return con.execute(sql, params).fetchall()
        except sqlite3.Error as e:
            print(f"   (query-fejl: {e})")
            return []

    DF = "substr(ts_utc,1,10)=?"   # dag-filter paa UTC-dato (= ET-dato i dagtimer)

    print("=" * 80)
    print(f"  K2 GENSTARTS-FORENSIK · source='{a.source}' · dag {day} · READ-ONLY")
    print(f"  Hypotese: backend-genstart midt i sessionen draebte K2 -> ingen 15:45-luk")
    print("=" * 80)

    # 1) Backend-genstarter
    print("\n── 1) BACKEND-GENSTARTER (event_type='system_startup') ──")
    sr = q(f"SELECT ts_utc, ts_local FROM events WHERE event_type='system_startup' "
           f"AND {DF} ORDER BY ts_utc", (day,))
    last_restart = None
    if sr:
        for r in sr:
            dt = et_dt(r["ts_utc"])
            mid = ""
            if dt and dtime(9, 30) <= dt.time() <= dtime(16, 0):
                mid = "   <<< MIDT I SESSIONEN (09:30-16:00 ET) — rygende bevis"
            print(f"   {hm(r['ts_utc'], r['ts_local'])}{mid}")
            last_restart = r["ts_utc"]
    else:
        print("   (ingen — backenden blev IKKE genstartet i dag)")

    # 2) K2 lifecycle (alt undtagen bar_evaluation) — kollaps gentaget status
    print(f"\n── 2) K2 LIFECYCLE (status-skift + handler; heartbeat kollapset) ──")
    lc = q(f"SELECT ts_utc, ts_local, event_type, symbol, payload_json "
           f"FROM events WHERE source=? AND event_type!='bar_evaluation' AND {DF} "
           f"ORDER BY ts_utc", (a.source, day))

    def _status_val(et, pj):
        if et == "status" and pj:
            try:
                import json
                return json.loads(pj).get("status", "")
            except Exception:
                return ""
        return ""

    prev_key = None
    grp_first = None
    grp_last = None
    grp_n = 0

    def _flush():
        if grp_key is None:
            return
        et, sval = grp_key
        label = f"{et}:{sval}" if sval else et
        rng = f"  (×{grp_n}, frem til {hm(grp_last)})" if grp_n > 1 else ""
        snip = f"  {grp_snip[:60]}" if (et != "status" and grp_snip) else ""
        print(f"   {hm(grp_first)}  {label:<28}{rng}{snip}")

    grp_key = None
    grp_snip = ""
    for r in lc:
        sval = _status_val(r["event_type"], r["payload_json"])
        key = (r["event_type"], sval)
        if key == grp_key:
            grp_last = r["ts_utc"]; grp_n += 1; continue
        _flush()
        grp_key = key; grp_first = r["ts_utc"]; grp_last = r["ts_utc"]; grp_n = 1
        grp_snip = (r["payload_json"] or "")[:60]
    _flush()
    if not lc:
        print("   (ingen)")

    # 3) Sidste bar_evaluation = hvornaar K2 sidst var i live
    print("\n── 3) K2 SIDSTE AKTIVITET vs FORCE-CLOSE 15:45 ET ──")
    be = q(f"SELECT MIN(ts_utc) lo, MAX(ts_utc) hi, COUNT(*) n FROM events "
           f"WHERE source=? AND event_type='bar_evaluation' AND {DF}", (a.source, day))
    if be and be[0]["n"]:
        lo, hi, n = be[0]["lo"], be[0]["hi"], be[0]["n"]
        print(f"   bar_evaluation: {n} stk · foerste {hm(lo)} · SIDSTE {hm(hi)}")
        last_dt = et_dt(hi)
        if last_dt and last_dt.time() < FORCE_CLOSE_ET:
            print(f"   -> K2 gik TAVS kl. {last_dt:%H:%M ET} — FOER force-close 15:45 ET."
                  f"  Den naaede aldrig sin luk.")
        else:
            print(f"   -> K2 var aktiv frem til/forbi 15:45 ET.")
    else:
        print("   (ingen bar_evaluation i dag)")

    # 4) K2-events efter seneste genstart
    if last_restart:
        print("\n── 4) K2-EVENTS EFTER SENESTE GENSTART ──")
        af = q("SELECT COUNT(*) n, MAX(ts_utc) hi FROM events WHERE source=? AND ts_utc>?",
               (a.source, last_restart))
        n = af[0]["n"] if af else 0
        print(f"   Genstart {hm(last_restart)} -> {n} K2-events derefter"
              + (f" (sidste {hm(af[0]['hi'])})" if n else " = K2 KOM ALDRIG TILBAGE (bekraefter hypotesen)"))

    # 5) Aabne K2-positioner (de foraeldreloese) — aaben = exit_time_et IS NULL
    print("\n── 5) AABNE K2-POSITIONER (exit_time_et IS NULL) ──")
    op = q("SELECT symbol, side, shares, entry_time_et, entry_price FROM trades "
           "WHERE source=? AND (exit_time_et IS NULL OR exit_time_et='') "
           "ORDER BY entry_time_et", (a.source,))
    if op:
        for r in op:
            ep = f" @ {r['entry_price']}" if r["entry_price"] is not None else ""
            print(f"   {r['symbol']:<7} {r['side']:<6} {r['shares']} stk{ep} · entry {r['entry_time_et']}")
        print(f"   = {len(op)} foraeldreloese aabne K2-positioner (aldrig lukket).")
    else:
        print("   (ingen aabne K2-positioner — alt lukket)")

    print("\n" + "=" * 80)
    print("  KONKLUSION: en 'system_startup' midt i sessionen (1) + K2 tavs foer 15:45 (3)")
    print("  + 0 K2-events efter genstart (4) + aabne positioner (5) = genstarten draebte K2.")
    print("=" * 80)


if __name__ == "__main__":
    main()
