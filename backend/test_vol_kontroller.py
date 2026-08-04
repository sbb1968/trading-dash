"""
test_vol_kontroller.py — Revision G: enhver kontrols FEJLVEJ skal vaere koert
═══════════════════════════════════════════════════════════════════════════════════
Tre gange i dette projekt har samme sygdom vist sig: en kontrol hvis udfald er
strukturelt afgjort paa forhaand. Den ser ud som en test, rapporterer som en test, og
kan ikke fejle.

  · den forrige regime-motors "≥3 af 4 etiketter"  — bestod paa hvid stoej, 200/200
  · `if q:` paa qualifyContractsAsync             — listen er altid truthy
  · `"2 kopieret" in output`                      — matcher ogsaa "102 kopieret"

Reglen herefter: **formulér det input der VILLE faa kontrollen til at fejle, og koer
det.** Kan man ikke formulere saadan et input, er det ikke en kontrol.

Denne fil er den regel anvendt paa byggeklodsens egne kontroller. Hver test herunder
fodrer en kontrol med noget den BURDE afvise, og verificerer at den afviser. Uden det
er kontrolfiksturerne i proberne blot kode ingen har set virke.

    python test_vol_kontroller.py
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime

# Python 3.14: skal staa foer ib_async-afhaengige moduler importeres
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import sys
from types import SimpleNamespace

import ibkr_kvalificer as ik
import vol_kvartalsjob as kj

FEJL: list[str] = []


def paastand(betingelse: bool, hvad: str) -> None:
    if betingelse:
        print(f"  OK    {hvad}")
    else:
        print(f"  FEJL  {hvad}")
        FEJL.append(hvad)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ibkr_kvalificer — den fejl der startede det hele
# ═══════════════════════════════════════════════════════════════════════════════
class FalskIB:
    """Efterligner ib_async's faktiske adfaerd, inklusive den farlige del."""

    def __init__(self, svar):
        self.svar = svar          # callable(contract) -> liste

    async def qualifyContractsAsync(self, contract):
        return self.svar(contract)

    async def reqContractDetailsAsync(self, contract):
        return self.svar(contract)


def kontrakt(con_id=0, **kw):
    return SimpleNamespace(conId=con_id, **kw)


print("\n[1] kvalificer_eller_none afviser den tomme skal")
# DET AFGOERENDE TILFAELDE: en ikke-tom liste med conId=0. Praecis hvad IBKR svarer
# for en purget kontrakt, og praecis hvad `if q:` sagde ja til.
ib = FalskIB(lambda c: [kontrakt(con_id=0)])
paastand(asyncio.run(ik.kvalificer_eller_none(ib, kontrakt())) is None,
         "liste med conId=0 -> None (dette er fejlen der kostede os naesten et arkiv)")
paastand([kontrakt(con_id=0)] and True,
         "… og bemaerk: den samme liste er truthy, saa `if q:` ville have sagt ja")

ib = FalskIB(lambda c: [kontrakt(con_id=12345)])
r = asyncio.run(ik.kvalificer_eller_none(ib, kontrakt()))
paastand(r is not None and r.conId == 12345, "liste med rigtigt conId -> kontrakten")

paastand(asyncio.run(ik.kvalificer_eller_none(FalskIB(lambda c: []), kontrakt())) is None,
         "tom liste -> None")


def sprael(c):
    raise RuntimeError("TWS svarer ikke")


paastand(asyncio.run(ik.kvalificer_eller_none(FalskIB(sprael), kontrakt())) is None,
         "undtagelse -> None (ikke et nedbrud)")
paastand(not ik.er_kvalificeret(kontrakt(con_id=0)), "er_kvalificeret(conId=0) = False")
paastand(ik.er_kvalificeret(kontrakt(con_id=7)), "er_kvalificeret(conId=7) = True")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Kvartalsjobbets kontrolfikstur — kasseringsvejen skal FYRE
# ═══════════════════════════════════════════════════════════════════════════════
print("\n[2] Kontrolfiksturet kasserer en kortlaegning der siger ja til alt")


