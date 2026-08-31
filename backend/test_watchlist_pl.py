"""
test_watchlist_pl.py — watchlistens urealiserede P/L maa ikke gaa i stykker igen
════════════════════════════════════════════════════════════════════════════════
31-08-2026 viste watchlisten "Ur. P/L $-115.00" paa en MES hvor brokeren havde
1 kontrakt @ 7704,12 og prisen stod i 7703. Det rigtige tal var -$5,61.

To fejl paa én gang, og de forstaerkede hinanden:

  1. POSITIONEN KOM FRA localStorage. `meta.bought` er et regnskab akkumuleret
     af de fills netop denne browser tilfaeldigvis saa. Det var drevet fra
     virkeligheden (qty=2 @ 7760,50 mod brokerens 1 @ 7704,12). Et tal der ser
     autoritativt ud og aldrig er tjekket mod brokeren.

  2. MULTIPLIKATOREN MANGLEDE. futures_katalog.py's egen indledning advarer
     ordret mod netop den fejl:
         "glemt i MULTIPLIER -> P&L regnes med 1,0 i stedet for fx 2,0.
          Journalen ser rigtig ud og er forkert med faktor to.
          Den sidste er den vaerste: den opdages foerst naar kontoudtoget
          ikke stemmer."
     Kataloget blev bygget for at forhindre det. Watchlisten spurgte det aldrig.

⚠ HVORFOR DENNE TEST LAESER TSX SOM TEKST. Projektet har ingen frontend-
testopsaetning, og en P/L-formel er for dyr at have ubevogtet. Samme greb som
test_futures_katalog.py, der laeser src/futures.ts. Det er groft, men det
fanger praecis den regression der faktisk skete.

⚠ OG TESTEN MUTERER SIG SELV TIL SIDST. En tekstsoegning der altid finder det
den leder efter, beviser ingenting. Derfor fjernes multiplikatoren fra en kopi
af kildeteksten, og testen SKAL da fejle.

    python test_watchlist_pl.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

APP = Path(__file__).parent.parent / "src" / "App.tsx"
LAYOUTS = Path(__file__).parent.parent / "src" / "layouts.ts"

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


def find_upl_formel(kilde: str) -> str | None:
    """Linjen der beregner uplAmt. None hvis den ikke findes laengere."""
    m = re.search(r"const\s+uplAmt\s*=.*?;", kilde, re.S)
    return m.group(0) if m else None


def kontroller(kilde: str, layouts: str, maerke: str = "") -> list[str]:
    """Returnerer listen af fejl for en given kildetekst."""
    lokale: list[str] = []

    def k(betingelse: bool, hvad: str) -> None:
        if not maerke:
            kraev(betingelse, hvad)
        if not betingelse:
            lokale.append(hvad)

    formel = find_upl_formel(kilde)
    k(formel is not None, "uplAmt-formlen findes i App.tsx")
    if formel:
        # ⚠ Kernen. Uden multiplikatoren er et MES-punkt $1 og ikke $5.
        k(".mult" in formel, "uplAmt ganger med multiplikatoren (.mult)")
        k(".qty" in formel, "uplAmt ganger med antallet (.qty)")
        k(".avgPrice" in formel, "uplAmt traekker koebsprisen fra (.avgPrice)")

    # Positionen skal komme fra brokeren, ikke fra vores eget regnskab.
    k("const b = brokerPos" in kilde,
      "positionen (b) hentes fra brokerPos, ikke fra meta.bought")
    k("account/dash-snapshot" in kilde,
      "brokerPos hentes fra /account/dash-snapshot")

    # ⚠ 'ikke hentet' og 'ingen position' maa ikke smelte sammen.
    k("posUkendt" in kilde,
      "der skelnes mellem 'kunne ikke hentes' og 'ingen position'")

    # Kolonnen skal staa lige efter Pris — baade i tabellen og i kolonnevalget,
    # ellers passer Konfiguratorens raekkefoelge ikke til det man ser.
    hoveder = re.findall(r'vist\("(\w+)"\)\s*&&\s*<th', kilde)
    if "pris" in hoveder and "upl" in hoveder:
        k(hoveder.index("upl") == hoveder.index("pris") + 1,
          f"Ur. P/L staar lige efter Pris i tabellen ({hoveder[:4]})")
    celler = re.findall(r'vist\("(\w+)"\)\s*&&\s*<td', kilde)
    k(celler == hoveder,
      f"celler og overskrifter staar i SAMME raekkefoelge\n"
      f"          th: {hoveder}\n          td: {celler}")

    kol = re.findall(r'\{\s*id:\s*"(\w+)"', layouts)
    if "pris" in kol and "upl" in kol:
        k(kol.index("upl") == kol.index("pris") + 1,
          "kolonnevalget i layouts.ts har samme raekkefoelge")
    return lokale


def main() -> int:
    kilde = APP.read_text(encoding="utf-8")
    layouts = LAYOUTS.read_text(encoding="utf-8")

    print("  ── watchlistens P/L ──")
    kontroller(kilde, layouts)

    # ── Regneeksemplet fra den faktiske fejl ───────────────────────────────
    print("\n  ── regnestykket der gik galt ──")
    pris, koeb, antal, mult = 7703.0, 7704.122, 1, 5.0
    rigtigt = (pris - koeb) * antal * mult
    uden_mult = (pris - koeb) * antal
    kraev(abs(rigtigt - (-5.61)) < 0.01,
          f"(7703 - 7704,122) x 1 x 5 = {rigtigt:.2f} USD")
    kraev(abs(uden_mult - (-1.12)) < 0.01,
          f"uden multiplikator ville der staa {uden_mult:.2f} USD — faktor 5 forkert")

    # ── ⚠ MUTATION: testen skal kunne fejle ────────────────────────────────
    print("\n  ── mutation: fjern multiplikatoren og se testen fejle ──")
    muteret = re.sub(r"(const\s+uplAmt\s*=.*?)\s*\*\s*b\.mult", r"\1",
                     kilde, count=1, flags=re.S)
    kraev(muteret != kilde, "mutationen aendrede faktisk kildeteksten")
    m_fejl = kontroller(muteret, layouts, maerke="mutation")
    kraev(any(".mult" in f for f in m_fejl),
          f"uden multiplikatoren FEJLER testen ({len(m_fejl)} fejl)")

    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
