"""
test_vol_harvest.py — V1's krav afproevet uden at roere IBKR
═══════════════════════════════════════════════════════════════════════════════════
Specen stiller fire krav til harvesten (afsnit 4, V1) plus kontrolfiksturet fra E2.
Alle fem afproeves her paa en falsk IB, saa de er verificeret FOER den lange hentning
saettes i gang — og saa fejlvejene faktisk er koert, jf. Revision G.

  1. Inkrementel opdatering  — anden koersel henter kun det nye, MAALT ikke antaget
  2. Manifest pr. serie      — med de IBKR-parametre der blev brugt
  3. Idempotens              — koer to gange, faa samme fil
  4. Noeglet paa DATA-dato   — ikke paa koerselsdato
  5. Kontrolfikstur begge veje — og BEGGE kasseringsveje demonstreret

    python test_vol_harvest.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import vol_harvest as vh

FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


def tavs(s=""):
    pass


def falsk_bar(t: datetime, pris: float = 100.0):
    return SimpleNamespace(date=t, open=pris, high=pris + 1, low=pris - 1,
                           close=pris + 0.5, volume=1000)


print("\n[1] siden_sidst — inkrementel naar der er data, dybt naar der ikke er")
paastand(vh.siden_sidst({}, date(2009, 8, 17)) == date(2009, 8, 17),
         "tom cache -> hent helt tilbage til referencestarten")
by = {datetime(2026, 7, 1).isoformat(): []}
paastand(vh.siden_sidst(by, date(2009, 8, 17)) == date(2026, 6, 28),
         "fyldt cache -> hent fra sidste bar minus tre dages overlap")
paastand(vh.siden_sidst(by, date(2026, 7, 15)) == date(2026, 7, 15),
         "aldrig laengere tilbage end det dybeste der giver mening")

print("\n[2] Cachen er noeglet paa DATA-dato, ikke paa koerselsdato")
tmp = Path(tempfile.mkdtemp(prefix="volharvest_"))
try:
    p = tmp / "vol_cache" / "TEST_1dag.csv"
    raekker = {}
    for i in range(10):
        t = (date(2026, 1, 5) + timedelta(days=i)).isoformat()
        raekker[t] = [t, 100, 101, 99, 100.5, 1000]
    vh.skriv_cache(p, raekker)
    foerste_indhold = p.read_text(encoding="utf-8")

    # "Koer igen" med NOEJAGTIG samme data — men senere paa dagen.
    vh.skriv_cache(p, vh.laes_cache(p))
    paastand(p.read_text(encoding="utf-8") == foerste_indhold,
             "genskrivning af samme data giver BIT-IDENTISK fil (idempotens)")
    paastand(len(vh.laes_cache(p)) == 10, "ti barer, ikke tyve — ingen dubletter")

    # Det forrige projekts fejl: havde noeglen vaeret koerselsdatoen, ville en
    # genkoersel have lagt ti NYE raekker med identiske maalinger.
    med_overlap = dict(vh.laes_cache(p))
    for i in range(8, 14):
        t = (date(2026, 1, 5) + timedelta(days=i)).isoformat()
        med_overlap[t] = [t, 100, 101, 99, 100.5, 1000]
    vh.skriv_cache(p, med_overlap)
    paastand(len(vh.laes_cache(p)) == 14,
             "overlappende hentning gav 14 barer, ikke 16 — de fire faelles blev dedupet")

    print("\n[3] Manifestet baerer IBKR-parametrene og fletter frem for at overskrive")
    vh.skriv_manifest(tmp, [{"instrument": "SPY", "barstoerrelse": "1 day",
                             "ibkr": {"useRTH": False, "conId": 756733}, "barer": 4000}])
    vh.skriv_manifest(tmp, [{"instrument": "IWM", "barstoerrelse": "1 day",
                             "ibkr": {"useRTH": False, "conId": 9579}, "barer": 4000}])
    import json
    man = json.loads((tmp / "vol_cache" / "manifest.json").read_text(encoding="utf-8"))
    navne = {s["instrument"] for s in man["serier"]}
    paastand(navne == {"SPY", "IWM"}, "anden skrivning slettede ikke den foerste")
    paastand(all("ibkr" in s for s in man["serier"]),
             "hver post baerer de IBKR-parametre der blev brugt")
    paastand(man["reference_start"] == "2009-08-17",
             "percentilreferencens start staar i manifestet (B4)")

    print("\n[4] Inkrementel hentning MAALES, ikke antages")
    # Falsk IB: leverer 5 dagsbarer omkring en fast dato, uanset hvad der spoerges om.
    class FalskIB:
        def __init__(self, bars): self.bars = bars; self.kald = 0
        async def reqHistoricalDataAsync(self, *a, **k):
            self.kald += 1
            return self.bars if self.kald <= 1 else []

    dage = [falsk_bar(datetime(2026, 3, 2) + timedelta(days=i)) for i in range(5)]
    spec = dict(navn="TESTX", art="stk", boers="SMART", bars=["1 day"], hvorfor="test")

    import ibkr_kvalificer
    ibkr_kvalificer.kvalificer_eller_none = (
        lambda ib, c, timeout=15.0: asyncio.sleep(0, result=SimpleNamespace(conId=1)))

    post1 = asyncio.run(vh.hent_serie(FalskIB(dage), spec, "1 day", tmp, tavs))
    paastand(post1["nye_barer"] == 5, f"foerste koersel: 5 nye barer (fik {post1['nye_barer']})")

    post2 = asyncio.run(vh.hent_serie(FalskIB(dage), spec, "1 day", tmp, tavs))
    paastand(post2["nye_barer"] == 0,
             f"anden koersel med SAMME data: 0 nye barer (fik {post2['nye_barer']})")
    paastand(post2["barer"] == 5, "og cachen indeholder stadig praecis 5 barer")

    nye_dage = dage + [falsk_bar(datetime(2026, 3, 7) + timedelta(days=i)) for i in range(3)]
    post3 = asyncio.run(vh.hent_serie(FalskIB(nye_dage), spec, "1 day", tmp, tavs))
    paastand(post3["nye_barer"] == 3,
             f"tredje koersel med 3 ekstra: 3 nye (fik {post3['nye_barer']})")

    print("\n[5] Kontrolfiksturet — BEGGE kasseringsveje demonstreret")

    class KvalHelper:
        """Styrer hvad kvalificeringen svarer, saa begge fejlveje kan fremkaldes."""
        def __init__(self, positiv_ok=True, negativ_kvalificerer=False):
            self.positiv_ok = positiv_ok
            self.negativ_kvalificerer = negativ_kvalificerer

        async def __call__(self, ib, contract, timeout=15.0):
            sym = getattr(contract, "symbol", "")
            if sym == vh.KENDT_NEGATIV_SYM:
                return SimpleNamespace(conId=999) if self.negativ_kvalificerer else None
            return SimpleNamespace(conId=1)

    class IBmedSvar:
        def __init__(self, bars): self.bars = bars
        async def reqHistoricalDataAsync(self, *a, **k): return self.bars

    # (a) sund: positiv giver barer, negativ kvalificerer ikke
    ibkr_kvalificer.kvalificer_eller_none = KvalHelper()
    ok = asyncio.run(vh.kontrolfikstur(IBmedSvar(dage), tavs))
    paastand(ok is True, "sund forbindelse -> kontrolfiksturet godkender")

    # (b) DOED FORBINDELSE: den kendt-positive giver ingen barer
    ok = asyncio.run(vh.kontrolfikstur(IBmedSvar([]), tavs))
    paastand(ok is False,
             "SPY uden barer -> KASSERET (det er forbindelsen, ikke markedet)")

    # (c) IBKR SIGER JA TIL ALT: den kendt-negative kvalificerer
    ibkr_kvalificer.kvalificer_eller_none = KvalHelper(negativ_kvalificerer=True)
    ok = asyncio.run(vh.kontrolfikstur(IBmedSvar(dage), tavs))
    paastand(ok is False, "et umuligt symbol kvalificerer -> KASSERET")

    print("\n[6] En AFKORTET serie maa ikke passere som faerdig")
    # Den faktiske fejl fra foerste koersel: ContFuture afviser endDateTime (fejl
    # 10339), saa kun det foerste vindue kom med — og hoesten meldte exit 0.
    grund = vh.for_kort({"foerste": "2025-08-04", "barer": 252}, date(2023, 11, 24))
    paastand(grund is not None and "AFKORTET" in grund,
             "VX med 252 barer fra 2025 -> fanget som afkortet")
    paastand("619 dage" in grund, f"og siger HVOR meget: {grund[-30:]}")
    paastand(vh.for_kort({"foerste": "2023-11-24"}, date(2023, 11, 24)) is None,
             "en HEL serie giver ingen advarsel — kontrollen raaber ikke bare altid")
    paastand(vh.for_kort({"foerste": "2009-09-15"}, date(2009, 8, 17)) is None,
             "nogle ugers slup tolereres (helligdagsklynger, rul)")
    paastand(vh.for_kort({"fejl": "ikke kvalificeret"}, date(2009, 8, 17))
             == "ingen barer hentet", "en serie uden barer fanges ogsaa")

    print("\n[6b] ContFuture bruger en varighedsstige, ALDRIG endDateTime")
    paastand(vh.CONTFUT_STIGE[0] == "5 Y" and len(vh.CONTFUT_STIGE) >= 4,
             f"stige fra langt mod kort: {vh.CONTFUT_STIGE}")
    vx = next(s for s in vh.SERIER if s["navn"] == "VX")
    paastand(vx["art"] == "contfut" and vx["forvent_fra"] == date(2023, 11, 24),
             "VX forventes fra V0's maalte ContFuture-graense")

    print("\n[7] Serie-udvalget daekker det laaste saet fra Revision A")
    navne = {s["navn"] for s in vh.SERIER}
    paastand(navne == {"SPY", "IWM", "VIX", "VIX3M", "VIX9D", "RVX", "VX"},
             f"syv serier: {', '.join(sorted(navne))}")
    intradag = {s["navn"] for s in vh.SERIER if "1 min" in s["bars"]}
    paastand(intradag == {"SPY", "IWM", "VIX"},
             "1-min kun for de tre A9 verificerede: SPY, IWM, VIX")
    paastand(all(s["hvorfor"] for s in vh.SERIER),
             "hver serie har en skreven grund til at vaere med")


    # ═══════════════════════════════════════════════════════════════════════
    # Genoptagelse af en AFBRUDT dyb hentning
    # ═══════════════════════════════════════════════════════════════════════
    # 1-min-hentningen tager timer og gaar BAGUD fra i dag. Bliver den afbrudt,
    # staar der en cache der kun daekker den NYESTE ende, fx 2025-02 -> 2026-08.
    #
    # Startpunktet blev beregnet alene af cachens nyeste bar. Genoptagelsen
    # hentede derfor tre dage, erklaerede sig faerdig og skrev et manifest der
    # meldte serien hjemme — med 4.793 af 5.329 dage aldrig hentet. Et
    # "genoptag" der ALTID konkluderer "faerdig" er en kontrol hvis udfald er
    # afgjort paa forhaand (Revision G).
    print("")
    print("[8] Genoptagelse: to huller, ikke ét")

    from datetime import date as _d, timedelta as _td

    def _cache(a, b):
        c, x = {}, a
        while x <= b:
            c[f"{x.isoformat()}T15:30:00-05:00"] = [1.0]
            x += _td(days=1)
        return c

    _I_DAG, _DYB = _d(2026, 8, 6), vh.INTRADAG_START

    _s = vh.segmenter({}, _DYB, _I_DAG)
    paastand(len(_s) == 1 and _s[0][0] == "" and _s[0][1] == _DYB,
             "tom cache -> ét segment der daekker det hele")

    _s = vh.segmenter(_cache(_d(2025, 2, 14), _d(2026, 8, 4)), _DYB, _I_DAG)
    paastand(len(_s) == 2, f"afbrudt hentning -> TO segmenter (fik {len(_s)})")
    paastand(_s[0][0] == "", "foerste segment lukker FRONT-hullet (nye dage)")
    paastand(_s[1][1] == _DYB,
             f"andet segment gaar helt til dybden {_DYB} (fik {_s[1][1]})")
    paastand(_s[1][0].date() > _d(2025, 2, 14),
             "bag-segmentet starter EFTER cachens aeldste — soemmen overlapper")

    _s = vh.segmenter(_cache(_DYB, _d(2026, 8, 4)), _DYB, _I_DAG)
    paastand(len(_s) == 1 and _s[0][0] == "",
             "komplet men et par dage bagud -> kun FRONT (daglig opdatering)")

    paastand(vh.segmenter(_cache(_DYB, _I_DAG), _DYB, _I_DAG) == [],
             "komplet og aktuel -> INTET at hente")

    # Kendt-negativ: vis at den GAMLE beregning ville have stoppet for tidligt,
    # saa testen ikke blot bekraefter sig selv.
    _gl = vh.siden_sidst(_cache(_d(2025, 2, 14), _d(2026, 8, 4)), _DYB)
    paastand(_gl > _d(2025, 1, 1),
             f"siden_sidst() alene ville stoppe ved {_gl} — derfor segmenter()")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")
