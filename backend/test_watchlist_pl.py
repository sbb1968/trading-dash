"""
test_watchlist_pl.py — watchlistens urealiserede P/L maa ikke gaa i stykker igen
════════════════════════════════════════════════════════════════════════════════
31-08-2026 viste watchlisten "Ur. P/L $-115.00" paa en MES hvor brokeren havde
1 kontrakt @ 7704,12 og prisen stod i 7703. Det rigtige tal var -$5,61.

Tre fejl er fanget her, og de blev fundet i den raekkefoelge:

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

  3. KUN DEN HALVE KURTAGE VAR MED. IBKR laegger entry-kurtagen ind i avgCost,
     saa tallet var nettet for den ene side og ikke den anden. Det LIGNEDE
     "hvad faar jeg hvis jeg lukker nu" uden at vaere det. Kolonnen svarer nu
     paa det spoergsmaal, og hele rundturen traekkes fra.

⚠ HVORFOR DENNE TEST LAESER TSX SOM TEKST. Projektet har ingen frontend-
testopsaetning, og en P/L-formel er for dyr at have ubevogtet. Samme greb som
test_futures_katalog.py, der laeser src/futures.ts. Groft, men det fanger
praecis de regressioner der faktisk skete.

⚠ OG TESTEN MUTERER SIG SELV TIL SIDST. En tekstsoegning der altid finder det
den leder efter, beviser ingenting. Derfor fjernes multiplikatoren fra en kopi
af kildeteksten, og testen SKAL da fejle.

    python test_watchlist_pl.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import futures_katalog

APP = Path(__file__).parent.parent / "src" / "App.tsx"
LAYOUTS = Path(__file__).parent.parent / "src" / "layouts.ts"

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


def _find(kilde: str, navn: str) -> str | None:
    m = re.search(r"const\s+" + navn + r"\s*=.*?;", kilde, re.S)
    return m.group(0) if m else None


def kontroller(kilde: str, layouts: str, stille: bool = False) -> list[str]:
    """Fejlene for en given kildetekst. stille=True bruges af mutationen."""
    lokale: list[str] = []

    def k(betingelse: bool, hvad: str) -> None:
        if not stille:
            kraev(betingelse, hvad)
        if not betingelse:
            lokale.append(hvad)

    brutto = _find(kilde, "brutto")
    k(brutto is not None, "brutto-formlen findes i App.tsx")
    if brutto:
        # ⚠ Kernen. Uden multiplikatoren er et MES-punkt $1 og ikke $5.
        k(".mult" in brutto, "brutto ganger med multiplikatoren (.mult)")
        k(".qty" in brutto, "brutto ganger med antallet (.qty)")
        k(".avgPrice" in brutto, "brutto traekker koebsprisen fra (.avgPrice)")

    upl = _find(kilde, "uplAmt")
    k(upl is not None, "uplAmt-formlen findes i App.tsx")
    if upl:
        # ⚠ Hele rundturen. Uden dette viser kolonnen et HALVT nettet tal.
        k("exitKurtage" in upl, "uplAmt traekker exit-kurtagen fra")
        k("?? 0" in upl, "en ikke-maalt kurtage traekker 0 fra, ikke et gaet")

    # Positionen skal komme fra brokeren, ikke fra vores eget regnskab.
    k("const b = brokerPos" in kilde,
      "positionen (b) hentes fra brokerPos, ikke fra meta.bought")
    k("account/dash-snapshot" in kilde,
      "brokerPos hentes fra /account/dash-snapshot")
    k("exit_kurtage" in kilde, "exit_kurtage laeses fra snapshot'et")

    # ⚠ 'ikke hentet' og 'ingen position' maa ikke smelte sammen.
    k("posUkendt" in kilde,
      "der skelnes mellem 'kunne ikke hentes' og 'ingen position'")

    # Kolonnen skal staa lige efter Pris — i tabellen OG i kolonnevalget,
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

    # ── Regnestykket der gik galt ──────────────────────────────────────────
    print("\n  ── regnestykket der gik galt ──")
    rigtigt = (7703.0 - 7704.122) * 1 * 5.0
    uden_mult = (7703.0 - 7704.122) * 1
    kraev(abs(rigtigt - (-5.61)) < 0.01,
          f"(7703 - 7704,122) x 1 x 5 = {rigtigt:.2f} USD")
    kraev(abs(uden_mult - (-1.12)) < 0.01,
          f"uden multiplikator ville der staa {uden_mult:.2f} — faktor 5 forkert")

    # ── Hele rundturen ─────────────────────────────────────────────────────
    print("\n  ── hele rundturen ──")
    kurtage = futures_katalog.kurtage_pr_side("MES")
    kraev(kurtage is not None,
          f"kataloget kender MES' kurtage ({kurtage} USD pr. kontrakt pr. side)")
    # ⚠ None og 0,0 er ikke det samme. 0,0 ville paastaa at handlen er gratis.
    kraev(futures_katalog.kurtage_pr_side("AAPL") is None,
          "en aktie giver None, ikke 0,0")
    kraev(futures_katalog.kurtage_pr_side("MNQ") is None,
          "et umaalt instrument giver None, ikke et laant tal fra MES")
    if kurtage:
        # Soerens faktiske handel 31-08: koebt 7704,00, solgt 7708,50.
        brutto_h = (7708.50 - 7704.00) * 1 * 5.0
        netto_h = brutto_h - 2 * kurtage
        kraev(abs(brutto_h - 22.50) < 0.01, f"brutto paa handlen = {brutto_h:.2f} USD")
        kraev(abs(netto_h - 21.28) < 0.01,
              f"netto efter rundtur ({2 * kurtage:.2f}) = {netto_h:.2f} USD")

    # ── ⚠ MUTATION: testen skal kunne fejle ────────────────────────────────
    print("\n  ── mutation: fjern multiplikatoren og se testen fejle ──")
    muteret = re.sub(r"(const\s+brutto\s*=.*?)\s*\*\s*b\.mult", r"\1",
                     kilde, count=1, flags=re.S)
    kraev(muteret != kilde, "mutationen aendrede faktisk kildeteksten")
    m_fejl = kontroller(muteret, layouts, stille=True)
    kraev(any(".mult" in f for f in m_fejl),
          f"uden multiplikatoren FEJLER testen ({len(m_fejl)} fejl)")

    # Anden mutation: fjern exit-kurtagen igen.
    muteret2 = re.sub(r"(const\s+uplAmt\s*=.*?)\n\s*:\s*brutto[^;]*;",
                      r"\1\n                : brutto;", kilde, count=1, flags=re.S)
    kraev(muteret2 != kilde, "mutation 2 aendrede faktisk kildeteksten")
    m2 = kontroller(muteret2, layouts, stille=True)
    kraev(any("exit-kurtagen" in f for f in m2),
          f"uden exit-kurtagen FEJLER testen ({len(m2)} fejl)")

    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
