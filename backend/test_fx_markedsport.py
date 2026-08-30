"""
test_fx_markedsport.py — kan P3's markedsport overhovedet fejle?
════════════════════════════════════════════════════════════════════════════════
`fx_probe.p3()` laegger den ENESTE aegte ordre i hele proben. Porten
`_markedet_er_aabent()` er det der staar mellem den ordre og et lukket marked.

⚠ HVORFOR DEN PORT FINDES. En markedsordre lagt naar FX er lukket staar
'PreSubmitted' og fylder VED AABNING — timer senere, uden at scriptet koerer
laengere og uden nogen til at lukke positionen igen. Proben ville forlaenge sig
selv til en ejerloes position. Samme fejlklasse som over-salget 31-07: en ordre
man troede var faerdig, fordi man holdt op med at kigge.

⚠ TESTEN SKAL SE BEGGE UDFALD. En port der kun er set sige nej er ikke
bevist — den kunne vaere en funktion der altid siger nej. Derfor er
tidspunkterne valgt saa BAADE 'aabent' og 'lukket' optraeder, og graenserne
(minuttet foer aabning, aabningsminuttet, lukkeminuttet, det daglige ophold)
er med.

    python test_fx_markedsport.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import fx_probe

ET = ZoneInfo("US/Eastern")


class Kontraktdetalje:
    """Kun de to felter porten laeser. Rigtige vaerdier fra IDEALPRO 30-08-2026."""
    timeZoneId = "US/Eastern"
    tradingHours = ("20260830:1715-20260831:1700;"
                    "20260831:1715-20260901:1700;"
                    "20260901:1715-20260902:1700")


class UdenGyldigTidszone(Kontraktdetalje):
    timeZoneId = "Ikke/EnTidszone"


class UdenHandelstimer(Kontraktdetalje):
    tradingHours = ""


SAGER = [
    # navn                              tidspunkt                              aabent?
    ("soendag 13:43 ET (foer aabning)", datetime(2026, 8, 30, 13, 43, tzinfo=ET), False),
    ("soendag 17:14 ET (1 min foer)",   datetime(2026, 8, 30, 17, 14, tzinfo=ET), False),
    ("soendag 17:15 ET (aabningen)",    datetime(2026, 8, 30, 17, 15, tzinfo=ET), True),
    ("mandag 03:00 ET (midt i)",        datetime(2026, 8, 31, 3, 0, tzinfo=ET), True),
    ("mandag 17:00 ET (lukkeminuttet)", datetime(2026, 8, 31, 17, 0, tzinfo=ET), True),
    ("mandag 17:08 ET (i opholdet)",    datetime(2026, 8, 31, 17, 8, tzinfo=ET), False),
    ("tirsdag 09:00 ET (naeste blok)",  datetime(2026, 9, 1, 9, 0, tzinfo=ET), True),
]


def main() -> int:
    d = Kontraktdetalje()
    fejl = 0
    set_udfald = set()

    for navn, nu, forventet in SAGER:
        faktisk, forklaring = fx_probe._markedet_er_aabent(d, nu)
        set_udfald.add(faktisk)
        ok = faktisk == forventet
        fejl += not ok
        print(f"  {'OK  ' if ok else 'FEJL'} {navn:32} -> "
              f"{'AABENT' if faktisk else 'LUKKET'}"
              f"  (forventet {'AABENT' if forventet else 'LUKKET'})")

    # ⚠ Uden dette er testen vaerdiloes: en funktion der altid returnerer False
    # ville bestaa alle 'lukket'-sagerne.
    if set_udfald != {True, False}:
        print("  FEJL porten viste kun ét udfald — testen beviser intet")
        fejl += 1

    # ── Mutationer: usikkerhed skal give NEJ, ikke et gaet ─────────────────
    for navn, klasse in [("uparselig tidszone", UdenGyldigTidszone),
                         ("tomme handelstimer", UdenHandelstimer)]:
        faktisk, forklaring = fx_probe._markedet_er_aabent(klasse())
        ok = faktisk is False
        fejl += not ok
        print(f"  {'OK  ' if ok else 'FEJL'} {navn:32} -> "
              f"{'AABENT' if faktisk else 'LUKKET'}  ({forklaring[:46]})")

    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {fejl} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
