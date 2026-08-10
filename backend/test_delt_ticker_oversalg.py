"""
test_delt_ticker_oversalg.py — den delte ticker, begge halvdele
════════════════════════════════════════════════════════════════════════════════
Konstruerer præcis det tilfælde der skabte fem uønskede shorts 6.–10. august:

    To strategier holder samme ticker. Den ene lukker. Ordren FYLDER,
    men bekræftelsen mistes. Hvad gør vagten?

⚠ BEGGE HALVDELE ER PÅKRÆVET. En test der kun viser at den nye kode opfører sig
rigtigt, beviser ikke at noget blev bedre — den ville også være grøn hvis fejlen
aldrig havde eksisteret. Derfor køres samme fikstur mod den GAMLE vagt
(`_ibkr_still_holds`, som stadig findes) og mod den NYE (`_lukkeordre_ufyldt`):

    gammel vagt  ->  "genafgiv"   (fejlen, gengivet)
    ny vagt      ->  "fyldt"      (rettelsen)

Falder den gamle ikke igennem, måler testen ikke fejlen, og så siger den intet om
rettelsen.

    python test_delt_ticker_oversalg.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import algo_confluence2 as k2mod

FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# ── Fikstur ─────────────────────────────────────────────────────────────────
# IOVA: to strategier med 39 hver. Konfluens 2 lukker sine 39; ordren FYLDER,
# men close_result siger filled=0 fordi bekræftelsen blev misset.

TICKER, ANDEL = "IOVA", 39


class FalskConn:
    """`positioner` er kontoens NETTO efter at ordren er fyldt — altså den anden
    strategis andel. `ordre` er den konkrete ordres sandhed."""

    def __init__(self, netto: float, ordre: dict):
        self.connected = True
        self._netto = netto
        self._ordre = ordre

    async def get_positions_reliable(self):
        return ([{"ticker": TICKER, "position": self._netto}], True)

    async def ordre_status(self, order_id):
        return dict(self._ordre)


def Strategi(conn):
    """En BaseStrategy med kun det vagterne bruger.

    ⚠ Bygget med __new__ paa den RIGTIGE klasse — samme greb som
    test_k2_close_robusthed. BaseStrategy er abstrakt og kan ikke instantieres;
    en underklasse med tomme metoder ville derimod betyde at vi ogsaa testede at
    attrapperne var rigtige. Vagterne bruger kun self.name og self.conn.
    """
    s = k2mod.Confluence2Live.__new__(k2mod.Confluence2Live)
    s.conn = conn
    # `name` er en property der laeser self._strategy.name. Vagternes logtekster
    # bruger den, saa attrappen skal have et navn — men klassens egen property
    # skal blive staaende, saa vi tester den rigtige kode.
    s._strategy = type("S", (), {"name": "Konfluens 2"})()
    return s


async def hoved():
    # Sandheden: ordren FYLDTE 39 af 39. Kontoen står på 39 — den ANDEN
    # strategis andel.
    conn = FalskConn(netto=ANDEL,
                     ordre={"kendt": True, "status": "Filled",
                            "filled": ANDEL, "remaining": 0})
    s = Strategi(conn)
    close_result = {"order_id": 4711, "status": "Submitted", "filled": 0}

    print("\n1. ⚠ Den GAMLE vagt — fejlen gengivet")
    print("   Kontoen viser 39 (den andens andel). Spørger vi beholdningen,")
    print("   kan den ikke se hvis aktier det er.")
    gammel = await s._ibkr_still_holds(TICKER, "long", ANDEL)
    print(f"     _ibkr_still_holds -> {gammel}")
    kraev(gammel is True,
          "den gamle vagt siger 'holder stadig' = GENAFGIV — det er fejlen, og "
          "uden at den falder her måler testen ingenting")

    print("\n2. Den NYE vagt — samme fikstur")
    ny = await s._lukkeordre_ufyldt(close_result, TICKER, "long", ANDEL)
    print(f"     _lukkeordre_ufyldt -> {ny}")
    kraev(ny is False,
          "den nye vagt siger 'ordren ER fyldt' = bogfør, gen-afgiv ikke")

    kraev(gammel is not ny,
          "⚠ og de to er UENIGE på præcis dette tilfælde — ellers ville "
          "rettelsen ikke have ændret noget")

    print("\n3. Den nye vagt siger stadig ja når ordren faktisk IKKE fyldte")
    # Ellers havde vi bare slået genforsøg fra, hvilket ville efterlade
    # positioner åbne i stedet for at over-sælge. Lige så galt, modsat vej.
    c2 = FalskConn(netto=2 * ANDEL,
                   ordre={"kendt": True, "status": "Cancelled",
                          "filled": 0, "remaining": ANDEL})
    kraev(await Strategi(c2)._lukkeordre_ufyldt(close_result, TICKER, "long", ANDEL) is True,
          "annulleret ordre -> genafgiv")

    print("\n4. Ukendt udfald udløser ALDRIG en ny ordre")
    for navn, ordre in (
        ("ordren kendes ikke (efter genforbindelse)", {"kendt": False, "grund": "ikke i trades()"}),
        ("delvis fyldning 20/39",                     {"kendt": True, "status": "Submitted",
                                                       "filled": 20, "remaining": 19}),
        ("stadig arbejdende",                         {"kendt": True, "status": "PreSubmitted",
                                                       "filled": 0, "remaining": ANDEL}),
        ("Inactive — IKKE terminal",                  {"kendt": True, "status": "Inactive",
                                                       "filled": 0, "remaining": ANDEL}),
    ):
        r = await Strategi(FalskConn(ANDEL, ordre))._lukkeordre_ufyldt(
            close_result, TICKER, "long", ANDEL)
        kraev(r is None, f"{navn} -> None (behold, gen-afgiv ikke)")

    print("\n5. Uden ordre-id gættes der ikke")
    kraev(await s._lukkeordre_ufyldt({}, TICKER, "long", ANDEL) is None,
          "manglende order_id -> None")
    s2 = Strategi(FalskConn(ANDEL, {"kendt": True, "status": "Filled",
                                    "filled": ANDEL, "remaining": 0}))
    s2.conn.connected = False
    kraev(await s2._lukkeordre_ufyldt(close_result, TICKER, "long", ANDEL) is None,
          "ingen forbindelse -> None")


asyncio.run(hoved())

print("\n" + "=" * 74)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent — den gamle vagt fejler paa fiksturet, den nye ikke.")