class Detaljer:
    def __init__(self, con_id):
        self.contract = kontrakt(con_id=con_id)


ARGS = SimpleNamespace(symbols="MES,M2K", port=7497, client_id=99)

# Den kendt-negative kontrakt er elleve aar gammel. Svarer IBKR ja til DEN, svarer
# den ja til alt, og saa er hele resultatet stoej. Her fodres jobbet praecis saadan
# en IBKR — og skal kassere.
alt_lever = FalskIB(lambda c: [Detaljer(999)])
res = asyncio.run(kj.kortlaeg(ARGS, lambda s="": None, ib=alt_lever))
paastand(res == {}, "kortlaegningen KASSERET da den kendt-negative kvalificerede")


def realistisk(c):
    """En troværdig IBKR: alt aeldre end 202409 er purget."""
    ym = (c.lastTradeDateOrContractMonth or "")[:6]
    return [Detaljer(4242)] if ym >= "202409" else []


res = asyncio.run(kj.kortlaeg(ARGS, lambda s="": None, ib=FalskIB(realistisk)))
paastand(res != {}, "en trovaerdig IBKR giver et resultat frem for en kassering")
paastand(all(not pr.get("201503", False) for pr in res.values()),
         "… og den kendt-negative er ikke med som levende")
aeldste, yngste = kj.graense_af(res)
paastand(aeldste == "202409", f"aeldste levende korrekt udledt: {aeldste}")

print("\n[2b] Kontrolfiksturet maa ikke kassere naar alt er som det skal")
paastand(kj.KENDT_NEGATIV_YM == "201503",
         "den kendt-negative er en kontrakt der umuligt kan leve (2015)")

print("\n[3] graense_af kan udpege en graense — og siger fra naar den ikke kan")
paastand(kj.graense_af({}) == (None, None), "tomt resultat -> ingen graense paastaas")
paastand(kj.graense_af({"MES": {"202409": True, "202412": True}}) == ("202409", None),
         "alt lever -> ingen purget graense paastaas")
paastand(kj.graense_af({"MES": {"202406": False, "202409": True}}) == ("202409", "202406"),
         "graensen indrammes af begge sider")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. mangler_arkivering skal kunne sige "intet mangler" OG "dette mangler"
# ═══════════════════════════════════════════════════════════════════════════════
print("\n[4] mangler_arkivering peger kun paa udloebne, levende, uhoestede kontrakter")
from pathlib import Path
import tempfile

tmp = Path(tempfile.mkdtemp(prefix="kvartalstest_"))
(tmp / "data_harvest" / "mes_m2k_clean").mkdir(parents=True)
res = {"MES": {"202409": True, "202412": True, "202709": True}}
m = kj.mangler_arkivering(res, tmp)
navne = {ym for _s, ym, _a in m}
paastand("202409" in navne and "202412" in navne, "udloebne, levende, uhoestede med")
paastand("202709" not in navne, "en kontrakt der endnu ikke er udloebet er IKKE med")
paastand(m[0][1] == "202409", "aeldste — mest udsatte — foerst")

(tmp / "data_harvest" / "mes_m2k_clean" / "MES_202409_1min.csv").write_text("x", encoding="utf-8")
navne2 = {ym for _s, ym, _a in kj.mangler_arkivering(res, tmp)}
paastand("202409" not in navne2, "en hoestet kontrakt falder ud af listen")

res_purget = {"MES": {"202406": False}}
paastand(kj.mangler_arkivering(res_purget, tmp) == [],
         "en PURGET kontrakt staar ikke paa listen — den kan ikke reddes")

import shutil
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 70)
if FEJL:
    print(f"DUMPET — {len(FEJL)} fejl:")
    for f in FEJL:
        print(f"   · {f}")
    sys.exit(1)
print("ALLE TESTS BESTAAET")
