#!/usr/bin/env python3
r"""
test_ordre_afstemning.py — "ikke fyldt" var en paastand, ikke en maaling
════════════════════════════════════════════════════════════════════════════════
TRE ORDRER PAA DUQ441063 BLEV AFSKREVET SOM `ordre_ikke_fyldt`. ALLE TRE FYLDTE:

    13-08 16:45  ordre 29  PreSubmitted,  filled 0  ->  Filled @ 7827,25
    17-08 15:31  ordre 67  PreSubmitted,  filled 0  ->  Filled @ 7808,75
    18-08 16:30  ordre 76  PendingSubmit, filled 0  ->  Filled @ 7730,75

Journalen kiggede paa ordrestatus ét sekund efter afsendelsen og konkluderede.
Ordre-trackeren fulgte op og fik alle tre bekraeftet af IBKR. De to systemer stod
paa samme maskine med hvert sit svar, og ingen sammenlignede dem.

⚠ AT VENTE LAENGERE LOESER DET IKKE — DET FLYTTER KLIPPEKANTEN.
`await_fill_sec=15` havde fanget alle tre, men en ordre kan fylde paa det
sekstende sekund. Det er praecis reconcile-timeoutens fejl: budgettet var 30
sekunder, K2 brugte 32, og konsekvensen af at loebe toer var at fortsaette som om
man bestod. Derfor proever denne fil den kontrol der IKKE har en deadline:
afstemningen mod trackeren, som spoerger bagefter uanset hvor lang tid der gik.

⚠ OG DEN MAA IKKE FLAGE FOR MEGET. En afstemning der raaber op ved hver
almindelig handel, bliver slaaet fra i loebet af en uge — og saa er den vaerre end
ingen. Derfor er halvdelen af proeverne her kendt-positive: det normale skal
passere i stilhed.

    python test_ordre_afstemning.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import aiosqlite

import manuel_forensik as mf

FEJL: list[str] = []


def kraev(betingelse, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'}  {hvad}")
    if not betingelse:
        FEJL.append(hvad)


# ── Stubbe ──────────────────────────────────────────────────────────────────
class FakeJournal:
    """Rigtig sqlite bagved — saa SQL'en i _bogfoerte_ordre_ider ogsaa proeves.
    En attrap der returnerer det jeg forventer, ville have bestaaet uanset."""

    def __init__(self, db):
        self.db = db
        self.events: list[dict] = []

    async def log_event(self, **kw):
        self.events.append(kw)


async def _byg_journal(dir_: Path, raekker: list[tuple]) -> FakeJournal:
    db = await aiosqlite.connect(str(dir_ / "proeve.db"))
    await db.execute("CREATE TABLE trades (trade_id TEXT, source TEXT, payload TEXT)")
    for i, (kilde, payload) in enumerate(raekker):
        await db.execute("INSERT INTO trades VALUES (?,?,?)",
                         (f"t{i}", kilde, json.dumps(payload) if payload else None))
    await db.commit()
    return FakeJournal(db)


def ordre(oid, *, filled=1.0, bekraeftet=True, kilde="manual_watchlist",
          action="BUY", ticker="MES", pris=7730.75, tid="2026-08-18T16:30:43"):
    return {"order_id": oid, "source": kilde, "ticker": ticker, "action": action,
            "filled": filled, "avg_fill": pris, "bekraeftet": bekraeftet,
            "placed_at": tid, "status": "Filled" if filled else "PreSubmitted"}


# ════════════════════════════════════════════════════════════════════════════
def test_bogfoerte_flages_ikke():
    """Kendt-positiv: det normale skal passere i stilhed."""
    print("\n[1] Bogfoerte fyldninger")

    async def koer():
        d = Path(tempfile.mkdtemp(prefix="afstem_"))
        try:
            j = await _byg_journal(d, [
                ("manual", {"ibkr_order_id": 76}),          # entry
                ("manual", {"ibkr_order_id_exit": 77}),     # exit
            ])
            r = await mf.afstem_mod_tracker(j, [ordre(76), ordre(77, action="SELL")])
            await j.db.close()
            return r
        finally:
            shutil.rmtree(d, ignore_errors=True)

    r = asyncio.run(koer())
    kraev(r["ubogfoerte"] == [], "entry OG exit bogfoert -> intet flag")
    kraev(r["bekraeftede_fills"] == 2, "begge fyldninger blev set")
    kraev(r["bogfoerte_ordre_ider"] == 2, "begge id'er fundet i journalen")


def test_ubogfoert_fyldning_flages():
    """⚠ KENDT-NEGATIV: de tre rigtige sager, genspillet."""
    print("\n[2] Ubogfoerte fyldninger — 13/8, 17/8 og 18/8")

    async def koer():
        d = Path(tempfile.mkdtemp(prefix="afstem_"))
        try:
            # Journalen kender kun 68, 71 og 72 — praecis som i virkeligheden.
            j = await _byg_journal(d, [
                ("manual", {"ibkr_order_id_exit": 68}),
                ("manual", {"ibkr_order_id_exit": 71}),
                ("manual", {"ibkr_order_id": 72}),
            ])
            ordrer = [
                ordre(29, action="SELL", pris=7827.25, tid="2026-08-13T16:45:32"),
                ordre(67, pris=7808.75, tid="2026-08-17T15:31:22"),
                ordre(68, action="SELL", pris=7799.25, tid="2026-08-17T15:34:13"),
                ordre(71, action="SELL", pris=7797.25, tid="2026-08-17T15:34:37"),
                ordre(72, pris=7797.25, tid="2026-08-17T18:22:42"),
                ordre(76, pris=7730.75, tid="2026-08-18T16:30:43"),
            ]
            r = await mf.afstem_mod_tracker(j, ordrer)
            await j.db.close()
            return r
        finally:
            shutil.rmtree(d, ignore_errors=True)

    r = asyncio.run(koer())
    ider = [u["order_id"] for u in r["ubogfoerte"]]
    kraev(ider == [29, 67, 76], f"praecis 29, 67 og 76 flages (fik {ider})")
    kraev(r["ubogfoerte"][0]["avg_fill"] == 7827.25, "fyldprisen kommer med")
    kraev(r["ubogfoerte"][0]["tid"] < r["ubogfoerte"][-1]["tid"],
          "sorteret i tid, saa den foerste divergens staar oeverst")


def test_stoej_flages_ikke():
    """Kendt-positiv 2: kun BEKRAEFTEDE, FYLDTE, MANUELLE ordrer taeller."""
    print("\n[3] Hvad der IKKE er et hul")

    async def koer():
        d = Path(tempfile.mkdtemp(prefix="afstem_"))
        try:
            j = await _byg_journal(d, [])
            ordrer = [
                ordre(1, filled=0.0),                       # aldrig fyldt
                ordre(2, bekraeftet=False),                 # ubekraeftet gaet
                ordre(3, kilde="Konfluens 2"),              # algoens, anden vej
                ordre(4, kilde="BuyTheDip"),
            ]
            r = await mf.afstem_mod_tracker(j, ordrer)
            await j.db.close()
            return r
        finally:
            shutil.rmtree(d, ignore_errors=True)

    r = asyncio.run(koer())
    kraev(r["ubogfoerte"] == [], "ufyldt, ubekraeftet og algo-ordrer flages ikke")
    kraev(r["bekraeftede_fills"] == 0, "ingen af dem taelles som fyldning")
    # ⚠ Og den modsatte vej: en ubekraeftet ordre der SENERE bekraeftes, SKAL
    # flages. Ellers ville kontrollen kunne gemme et hul bag et manglende flag.
    r2 = asyncio.run(_med_bekraeftelse())
    kraev([u["order_id"] for u in r2["ubogfoerte"]] == [2],
          "samme ordre flages naar den bliver bekraeftet")


async def _med_bekraeftelse():
    d = Path(tempfile.mkdtemp(prefix="afstem_"))
    try:
        j = await _byg_journal(d, [])
        r = await mf.afstem_mod_tracker(j, [ordre(2, bekraeftet=True)])
        await j.db.close()
        return r
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_alarm_og_haendelse():
    """En afstemning ingen laeser, er ikke en kontrol."""
    print("\n[4] Alarm ved huller")

    import notifier
    aegte = notifier.alert_backend_error
    kaldt: list[str] = []

    async def fake(besked):
        kaldt.append(besked)

    notifier.alert_backend_error = fake

    async def koer():
        d = Path(tempfile.mkdtemp(prefix="afstem_"))
        try:
            j = await _byg_journal(d, [])
            await mf.alarmer_om_ubogfoerte(j, [ordre(76)])
            tom = FakeJournal(j.db)
            await mf.alarmer_om_ubogfoerte(
                tom, [ordre(1, filled=0.0)])          # intet hul
            await j.db.close()
            return j, tom
        finally:
            shutil.rmtree(d, ignore_errors=True)

    try:
        j, tom = asyncio.run(koer())
        kraev(len(kaldt) == 1, "hul -> praecis én alarm")
        kraev("7730.75" in kaldt[0] and "MES" in kaldt[0],
              "alarmen baerer ticker og fyldpris")
        kraev(any(e["event_type"] == "fills_uden_journalspor" for e in j.events),
              "og hændelsen staar i journalen, saa den kan findes bagefter")
        # ⚠ KENDT-NEGATIV: ingen huller -> INGEN stoej. En kontrol der raaber ved
        # hver handel, bliver slaaet fra i loebet af en uge.
        kraev(tom.events == [], "ingen huller -> ingen hændelse, ingen alarm")
    finally:
        notifier.alert_backend_error = aegte


def test_deterministisk_match():
    """⚠ INTET FUZZY MATCH. Samme ticker, samme pris, naesten samme tid — men
    forskellige ordre-id'er er forskellige ordrer."""
    print("\n[5] Matchet er paa id, ikke paa lighed")

    async def koer():
        d = Path(tempfile.mkdtemp(prefix="afstem_"))
        try:
            j = await _byg_journal(d, [("manual", {"ibkr_order_id": 46})])
            # 46 og 49 fyldte 1 sekund fra hinanden til SAMME pris (14-08, ægte).
            # Et match paa "symbol + pris + omtrent samme tid" ville se dem som én.
            r = await mf.afstem_mod_tracker(j, [
                ordre(46, action="SELL", pris=7827.00, tid="2026-08-14T15:14:19"),
                ordre(49, action="SELL", pris=7827.00, tid="2026-08-14T15:14:20"),
            ])
            await j.db.close()
            return r
        finally:
            shutil.rmtree(d, ignore_errors=True)

    r = asyncio.run(koer())
    kraev([u["order_id"] for u in r["ubogfoerte"]] == [49],
          "49 flages, 46 ikke — trods identisk pris og 1 sekunds forskel")


