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

    print("\n[6] Serie-udvalget daekker det laaste saet fra Revision A")
    navne = {s["navn"] for s in vh.SERIER}
    paastand(navne == {"SPY", "IWM", "VIX", "VIX3M", "VIX9D", "RVX", "VX"},
             f"syv serier: {', '.join(sorted(navne))}")
    intradag = {s["navn"] for s in vh.SERIER if "1 min" in s["bars"]}
    paastand(intradag == {"SPY", "IWM", "VIX"},
             "1-min kun for de tre A9 verificerede: SPY, IWM, VIX")
    paastand(all(s["hvorfor"] for s in vh.SERIER),
             "hver serie har en skreven grund til at vaere med")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")
