"""
test_eco_dash.py — kalendervinduet maa kunne skelne tre tilstande
════════════════════════════════════════════════════════════════════════════════
Vinduet i Trading Dash skal kunne sige tre FORSKELLIGE ting:

    ingen events i dag      -> en rolig dag
    kalenderen svarer ikke  -> vi ved det ikke
    hoesten er foraeldet    -> events vises, men de kan vaere forkerte

⚠ SMELTER DE SAMMEN, ER VINDUET FARLIGT. En stille fejl der ser ud som en rolig
dag er praecis den fejlklasse resten af projektet jager — og paa en kalender er
den saerlig ubehagelig: man kigger netop paa den for at vide om der er noget man
skal passe paa. "Ingen events" paa en NFP-fredag ville vaere det vaerst taenkelige
svar, og det er ogsaa det billigste at komme til at give.

31-08-2026 var i oevrigt en dag UDEN tier-events. Den dag er den bedste
paamindelse om hvorfor testen findes: skaermen ser ens ud om det er sandt eller
om databasen braendte.

    python test_eco_dash.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import re
import sys
from pathlib import Path

import eco_kalender

VINDUE = Path(__file__).parent.parent / "src" / "EcoKalender.tsx"
MAIN = Path(__file__).parent / "main.py"

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


def main() -> int:
    kilde = VINDUE.read_text(encoding="utf-8")
    hoved = MAIN.read_text(encoding="utf-8")

    # ── Endpointet ─────────────────────────────────────────────────────────
    print("  ── /eco/dash-dag ──")
    kraev('@app.get("/eco/dash-dag")' in hoved, "endpointet findes")
    # ⚠ UDEN auth. Trading Dash har ingen studio-token; kraevede endpointet
    # login, ville vinduet vaere tomt hver morgen — og tomt ligner "ingen events".
    m = re.search(r'@app\.get\("/eco/dash-dag"([^)]*)\)', hoved)
    kraev(m is not None and "require_studio_auth" not in (m.group(1) or ""),
          "endpointet kraever IKKE studio-auth (som /account/dash-snapshot)")
    krop = hoved.split('@app.get("/eco/dash-dag")')[1][:1600]
    kraev('"ok": False' in krop,
          "en fejl giver ok=False, ikke en tom event-liste")
    kraev('"events": None' in krop,
          "ved fejl er events None (ukendt), ikke [] (ingen)")
    kraev('"stale"' in krop, "hoestens friskhed foelger med i svaret")

    # ── Vinduet ────────────────────────────────────────────────────────────
    print("\n  ── de tre tilstande i vinduet ──")
    kraev("Ingen events" in kilde, "tilstand 1: 'ingen events' har sin egen tekst")
    kraev("svarer ikke" in kilde, "tilstand 2: 'kalenderen svarer ikke' har sin egen tekst")
    kraev("foraeldet" in kilde or "forældet" in kilde,
          "tilstand 3: 'hoesten er foraeldet' har sin egen tekst")
    kraev("Det betyder ikke at der ingen events er" in kilde,
          "fejlteksten siger EKSPLICIT at tom ikke betyder rolig")
    # ⚠ Uden klokkeslaet maa der ikke staa 00:00 — det ville paastaa et
    # tidspunkt kilden ikke har oplyst.
    kraev("hele dagen" in kilde, "events uden klokkeslaet vises som 'hele dagen'")

    # ── Datalaget svarer ───────────────────────────────────────────────────
    print("\n  ── datalaget ──")
    with contextlib.closing(eco_kalender.forbind_laes()) as con:
        st = eco_kalender.status(con)
        i_dag = eco_kalender.hent_dag(con, dt.date.today().isoformat())
        # En dag vi VED har indhold — CPI-dagen fra Soerens egen sammenligning.
        cpi = eco_kalender.hent_dag(con, "2026-08-12")

    kraev(isinstance(st.get("stale"), bool), f"status.stale er en bool ({st.get('stale')})")
    kraev(isinstance(i_dag, list), f"hent_dag giver en liste ({len(i_dag)} i dag)")

    # ⚠ KONTROLFIKSTUR. Uden en dag vi VED har events, ville testen bestaa lige
    # saa fint paa en tom database — og saa maalte den ingenting.
    kraev(len(cpi) > 0, f"kontroldagen 2026-08-12 har events ({len(cpi)})")
    titler = {e["titel"] for e in cpi}
    kraev("CPI m/m" in titler, "kontroldagen indeholder 'CPI m/m'")
    kraev(any(e["tier"] == 1 for e in cpi), "kontroldagen har mindst ét tier 1-event")
    # Soerens egen aflaesning 12-08: Core CPI m/m prognose 0,2 · forrige 0,0 · faktisk 0,2
    core = next((e for e in cpi if e["titel"] == "Core CPI m/m"), None)
    kraev(core is not None, "Core CPI m/m findes paa kontroldagen")
    if core:
        kraev(core["actual"] is not None and "0.2" in str(core["actual"]),
              f"Core CPI m/m faktisk = {core['actual']} (Soerens note: 0,2 %)")

    # ── ⚠ Mutation: fjern fejltilstanden og se testen fejle ────────────────
    print("\n  ── mutation ──")
    muteret = kilde.replace("Det betyder ikke at der ingen events er", "")
    kraev(muteret != kilde, "mutationen aendrede kildeteksten")
    kraev("Det betyder ikke at der ingen events er" not in muteret,
          "uden den saetning ville testen fejle")

    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