def test_taaler_daarlige_payloads():
    """Journalen indeholder gamle raekker uden payload og med skrald i."""
    print("\n[6] Robusthed")

    async def koer():
        d = Path(tempfile.mkdtemp(prefix="afstem_"))
        try:
            j = await _byg_journal(d, [
                ("manual", None),                     # ingen payload
                ("manual", {"noget_andet": 1}),       # ingen ordre-id
                ("manual", {"ibkr_order_id": "76"}),  # id som STRENG
            ])
            r = await mf.afstem_mod_tracker(j, [ordre(76)])
            await j.db.close()
            return r
        finally:
            shutil.rmtree(d, ignore_errors=True)

    r = asyncio.run(koer())
    kraev(r["ubogfoerte"] == [],
          "id gemt som streng matcher id gemt som tal (begge normaliseres)")


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("test_ordre_afstemning — begge retninger proeves")
    for f in (test_bogfoerte_flages_ikke, test_ubogfoert_fyldning_flages,
              test_stoej_flages_ikke, test_alarm_og_haendelse,
              test_deterministisk_match, test_taaler_daarlige_payloads):
        f()
    print("\n" + "=" * 70)
    if FEJL:
        print(f"{len(FEJL)} FEJL:")
        for x in FEJL:
            print(f"  · {x}")
        sys.exit(1)
    print("ALLE KONTROLLER BESTAAET")
    print("  · en bogfoert fyldning flages aldrig (ingen stoej)")
    print("  · en ubogfoert flages ALTID — uanset hvor lang tid der gik")
