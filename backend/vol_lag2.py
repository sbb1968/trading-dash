"""
vol_lag2.py — lag 2: dagens tilstand, afgivet FØR åbning
════════════════════════════════════════════════════════════════════════════════
Spec v2.1. Lag 1 svarer på hvilket regime vi står i; lag 2 svarer på hvad vi
forventer af DAGEN, og det svar skal ligge før klokken 09:30.

Tre komponenter, lige vægtede og præregistrerede:

  L2-K1  natbevægelse    |gap| / typisk range
  L2-K2  gårsdagens range RTH-range(d−1) / typisk range
  L2-K3  baggrundstilstand lag 1's score på d−1

⚠ K2 ER BENCHMARKEN SELV. Det er med vilje: testen spørger om K1 og K3 tilføjer
noget ud over gårsdagens range. Gør de ikke det, er svaret at bruge K2 alene, og
det er et gyldigt udfald — ikke en fejl der skal skjules bag en sammenvejning.

────────────────────────────────────────────────────────────────────────────────
⚠ HVORFOR ALT UDLEDES AF 1-MIN-SÆTTET OG IKKE AF SPY_1dag.csv

Specen skriver målet som `High(d) − Low(d)` på SPY's dagsbarer og gappet som
`Open(d) − Close(d−1)`. Dagsserien i vol_cache er hentet med **useRTH=False**, og
forskellen er ikke akademisk — den er målt på de 3.668 fælles dage:

    dagsbar-range mod ægte RTH-range   median +15,9 %   90-pct +68,8 %   max +1625 %
    dagsbar-open mod RTH-open 09:30    median  0,18 %   90-pct   0,61 %  max  5,1 %

Målet ville altså måle noget andet end RTH-range — det ville rumme nat, for- og
eftermarked — og gappet ville bære en fejl af samme størrelsesorden som selve
gappet. Begge udledes derfor af SPY_1min.csv, der ER useRTH=True.

Prisen er ærlig: udviklingsperioden bliver 2012 → 2023 i stedet for 2010 → 2023,
fordi 1-min-sættet ikke rækker længere tilbage. Cirka 3.000 handelsdage i stedet
for 3.500. Det er rigeligt, og alternativet var at måle det forkerte i to år mere.

────────────────────────────────────────────────────────────────────────────────
⚠ SUBSTITUTIONEN, OG HVAD DEN KOSTER I UDVIKLING

Lag 2's tiltænkte kerneinput var futures' natbevægelse klokken 09:00. Den findes
først fra 2024-06-21, og udviklingsperioden slutter 2023-12-31 — overlappet er
NUL dage. Derfor udvikles på SPY's åbningsgap, som er samme information en halv
time senere.

Det betyder at udviklingsproxyen kender noget produktionsinputtet ikke gør:
åbningskursen klokken 09:30. Det er en bevidst, dokumenteret afvigelse, ikke en
forglemmelse — og den er netop grunden til at `substitutionstest()` findes.
Rækker futures-data engang, måles Spearman mellem MES kl. 09:00 og SPY's gap kl.
09:30; forventningen er over 0,95. Holder den ikke, er lag 2's fundament ikke det
vi troede, og det skal vi vide frem for at tro noget forkert.

    python vol_lag2.py
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import statistics as st
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pytz

import vol_lag1 as l1
import vol_percentil as vp
from nyse_kalender import handelsdage

ET = pytz.timezone("America/New_York")
ROD = Path(__file__).parent
CACHE = ROD / "vol_cache"

# ── Præregistreret konfiguration ────────────────────────────────────────────
# Ændres noget her, ændres config_hash, og et gammelt resultat kan ikke længere
# forveksles med et nyt. Samme regel som lag 1.

INSTRUMENT = "SPY"
KOMPONENTER = ("nat_pctl", "igaar_range_pctl", "lag1_pctl")
VAEGTE = {k: 1.0 for k in KOMPONENTER}      # lige vægte — ingen optimering

# Længden på "typisk range". Valgt i udviklingsperioden og logget her, ikke
# justeret bagefter. 20 handelsdage ≈ en måned: langt nok til at et enkelt
# udfald ikke dominerer, kort nok til at følge et regimeskift.
TYPISK_VINDUE = 20

# Normalisering af K1 og K2. "typisk_range" er specens ordlyd (§2) og det
# præregistrerede valg. "forrige_luk" er den variant testen afdækkede: divisionen
# med typisk range fjerner NIVEAUET, og niveauet er netop det der forudsiger
# morgendagens range. Målt isoleret på udviklingsperioden:
#
#     rå range(d−1) / luk(d−2)          +0,6361   ← benchmarken
#     percentil af range/luk            +0,6239   ← percentilering koster 0,012
#     percentil af range/typisk range   +0,2431   ← normaliseringen koster 0,38
#
# ⚠ Standarden ændres IKKE her. Specen erklærer i §2 at "K2 er benchmarken selv";
# det er den ikke som skrevet, og den modsigelse hører til i v2.2 frem for at
# blive rettet stiltiende efter at resultatet er set.
NORMALISERING = "typisk_range"

# Klassegrænser. ⚠ SAT MOD DEN MÅLTE FORDELING, FØR DEN PRÆDIKTIVE TEST BLEV
# KØRT FØRSTE GANG (spec §7 punkt 5). Rækkefølgen er hele pointen: at vælge
# grænser efter en fordeling er fordelingsfornuft; at vælge dem efter et
# testresultat er at søge et pænere tal.
#
# Mål-fordelingen er lag 1's: 20 % / 50 % / 20 % / 10 %. Et gæt på 25/57/78 gav
# 6,6 / 49,9 / 32,2 / 11,3 — for få "rolige" og for mange "livlige", fordi
# gennemsnittet af tre percentiler trækker mod midten på samme måde som lag 1's
# fire gjorde. De målte 20./70./90. percentiler af scoren er 36,2 / 64,0 / 79,1,
# og det er dem der står her.
#
# Kompressionen er reel og skal med videre: se fordeling() og spec §6.
GRAENSER = (36.2, 64.0, 79.1)
KLASSER = ("rolig", "normal", "livlig", "urolig")

UDVIKLING_SLUT = dt.date(2023, 12, 31)


class Lag2Fejl(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# RTH-dage udledt af 1-min-sættet
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RTHDag:
    dag: dt.date
    aabning: float      # 09:30-baren
    hoej: float
    lav: float
    luk: float

    @property
    def range_(self) -> float:
        return self.hoej - self.lav


def laes_rth(instrument: str = INSTRUMENT) -> dict[dt.date, RTHDag]:
    """RTH-OHLC pr. dag, udledt af 1-min-sættet.

    ⚠ Tidsstemplerne i vol_cache er UTC. Læses de som ET, forskydes hele dagen —
    det var netop den fejl der fik B2 til at melde falsk strukturbrud på SPY og
    IWM (se sessions_revision.laes_tider)."""
    sti = CACHE / f"{instrument}_1min.csv"
    if not sti.exists():
        raise Lag2Fejl(f"{sti} findes ikke — kør vol_harvest.py --hvad 1min")

    ud: dict[dt.date, list] = {}
    with sti.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            u = dt.datetime.fromisoformat(r["timestamp"]).replace(tzinfo=dt.timezone.utc)
            e = u.astimezone(ET)
            d = e.date()
            o, h, l, c = (float(r["open"]), float(r["high"]),
                          float(r["low"]), float(r["close"]))
            v = ud.get(d)
            if v is None:
                ud[d] = [e, o, h, l, c]
            else:
                if h > v[2]:
                    v[2] = h
                if l < v[3]:
                    v[3] = l
                v[4] = c
                if e < v[0]:
                    v[0], v[1] = e, o
    return {d: RTHDag(d, v[1], v[2], v[3], v[4]) for d, v in ud.items()}


# ═══════════════════════════════════════════════════════════════════════════════
# Look-ahead-porten
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TilladtInput:
    """Alt lag 2 må se om dag `dag` — og intet andet.

    ⚠ DETTE ER DEN ENESTE DATAVEJ. Håndhæv det i kode, ikke i disciplin: grænsen
    mellem natten og dagen er en halv time bred og let at træde over, og et læk
    ville få resultatet til at se glimrende ud frem for forkert.

    Bemærk hvad der IKKE er her: dag d's høj, lav, luk eller nogen bar efter
    09:30. `aabning` er med, fordi den ER lag 2's input — i produktion erstattes
    den af MES' natbevægelse kl. 09:00, som er kendt før åbning.
    """
    dag: dt.date
    aabning: float                 # dag d's 09:30-kurs — ENESTE felt fra dag d
    luk_i_gaar: float
    range_i_gaar: float
    typisk_range: float            # rullende middel til og med d−1
    luk_i_forgaars: float          # luk(d−2) — benchmarkens normalisering
    lag1_i_gaar: Optional[float]

    def gap(self) -> float:
        return self.aabning - self.luk_i_gaar


def tilladt_input(dag: dt.date, rth: dict[dt.date, RTHDag],
                  dage: list[dt.date], lag1: dict[dt.date, Optional[float]],
                  typisk_vindue: int = TYPISK_VINDUE) -> Optional[TilladtInput]:
    """Byg det tilladte input for `dag`. None hvis historikken ikke rækker."""
    i = dage.index(dag) if dag in dage else -1
    if i < typisk_vindue:
        return None
    i_gaar = dage[i - 1]
    forrige = dage[i - typisk_vindue:i]          # STRENGT før dag — ikke [i-n:i+1]
    typisk = st.fmean(rth[d].range_ for d in forrige)
    if typisk <= 0:
        return None
    return TilladtInput(
        dag=dag,
        aabning=rth[dag].aabning,
        luk_i_gaar=rth[i_gaar].luk,
        range_i_gaar=rth[i_gaar].range_,
        typisk_range=typisk,
        luk_i_forgaars=rth[dage[i - 2]].luk,
        lag1_i_gaar=lag1.get(i_gaar),
    )


def tilladt_input_laekkende(dag: dt.date, rth: dict[dt.date, RTHDag],
                            dage: list[dt.date], lag1, typisk_vindue=TYPISK_VINDUE):
    """⚠ BEVIDST LÆKKENDE — findes KUN så testen kan vise at den fanges.

    Den bytter gårsdagens range ud med DAGENS. Bruges den, kender lag 2 svaret på
    forhånd, og testen skal fange det. Gør den ikke det, måler testen ikke det
    den påstår (spec §3, samme fikstur-krav som H3).
    """
    t = tilladt_input(dag, rth, dage, lag1, typisk_vindue)
    if t is None:
        return None
    return TilladtInput(dag=t.dag, aabning=t.aabning, luk_i_gaar=t.luk_i_gaar,
                        range_i_gaar=rth[dag].range_,       # ← lækket
                        typisk_range=t.typisk_range,
                        luk_i_forgaars=t.luk_i_forgaars, lag1_i_gaar=t.lag1_i_gaar)


# ═══════════════════════════════════════════════════════════════════════════════
# Komponenter og score
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Lag2Dag:
    dag: dt.date
    score: Optional[float]
    klasse: Optional[str]
    status: str
    komponenter: dict[str, Optional[float]] = field(default_factory=dict)
    manglende: list[str] = field(default_factory=list)


def klasse_af(score: float) -> str:
    lav, hoej, uro = GRAENSER
    if score < lav:
        return KLASSER[0]
    if score < hoej:
        return KLASSER[1]
    if score < uro:
        return KLASSER[2]
    return KLASSER[3]


def byg_raaserier(porte: dict[dt.date, TilladtInput],
                  normalisering: str = None) -> dict[str, dict]:
    """De tre rå komponentserier. Se NORMALISERING for hvorfor valget betyder noget."""
    norm = normalisering or NORMALISERING
    if norm not in ("typisk_range", "forrige_luk"):
        raise Lag2Fejl(f"ukendt normalisering {norm!r}")
    nat, igaar, lag1 = {}, {}, {}
    for d, t in porte.items():
        if norm == "typisk_range":
            nat[d] = abs(t.gap()) / t.typisk_range
            igaar[d] = t.range_i_gaar / t.typisk_range
        else:
            nat[d] = abs(t.gap()) / t.luk_i_gaar
            igaar[d] = t.range_i_gaar / t.luk_i_forgaars
        if t.lag1_i_gaar is not None:
            lag1[d] = t.lag1_i_gaar
    return {"nat_pctl": nat, "igaar_range_pctl": igaar, "lag1_pctl": lag1}


def beregn_lag2(slut: Optional[dt.date] = None,
                reference: str = vp.PRIMAER_REF,
                burnin: int = vp.BURNIN_LAG1,
                port=tilladt_input,
                normalisering: str = None) -> list[Lag2Dag]:
    """Lag 2 for hver NYSE-handelsdag. Rent offline mod vol_cache/.

    `slut` afskærer hårdt — udviklingsperioden slutter 2023-12-31 og håndhæves
    i `byg_grundlag`, ikke i disciplin.
    """
    rth = laes_rth()
    dage = sorted(rth)

    l1_dage = l1.beregn_lag1(slut=slut) if slut else l1.beregn_lag1()
    lag1 = {x.dag: x.score for x in l1_dage}

    if slut:
        dage = [d for d in dage if d <= slut]

    porte: dict[dt.date, TilladtInput] = {}
    for d in dage:
        t = port(d, rth, dage, lag1)
        if t is not None:
            porte[d] = t

    raa = byg_raaserier(porte, normalisering)
    pct = {navn: vp.som_opslag(vp.beregn(s, reference, burnin))
           for navn, s in raa.items()}

    ud: list[Lag2Dag] = []
    for d in sorted(porte):
        k = {navn: pct[navn].get(d) for navn in KOMPONENTER}
        mangler = [navn for navn, v in k.items() if v is None]
        if mangler == list(KOMPONENTER):
            ud.append(Lag2Dag(d, None, None, "STALE", k, mangler))
            continue
        vaerdier = [(v, VAEGTE[navn]) for navn, v in k.items() if v is not None]
        score = sum(v * w for v, w in vaerdier) / sum(w for _, w in vaerdier)
        ud.append(Lag2Dag(d, round(score, 2), klasse_af(score),
                          "OK" if not mangler else "DEGRADED", k, mangler))
    return ud


# ═══════════════════════════════════════════════════════════════════════════════
# Mål og benchmark
# ═══════════════════════════════════════════════════════════════════════════════

def maal_og_benchmark(slut: Optional[dt.date] = None) -> tuple[dict, dict]:
    """(mål, benchmark) pr. dag.

    Mål: RTH-range på dag d, normaliseret ved gårsdagens luk.
    Benchmark: RTH-range på dag d−1, samme normalisering — den naive antagelse
    at i dag ligner i går.

    ⚠ Tidspunktet er rettet i forhold til revision M: tilstanden på dag d
    forudsiger dag d SELV, ikke d+1. Lag 2 beregnes før åbning og skal svare på
    "hvad forventer vi af i dag"; en tilstand der bedømmes på morgendagen måler
    ikke det den bruges til.
    """
    rth = laes_rth()
    dage = sorted(d for d in rth if slut is None or d <= slut)
    maal, bench = {}, {}
    for i in range(2, len(dage)):
        d, i_gaar, i_forgaars = dage[i], dage[i - 1], dage[i - 2]
        luk = rth[i_gaar].luk
        if luk <= 0:
            continue
        maal[d] = rth[d].range_ / luk
        bench[d] = rth[i_gaar].range_ / rth[i_forgaars].luk
    return maal, bench


# ═══════════════════════════════════════════════════════════════════════════════
# Substitutionstest — bygget, ikke kørt (spec §7 punkt 7)
# ═══════════════════════════════════════════════════════════════════════════════

def substitutionstest(mes_nat: dict[dt.date, float],
                      spy_gap: dict[dt.date, float]) -> dict:
    """Spearman mellem MES' natbevægelse kl. 09:00 og SPY's gap kl. 09:30.

    Substitutionen i §1 ANTAGES ikke — den måles, når futures-data rækker.
    Forventning > 0,95. Bliver den det ikke, er udviklingsproxyen ikke den samme
    information som produktionsinputtet, og lag 2's fundament er et andet end vi
    troede. Kaldes ikke af noget endnu; den venter på data.
    """
    faelles = sorted(set(mes_nat) & set(spy_gap))
    if len(faelles) < 60:
        return {"nok_data": False, "dage": len(faelles),
                "besked": f"kun {len(faelles)} fælles dage — mindst 60 kræves"}
    rho = _spearman([mes_nat[d] for d in faelles], [spy_gap[d] for d in faelles])
    return {"nok_data": True, "dage": len(faelles), "spearman": round(rho, 4),
            "bestaaet": rho > 0.95}


# ═══════════════════════════════════════════════════════════════════════════════
# Hjælpere
# ═══════════════════════════════════════════════════════════════════════════════

def _rang(xs: list[float]) -> list[float]:
    par = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(par):
        j = i
        while j + 1 < len(par) and xs[par[j + 1]] == xs[par[i]]:
            j += 1
        snit = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[par[k]] = snit
        i = j + 1
    return r


def _spearman(a: list[float], b: list[float]) -> float:
    ra, rb = _rang(a), _rang(b)
    n = len(ra)
    ma, mb = st.fmean(ra), st.fmean(rb)
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((x - mb) ** 2 for x in rb) ** 0.5
    return num / (da * db) if da and db else 0.0


def config_hash() -> str:
    grundlag = json.dumps({
        "instrument": INSTRUMENT,
        "komponenter": list(KOMPONENTER),
        "vaegte": {k: VAEGTE[k] for k in sorted(VAEGTE)},
        "graenser": list(GRAENSER),
        "klasser": list(KLASSER),
        "typisk_vindue": TYPISK_VINDUE,
    }, sort_keys=True)
    return hashlib.sha256(grundlag.encode()).hexdigest()[:12]


def fordeling(dage: list[Lag2Dag]) -> dict:
    """Til brug for sammenvævningen (spec §6).

    Lag 1 komprimerer, lag 2 komprimerer, og væves de sammen komprimerer det
    IGEN. Efter to lag gennemsnit er yderpunkterne næsten væk. Denne spec løser
    det ikke, men uden tallene her ville problemet først blive opdaget dér.
    """
    s = [d.score for d in dage if d.score is not None]
    if not s:
        return {"n": 0}
    midte = sum(1 for x in s if 40 <= x <= 60)
    return {
        "n": len(s),
        "middel": round(st.fmean(s), 2),
        "spredning": round(st.pstdev(s), 2),
        "min": round(min(s), 2), "max": round(max(s), 2),
        "andel_40_60": round(midte / len(s) * 100, 1),
        "klassefordeling": {k: round(
            sum(1 for d in dage if d.klasse == k) / len(s) * 100, 1) for k in KLASSER},
    }


def som_kontrakt(d: Lag2Dag) -> Optional[dict]:
    """lag2-blokken til vol_current.json. None ved STALE — ingen stiltiende
    rapportering på forældede data (samme regel som lag 1)."""
    if d.status == "STALE" or d.score is None:
        return None
    return {"score": d.score, "klasse": d.klasse, "status": d.status,
            "komponenter": d.komponenter, "manglende": d.manglende,
            "config_hash": config_hash()}


if __name__ == "__main__":
    dage = beregn_lag2(slut=UDVIKLING_SLUT)
    med = [d for d in dage if d.score is not None]
    print(f"config_hash: {config_hash()}")
    print(f"{len(dage)} handelsdage, {len(med)} med score")
    print(f"periode: {dage[0].dag} .. {dage[-1].dag}")
    print(f"\nfordeling: {json.dumps(fordeling(dage), ensure_ascii=False)}")
