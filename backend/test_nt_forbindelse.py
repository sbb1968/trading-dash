"""
test_nt_forbindelse.py — kan NinjaTrader-vagterne overhovedet spærre?
════════════════════════════════════════════════════════════════════════════════
`nt_forbindelse.py` er ordrevejen til NT8. Fire vagter står mellem en
watchlist-knap og en ordre. En vagt der aldrig er set sige nej, er ikke en vagt.

  V1  kontoen skrives EKSPLICIT i hver kommando — aldrig tom
  V2  kontoen skal være en kendt simulationskonto (medmindre tillad_live)
  V3  kontoen skal FINDES i ATI-strømmen, ikke kun i konfigurationsfilen
  V4  en ukendt konto i strømmen råber op — blast radius kan have ændret sig

⚠ HVORFOR V1 ER MERE END PEDANTERI. IBKR binder en forbindelse til en konto;
ordrer arver den. **ATI har ingen forbindelse.** Kontoen er felt to i hver
OIF-kommando, og lades det tomt, bruger NT8 sin *Default account* — det der
tilfældigvis står i platformens dropdown. Konfigurationen kan altså sige én
ting og ordren gå et andet sted hen, uden at noget fejler.

⚠ OG HVORFOR "TOM STRØM" IKKE MÅ BLIVE TIL "INGEN KONTI".
Kunne `tilstand()` svare `{"konti": []}` når socket'en ikke svarede, ville V3
bestå ved at fejle — den ville konkludere at kontoen ikke findes og spærre,
hvilket ser rigtigt ud, men af den forkerte grund. Værre: en senere udgave
kunne vende logikken om. Derfor kaster den `NtTilstandUkendt`.
Det er samme fejlklasse som afstemningen der sagde "nul bogførte" fordi den
ikke kunne læse tabellen.

    python test_nt_forbindelse.py
"""
from __future__ import annotations

import sys

import accounts
import nt_forbindelse as NT

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


def spaerrer(fn, *a, **kw) -> tuple[bool, str]:
    """Kaster funktionen en vagt-fejl? Returnerer (ja/nej, beskeden)."""
    try:
        fn(*a, **kw)
        return False, ""
    except (NT.NtForbindelseFejl, NT.NtTilstandUkendt) as e:
        return True, str(e)


