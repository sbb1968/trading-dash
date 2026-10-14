"""
test_delt_forbindelse.py — har DENNE maskine en delt IBKR-forbindelse?
════════════════════════════════════════════════════════════════════════════════
Koden gik ud fra at hver maskine har en IBKR paa 127.0.0.1:7497. Soerens har
(TWS), algoserveren har. Ibens har IKKE — hendes gaar gennem en Gateway paa
4002. Antagelsen er hardkodet i ibkr_connect.py:28-30 og kan ikke konfigureres.

⚠ TRE FEJL AF SAMME SLAGS I OKTOBER, alle kun paa hendes maskine:
    07-10  kontraktmaaneden til NT8-ordrer (ordrestien)       051d79b
    14-10  kontraktmaaneden igen (baggrundsopgaven)           40f71d9
    14-10  _handelskonto() — journalens kontomaerke           denne spec

Og en fjerde konsekvens uden fejl: watchdog'en prober 7497 hvert 30. sekund,
finder intet, og staar permanent paa "TWS offline". Et roedt lys der altid
lyser, er ikke et lys.

`har_delt_forbindelse()` samler svaret ét sted. ⚠ Den er en MIDLERTIDIG
stedfortraeder — det rigtige svar hoerer i en `delt_forbindelse`-noegle i
account.yaml, som er udskudt fordi den skal rulles ud i haanden paa tre
maskiner. Indtil da udledes det, og denne test holder udledningen aerlig.
"""
from __future__ import annotations

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


def _maskine(rolle: str, har_ordreforbindelse: bool):
    """Stil maskinen op som en af de tre. Returnerer gendan-funktionen.

    ⚠ `identity` er en FROZEN dataclass — den kan ikke aendres, kun erstattes.
    Det er med vilje i produktionen: maskinens identitet skal ikke kunne
    skifte under en koersel.
    """
    import dataclasses
    gl_id = main.accounts.identity
    gl_konf = ordre_forbindelse.konfigureret
    main.accounts.identity = dataclasses.replace(gl_id, instance_role=rolle)
    ordre_forbindelse.konfigureret = lambda: har_ordreforbindelse

    def gendan():
        main.accounts.identity = gl_id
        ordre_forbindelse.konfigureret = gl_konf
    return gendan


def test_de_tre_maskiner() -> None:
    print("  -- de tre maskiner, som de er 14-10 --")

    # Soeren: workstation, TWS paa 7497, ingen ordre_forbindelse.
    g = _maskine("workstation", False)
    try:
        kraev(main.har_delt_forbindelse() is True,
              "Soeren (TWS paa 7497, ingen ordre-Gateway) -> HAR delt")
    finally:
        g()

    # Algoserveren: har delt, ingen ordre_forbindelse (maalt: forbindelse "delt").
    g = _maskine("algoserver", False)
    try:
        kraev(main.har_delt_forbindelse() is True,
              "algoserveren -> HAR delt")
    finally:
        g()

    # Iben: workstation, Gateway paa 4002 som ordre_forbindelse, intet paa 7497.
    g = _maskine("workstation", True)
    try:
        kraev(main.har_delt_forbindelse() is False,
              "⚠ Iben (Gateway 4002, intet paa 7497) -> har INGEN delt")
    finally:
        g()


def test_algoserveren_kan_ikke_slukkes() -> None:
    """⚠ ROLLE-VAGTEN. Den maskine der handler, maa ikke kunne miste sin
    watchdog paa grund af en UDLEDNING.

    `ordre_forbindelse.konfigureret()` svarer paa "har denne maskine en separat
    ordreforbindelse" — ikke paa "mangler den en delt". De to falder sammen paa
    alle tre maskiner i dag, men det er ikke samme spoergsmaal. Sætter nogen en
    ordre_forbindelse paa algoserveren, vil udledningen sige "ingen delt" —
    hvilket er forkert, og konsekvensen ville vaere at dens watchdog slukkede.
    """
    print()
    print("  -- rolle-vagten --")
    g = _maskine("algoserver", True)      # BEGGE dele sat
    try:
        kraev(main.har_delt_forbindelse() is True,
              "⚠ algoserver MED ordre_forbindelse -> stadig HAR delt")
    finally:
        g()


