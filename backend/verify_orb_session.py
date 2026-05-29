"""
verify_orb_session.py
──────────────────────
Efter-session verifikation af ORB's diagnostik-logging.

Formål: gøre STILLE fejl SYNLIGE. ORB's diagnostik-logging er designet til
at fejle stille (try/except uden re-raise), så hvis den ikke virker, er
symptomet FRAVÆR af events — ikke en fejlmeddelelse. Dette script vender
det til et konkret svar: "diagnostikken virkede" eller "ORB kørte men
loggede ikke — tjek backend-loggen".

Kør EFTER ORB's session (fx efter lukketid):
    python verify_orb_session.py
    python verify_orb_session.py --date 2026-05-27   # specifik dag
    python verify_orb_session.py --source MomentumORB # specifik strategi

Læser kun (SELECT) — ændrer INTET i databasen.
"""
import sqlite3
import sys
import json
from datetime import datetime

DB_PATH = r"C:\Projects\trading_dash\backend\trading_dash.db"

# Diagnostik-event-typerne vi forventer fra en kørende strategi
DIAG_TYPES = ["universe_selected", "entry_rejected", "daily_diagnostics"]


def parse_args():
    date = datetime.now().strftime("%Y-%m-%d")
    source = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--date" and i + 1 < len(args):
            date = args[i + 1]; i += 2
        elif args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]; i += 2
        else:
            i += 1
    return date, source


def q(c, sql, params=()):
    return c.execute(sql, params).fetchall()