def main() -> int:
    # ── V2: konfigurationen, før noget røres ──────────────────────────────
    print("  ── V2 · paper/sim-bekraeftelse paa konfigurationen ──")
    for konto, forventet, hvorfor in [
        ("Sim101",      False, "Sim101 er en kendt simulationskonto"),
        ("DEMO8580770", False, "DEMO8580770 er Tradovates demokonto"),
        ("sim101",      False, "store/smaa bogstaver maa ikke spaerre en gyldig konto"),
        ("2080414",     True,  "⚠ LIVE-kontoen spaerres"),
        ("",            True,  "tom konto spaerres"),
    ]:
        blev, besked = spaerrer(NT.verificer_profil, {"konto": konto})
        kraev(blev == forventet, f"{hvorfor} (konto={konto!r})")
        if blev and konto:
            kraev(konto in besked or "konto" in besked.lower(),
                  f"    fejlen naevner hvad der er galt: {besked[:64]}")

    # ⚠ Og live SKAL kunne tillades bevidst — ellers er vagten en blokade,
    # ikke en kontrol, og nogen ville fjerne den helt den dag den er i vejen.
    blev, _ = spaerrer(NT.verificer_profil, {"konto": "2080414", "tillad_live": True})
    kraev(not blev, "tillad_live: true aabner bevidst for live-kontoen")

    # ── V1: kontoen i hver kommando ───────────────────────────────────────
    print("\n  ── V1 · kontoen skrives eksplicit ──")
    blev, besked = spaerrer(NT.send_ordre, konto="", instrument="MES 09-26",
                            action="BUY", antal=1)
    kraev(blev, "tom konto spaerrer send_ordre")
    kraev("Default account" in besked,
          f"    fejlen forklarer HVORFOR: {besked[:70]}")

    blev, _ = spaerrer(NT.send_ordre, konto="Sim101", instrument="MES 09-26",
                       action="BUY", antal=0)
    kraev(blev, "antal 0 spaerrer")
    blev, _ = spaerrer(NT.send_ordre, konto="Sim101", instrument="MES 09-26",
                       action="HOP", antal=1)
    kraev(blev, "ukendt action spaerrer")
    # ⚠ GTC er forbudt: en ordre vi ikke faar annulleret skal doe af sig selv.
    blev, besked = spaerrer(NT.send_ordre, konto="Sim101", instrument="MES 09-26",
                            action="BUY", antal=1, tif="GTC")
    kraev(blev, f"TIF=GTC spaerrer ({besked[:40]})")

    blev, _ = spaerrer(NT.annuller, "")
    kraev(blev, "annuller uden id spaerrer")
    blev, _ = spaerrer(NT.annuller_alt, "")
    kraev(blev, "annuller_alt uden konto spaerrer")

    # ── ⚠ Tom stroem maa ikke blive til "ingen konti" ─────────────────────
    print("\n  ── tom stroem er UKENDT, ikke tom ──")
    aegte = NT._laes_raat
    try:
        NT._laes_raat = lambda sekunder=NT.LYT_SEK: ""          # type: ignore
        blev, besked = spaerrer(NT.tilstand)
        kraev(blev, "en tom stroem KASTER frem for at svare {konti: []}")
        kraev("tomt" in besked.lower() or "opstart" in besked.lower(),
              f"    fejlen siger hvad der skete: {besked[:60]}")
    finally:
        NT._laes_raat = aegte                                   # type: ignore

    # ── V3/V4 mod en opdigtet stroem ──────────────────────────────────────
    print("\n  ── V3 · kontoen skal findes i virkeligheden ──")
    STROEM_SIM = ("2 Orders|Sim101  2 CashValue|Sim101 100000 "
                  "2 Orders|DEMO8580770  2 CashValue|DEMO8580770 50000 2 ATI True ")
    STROEM_LIVE = STROEM_SIM + "2 Orders|2080414  2 CashValue|2080414 5000 "
    STROEM_UDEN_ATI = STROEM_SIM.replace("ATI True", "ATI False")

    aegte_profil = accounts.nt_forbindelse
    try:
        def med(stroem: str, profil: dict):
            NT._laes_raat = lambda sekunder=NT.LYT_SEK: stroem   # type: ignore
            accounts.nt_forbindelse = lambda: dict(profil)       # type: ignore
            return spaerrer(NT.klar)

        blev, _ = med(STROEM_SIM, {"konto": "Sim101"})
        kraev(not blev, "Sim101 findes i stroemmen -> klar() gaar igennem")

        # ⚠ Kontoen skal BESTAA V2 for at V3 overhovedet naas — V2 koerer
        # foerst med vilje (det billige tjek foerst). DEMO8580770 er en kendt
        # simulationskonto, men findes ikke i DENNE stroem.
        kun_sim101 = "2 Orders|Sim101  2 CashValue|Sim101 100000 2 ATI True "
        blev, besked = med(kun_sim101, {"konto": "DEMO8580770"})
        kraev(blev, "en KENDT konto der ikke er i stroemmen spaerrer (V3)")
        kraev("FINDES IKKE" in besked, f"    og siger det tydeligt: {besked[:52]}")

        # Og en ukendt konto stoppes allerede af V2 — foer V3.
        blev, besked = med(STROEM_SIM, {"konto": "Sim999"})
        kraev(blev and "simulationskonti" in besked,
              "en UKENDT konto stoppes af V2, foer V3 naas")

        blev, besked = med(STROEM_UDEN_ATI, {"konto": "Sim101"})
        kraev(blev, "ATI slaaet fra spaerrer")
        kraev("ATI True" in besked, "    fejlen peger paa ATI-indstillingen")

        print("\n  ── V4 · en ukendt konto i stroemmen raaber op ──")
        blev, besked = med(STROEM_LIVE, {"konto": "Sim101"})
        kraev(blev, "⚠ live-kontoen dukker op i stroemmen -> spaerret")
        kraev("2080414" in besked, f"    og den navngives: {besked[:60]}")

        blev, _ = med(STROEM_LIVE, {"konto": "Sim101", "tillad_live": True})
        kraev(not blev, "tillad_live accepterer den bevidst")
    finally:
        NT._laes_raat = aegte                                   # type: ignore
        accounts.nt_forbindelse = aegte_profil                  # type: ignore

    # ── Instrumentnavnet: maaneden maa ALDRIG hardkodes ──────────────────
    print("\n  ── instrumentnavn ──")

    class Kontrakt:
        def __init__(self, udloeb="", lokal=""):
            self.lastTradeDateOrContractMonth = udloeb
            self.localSymbol = lokal

    kraev(NT.nt_instrument("MES", Kontrakt("20261218", "MESZ6")) == "MES 12-26",
          "udloebsdato -> 'MES 12-26'")
    kraev(NT.nt_instrument("MES", Kontrakt("", "MESZ6")) == "MES 12-26",
          "localSymbol alene giver samme svar (MESZ6 -> 12-26)")
    kraev(NT.nt_instrument("M2K", Kontrakt("20270319", "M2KH7")) == "M2K 03-27",
          "marts naeste aar -> 'M2K 03-27'")
    # ⚠ Den vigtigste: uden kontrakt GAETTES der ikke.
    blev, besked = spaerrer(NT.nt_instrument, "MES", None)
    kraev(blev, "uden kvalificeret kontrakt KASTES der — maaneden gaettes ikke")
    kraev("GAETTES" in besked or "gaettes" in besked.lower(),
          f"    og fejlen siger hvorfor: {besked[:58]}")
    blev, _ = spaerrer(NT.nt_instrument, "MES", Kontrakt("", ""))
    kraev(blev, "en ulaeselig kontrakt kaster ogsaa")

    # ⚠ MESU6 udloeb 18-09-2026. En konstant skrevet i august ville have
    # virket i seks uger og derefter vaeret tavst forkert.
    kraev(NT.nt_instrument("MES", Kontrakt("20260918", "MESU6")) == "MES 09-26",
          "den udloebne september-kontrakt oversaettes stadig korrekt")

    # ── Ordre-id ──────────────────────────────────────────────────────────
    print("\n  ── ordre-id ──")
    a, b = NT.order_ref(), NT.order_ref()
    kraev(a.startswith("NTM"), f"id'et roeber kilden: {a}")
    kraev(len(a) <= 24, f"id'et er kort nok til NT8 ({len(a)} tegn)")

    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
