"""
algoserver_vagt.py — er algoserveren stadig i live?
════════════════════════════════════════════════════════════════════════════════
Afbrydelsesreglen i konto 2-specens §4b kræver at algoserverens tilstand tjekkes
FØR T1 og efter hvert af trinnene T1–T3, samt efter T10. Mister den forbindelsen
på noget tidspunkt: stop øjeblikkeligt.

⚠ EN AFBRYDELSESREGEL DER SKAL HUSKES, ER IKKE EN REGEL. Derfor er den en
kommando. Kør den mellem trinnene:

    python algoserver_vagt.py --gem      # før T1: gem udgangspunktet
    python algoserver_vagt.py            # efter hvert trin: sammenlign

Exit 0 = uændret · exit 1 = ⚠ ÆNDRET, STOP · exit 2 = kunne ikke måles

Exit 2 er ikke det samme som exit 0. Kan tilstanden ikke måles, ved vi ikke om
den er uændret — og "vi ved det ikke" må ikke se ud som "alt er fint". Det er
samme skelnen som `_lukkeordre_ufyldt`s None.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from accounts import identity

GEMT = Path(__file__).parent / ".algoserver_vagt.json"


def _hent(url: str, noegle: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"X-Internal-Key": noegle})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def maal(base: str, noegle: str) -> dict:
    """Tilstanden lige nu. `maalt` er False hvis noget ikke kunne læses."""
    ud = {"tid": datetime.now(timezone.utc).isoformat(), "maalt": False}
    try:
        h = _hent(f"{base}/health", noegle)
        ud["forbundet"] = bool(h.get("ibkr_connected"))
        ud["algo_running"] = bool(h.get("algo_running"))
        ud["hjerteslag"] = h.get("time")
    except Exception as e:
        ud["fejl"] = f"/health: {e}"
        return ud
    try:
        s = _hent(f"{base}/account/dash-snapshot", noegle)
        ud["konto"] = s.get("ibkr_account")
        ud["positioner"] = len(s.get("positions") or []) if s.get("ok") else None
        ud["snapshot_ok"] = bool(s.get("ok"))
    except Exception as e:
        ud["fejl"] = f"/account/dash-snapshot: {e}"
        return ud
    ud["maalt"] = True
    return ud


def vis(t: dict, navn: str) -> None:
    if not t.get("maalt"):
        print(f"  {navn:6} ⚠ KUNNE IKKE MAALES — {t.get('fejl')}")
        return
    print(f"  {navn:6} forbundet={t['forbundet']} · algo_running={t['algo_running']} "
          f"· konto={t['konto']} · positioner={t['positioner']} "
          f"· hjerteslag {str(t.get('hjerteslag'))[11:19]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Algoserverens tilstand, foer og efter")
    ap.add_argument("--url", default=(identity.replication_target_url or "").rstrip("/"),
                    help="algoserverens base-URL (default: replication.target_url)")
    ap.add_argument("--gem", action="store_true",
                    help="gem denne maaling som udgangspunkt (koeres FOER T1)")
    ap.add_argument("--i-vindue", dest="i_vindue", action="store_true",
                    help="testvinduet 08:00-14:00 dansk, hvor strategierne IKKE "
                         "handler: da er et aendret positionstal ogsaa et stopsignal")
    args = ap.parse_args()

    if not args.url:
        print("Ingen URL. Angiv --url, eller saet replication.target_url i account.yaml.")
        return 2

    nu = maal(args.url, identity.internal_key)

    if args.gem:
        GEMT.write_text(json.dumps(nu, indent=2), encoding="utf-8")
        print("UDGANGSPUNKT GEMT")
        vis(nu, "nu")
        if not nu.get("maalt"):
            print("\n⚠ Udgangspunktet kunne ikke maales. Start ikke testen — "
                  "uden et udgangspunkt kan en aendring ikke opdages.")
            return 2
        return 0

    if not GEMT.exists():
        print("Intet udgangspunkt gemt. Koer 'python algoserver_vagt.py --gem' foer T1.")
        vis(nu, "nu")
        return 2

    foer = json.loads(GEMT.read_text(encoding="utf-8"))
    print("ALGOSERVEREN")
    vis(foer, "FOER")
    vis(nu, "NU")

    if not nu.get("maalt"):
        print("\n⚠ KUNNE IKKE MAALES. Det er IKKE det samme som uaendret.")
        print("  Stop testen og find ud af hvorfor foer du fortsaetter.")
        return 2

    # ⚠ TO SLAGS AENDRINGER, OG DE MAA IKKE BLANDES SAMMEN.
    #
    # Mistet forbindelse, stoppet algo og skiftet konto er ALTID stopsignaler.
    #
    # Positionstallet er det ikke. Handler strategierne, aendrer det sig lovligt
    # hvert par minutter — og en vagt der raaber ved hver eneste handel, laerer
    # man at se forbi paa en time. Netop derfor ligger testvinduet 08:00-14:00
    # dansk, hvor strategierne IKKE handler; dér ER en aendring et signal, og saa
    # saettes --i-vindue.
    afvig, bemaerk = [], []
    if foer.get("forbundet") and not nu.get("forbundet"):
        afvig.append("IBKR-forbindelsen er VAEK")
    if foer.get("algo_running") and not nu.get("algo_running"):
        afvig.append("algo_running er slaaet fra")
    if foer.get("konto") != nu.get("konto"):
        afvig.append(f"kontoen er skiftet: {foer.get('konto')} -> {nu.get('konto')}")
    if foer.get("positioner") != nu.get("positioner"):
        _t = f"antal positioner: {foer.get('positioner')} -> {nu.get('positioner')}"
        (afvig if args.i_vindue else bemaerk).append(_t)

    if afvig:
        print("\n⚠⚠ AENDRET — STOP TESTEN NU")
        for a in afvig:
            print("   -", a)
        print("\n  1. Luk workstationens Gateway")
        print("  2. Bring algoserveren op")
        print("  3. Rapportér hvilket trin der udloeste det")
        print("  Fortsaet IKKE for at se om det var et tilfaelde.")
        return 1

    for b_ in bemaerk:
        print(f"\n  (bemaerk: {b_}")
        print(f"   — forventet naar strategierne handler. Koerer du i vinduet")
        print(f"   08:00-14:00, saet --i-vindue, saa taeller det med som stopsignal.)")
    print("\n  Ingen stopsignaler — fortsaet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