def main():
    date, source_filter = parse_args()
    print("=" * 66)
    print(f"  ORB SESSION-VERIFIKATION — dato: {date}")
    if source_filter:
        print(f"  (filtreret til source = {source_filter})")
    print("=" * 66)

    try:
        c = sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"❌ Kunne ikke åbne databasen: {e}")
        return 1

    like = f"{date}%"
    src_clause = " AND source = ?" if source_filter else ""
    src_param = (source_filter,) if source_filter else ()

    # ── 1. Kørte en strategi overhovedet i dag? ──────────────
    # Vi ser efter status-events (start/stop/done) som tegn på aktivitet.
    print("\n[1] Kørte strategien i dag?")
    status_events = q(c,
        f"SELECT source, COUNT(*) FROM events "
        f"WHERE event_type='status' AND ts_utc LIKE ?{src_clause} "
        f"GROUP BY source", (like, *src_param))
    if status_events:
        for src, n in status_events:
            print(f"    {src}: {n} status-events  → strategien var aktiv")
        strategy_ran = True
    else:
        print("    ⚠ Ingen status-events i dag — strategien lader ikke til at have kørt.")
        print("      (Hvis ORB ikke startede, er der intet at diagnosticere.)")
        strategy_ran = False

    # ── 2. Kom der diagnostik-events? ────────────────────────
    print("\n[2] Diagnostik-events (Lag A/B/C):")
    counts = {}
    for et in DIAG_TYPES:
        rows = q(c,
            f"SELECT COUNT(*) FROM events "
            f"WHERE event_type=? AND ts_utc LIKE ?{src_clause}",
            (et, like, *src_param))
        counts[et] = rows[0][0] if rows else 0
        lag = {"universe_selected":"A","entry_rejected":"B","daily_diagnostics":"C"}[et]
        print(f"    Lag {lag}  {et:20s}: {counts[et]}")

    # ── 3. Dom: virkede diagnostikken? ───────────────────────
    print("\n[3] Vurdering:")
    total_diag = sum(counts.values())

    if not strategy_ran and total_diag == 0:
        print("    ⚪ Strategien kørte ikke, og der er ingen diagnostik. Forventeligt.")
        print("       Tjek om ORB overhovedet startede (TWS oppe? scheduler kørte?).")
        verdict = "ikke-kørt"
    elif strategy_ran and total_diag == 0:
        print("    🔴 ADVARSEL: Strategien kørte, men der er NUL diagnostik-events.")
        print("       Dette tyder på at logging-laget fejlede STILLE.")
        print("       → Tjek backend-loggen for fejllinjer:")
        print('          Select-String -Path "C:\\Projects\\trading_dash\\backend\\logs\\*.log" '
              '-Pattern "fejlede|log_universe|log_rejection|log_daily"')
        verdict = "fejlet-stille"
    elif strategy_ran and counts["universe_selected"] == 0:
        print("    🟠 DELVIST: Der er events, men INGEN universe_selected (Lag A).")
        print("       Universe logges ved dagsstart — fraværet er mistænkeligt.")
        print("       Lag B/C virker måske, men Lag A's kald blev ikke nået.")
        verdict = "delvist"
    elif strategy_ran and counts["daily_diagnostics"] == 0:
        print("    🟠 DELVIST: Lag A/B virker, men INGEN daily_diagnostics (Lag C).")
        print("       Lag C logges ved market-close. Hvis sessionen ikke nåede")
        print("       lukketid (fx stoppet manuelt), er fraværet forventeligt.")
        print("       Hvis den KØRTE til luk, blev market-close-grenen ikke nået.")
        verdict = "mangler-lagC"
    else:
        print("    ✅ Diagnostikken virkede: alle tre lag producerede events.")
        verdict = "ok"

    # ── 4. Indholds-stikprøve (hvis der er events) ───────────
    if counts["universe_selected"] > 0:
        print("\n[4] Stikprøve — seneste universe_selected:")
        rows = q(c,
            f"SELECT ts_local, source, payload_json FROM events "
            f"WHERE event_type='universe_selected' AND ts_utc LIKE ?{src_clause} "
            f"ORDER BY id DESC LIMIT 1", (like, *src_param))
        for ts, src, pj in rows:
            try:
                p = json.loads(pj)
                print(f"    {ts}  [{src}]")
                print(f"      univers: {p.get('count')} aktier, fallback={p.get('used_fallback')}")
                tk = p.get('tickers', [])
                print(f"      tickers: {', '.join(tk[:8])}{'...' if len(tk)>8 else ''}")
            except Exception:
                print(f"    (kunne ikke parse payload: {pj[:80]})")

    if counts["daily_diagnostics"] > 0:
        print("\n[5] Stikprøve — daily_diagnostics (dagens facit):")
        rows = q(c,
            f"SELECT ts_local, source, payload_json FROM events "
            f"WHERE event_type='daily_diagnostics' AND ts_utc LIKE ?{src_clause} "
            f"ORDER BY id DESC LIMIT 1", (like, *src_param))
        for ts, src, pj in rows:
            try:
                p = json.loads(pj)
                print(f"    {ts}  [{src}]")
                # ORB-felter
                if "max_state_distribution" in p:
                    print(f"      universe: {p.get('universe_size')}, entries: {p.get('entries')}")
                    print("      hvor langt nåede aktierne i breakout-sekvensen:")
                    for label, n in p.get("max_state_distribution", {}).items():
                        print(f"        {n:3d} × {label}")
                # Konfluens-felter
                elif "peak_score" in p:
                    print(f"      universe: {p.get('universe_size')}, entries: {p.get('entries')}")
                    print(f"      højeste score nået: {p.get('peak_score')}/6")
                    print(f"      oftest manglende: {p.get('most_missing_condition')}")
            except Exception:
                print(f"    (kunne ikke parse payload: {pj[:80]})")

    if counts["entry_rejected"] > 0:
        print(f"\n[6] Lag B: {counts['entry_rejected']} afvisnings-events "
              f"(kun logget ved ÆNDRING i grund).")
        rows = q(c,
            f"SELECT symbol, payload_json FROM events "
            f"WHERE event_type='entry_rejected' AND ts_utc LIKE ?{src_clause} "
            f"ORDER BY id DESC LIMIT 5", (like, *src_param))
        print("    Seneste 5 afvisninger:")
        for sym, pj in rows:
            try:
                p = json.loads(pj)
                print(f"      {sym or '?':8s} {p.get('detail','')}")
            except Exception:
                pass

    print("\n" + "=" * 66)
    print(f"  DOM: {verdict}")
    print("=" * 66)
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