def test_tre_tilstande() -> None:
    """⚠ "ingen" er ikke "nede"."""
    print()
    print("  -- delt_forbindelse_tilstand: tre tilstande, ikke to --")

    class Conn:
        def __init__(self, c): self.connected = c

    gl_get = main.strategy_manager.get_ibkr

    def med(conn):
        main.strategy_manager.get_ibkr = lambda: conn

    try:
        g = _maskine("workstation", False)
        try:
            med(Conn(True))
            kraev(main.delt_forbindelse_tilstand() == "forbundet",
                  "delt findes og lever -> 'forbundet'")
            med(Conn(False))
            kraev(main.delt_forbindelse_tilstand() == "nede",
                  "delt findes men er nede -> 'nede'")
            med(None)
            kraev(main.delt_forbindelse_tilstand() == "nede",
                  "ingen forbindelse paa en maskine der SKULLE have en -> 'nede'")
        finally:
            g()

        g = _maskine("workstation", True)
        try:
            med(None)
            kraev(main.delt_forbindelse_tilstand() == "ingen",
                  "⚠ maskinen har ingen delt -> 'ingen', IKKE 'nede'")
            # ⚠ Og selv hvis der mod forventning laa en levende forbindelse,
            # er svaret stadig "ingen": maskinen er indrettet uden.
            med(Conn(True))
            kraev(main.delt_forbindelse_tilstand() == "ingen",
                  "…ogsaa selv om der skulle ligge en forbindelse")
        finally:
            g()
    finally:
        main.strategy_manager.get_ibkr = gl_get


def test_handelskonto() -> None:
    """⚠ Hul nummer tre: journalens kontomaerke kom fra den DELTE forbindelse.

    Docstringen sagde "den forbindelse ordrer FAKTISK gaar igennem". Paa Ibens
    maskine er det ordre-Gatewayen. Funktionen faldt tilbage paa konfigurationen
    og gav det RIGTIGE svar af den FORKERTE grund — latent indtil maskinen har
    to konti.
    """
    print()
    print("  -- _handelskonto: ordre-Gatewayen foerst --")

    class Conn:
        def __init__(self, konto, paper=True):
            self.connected, self.account, self.paper = True, konto, paper

    gl_get = main.strategy_manager.get_ibkr
    gl_konf = ordre_forbindelse.konfigureret
    gl_akt = ordre_forbindelse.aktuel
    try:
        # Ibens form: ordre-Gateway paa DUQ441063, delt forbindelse doed.
        ordre_forbindelse.konfigureret = lambda: True
        ordre_forbindelse.aktuel = lambda: Conn("DUQ441063")
        main.strategy_manager.get_ibkr = lambda: Conn("DUN748991")
        konto, _ = main._handelskonto()
        kraev(konto == "DUQ441063",
              f"⚠ kontoen kommer fra ORDRE-Gatewayen ({konto})")

        # ⚠ Og den maa IKKE kunne tage den deltes konto. Det var fejlen.
        #
        # ⚠ KONTOEN SKAL VAERE DISTINKT. Foerste udgave af denne test brugte
        # "DUN748991" som den deltes konto — men det er ogsaa aktiv_konto() paa
        # maskinen her, saa assertionen kunne ikke skelne "tog den delte" fra
        # "faldt tilbage paa konfigurationen". Den fejlede paa noget rigtigt.
        ordre_forbindelse.aktuel = lambda: None
        main.strategy_manager.get_ibkr = lambda: Conn("DELT_MAA_IKKE_BRUGES")
        konto, _ = main._handelskonto()
        kraev(konto != "DELT_MAA_IKKE_BRUGES",
              f"⚠ ingen levende ordre-Gateway -> IKKE den deltes konto ({konto})")
        kraev(konto == main.aktiv_konto(),
              f"…men maskinens konfigurerede konto ({konto})")

        # Soerens form: ingen ordre_forbindelse -> den delte, som hidtil.
        ordre_forbindelse.konfigureret = lambda: False
        main.strategy_manager.get_ibkr = lambda: Conn("DUN748991")
        konto, _ = main._handelskonto()
        kraev(konto == "DUN748991",
              f"uaendret uden ordre_forbindelse ({konto})")
    finally:
        main.strategy_manager.get_ibkr = gl_get
        ordre_forbindelse.konfigureret = gl_konf
        ordre_forbindelse.aktuel = gl_akt


def main_() -> int:
    print("  ── delt forbindelse ──")
    test_de_tre_maskiner()
    test_algoserveren_kan_ikke_slukkes()
    test_tre_tilstande()
    test_handelskonto()
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main_())
