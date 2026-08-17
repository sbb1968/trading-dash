#!/usr/bin/env python3
r"""
vx_drift_test.py — hvad ændrer sig i VX mellem to identiske harvests?
════════════════════════════════════════════════════════════════════════════════
T7 fandt at ni af ti serier er idempotente, og at VX_1dag.csv ikke er: samme
antal rækker, samme sidste bar — og alligevel ændret indhold. Harvesten meldte
selv "+0 nye barer".

⚠ DET ER IKKE NØDVENDIGVIS EN FEJL I HØSTEN. VX hentes som **ContFuture** —
en syet, kontinuerlig serie. Ruller fronten, kan IBKR justere ældre bars
bagud, og så ændrer historikken sig af sig selv. Det ville være en egenskab
ved kilden, ikke ved koden.

⚠ MEN DET ER EN EGENSKAB DER SKAL KENDES, ikke opdages senere. VX er lag 2's
kilde til FRISK implicit vol, og den har i forvejen den korteste historik af
alle serier (686 dage mod 4.286 for SPY). En prædiktor der ændrer sig under
fødderne på én, kan ikke fryses, og et holdout-resultat på den betyder
ingenting hvis serien er en anden næste uge.

Målingen svarer på tre ting:
    · HVOR MANGE rækker ændrede sig
    · HVOR i serien (kun gamle? kun tæt på rullet?)
    · HVOR MEGET (er det afrunding, eller er det niveauskift?)

KØRSEL — kopien tages FØR harvesten:

    venv\Scripts\python vx_drift_test.py --gem
    venv\Scripts\python vol_harvest.py --hvad dagligt --kun VX
    venv\Scripts\python vx_drift_test.py --sammenlign
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

HER = Path(__file__).resolve().parent
FIL = HER / "vol_cache" / "VX_1dag.csv"
KOPI = HER / "vol_cache" / "VX_1dag.csv.foer_naeste_harvest"


def laes(p: Path) -> dict[str, list[str]]:
    """{dato: række}. ⚠ Nøglet på DATO, ikke på position — ellers ville en
    indsat række midt i serien få alt efter den til at se ændret ud, og man
    ville jage et niveauskift der ikke fandtes."""
    ud = {}
    with p.open(encoding="utf-8", newline="") as f:
        for r in csv.reader(f):
            if r and r[0][:1].isdigit():
                ud[r[0][:10]] = r
    return ud


def gem() -> int:
    if not FIL.exists():
        print(f"⚠ {FIL} findes ikke")
        return 1
    shutil.copyfile(FIL, KOPI)
    d = laes(FIL)
    print(f"Kopi gemt: {len(d)} rækker, {min(d)} .. {max(d)}")
    print("\nKør nu:  venv\\Scripts\\python vol_harvest.py --hvad dagligt --kun VX")
    print("Derefter: venv\\Scripts\\python vx_drift_test.py --sammenlign")
    return 0


def sammenlign() -> int:
    if not KOPI.exists():
        print("⚠ Ingen kopi. Kør --gem før harvesten.")
        return 1
    foer, efter = laes(KOPI), laes(FIL)

    kun_foer = sorted(set(foer) - set(efter))
    kun_efter = sorted(set(efter) - set(foer))
    faelles = sorted(set(foer) & set(efter))
    aendret = [d for d in faelles if foer[d] != efter[d]]

    print(f"VX_1dag.csv — to identiske harvests\n")
    print(f"  rækker før / efter : {len(foer)} / {len(efter)}")
    print(f"  forsvundne datoer  : {len(kun_foer)}"
          + (f"  {kun_foer[:5]}" if kun_foer else ""))
    print(f"  nye datoer         : {len(kun_efter)}"
          + (f"  {kun_efter[:5]}" if kun_efter else ""))
    print(f"  ÆNDREDE rækker     : {len(aendret)} af {len(faelles)} fælles"
          f"  ({len(aendret)/max(1,len(faelles))*100:.1f} %)")

    if not aendret:
        print("\n  → VX er idempotent i denne kørsel. Kør igen over et roll for"
              "\n    at være sikker — det er dér en ContFuture kan justeres bagud.")
        return 0

    print(f"\n  hvor i serien:")
    print(f"    ældste ændrede : {aendret[0]}")
    print(f"    nyeste ændrede : {aendret[-1]}")

    # ⚠ HVOR MEGET. Uden størrelsen kan man ikke skelne afrunding fra
    # niveauskift — og de to har vidt forskellige konsekvenser for lag 2.
    print(f"\n  {'dato':12}{'felt':>6}{'før':>14}{'efter':>14}{'afvigelse':>14}")
    vist = 0
    stoerst = 0.0
    for d in aendret:
        a, b = foer[d], efter[d]
        for i in range(1, min(len(a), len(b))):
            if a[i] == b[i]:
                continue
            try:
                x, y = float(a[i]), float(b[i])
                afv = abs(y - x) / abs(x) * 100 if x else float("inf")
                stoerst = max(stoerst, afv)
                if vist < 12:
                    print(f"  {d:12}{i:>6}{x:>14.4f}{y:>14.4f}{afv:>13.4f} %")
                    vist += 1
            except ValueError:
                if vist < 12:
                    print(f"  {d:12}{i:>6}{a[i]:>14}{b[i]:>14}{'(tekst)':>14}")
                    vist += 1
    if len(aendret) > 12:
        print(f"  … {len(aendret)-vist} flere")
    print(f"\n  største relative afvigelse: {stoerst:.4f} %")
    print("\n  → VX er IKKE reproducerbar. Konsekvens for lag 2: komponenten kan"
          "\n    ikke fryses, og et holdout-resultat på den er ikke gentageligt.")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gem", action="store_true")
    ap.add_argument("--sammenlign", action="store_true")
    a = ap.parse_args()
    if a.gem:
        sys.exit(gem())
    if a.sammenlign:
        sys.exit(sammenlign())
    ap.print_help()
