"""
vol_lag1.py — lag 1's formler: baggrundsregimet for volatilitet
═══════════════════════════════════════════════════════════════════════════════════
Lag 1 svarer paa ét spoergsmaal: **hvor ligger volatiliteten lige nu i forhold til
sin egen historie siden 2009?** Ikke om den er hoej i absolut forstand, og ikke hvad
man boer goere ved det.

FIRE KOMPONENTER, alle som percentil mod egen historik (spec v2.0 afsnit 3):

  K1  pct(VIX)              hvor dyrt er forsikring lige nu
  K2  pct(VIX / VIX3M)      terminsstruktur. Hoej = den korte ende dyrere end den
                            lange = stress. Lav = contango = ro
  K3  pct(rv20 / rv60)      SPY's realiserede ekspansion — vokser bevaegelsen?
  K4  pct(RVX)              small-cap-volatilitetens eget niveau

⚠ RVX/VIX ER IKKE HER. Forholdet mellem de to er segment-DIVERGENS og hoerer til
byggeklods 6. Lagde vi det ind i volatilitetsaksen, ville byggeklods 6 senere maale
det samme igen — og vi ville tro vi havde to uafhaengige signaler hvor vi har ét.
RVX' NIVEAU er derimod volatilitet, og det er dét K4 er.

SAMMENVEJNING: simpelt gennemsnit, lige vaegte, PRAEREGISTRERET. Ingen optimering.
Bestaar lag 1 sin test med lige vaegte, er vi faerdige. Bestaar den ikke, taeller
enhver aendring af vaegtene som en re-kalibrering med lognotat — vaegte fundet ved at
kigge paa testresultatet er overfitting, uanset hvor rimelige de ser ud bagefter.

KLASSERNE ER RELATIVE. "stress" betyder "i den oeverste tiendedel af referencen
siden 2009", ikke "som i 2008". Uden den praecisering vil nogen paa et tidspunkt
laese etiketten som en absolut tilstand — og handle derefter.

Alt her er OFFLINE mod vol_cache/. Ingen IBKR-kald.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import vol_percentil as vp
import vol_serier as vs

# ── Praeregistrerede parametre ────────────────────────────────────────────────
KOMPONENTER = ("vix_pctl", "term_pctl", "rv_ekspansion_pctl", "rvx_pctl")

# Lige vaegte. Staar eksplicit frem for at vaere underforstaaet i et gennemsnit,
# saa en senere aendring er synlig i diffen og i config_hash.
VAEGTE = {k: 1.0 for k in KOMPONENTER}

RV_KORT, RV_LANG = 20, 60          # handelsdage til realiseret vol
HANDELSDAGE_PR_AAR = 252           # annualisering

# Klassegraenser paa scoren (spec v2.0 afsnit 4). Maalet er ~20/50/20/10 %.
#
# JUSTERET ÉN GANG, 2026-08-07 — specens ene tilladte justering, og den er brugt.
# Startgraenserne 20/70/90 gav 17,3 / 63,3 / 16,7 / 2,6 %. "stress" ramte altsaa
# 2,6 % mod de tilsigtede 10.
#
# Aarsagen er strukturel og ikke et datafaenomen: scoren er et GENNEMSNIT af fire
# percentiler, og et gennemsnit traekker mod midten. Alle fire komponenter skal
# vaere ekstreme SAMTIDIG for at loefte scoren over 90 — enkeltkomponenterne
# spaender hver isaer fuldt 0-100, men deres gennemsnit goer ikke.
#
# Nye graenser = scorens empiriske 20./70./90. percentil over udviklingsperioden
# (21,75 / 58,46 / 79,81), afrundet. Kalibreret mod FORDELINGEN, aldrig mod
# testresultatet — og gjort FOER den praediktive test blev koert foerste gang.
# Se vol_kalibreringslog.md.
GRAENSER = (22.0, 58.0, 80.0)
KLASSER = ("lav", "normal", "forhoejet", "stress")

# Serier lag 1 kraever. VIX9D og VX hoerer til lag 2 og er bevidst ikke med.
SERIER_LAG1 = ("SPY", "VIX", "VIX3M", "RVX")

SKEMA_VERSION = "1.0"


@dataclass
class Lag1Dag:
    """Én handelsdags lag 1-tilstand med alt der skal til for at efterproeve den."""
    dag: date
    score: Optional[float]
    klasse: Optional[str]
    konfidens: float
    status: str
    komponenter: dict[str, Optional[float]] = field(default_factory=dict)
    manglende: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Realiseret volatilitet
# ═══════════════════════════════════════════════════════════════════════════════
def log_afkast(serie: dict[date, float]) -> dict[date, float]:
    """Daglige log-afkast. Foerste dag har intet afkast og udelades.

    ⚠ LOGNOTAT (spec v2.2 §5.2) — ETH kontra RTH, kontrolleret 2026-08-10.
    Dagsserien i vol_cache er hentet med useRTH=False, saa `close` er lukket
    20:00 ET (efter eftermarkedet), ikke 16:00. Spoergsmaalet var om lag 1's
    realiserede vol dermed er bygget paa noget andet end den maaler.

    Svaret er nej, af to grunde:
      1. Komponenten er LUK-TIL-LUK, ikke range. ETH flytter hvilket luk der
         bruges, men serien er intern konsistent — luk er luk.
      2. Maalet i vol_lag1_test bruger de SAMME funktioner paa den SAMME serie
         (vs.laes_serie("SPY") -> log_afkast -> realiseret_vol). Komponent og
         maal kan altsaa ikke vaere uenige om definitionen.

    Maalt: rv20 paa ETH-luk mod rv20 paa RTH-luk (udledt af 1-min-saettet) har
    Spearman +0,974 over 3.648 dage. Medianforskellen er +0,65 %, men paa
    enkeltdage op til ±11-13 %. Rangkorrelationen er det der betyder noget her,
    fordi komponenten percentileres — saa valget aendrer ikke lag 1's dom.

    Det er IKKE samme sag som K2's normaliseringsfejl i v2.1: dér maalte
    komponenten en anden STOERRELSE end benchmarken. Her er det samme stoerrelse
    aflaest et andet klokkeslaet, konsistent i baade komponent og maal.

    Log frem for procent, fordi de er additive over tid — summen af log-afkast
    over 20 dage ER 20-dages afkastet. Det goer standardafvigelsen til et
    velformet maal for spredning over perioden.
    """
    dage = sorted(serie)
    ud = {}
    for i in range(1, len(dage)):
        f, n = serie[dage[i - 1]], serie[dage[i]]
        if f > 0 and n > 0:
            ud[dage[i]] = math.log(n / f)
    return ud


def realiseret_vol(afkast: dict[date, float], vindue: int) -> dict[date, float]:
    """Annualiseret std. af log-afkast over de seneste `vindue` dage, TIL OG MED d.

    Dagen selv ER med her, og det er korrekt: rv20 paa dag d er en beskrivelse af
    hvad der ALLEREDE er sket op til og med d. Det er ikke look-ahead — modsat
    percentilen, hvor grundlaget skal ligge strengt foer.
    """
    dage = sorted(afkast)
    ud = {}
    for i in range(vindue - 1, len(dage)):
        vindue_v = [afkast[dage[j]] for j in range(i - vindue + 1, i + 1)]
        m = sum(vindue_v) / vindue
        var = sum((x - m) ** 2 for x in vindue_v) / (vindue - 1)
        ud[dage[i]] = math.sqrt(var) * math.sqrt(HANDELSDAGE_PR_AAR)
    return ud


# ═══════════════════════════════════════════════════════════════════════════════
# Komponenter
# ═══════════════════════════════════════════════════════════════════════════════
def byg_raaserier(serier: dict[str, dict[date, float]]) -> dict[str, dict[date, float]]:
    """De fire RAA komponentserier, foer percentilering.

    Forholdstal beregnes KUN paa dage hvor begge ben findes. Mangler VIX3M
    2011-09-12, findes terminsstrukturen ikke den dag — den gaettes ikke ud fra
    naboen, og dagen bliver DEGRADED i stedet (L1).
    """
    vix, vix3m, rvx, spy = (serier.get(k, {}) for k in ("VIX", "VIX3M", "RVX", "SPY"))

    term = {d: vix[d] / vix3m[d]
            for d in set(vix) & set(vix3m) if vix3m[d] > 0}

    afk = log_afkast(spy)
    rv_k = realiseret_vol(afk, RV_KORT)
    rv_l = realiseret_vol(afk, RV_LANG)
    eksp = {d: rv_k[d] / rv_l[d] for d in set(rv_k) & set(rv_l) if rv_l[d] > 0}

    return {"vix_pctl": vix, "term_pctl": term,
            "rv_ekspansion_pctl": eksp, "rvx_pctl": rvx}


def klasse_af(score: float) -> str:
    """Score -> klasse. Graenserne er INKLUSIVE nedadtil: 20,0 er 'normal'."""
    lav, hoej, stress = GRAENSER
    if score < lav:
        return KLASSER[0]
    if score < hoej:
        return KLASSER[1]
    if score < stress:
        return KLASSER[2]
    return KLASSER[3]


def beregn_lag1(reference: str = vp.PRIMAER_REF,
                slut: Optional[date] = None,
                burnin: int = vp.BURNIN_LAG1) -> list[Lag1Dag]:
    """Lag 1 for hver NYSE-handelsdag. Rent offline mod vol_cache/.

    `slut` afskaerer haardt. Udviklingsperioden slutter 2023-12-31, og kalderen
    SKAL saette den — se iterationsdisciplinen i spec v2.0 afsnit 6.
    """
    serier = {n: vs.laes_serie(n) for n in SERIER_LAG1}
    raa = byg_raaserier(serier)

    # Percentilér hver komponent for sig, hver mod SIN egen historik.
    pct = {navn: vp.som_opslag(vp.beregn(s, reference, burnin))
           for navn, s in raa.items()}

    # Sammenstil paa DATO via vol_serier, saa L1 og L2 gaelder: NYSE-dage
    # definerer raekkerne, og en manglende serie giver nan frem for naboens tal.
    raekker = vs.sammenstil(pct, slut=slut)

    ud = []
    for r in raekker:
        # ⚠ `i_dag=r.dag`, ikke `slut`. STALE betyder "data er for gamle til at
        # udtale sig I DAG" og hoerer til LIVE drift. Ved en historisk beregning
        # bedoemmes hver dag som den saa ud dengang — ellers blev 3.624 af 3.628
        # dage stemplet STALE alene fordi de laa laengere tilbage end
        # MAKS_ALDER_DAGE fra periodens slutning.
        status, konf = vs.vurder_status(r, list(KOMPONENTER), i_dag=r.dag)
        komp = {k: (r.vaerdier.get(k) if r.har(k) else None) for k in KOMPONENTER}
        mangler = [k for k in KOMPONENTER if komp[k] is None]

        if status == "STALE" or len(mangler) == len(KOMPONENTER):
            # NAEGT at afgive en score. Stiltiende rapportering paa foraeldede
            # eller tomme data var en konkret fejl i det forrige forsoeg.
            ud.append(Lag1Dag(r.dag, None, None, 0.0, "STALE", komp, mangler))
            continue

        vaegt = sum(VAEGTE[k] for k in KOMPONENTER if komp[k] is not None)
        score = sum(VAEGTE[k] * komp[k] for k in KOMPONENTER if komp[k] is not None) / vaegt
        ud.append(Lag1Dag(r.dag, round(score, 4), klasse_af(score),
                          round(konf, 4), status, komp, mangler))
    return ud


# ═══════════════════════════════════════════════════════════════════════════════
# Provenans og output (C2/C3)
# ═══════════════════════════════════════════════════════════════════════════════
def config_hash() -> str:
    """Hash af ALT der paavirker et resultat. Aendrer nogen en vaegt, en graense
    eller et vindue, aendrer hashen sig — og saa kan en logget tilstand ikke
    forveksles med én fra en anden motor.

    Uden den er seks maaneders fremadrettet logning vaerdiloes i det oejeblik nogen
    har rettet en linje undervejs. Og nogen retter altid en linje (C3).
    """
    grundlag = {
        "skema": SKEMA_VERSION, "komponenter": list(KOMPONENTER),
        "vaegte": VAEGTE, "graenser": list(GRAENSER), "klasser": list(KLASSER),
        "rv": [RV_KORT, RV_LANG, HANDELSDAGE_PR_AAR],
        "burnin": vp.BURNIN_LAG1, "reference": vp.PRIMAER_REF,
        "serier": list(SERIER_LAG1),
    }
    tekst = json.dumps(grundlag, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(tekst.encode("utf-8")).hexdigest()[:8]


def som_kontrakt(d: Lag1Dag, beregnet_kl: str,
                 data_as_of: Optional[dict] = None,
                 lag2: Optional[dict] = None) -> dict:
    """C2's outputkontrakt. Lag 3 er null indtil videre — skemaet aendrer sig IKKE
    naar det kommer til.

    `lag2` fyldes med vol_lag2.som_kontrakt(...) naar lag 2 er godkendt. Det er
    None her, fordi lag 2 IKKE bestod sin praediktive test som specificeret i
    v2.1 — se vol_lag2_test.py. At udfylde pladsen alligevel ville lade et
    ugodkendt lag se godkendt ud i outputtet."""
    advarsler = []
    if d.manglende:
        advarsler.append(f"manglende komponenter: {', '.join(d.manglende)}")
    return {
        "skema_version": SKEMA_VERSION,
        "config_hash": config_hash(),
        "beregnet_kl": beregnet_kl,
        "handelsdag": d.dag.isoformat(),
        "status": d.status,
        "advarsler": advarsler,
        "lag1": None if d.score is None else {
            "score": d.score,
            "klasse": d.klasse,
            "konfidens": d.konfidens,
            "komponenter": {k: d.komponenter.get(k) for k in KOMPONENTER},
            "data_as_of": data_as_of or {},
        },
        # lag2 udfyldes af vol_lag2.som_kontrakt naar laget er godkendt. Skemaet
        # er uaendret — pladsen har vaeret her hele tiden, saa den dag lag 2 og 3
        # kommer til, aendrer outputtets FORM sig ikke.
        "lag2": lag2,
        "lag3": None,
    }
