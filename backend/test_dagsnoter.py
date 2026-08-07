"""
test_dagsnoter.py — dage uden handler
════════════════════════════════════════════════════════════════════════════════
Fire ting afgoer om baandet bliver brugbart frem for misvisende:

  1  Kun NYSE-handelsdage kan vaere huller. Ellers staar hver loerdag og hver
     helligdag som en uforklaret tom dag, og de faa rigtige drukner i de mange
     falske.
  2  Kun datointervallet filtrerer. Strategi og symbol maa IKKE roere baandet —
     ellers bliver man tilbudt at skrive "ferie" paa en dag man sad og handlede.
  3  Praecis én note pr. dag. "Ferie i uge 30" er ikke én note, det er fem.
  4  En note paa en dag der viser sig at have handler forsvinder ikke tavst.

Afsnit 7 viser at kontrollerne kan fejle. En kontrol hvis udfald er afgjort paa
forhaand er projektets tilbagevendende fejlklasse.

    python test_dagsnoter.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aiosqlite

import dagsnoter as dn

FEJL: list[str] = []
DB_FIL = "test_dagsnoter.db"

# Juli 2026: 1/7 = onsdag. 4/7 (uafhaengighedsdag) falder paa en LOERDAG og
# observeres derfor fredag 3/7 — den dag er NYSE lukket. Det goer maaneden til et
# godt maalepunkt: baade weekender og en forskudt helligdag i samme interval.
FRA, TIL = "2026-07-01", "2026-07-31"


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


async def ny_db():
    if os.path.exists(DB_FIL):
        os.remove(DB_FIL)
    db = await aiosqlite.connect(DB_FIL)
    await db.executescript(
        Path("db_schema.sql").read_text(encoding="utf-8"))
    await db.commit()
    return db


async def laeg_handel(db, dato, source="Konfluens 2", symbol="AAPL"):
    await db.execute(
        "INSERT INTO trades (trade_id, account_id, instance_id, ibkr_account, "
        "source, symbol, side, shares, entry_time_utc, entry_time_et, "
        "entry_price, capital_used) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"T{dato}{symbol}", "iben", "algoserver", "DUO509856",
         source, symbol, "long", 10,
         f"{dato}T14:30:00+00:00", f"{dato}T09:30:00", 100.0, 1000.0))
    await db.commit()


async def hoved():
    db = await ny_db()

    # Faste "i dag" saa testen ikke skifter opfoersel over tid. Uden det ville
    # fremtids-maerkningen vaere sand i dag og falsk om et aar.
    dn.idag_et = lambda: date(2026, 7, 15)

    print("\n1. Kun NYSE-handelsdage taeller som huller")
    b = await dn.byg_baand(db, FRA, TIL)
    datoer = [h["dato"] for h in b["huller"]]
    kraev(b["handelsdage"] == 22, f"juli 2026 har {b['handelsdage']} handelsdage")
    kraev(len(datoer) == 22, "uden handler er ALLE handelsdage huller")
    kraev("2026-07-04" not in datoer and "2026-07-05" not in datoer,
          "weekenden 4.-5. juli er ikke et hul")
    kraev("2026-07-03" not in datoer,
          "fredag 3/7 er lukket (4. juli falder paa en loerdag og observeres "
          "dagen foer) — en helligdag der IKKE er en fast dato")

    print("\n2. En dag med handel er ikke et hul")
    await laeg_handel(db, "2026-07-08")
    b = await dn.byg_baand(db, FRA, TIL)
    datoer = [h["dato"] for h in b["huller"]]
    kraev("2026-07-08" not in datoer, "8/7 forsvandt fra hullerne")
    kraev(len(datoer) == 21, f"21 huller tilbage (fik {len(datoer)})")

    print("\n3. ⚠ Strategi- og symbolfiltret roerer IKKE baandet")
    # K2 handlede ikke 9/7, men BuyTheDip gjorde. Regnede baandet huller ud fra
    # de filtrerede raekker, ville 9/7 staa som tom — og man ville faa tilbudt at
    # skrive "ferie" paa en dag der blev handlet.
    await laeg_handel(db, "2026-07-09", source="BuyTheDip", symbol="TSLA")
    b = await dn.byg_baand(db, FRA, TIL)
    datoer = [h["dato"] for h in b["huller"]]
    kraev("2026-07-09" not in datoer,
          "9/7 er ikke et hul selv om KUN BuyTheDip handlede")

    print("\n4. Fremtid og i dag er ikke huller man har overset")
    kort = {h["dato"]: h for h in b["huller"]}
    kraev(kort["2026-07-15"]["i_dag"] and not kort["2026-07-15"]["fremtid"],
          "15/7 er i dag")
    kraev(kort["2026-07-16"]["fremtid"], "16/7 er fremtid")
    kraev(not kort["2026-07-14"]["fremtid"] and not kort["2026-07-14"]["i_dag"],
          "14/7 er fortid — det ER et hul man har overset")

    print("\n5. Én note pr. dag — ferieugen bliver fem raekker")
    for d in ("2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"):
        kraev(await dn.saet_note(db, d, "Ferie — Mallorca") == "gemt", f"{d} gemt")
    b = await dn.byg_baand(db, FRA, TIL)
    med = [h for h in b["huller"] if h["note"] == "Ferie — Mallorca"]
    kraev(len(med) == 5, f"fem dage baerer noten (fik {len(med)})")

    # Samme dag igen overskriver — den maa ikke blive til to raekker.
    await dn.saet_note(db, "2026-07-20", "Ferie — hjemme alligevel")
    cur = await db.execute("SELECT COUNT(*) FROM dagsnoter WHERE dato = ?",
                           ("2026-07-20",))
    kraev((await cur.fetchone())[0] == 1, "gentagen skrivning giver stadig én raekke")

    print("\n6. Tom tekst sletter — den eneste vej til at fortryde")
    kraev(await dn.saet_note(db, "2026-07-20", "   ") == "slettet",
          "kun mellemrum = slet")
    b = await dn.byg_baand(db, FRA, TIL)
    kraev({h["dato"]: h["note"] for h in b["huller"]}["2026-07-20"] == "",
          "20/7 er tilbage uden note")

    print("\n7. En note paa en weekend afvises ved indgangen")
    # Den ville ligge i databasen uden nogen vej tilbage — baandet viser kun
    # handelsdage, saa den kunne aldrig ses eller rettes igen.
    for d, hvad in (("2026-07-04", "loerdag"), ("2026-07-03", "helligdag")):
        try:
            await dn.saet_note(db, d, "noget")
            kraev(False, f"{hvad} {d} blev accepteret")
        except ValueError:
            kraev(True, f"{hvad} {d} afvist")

    print("\n8. ⚠ En note paa en dag der VISER sig at have handler")
    await dn.saet_note(db, "2026-07-28", "Ferie — Mallorca")
    b = await dn.byg_baand(db, FRA, TIL)
    kraev(not b["noter_paa_handlede_dage"], "ingen konflikt endnu")
    await laeg_handel(db, "2026-07-28")          # hun kom hjem og handlede
    b = await dn.byg_baand(db, FRA, TIL)
    kraev([n["dato"] for n in b["noter_paa_handlede_dage"]] == ["2026-07-28"],
          "noten dukker op som konflikt, ikke som et hul")
    kraev("2026-07-28" not in [h["dato"] for h in b["huller"]],
          "og dagen er IKKE laengere et hul")
    kraev(b["noter_paa_handlede_dage"][0]["note"] == "Ferie — Mallorca",
          "teksten er med, saa man kan se hvad der skal rettes")

    print("\n9. Ugyldige intervaller afvises")
    for fra, til, hvad in (("2026-07-31", "2026-07-01", "til foer fra"),
                           ("ikke-en-dato", TIL, "vroevl som fra")):
        try:
            await dn.byg_baand(db, fra, til)
            kraev(False, f"{hvad} blev accepteret")
        except ValueError:
            kraev(True, f"{hvad} afvist")

    print("\n10. ⚠ Falsifikation — kontrollerne kan faktisk fejle")
    # Afsnit 1: uden kalenderen ville weekender staa som huller. Vis at
    # kontrollen ville fange det ved at spoerge kalenderen om det modsatte.
    from nyse_kalender import er_handelsdag
    kraev(not er_handelsdag(date(2026, 7, 4)) and er_handelsdag(date(2026, 7, 8)),
          "kalenderen skelner faktisk 4/7 fra 8/7 — afsnit 1 maalte ikke en "
          "kalender der bare sagde ja til alt")

    # Afsnit 3: den handel der gjorde 9/7 til en ikke-tom dag SKAL vaere den
    # eneste. Var der ogsaa en K2-handel, ville testen bestaa uanset filtrering.
    cur = await db.execute(
        "SELECT DISTINCT source FROM trades WHERE entry_time_et LIKE '2026-07-09%'")
    kilder = [r[0] for r in await cur.fetchall()]
    kraev(kilder == ["BuyTheDip"],
          f"9/7 har KUN BuyTheDip ({kilder}) — ellers var afsnit 3 groent uanset")

    # Afsnit 2: en dag uden handel skal stadig VAERE et hul, ellers maalte
    # afsnit 2 bare at listen var tom.
    kraev("2026-07-10" in [h["dato"] for h in b["huller"]],
          "10/7 er stadig et hul — listen er ikke bare tom")

    print("\n11. Dagsnoter bor paa algoserveren — maskinvaelgeren roerer dem ikke")
    # Modsat en handelsnote. En handel PRODUCERES af en maskine, saa noten skal
    # hjem til den. En fravaersdag har ingen maskine: "Iben holdt ferie" er sandt
    # paa alle maskiner samtidig. Ét sted, ellers kan samme dag have to noter.
    import dataclasses
    import main

    som_algoserver = dataclasses.replace(
        main.identity, instance_role="algoserver",
        replication_target_url="http://iben-algo:8000")
    som_workstation = dataclasses.replace(
        main.identity, instance_role="workstation",
        replication_target_url="http://iben-algo:8000")

    main.identity = som_algoserver
    kraev(main._dagsnote_vaert() is None, "paa algoserveren skrives lokalt")
    main.identity = som_workstation
    kraev(main._dagsnote_vaert() == "http://iben-algo:8000",
          f"paa en workstation sendes videre: {main._dagsnote_vaert()}")

    # Er algoserveren nede, maa der IKKE skrives lokalt som reserve. En note der
    # landede lokalt ville vaere usynlig for alle andre og se gemt ud alligevel.
    class DoedSession:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def put(self, *a, **kw): raise OSError("ingen forbindelse")

    _rigtig = main.aiohttp
    main.aiohttp = type("A", (), {"ClientSession": DoedSession,
                                  "ClientTimeout": staticmethod(lambda **k: None)})
    main.journal = type("J", (), {"db": db})()
    try:
        await main.journal_saet_dagsnote("2026-07-14",
                                         main.DagsnoteRequest(note="Ferie"))
        kraev(False, "der kom et svar — burde have kastet")
    except main.HTTPException as e:
        kraev(e.status_code == 503, f"503 naar algoserveren er nede (fik {e.status_code})")
        kraev("IKKE gemt" in e.detail, f"og det siges lige ud: {e.detail[:50]}")
    cur = await db.execute("SELECT COUNT(*) FROM dagsnoter WHERE dato = ?",
                           ("2026-07-14",))
    kraev((await cur.fetchone())[0] == 0,
          "⚠ og INTET blev skrevet lokalt som reserve")

    # Falsifikation: paa algoserveren SKAL samme kald lykkes — ellers maalte
    # kontrollen ovenfor bare at endpointet altid fejler.
    main.identity = som_algoserver
    svar = await main.journal_saet_dagsnote("2026-07-14",
                                            main.DagsnoteRequest(note="Ferie"))
    kraev(svar.get("handling") == "gemt",
          "samme kald paa algoserveren gemmer — 503'en kom fra nedetiden, "
          "ikke fra et endpoint der altid fejler")
    main.aiohttp = _rigtig

    await db.close()
    os.remove(DB_FIL)
    for ekstra in (DB_FIL + "-wal", DB_FIL + "-shm"):
        if os.path.exists(ekstra):
            os.remove(ekstra)


asyncio.run(hoved())

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")
