"""
test_nt_forbindelse.py — kan NinjaTrader-vagterne overhovedet spærre?
════════════════════════════════════════════════════════════════════════════════
`nt_forbindelse.py` er ordrevejen til NT8. Fire vagter står mellem en
watchlist-knap og en ordre. En vagt der aldrig er set sige nej, er ikke en vagt.

  V1  kontoen skrives EKSPLICIT i hver kommando — aldrig tom
  V2  kontoen skal være en kendt simulationskonto (medmindre tillad_live)
  V3  kontoen skal FINDES i ATI-strømmen, ikke kun i konfigurationsfilen
  V4  en ukendt konto i strømmen RÅBER OP (spærrer ikke — V1 dækker)

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
        ("DEMO8635291", False, "DEMO8635291 er Ibens egen demokonto"),
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

        print("\n  ── V4 · en ukendt konto RAABER OP, men spaerrer ikke ──")
        # ⚠ AENDRET 28-09. Foer kastede V4, saa ÉN ukendt konto standsede AL
        # NT8-handel, ogsaa paa Sim101. Live-kontoen dukker op i stroemmen i
        # samme oejeblik den finansieres — og markedsdata KRAEVER finansiering.
        # Vagten ville altsaa have spaerret Ibens paper-handel som foelge af en
        # handling der var noedvendig for at komme videre.
        #
        # Beskyttelsen var overfloedig: V1 skriver kontoen eksplicit i hver
        # kommando, saa en fremmed konto i NT8 kan ikke modtage vores ordre.
        NT._laes_raat = lambda sekunder=NT.LYT_SEK: STROEM_LIVE   # type: ignore
        accounts.nt_forbindelse = lambda: {"konto": "Sim101"}     # type: ignore
        pr = NT.klar()
        kraev(True, "⚠ live-kontoen i stroemmen SPAERRER IKKE laengere")
        adv = pr.get("advarsler") or []
        kraev(len(adv) == 1, f"…men der kommer en advarsel ({len(adv)})")
        if adv:
            kraev("2080414" in adv[0], f"    den navngiver kontoen: {adv[0][:52]}")
            kraev("Sim101" in adv[0],
                  "    …og siger hvor ordrerne FAKTISK gaar hen")
        kraev(pr["konto"] == "Sim101", "profilen peger stadig paa Sim101")

        # ⚠ Og uden fremmed konto maa der IKKE komme stoej. En advarsel ved
        # hver ordre holder man op med at se efter i loebet af tre dage.
        NT._laes_raat = lambda sekunder=NT.LYT_SEK: STROEM_SIM    # type: ignore
        pr = NT.klar()
        kraev(not (pr.get("advarsler") or []),
              "ingen advarsel naar stroemmen kun har kendte konti")

        # tillad_live tier ogsaa — saa er valget truffet bevidst.
        NT._laes_raat = lambda sekunder=NT.LYT_SEK: STROEM_LIVE   # type: ignore
        accounts.nt_forbindelse = lambda: {"konto": "Sim101",     # type: ignore
                                           "tillad_live": True}
        pr = NT.klar()
        kraev(not (pr.get("advarsler") or []),
              "tillad_live: true -> ingen advarsel, valget er truffet")
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

    # ── OIF-kommandoernes felter ──────────────────────────────────────────
    # ⚠ TRETTEN FELTER, OG PLADSEN BETYDER ALT. Et felt for lidt forskyder
    # resten: en stoppris ville lande i TIF-feltet, og NT8 ville enten afvise
    # eller — vaerre — laese noget andet end vi mente. Derfor taelles de her.
    print("\n  ── OIF-felternes placering ──")
    sendt: list[str] = []
    aegte_skriv = NT._skriv_oif
    try:
        NT._skriv_oif = lambda kmd, maerke, **kw: (                  # type: ignore
            sendt.append(kmd) or {"kommando": kmd, "logliner": []})

        NT.send_ordre(konto="DEMO8580770", instrument="MES 12-26",
                      action="SELL", antal=1, ordretype="STOPMARKET",
                      stop=6800.0, ordre_id="NTX1", oco="TDOCO9")
        f = sendt[-1].split(";")
        kraev(len(f) == 13, f"PLACE har 13 felter ({len(f)})")
        kraev(f[8] == "DAY", f"felt 9 = TIF ({f[8]!r})")
        # ⚠ Feltet har altid vaeret der og altid staaet tomt. Det er dét P1 proever.
        kraev(f[9] == "TDOCO9", f"⚠ felt 10 = OCO ({f[9]!r})")
        kraev(f[10] == "NTX1", f"felt 11 = ordre-id ({f[10]!r})")
        kraev(f[7] == "6800.0", f"felt 8 = stop ({f[7]!r})")
        kraev(f[6] == "", f"felt 7 (limit) er tomt paa en STOPMARKET")

        # Uden oco skal feltet vaere tomt — bagudkompatibelt med alt hidtil.
        sendt.clear()
        NT.send_ordre(konto="DEMO8580770", instrument="MES 12-26",
                      action="BUY", antal=1, ordre_id="NTM1")
        kraev(sendt[-1].split(";")[9] == "",
              "uden oco staar felt 10 tomt — som alle hidtidige ordrer")

        # ── CHANGE ────────────────────────────────────────────────────────
        print("\n  ── CHANGE ──")
        for kw, felt, vaerdi, hvad in [
            ({"stop": 6805.0},  7, "6805.0", "stop i felt 8"),
            ({"limit": 6820.0}, 6, "6820.0", "limit i felt 7"),
            ({"antal": 2},      4, "2",      "antal i felt 5"),
        ]:
            sendt.clear()
            NT.aendr("NTX1", **kw)
            f = sendt[-1].split(";")
            kraev(len(f) == 13, f"CHANGE har 13 felter ({len(f)})")
            kraev(f[felt] == vaerdi, f"{hvad} ({f[felt]!r})")
            kraev(f[10] == "NTX1", "    ordre-id i felt 11")

        # ⚠ En CHANGE uden aendringer maa ikke sendes: NT8 ville svare noget
        # der kunne laeses som "det gik godt".
        blev, besked = spaerrer(NT.aendr, "NTX1")
        kraev(blev, "⚠ aendr() uden antal/limit/stop sender INTET")
        kraev("aendrer intet" in besked, f"    og siger hvorfor: {besked[:52]}")
        blev, _ = spaerrer(NT.aendr, "")
        kraev(blev, "aendr() uden id spaerrer")
        blev, _ = spaerrer(NT.aendr, "NTX1", antal=0)
        kraev(blev, "antal 0 spaerrer")

        # ⚠ TRAILING FINDES IKKE SOM ORDRETYPE I OIF. En ordre med den type
        # ville blive laest og lydloest intet goere — derfor afvises den her.
        print("\n  ── ordretyper ──")
        blev, besked = spaerrer(NT.send_ordre, konto="DEMO8580770",
                                instrument="MES 12-26", action="SELL", antal=1,
                                ordretype="TRAILINGSTOP")
        kraev(blev, "⚠ TRAILINGSTOP afvises — OIF kender den ikke")
        kraev("trailing" in besked.lower(),
              f"    og peger paa loesningen: {besked[-60:]}")
        for t in ("MARKET", "LIMIT", "STOPMARKET", "STOPLIMIT"):
            sendt.clear()
            NT.send_ordre(konto="DEMO8580770", instrument="MES 12-26",
                          action="BUY", antal=1, ordretype=t)
            kraev(sendt[-1].split(";")[5] == t, f"{t} accepteres")
    finally:
        NT._skriv_oif = aegte_skriv                                  # type: ignore

    # ── afvent_aktiv: en levende ordre er ikke en faerdig ordre ───────────
    print("\n  ── afvent_aktiv ──")
    aegte_laes = NT._laes_raat
    try:
        NT._laes_raat = lambda sekunder=NT.LYT_SEK: (                # type: ignore
            "OrderStatus|A Working ")
        r = NT.afvent_aktiv("A", sekunder=0.05, oplaeg_sek=0.01)
        kraev(r["aktiv"] and not r["terminal"],
              f"Working -> aktiv, ikke terminal ({r['status']})")

        NT._laes_raat = lambda sekunder=NT.LYT_SEK: (                # type: ignore
            "OrderStatus|B Rejected ")
        r = NT.afvent_aktiv("B", sekunder=0.05, oplaeg_sek=0.01)
        kraev(r["terminal"] and not r["aktiv"],
              f"Rejected -> terminal, ikke aktiv ({r['status']})")

        # ⚠ Det vigtigste: tavshed bliver ikke til et svar.
        NT._laes_raat = lambda sekunder=NT.LYT_SEK: "ATI True "      # type: ignore
        r = NT.afvent_aktiv("C", sekunder=0.05, oplaeg_sek=0.01)
        kraev(not r["aktiv"] and not r["terminal"],
              "⚠ ingen status -> HVERKEN aktiv ELLER terminal")
        kraev(r["status"] == "", f"…og status er tom ({r['status']!r})")
    finally:
        NT._laes_raat = aegte_laes                                   # type: ignore

    # ── Ordre-id ──────────────────────────────────────────────────────────
    print("\n  ── ordre-id ──")
    a, b = NT.order_ref(), NT.order_ref()
    kraev(a.startswith("NTM"), f"id'et roeber kilden: {a}")
    kraev(len(a) <= 24, f"id'et er kort nok til NT8 ({len(a)} tegn)")

    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
