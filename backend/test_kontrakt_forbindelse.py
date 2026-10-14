"""
test_kontrakt_forbindelse.py — NT8-ordrer maa ikke kraeve en forbindelse maskinen ikke har
════════════════════════════════════════════════════════════════════════════════
Fundet 07-10-2026, da Ibens workstation blev armet til NT8.

NT8 vil have "MES 12-26", og kontraktmaaneden hentes fra IBKR fordi der ikke
findes nogen lokal rullekalender. Opslaget laa paa `strategy_manager` —
altsaa ALTID den delte forbindelse paa 127.0.0.1:7497.

⚠ Soerens maskine har den. Ibens har det IKKE. Hun koerer en IB Gateway paa
4002 som `ordre_forbindelse`, og maalt paa hendes maskine lyttede INTET paa
7497. `handels_forbindelse()`s egen docstring sagde det allerede: "hun har
slet ingen delt forbindelse".

⚠ OG AFHAENGIGHEDEN VAR NY. Hendes futures gik hidtil til IBKR gennem netop
Gatewayen, saa 7497 var ligegyldig. Det var selve armeringen af NT8 der
indfoerte kravet — en maskine der virkede i gaar, ville fejle i dag, og kun
paa hendes. Hendes foerste MES-klik var blevet til "Kan ikke afgoere
kontraktmaaneden": sikkert, ingen ordre sendt, men uhandlet.

Testen daekker begge maskiner. En rettelse der kun er set virke i det ene
tilfaelde, er ikke set virke.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import main
import ordre_forbindelse

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


class FalskConn:
    """Minimal IBKR-forbindelse. Husker hvem den er, saa den kan kendes igen."""

    def __init__(self, navn: str):
        self.connected = True
        self.navn = navn


class FalskStrategyManager:
    """Den delte forbindelse. Taeller om nogen forsoegte at rejse den."""

    def __init__(self, conn=None):
        self._conn = conn
        self.connect_kald = 0

    async def connect_ibkr(self, paper_trading: bool = True):
        self.connect_kald += 1
        return self._conn is not None

    def get_ibkr(self):
        return self._conn


def _opsaet(*, via_ordre: bool, ordre_conn=None, ordre_fejl=None,
            delt_conn=None):
    """Erstat de to moduler main spoerger. Returnerer (sm, gendan)."""
    gl_konf = ordre_forbindelse.konfigureret
    gl_hent = ordre_forbindelse.hent
    gl_sm = main.strategy_manager

    async def hent(genforbind: bool = True, tving: bool = False):
        hent.tving_set.append(tving)
        if ordre_fejl is not None:
            raise ordre_fejl
        return ordre_conn
    hent.tving_set = []

    ordre_forbindelse.konfigureret = lambda: via_ordre
    ordre_forbindelse.hent = hent
    sm = FalskStrategyManager(delt_conn)
    main.strategy_manager = sm

    def gendan():
        ordre_forbindelse.konfigureret = gl_konf
        ordre_forbindelse.hent = gl_hent
        main.strategy_manager = gl_sm

    return sm, hent, gendan


async def koer() -> None:
    # ── 1. Ibens maskine: ordre-Gateway, ingen delt forbindelse ──────────
    print("\n  -- Ibens maskine: Gateway paa 4002, intet paa 7497 --")
    gw = FalskConn("gateway-4002")
    sm, hent, gendan = _opsaet(via_ordre=True, ordre_conn=gw, delt_conn=None)
    try:
        conn, fejltekst = await main.kontrakt_forbindelse()
        kraev(conn is gw,
              f"⚠ kontrakten slaas op paa ORDRE-Gatewayen ({getattr(conn, 'navn', conn)})")
        kraev(fejltekst == "", "…og der er ingen fejltekst")
        kraev(sm.connect_kald == 0,
              "⚠ …og den delte forbindelse roeres IKKE. Den findes ikke paa "
              "hendes maskine, og et forsoeg ville koste et timeout pr. klik")
        kraev(hent.tving_set == [True],
              "⚠ …og afkoelingen springes over — et MENNESKE venter paa svaret")
    finally:
        gendan()

    # ── 1b. Baggrundsopgaven venter ingen paa ───────────────────
    # ⚠ Kontraktmaaneden opdateres hvert minut af en baggrundsopgave. Den maa
    # IKKE springe afkoelingen over: gjorde den det, bankede den paa en Gateway
    # der lige har svaret nej, tres gange i timen. tving er til ordreklik, hvor
    # et menneske sidder og venter.
    print()
    print("  -- baggrundsopgaven respekterer afkoelingen --")
    gw2 = FalskConn("gateway-4002")
    sm, hent, gendan = _opsaet(via_ordre=True, ordre_conn=gw2, delt_conn=None)
    try:
        conn, _ = await main.kontrakt_forbindelse(tving=False)
        kraev(conn is gw2, "ogsaa baggrundsopgaven faar ORDRE-Gatewayen")
        kraev(hent.tving_set == [False],
              f"⚠ …men UDEN tving — afkoelingen respekteres ({hent.tving_set})")
    finally:
        gendan()

    # ── 2. Soerens maskine: ingen ordre_forbindelse, delt forbindelse ────
    print("\n  -- Soerens maskine: delt forbindelse paa 7497 --")
    delt = FalskConn("delt-7497")
    sm, hent, gendan = _opsaet(via_ordre=False, delt_conn=delt)
    try:
        conn, fejltekst = await main.kontrakt_forbindelse()
        kraev(conn is delt,
              f"uaendret: den DELTE bruges ({getattr(conn, 'navn', conn)})")
        kraev(fejltekst == "", "…og ingen fejltekst")
        kraev(sm.connect_kald == 1,
              "⚠ …og den rejses foerst. connect_ibkr er selv-helende, og uden "
              "kaldet ville en doed forbindelse fra i nat blive brugt som den var")
        kraev(hent.tving_set == [],
              "…og ordre-Gatewayen spoerges slet ikke")
    finally:
        gendan()

    # ── 3. Gatewayen er nede → egen fejltekst, ikke en kontraktfejl ──────
    print("\n  -- Gatewayen svarer ikke --")
    sm, hent, gendan = _opsaet(
        via_ordre=True,
        ordre_fejl=ordre_forbindelse.OrdreForbindelseFejl(
            "kunne ikke forbinde til Gateway paa 127.0.0.1:4002"))
    try:
        conn, fejltekst = await main.kontrakt_forbindelse()
        kraev(conn is None, "ingen forbindelse -> None, ikke et halvt objekt")
        kraev("4002" in fejltekst,
              f"⚠ …OG fejlen foelger med, saa beskeden kan sige HVAD der var "
              f"galt: {fejltekst[:46]}")
        kraev(sm.connect_kald == 0,
              "⚠ …og der faldes IKKE tilbage paa den delte. Paa hendes maskine "
              "findes den ikke, og et tavst fald tilbage ville give "
              "'kan ikke afgoere kontraktmaaneden' om en Gateway der er nede")
    finally:
        gendan()

    # ── 4. Delt forbindelse findes slet ikke → None, ikke en undtagelse ──
    print("\n  -- hverken Gateway eller delt forbindelse --")
    sm, hent, gendan = _opsaet(via_ordre=False, delt_conn=None)
    try:
        conn, fejltekst = await main.kontrakt_forbindelse()
        kraev(conn is None, "ingenting tilgaengeligt -> None")
        kraev(sm.connect_kald == 1, "…men der blev forsoegt")
    finally:
        gendan()


async def test_kontraktmaaned() -> None:
    """⚠ KONTRAKTMAANEDEN SKAL KUNNE SLAAS OP UDEN EN DELT FORBINDELSE.

    Maalt 14-10 paa Ibens workstation: STOP/TARGET/TRAIL dukkede aldrig op i
    ordrefanen, mens de virkede fint hos Soeren. Begge koerte samme exe og
    samme kode.

    `exit_mulig` kraever at kontraktmaaneden er slaaet op, og opslaget brugte
    `strategy_manager.get_ibkr()` — den DELTE forbindelse. Hos hende er den
    aldrig forbundet (Gateway paa 4002, intet paa 7497), saa navnet forblev
    None og knapperne kunne ALDRIG vises. Hos Soeren er der TWS paa 7497.

    ⚠ Samme fejl som 07-10, det andet sted: dengang blev ORDRESTIEN lagt om
    til kontrakt_forbindelse(), men baggrundsopgaven blev glemt.
    """
    print()
    print("  -- kontraktmaaneden uden delt forbindelse --")

    class Kontrakt:
        pass

    gl_inst = dict(main._EXIT_INSTRUMENT)
    gl_nt = main.nt_forbindelse.nt_instrument
    main.nt_forbindelse.nt_instrument = lambda sym, k: "MES 12-26"  # type: ignore
    try:
        # ⚠ KERNEN: Ibens opsaetning — ordre-Gateway, INGEN delt forbindelse.
        gw = FalskConn("gateway-4002")
        gw.qualify_future = lambda sym: _svar(Kontrakt())   # type: ignore
        sm, hent, gendan = _opsaet(via_ordre=True, ordre_conn=gw, delt_conn=None)
        try:
            main._EXIT_INSTRUMENT["navn"] = None
            navn = await main.opdater_kontraktmaaned()
            kraev(navn == "MES 12-26",
                  f"⚠ maaneden slaas op paa ORDRE-Gatewayen ({navn})")
            kraev(main._EXIT_INSTRUMENT["navn"] == "MES 12-26",
                  "⚠ …og gemmes, saa exit_mulig kan blive sand")
            kraev(sm.connect_kald == 0,
                  "⚠ …og den delte forbindelse roeres ikke")
        finally:
            gendan()

        # Soerens opsaetning: ingen ordre_forbindelse, delt forbindelse oppe.
        print()
        delt = FalskConn("delt-7497")
        delt.qualify_future = lambda sym: _svar(Kontrakt())  # type: ignore
        sm, hent, gendan = _opsaet(via_ordre=False, delt_conn=delt)
        try:
            main._EXIT_INSTRUMENT["navn"] = None
            navn = await main.opdater_kontraktmaaned()
            kraev(navn == "MES 12-26", f"uaendret paa den delte ({navn})")
        finally:
            gendan()

        # ⚠ Og et fejlet opslag maa ikke SLUKKE knapperne. En maaned fra for et
        # minut siden er stadig rigtig; kontrakten skifter fire gange om aaret.
        print()
        sm, hent, gendan = _opsaet(via_ordre=True, ordre_conn=None,
                                   ordre_fejl=RuntimeError("Gateway nede"))
        try:
            main._EXIT_INSTRUMENT["navn"] = "MES 12-26"
            navn = await main.opdater_kontraktmaaned()
            kraev(navn == "MES 12-26",
                  "⚠ Gateway nede -> maaneden BEHOLDES, knapperne slukker ikke")
        finally:
            gendan()
    finally:
        main.nt_forbindelse.nt_instrument = gl_nt           # type: ignore
        main._EXIT_INSTRUMENT.clear(); main._EXIT_INSTRUMENT.update(gl_inst)


async def _svar(v):
    return v


def main_() -> int:
    print("  ── kontraktopslagets forbindelse ──")
    asyncio.run(koer())
    asyncio.run(test_kontraktmaaned())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main_())
