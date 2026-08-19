#!/usr/bin/env python3
r"""
test_salgsvagt.py — et manuelt salg maa ikke kunne aabne en short
════════════════════════════════════════════════════════════════════════════════
DEN 17. AUGUST 2026 KL. 13:34:37 STOD DER I LOGGEN:

    ibkr_order_placed      SELL 1 MES  ordre 71  Filled @ 7797,25
    exit_uden_aaben_entry  MES         "salg uden en aaben manuel entry"

Salget fyldte. Der var ingen aaben raekke. Systemet NAVNGAV problemet korrekt —
og gik videre. Fra det sekund var journalen én kontrakt ude af fase med brokeren:

    17-08 16:22   koeb bogfoert som NY long        lukkede i virkeligheden shorten
    18-08 15:01   salg bogfoert som exit -406,25   aabnede i virkeligheden en short
    19-08 06:39   manuelt koeb for at flade ud     slet ikke i journalen

Journalen sagde -493,75. Brokeren sagde -33,75. **460 dollar forkert**, og ingen
af de tre foelgehændelser var en fejl i sig selv — de var alle konsekvenser af den
ene kontrol der loggede i stedet for at stoppe.

⚠ AT FLYTTE KONTROLLEN ER IKKE NOK — DEN SKAL FLYTTES DEN RIGTIGE VEJ.
Den gamle spurgte JOURNALEN ("kender jeg en aaben raekke?"). Det er det forkerte
spoergsmaal: kender journalen den ikke, men brokeren HAR den, er salget lovligt —
det er netop oprydning. Vagten spoerger derfor BROKEREN.

⚠ OG DEN MODSATTE VEJ ER LIGE SAA VIGTIG. En vagt der ogsaa spaerrede LUKNINGER
ville fange én i en position man ikke kan komme ud af — vaerre end fejlen den
loeser. Det er praecis den fejl T2 fangede i reconcile-spaerringen. Begge retninger
proeves her.

    python test_salgsvagt.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manuel_forensik as mf

FEJL: list[str] = []


def kraev(betingelse, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'}  {hvad}")
    if not betingelse:
        FEJL.append(hvad)


# ── Stubbe ──────────────────────────────────────────────────────────────────
class FakeIBKR:
    """Kun det vagten roerer: connected + get_positions_reliable."""

    def __init__(self, positioner=None, paalideligt=True, connected=True, rejs=False):
        self._pos = positioner or []
        self._paalideligt = paalideligt
        self.connected = connected
        self._rejs = rejs
        self.account = "DUQ441063"

    async def get_positions_reliable(self):
        if self._rejs:
            raise TimeoutError("reqPositions timeout")
        return list(self._pos), self._paalideligt


def pos(ticker, antal):
    return {"ticker": ticker, "position": float(antal), "avg_cost": 0.0}


class FakeCursor:
    def __init__(self, raekke):
        self._r = raekke

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetchone(self):
        return self._r


class FakeDB:
    def __init__(self, raekke=None):
        self._r = raekke

    def execute(self, *a, **kw):
        return FakeCursor(self._r)


class FakeJournal:
    def __init__(self, aaben_raekke=None):
        self.db = FakeDB(aaben_raekke)
        self.events: list[dict] = []

    async def log_event(self, **kw):
        self.events.append(kw)


# ════════════════════════════════════════════════════════════════════════════
def test_lovlige_salg_slipper_igennem():
    """⚠ VIGTIGST AF ALT: vagten maa ikke kunne fange nogen i en position."""
    print("\n[1] Lovlige salg — vagten maa ALDRIG spaerre en aegte lukning")

    ok, besked, d = asyncio.run(mf.kontroller_salg(FakeIBKR([pos("MES", 1)]), "MES", 1))
    kraev(ok and d["kontrolleret"], "brokeren har 1, salg af 1 -> tilladt")

    ok, _, _ = asyncio.run(mf.kontroller_salg(FakeIBKR([pos("MES", 3)]), "MES", 1))
    kraev(ok, "brokeren har 3, salg af 1 (delvis) -> tilladt")

    ok, _, _ = asyncio.run(mf.kontroller_salg(FakeIBKR([pos("MES", 3)]), "MES", 3))
    kraev(ok, "brokeren har 3, salg af 3 (hele) -> tilladt")

    # ⚠ DEN GAMLE KONTROL HAVDE DEN HER BAGLAENS. Journalen kender ikke
    # positionen, men brokeren HAR den — det er en ujournaliseret position der
    # ryddes op, ikke en fejl. Vagten spoerger brokeren og ser derfor rigtigt.
    ok, _, d = asyncio.run(mf.kontroller_salg(FakeIBKR([pos("MES", 1)]), "MES", 1))
    kraev(ok, "position hos broker UDEN journalraekke -> tilladt (det er oprydning)")


def test_upaalideligt_opslag_spaerrer_ikke():
    """Kan vi ikke laese positionen, VED vi ikke at salget er forkert."""
    print("\n[2] Upaalidelige opslag — tillad, men maerk det")
    for ibkr, hvad in (
        (FakeIBKR([], paalideligt=False), "reliable=False"),
        (FakeIBKR([], connected=False), "ikke forbundet"),
        (FakeIBKR([], rejs=True), "opslaget rejser"),
    ):
        ok, _, d = asyncio.run(mf.kontroller_salg(ibkr, "MES", 1))
        kraev(ok, f"{hvad} -> salget tillades (ingen faelde)")
        kraev(d["kontrolleret"] is False and d.get("grund"),
              f"{hvad} -> markeret som ukontrolleret MED grund")

    # ⚠ Og en TOM men PAALIDELIG liste er noget helt andet end et fejlet opslag.
    # Blandes de to sammen, er vagten enten blind eller en faelde.
    ok, _, d = asyncio.run(mf.kontroller_salg(FakeIBKR([], paalideligt=True), "MES", 1))
    kraev(not ok and d["kontrolleret"] is True,
          "tom MEN paalidelig liste -> AFVIST (ikke det samme som et fejlet opslag)")


def test_salg_der_ville_aabne_short_afvises():
    """De kendt-negative — herunder selve hændelsen fra 17-08."""
    print("\n[3] Salg der ville aabne en short")

    # ⚠ 17-08 13:34:37 GENSPILLET. Brokeren var flad; salget fyldte alligevel.
    ok, besked, d = asyncio.run(mf.kontroller_salg(FakeIBKR([]), "MES", 1))
    kraev(not ok, "brokeren flad, salg af 1 -> AFVIST (17-08 13:34:37 genspillet)")
    kraev("AABNE en short" in besked, "og beskeden siger hvorfor")
    kraev(d["netto_hos_broker"] == 0, "og den siger hvad brokeren faktisk har")

    ok, besked, _ = asyncio.run(mf.kontroller_salg(FakeIBKR([pos("MES", 1)]), "MES", 2))
    kraev(not ok, "brokeren har 1, salg af 2 -> AFVIST")
    kraev("short paa 1" in besked, "og den siger hvor stor shorten ville blive")

    ok, _, _ = asyncio.run(mf.kontroller_salg(FakeIBKR([pos("MES", -1)]), "MES", 1))
    kraev(not ok, "allerede short -1, salg af 1 -> AFVIST (ville goere den vaerre)")

    # ⚠ EN ANDEN TICKERS POSITION TAELLER IKKE. Uden symbolfiltret ville en aaben
    # M2K-position have godkendt et MES-salg — og fejlen ville vaere usynlig indtil
    # den kostede noget.
    ok, _, d = asyncio.run(mf.kontroller_salg(FakeIBKR([pos("M2K", 5)]), "MES", 1))
    kraev(not ok and d["netto_hos_broker"] == 0,
          "M2K-position godkender IKKE et MES-salg")


def test_alarm_ved_exit_uden_aaben_entry():
    """Slipper et salg alligevel igennem, skal et menneske vaekkes."""
    print("\n[4] exit_uden_aaben_entry alarmerer")

    import notifier
    kaldt: list[str] = []
    aegte = notifier.alert_backend_error

    async def fake_alert(besked):
        kaldt.append(besked)

    notifier.alert_backend_error = fake_alert
    try:
        j = FakeJournal(aaben_raekke=None)          # ingen aaben raekke
        r = asyncio.run(mf.registrer_exit(
            j, FakeIBKR([]), symbol="MES", shares=1, fill_pris=7797.25,
            ordre_id=71, ordre_status="Filled", et_tz=None))
        kraev(r is None, "ingen aaben raekke -> returnerer None")
        typer = [e["event_type"] for e in j.events]
        kraev("exit_uden_aaben_entry" in typer, "hændelsen skrives stadig i journalen")
        # ⚠ KENDT-NEGATIV: FOER RETTELSEN VAR DET HER DEN ENESTE VIRKNING.
        # En hændelse ingen laeser, er ikke en kontrol.
        kraev(len(kaldt) == 1, "OG der sendes en alarm (det gjorde der ikke foer)")
        kraev("7797.25" in kaldt[0] and "MES" in kaldt[0],
              "alarmen baerer ticker og fyldpris, saa den kan handles paa")
    finally:
        notifier.alert_backend_error = aegte


def test_alarmfejl_vaelter_ikke_ordrestien():
    """En alarm der ikke kan sendes, maa ikke koste handlen — men skal ses."""
    print("\n[5] Alarmen er best-effort, tavsheden er ikke")

    import notifier
    aegte = notifier.alert_backend_error

    async def doed_alert(besked):
        raise RuntimeError("Telegram svarer ikke")

    notifier.alert_backend_error = doed_alert
    try:
        j = FakeJournal(aaben_raekke=None)
        r = asyncio.run(mf.registrer_exit(
            j, FakeIBKR([]), symbol="MES", shares=1, fill_pris=7797.25,
            ordre_id=71, ordre_status="Filled", et_tz=None))
        kraev(r is None, "fejlende alarm vaelter ikke kaldet")
        typer = [e["event_type"] for e in j.events]
        kraev("exit_uden_aaben_entry" in typer, "hændelsen staar der stadig")
        kraev("alarm_fejlede" in typer,
              "OG det staar at alarmen ikke naaede frem (ellers er tavshed usynlig)")
    finally:
        notifier.alert_backend_error = aegte


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("test_salgsvagt — begge retninger proeves")
    for f in (test_lovlige_salg_slipper_igennem,
              test_upaalideligt_opslag_spaerrer_ikke,
              test_salg_der_ville_aabne_short_afvises,
              test_alarm_ved_exit_uden_aaben_entry,
              test_alarmfejl_vaelter_ikke_ordrestien):
        f()
    print("\n" + "=" * 70)
    if FEJL:
        print(f"{len(FEJL)} FEJL:")
        for x in FEJL:
            print(f"  · {x}")
        sys.exit(1)
    print("ALLE KONTROLLER BESTAAET")
    print("  · et aegte salg kan ALTID sendes (ingen faelde)")
    print("  · et salg der ville aabne en short kan IKKE (17-08 genspillet)")
