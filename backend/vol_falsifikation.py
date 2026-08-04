"""
vol_falsifikation.py — Revision G: en kontrol skal kunne dumpe, og det skal vises
═══════════════════════════════════════════════════════════════════════════════════
Tre gange i dette projekt har samme sygdom vist sig: en kontrol hvis udfald var
strukturelt afgjort paa forhaand.

  · den forrige regime-motors "≥3 af 4 etiketter, ≥6 skift" — bestod paa hvid stoej
    i 200 af 200 forsoeg, fordi percentiltransformationen pr. konstruktion lagde
    ~30 % over enhver cutoff
  · `if q:` paa qualifyContractsAsync — listen er truthy ogsaa ved fiasko
  · `"2 kopieret" in output` — matcher glad ogsaa "102 kopieret"

Alle tre saa ud som tests, rapporterede som tests, og kunne ikke fejle. Det er
vaerre end ingen test, for de gav tryghed praecis dér hvor man troede man var daekket.

DETTE MODUL ER REGLEN GJORT TIL KODE. Ingen V-test maa koeres foerste gang foer den
er registreret her med et konkret input der faar den til at DUMPE — og det input
skal vaere koert, ikke bare beskrevet.

    from vol_falsifikation import Falsifikationsregister
    reg = Falsifikationsregister()
    reg.kraev_egenskab("V-test 1", vtest1, egenskab="prediktiv")   # fiksturet vaelges FOR dig
    reg.rapport()          # skriver vol_falsifikation.md — og raiser hvis noget mangler

NAVNGIV EGENSKABEN, VAELG IKKE FIKSTURET (H1). Der findes ingen universel nulserie:
hvid stoej, random walk, shufflet og konstant er negative for FORSKELLIGE egenskaber.
En random walk er fx staerkt PREDIKTIV (maalt autokorr +0,995) og ville faa en
fuldstaendig korrekt V-test 1 til at se defekt ud. Registret vaelger derfor selv, og
afviser et haandplukket fikstur der ikke er nul for den navngivne egenskab.

⚠ TO RETNINGER, ALTID. Et input der faar kontrollen til at dumpe beviser at den KAN
fejle. Et input der faar den til at bestaa beviser at den ikke bare altid dumper.
Uden begge er "kontrollen dumpede" lige saa intetsigende som "kontrollen bestod".

Ingen forseglet data roeres her. Alt er syntetisk og deterministisk (faste seeds),
saa en registrering kan efterproeves aar senere med samme tal.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
from scipy.stats import ConstantInputWarning, spearmanr

# Faste seeds. Reproducerbarhed er ikke pedanteri her: registreringen er et bevis,
# og et bevis der ikke kan efterproeves er en paastand.
SEED_STOEJ = 20260804
SEED_WALK = 20260805
SEED_KLYNGE = 20260806
SEED_SHUFFLE = 20260807

# 'Ekspanderende' percentilreference = hele den hidtidige historik. Repraesenteret
# som et vindue stoerre end nogen serie, saa motoren ikke behoever en saerskilt sti.
EKSPANDERENDE = 10**9


# ═══════════════════════════════════════════════════════════════════════════════
# Kendt-negative fikstuer — serier UDEN den struktur vi paastaar at maale
# ═══════════════════════════════════════════════════════════════════════════════
def hvid_stoej_serie(n: int, seed: int = SEED_STOEJ) -> np.ndarray:
    """Uafhaengige positive "ranges" uden nogen volatilitetsklyngning.

    Det er den haardeste kendt-negative for lag 2: en range-serie hvor gaarsdagen
    intet siger om i dag. Enhver kontrol der bestaar paa DENNE, maaler ikke
    volatilitetsklyngning — den maaler sin egen konstruktion.
    """
    rng = np.random.default_rng(seed)
    return np.abs(rng.normal(1.0, 0.35, n)) + 0.05


def random_walk_serie(n: int, seed: int = SEED_WALK) -> np.ndarray:
    """Ranges udledt af en ren random walk. Ingen klyngning, men staerk niveaupersistens.

    Fanger den fejl at en kontrol forveksler "serien bevaeger sig langsomt" med
    "serien har regimer". Det var praecis den forrige motors opholdstids-fejl.

    ⚠ MAA IKKE BRUGES SOM KENDT-NEGATIV FOR V-TEST 1. Maalt lag-1-autokorrelation er
    +0,995 (se `fikstur_egenskaber`): en random walk ER trivielt forudsigelig fra i
    gaar til i dag, fordi niveauet naesten ikke flytter sig. En prediktiv test SKAL
    bestaa her; gjorde den ikke, var den forkert. Den rette kendt-negative for
    V-test 1 er `shufflet(klynget_serie(...))`, som har samme fordeling men nul
    tidsstruktur (maalt +0,000).

    Brug denne fikstur til paastande om REGIMER, opholdstid og skift — dér er den
    haard, netop fordi glathed uden regimer ligner regimer.
    """
    rng = np.random.default_rng(seed)
    niveau = np.cumsum(rng.normal(0, 0.05, n))
    return np.abs(niveau - niveau.min()) + 0.5


def konstant_serie(n: int, vaerdi: float = 1.0) -> np.ndarray:
    """Ingen variation overhovedet. En kontrol der udtaler sig her, udtaler sig om intet."""
    return np.full(n, float(vaerdi))


def shufflet(serie: np.ndarray, seed: int = SEED_SHUFFLE) -> np.ndarray:
    """Samme FORDELING, oedelagt RAEKKEFOELGE.

    Den skarpeste kendt-negative der findes: alt hvad der handler om niveauer og
    fordelinger er uroert, og kun tidsstrukturen er vaek. Bestaar en kontrol paa en
    shufflet serie, maaler den fordelingen og ikke dynamikken.
    """
    rng = np.random.default_rng(seed)
    ud = serie.copy()
    rng.shuffle(ud)
    return ud


def vaerdiloest_maal(n: int, seed: int = SEED_STOEJ + 1) -> np.ndarray:
    """Et "maal" der pr. konstruktion intet ved. Bruges mod den naive benchmark.

    Specen kraever at benchmarken vises at slaa noget beviseligt vaerdiloest —
    ellers maaler benchmarken heller ikke noget.
    """
    rng = np.random.default_rng(seed)
    return rng.normal(0, 1, n)


# ═══════════════════════════════════════════════════════════════════════════════
# H2 — SHUFFL MAALET, IKKE DEN VINDUESBEREGNEDE PRAEDIKTOR
# ═══════════════════════════════════════════════════════════════════════════════
def shufflet_via_underliggende(afkast: np.ndarray, vindue_fn, seed: int = SEED_SHUFFLE):
    """Shuffl de UNDERLIGGENDE afkast og koer dem gennem samme vinduesberegning.

    ⚠ DETTE ER IKKE DET SAMME som at shuffle den faerdige, vinduesberegnede serie,
    og forskellen er den der slog det forrige projekt ihjel.

    En 30-dages rullende beregning med dagligt skridt deler 29 af 30 observationer
    mellem to nabodage. Serien er derfor MEKANISK ekstremt glat — helt uafhaengigt af
    om markedet har regimer. Shuffler man den faerdige serie, oedelaegger man
    udglatningen OG regimestrukturen paa én gang, og resultatet kan ikke sige hvilken
    af de to der bar signalet. Det var praecis dén sammenblanding der fik den forrige
    motor til at konkludere at m7 var anti-persistent; konklusionen var formentlig et
    artefakt af metoden og ikke et fund om markedet.

    Her shuffles i stedet raavaren. Udglatningen genopstaar identisk gennem
    `vindue_fn`, mens tidsstrukturen i markedet er vaek. Det er det rene nul for en
    vinduesberegnet praediktor.

    HOVEDREGEL ALLIGEVEL: for V-test 1, hvor maalet er en ren dagsvaerdi og
    praediktoren er vinduesberegnet, **shuffl maalserien**. Det er enklere og
    entydigt. Denne funktion er til det tilfaelde hvor en enkelt vinduesberegnet
    KOMPONENT skal nulstilles for sig.
    """
    rng = np.random.default_rng(seed)
    blandet = np.asarray(afkast, float).copy()
    rng.shuffle(blandet)
    return vindue_fn(blandet)


def rullende_middel(x: np.ndarray, vindue: int = 30) -> np.ndarray:
    """Simpel rullende beregning — bruges til at demonstrere H2's pointe konkret."""
    x = np.asarray(x, float)
    if len(x) < vindue:
        return np.array([])
    kerne = np.ones(vindue) / vindue
    return np.convolve(x, kerne, mode="valid")


# ═══════════════════════════════════════════════════════════════════════════════
# Kendt-POSITIV fikstur — en serie der beviseligt HAR det vi maaler efter
# ═══════════════════════════════════════════════════════════════════════════════
def klynget_serie(n: int, seed: int = SEED_KLYNGE, persistens: float = 0.94,
                  stoej: float = 0.28) -> np.ndarray:
    """Ranges med aegte volatilitetsklyngning (GARCH-agtig, log-AR(1) i vol-niveauet).

    Uden en kendt-positiv er en dumpet kontrol intetsigende — den kunne dumpe paa
    alt. Denne serie er den kontrol: bestaar en V-test ikke HER, er testen for
    stram eller maalet forkert, ikke markedet der mangler struktur.
    """
    rng = np.random.default_rng(seed)
    log_vol = np.zeros(n)
    for i in range(1, n):
        log_vol[i] = persistens * log_vol[i - 1] + rng.normal(0, stoej)
    return np.exp(log_vol) * np.abs(rng.normal(1.0, 0.25, n)) + 0.05


# ═══════════════════════════════════════════════════════════════════════════════
# H3 — DE TESTS DER IKKE KAN FALSIFICERES MED EN NULSERIE
# ═══════════════════════════════════════════════════════════════════════════════
# Egenskabstaksonomien ovenfor daekker de tests der sammenligner et SIGNAL med STOEJ.
# To af V-testene goer noget andet: de sammenligner MOTOREN MED SIG SELV.
#
#   · realtidsgyldighed (look-ahead): genberegn med haardt trunkerede input og kraev
#     identiske vaerdier
#   · stabilitet: aendrer klassifikationen sig lidt naar parametrene rykkes?
#
# Fodrer man dem en nulserie, BESTAAR de stadig — trunkering og genberegning virker
# lige godt paa stoej, og en parametervariation flytter lige lidt uanset input. De er
# altsaa ikke daekket af kravet i Revision G ad den vej.
#
# Kravet gaelder alligevel, men maaden er en anden: byg en motor der beviseligt ER
# defekt, og vis at testen fanger den. Den defekte motor gemmes som fikstur paa linje
# med nulserierne, saa demonstrationen kan gentages efter enhver aendring.
#
# Det er praecis det hul der ville vaere dyrest at have: et look-ahead-laek goer ALLE
# oevrige resultater for gode, og realtidstesten er det eneste vaern imod det. Et vaern
# der aldrig er set fyre, er ikke et vaern.


def motor_uden_laek(serie: np.ndarray, i: int, vindue: int = 252) -> float:
    """Percentil af dag i, maalt KUN mod dage FOER i. Den korrekte beregning."""
    serie = np.asarray(serie, float)
    start = max(0, i - vindue)
    historik = serie[start:i]
    if len(historik) < 20:
        return float("nan")
    return float((historik < serie[i]).mean() * 100.0)


def motor_med_laek(serie: np.ndarray, i: int, vindue: int = 252) -> float:
    """⚠ BEVIDST DEFEKT: percentil maalt mod HELE serien, ogsaa fremtiden.

    Den klassiske look-ahead: referencefordelingen indeholder dage der endnu ikke er
    sket. Den er svaer at se i kode og goer alle resultater for gode — praecis derfor
    skal realtidstesten kunne fange den, og praecis derfor ligger den her som fikstur
    frem for kun at blive beskrevet.
    """
    serie = np.asarray(serie, float)
    if len(serie) < 20:
        return float("nan")
    return float((serie < serie[i]).mean() * 100.0)


def realtidstest(motor_fn, serie: np.ndarray, dage: list[int] | None = None,
                 vindue: int = 252) -> bool:
    """V-test 4's form: giver motoren samme svar naar fremtiden er hugget af?

    For hver proevedag beregnes scoren to gange — én gang med hele serien til
    raadighed, én gang med input haardt trunkeret ved netop den dag. Er de ikke
    identiske, laeser motoren fremtiden.

    Bestaar = ALLE proevedage identiske. 19 af 20 er ikke bestaaet; det er en fejl
    der skal findes.
    """
    serie = np.asarray(serie, float)
    if dage is None:
        dage = list(range(300, len(serie), max(1, (len(serie) - 300) // 20)))[:20]
    for i in dage:
        fuld = motor_fn(serie, i, vindue)
        trunkeret = motor_fn(serie[:i + 1], i, vindue)
        if np.isnan(fuld) and np.isnan(trunkeret):
            continue
        if not np.isclose(fuld, trunkeret, equal_nan=False):
            return False
    return True


def klassificer_percentil(p: float) -> int:
    """Fire klasser ud fra en percentil. Graenserne er fordelingsmaessige, ikke absolutte."""
    if np.isnan(p):
        return -1
    return 0 if p < 25 else 1 if p < 50 else 2 if p < 75 else 3


def stabilitetsmaal(motor_fn, serie: np.ndarray, basisvindue: int = 252) -> dict:
    """Maal hvor meget baade SCOREN og KLASSEN flytter sig naar vinduet rykkes ±50 %.

    ⚠ MAAL PAA SCOREN, IKKE KUN PAA KLASSEN — et fund fra 2026-08-04 der er vaerd at
    kende foer V-test 3 skrives. En KORREKT rangpercentil flytter sig kun 2-4
    percentilpoint naar vinduet halveres, men 21 % af dagene skifter alligevel klasse.
    Af de skift laa 83-97 % under ti point fra en klassegraense. Klasseskiftraten
    maaler altsaa overvejende diskretiseringen taet paa graenserne, ikke om maalet er
    robust — og et kriterium paa 15 % klasseskift dumper en motor der er fuldstaendig
    stabil. Rapportér begge tal; laeg bestaa-kriteriet paa scoren.
    """
    serie = np.asarray(serie, float)
    dage = list(range(300, len(serie)))
    basis = np.array([motor_fn(serie, i, basisvindue) for i in dage])
    basis_kl = [klassificer_percentil(x) for x in basis]
    ud = {"basisvindue": basisvindue, "varianter": {}}
    # ±50 % vinduesvariation OG de tre percentilreferencer specen kraever:
    # 252 (basis), 504, og ekspanderende (hele historikken = et vindue stoerre end
    # serien). EKSPANDERENDE er repraesenteret som et meget stort vindue, saa
    # motoren ikke behoever en saerskilt kodesti.
    for vindue in (int(basisvindue * 0.5), int(basisvindue * 1.5), 504, EKSPANDERENDE):
        variant = np.array([motor_fn(serie, i, vindue) for i in dage])
        variant_kl = [klassificer_percentil(x) for x in variant]
        flip = sum(1 for a, b in zip(basis_kl, variant_kl) if a != b) / max(1, len(dage))
        naer_graense = sum(
            1 for a, b, sc in zip(basis_kl, variant_kl, basis)
            if a != b and not np.isnan(sc)
            and min(abs(sc - 25), abs(sc - 50), abs(sc - 75)) < 10)
        ud["varianter"][vindue] = {
            "median_score_aendring_pp": float(np.nanmedian(np.abs(variant - basis))),
            "klasseskift_andel": float(flip),
            "andel_af_skift_naer_graense": float(naer_graense / max(1, flip * len(dage))),
        }
    return ud


# Sat EMPIRISK, ikke valgt som et rundt tal — se `kalibrer_stabilitetstaerskel`
# og vol_kalibreringslog.md. Vaerdien staar her som konstant saa den er frosset og
# versionsstyret; genkoer kalibreringen hvis fiksturerne aendres.
#
# Kalibreret 2026-08-04 over 8 syntetiske serier:
#     ren motor, VAERSTE variant       :  9,13 pp   (spredning 4,37-9,13)
#     skroebelig motor, BEDSTE variant : 51,60 pp   (spredning 51,60-54,17)
#     geometrisk midtpunkt             : 21,7 pp — 2,38x til begge sider
#
# Et foerste gaet paa 11,0 blev forkastet: det laa kun 1,2x over den vaerste rene
# serie, og en enkelt uheldig seed ville have dumpet en korrekt motor.
STABILITET_TAERSKEL_PP = 21.7


def stabilitetstest(motor_fn, serie: np.ndarray, basisvindue: int = 252,
                    maks_score_aendring_pp: float = STABILITET_TAERSKEL_PP) -> bool:
    """V-test 3's form. Bestaar hvis SCOREN flytter sig under graensen.

    Bemaerk hvad der IKKE er bestaa-kriterium: klasseskiftraten. Den rapporteres,
    men et hoejt tal dér betyder noget andet end et hoejt tal paa scoren — se
    `stabilitetsdiagnose`.
    """
    m = stabilitetsmaal(motor_fn, serie, basisvindue)
    return all(v["median_score_aendring_pp"] < maks_score_aendring_pp
               for v in m["varianter"].values())


def stabilitetsdiagnose(maal: dict,
                        taerskel_pp: float = STABILITET_TAERSKEL_PP) -> tuple[str, str]:
    """De to tal peger paa HVER SIN aarsag. Returnerer (konklusion, hvad_der_skal_goeres).

    ⚠ Uden denne skelnen er den naerliggende reaktion paa et hoejt klasseskifttal at
    rette i motoren — altsaa at fjerne noget der virker, for at tilfredsstille et
    kriterium der maalte noget andet end det troede.
    """
    maks_score = max(v["median_score_aendring_pp"] for v in maal["varianter"].values())
    maks_klasse = max(v["klasseskift_andel"] for v in maal["varianter"].values())
    if maks_score >= taerskel_pp:
        return ("USTABIL SCORE",
                "Maalet er parameterafhaengigt. MAALET skal laves om — det er en "
                "aegte dump af V-test 3.")
    if maks_klasse >= 0.15:
        return ("STABIL SCORE, USTABIL KLASSE",
                "Graenserne ligger uheldigt i forhold til hvor data er taet. "
                "BESLUTNINGSLAGET skal have hysterese eller doedzoner. "
                "Maaleklodsen fejler IKKE.")
    return ("STABIL", "Ingen handling.")


def kalibrer_stabilitetstaerskel(n_serier: int = 8, n: int = 1200) -> dict:
    """Sæt taersklen fra den RENE og den SKROEBELIGE motor — ikke fra et rundt tal.

    ⚠ DETTE ER IKKE KONTAMINERING (Revision I3). Kontaminationsreglen forbyder at
    kalibrere mod RESULTATET — mod strategiafkast eller mod holdout. Her kalibreres
    et instrument mod en kendt-god og en kendt-daarlig standard, begge syntetiske og
    begge konstrueret foer maaledata er set. Det er samme slags handling som at
    nulstille en vaegt med et lod af kendt masse.

    Flere seeds, saa taersklen ikke er tilpasset ét traek.
    """
    rene, skroebelige = [], []
    for k in range(n_serier):
        s = klynget_serie(n, seed=SEED_KLYNGE + k)
        for motor, kurv in ((motor_uden_laek, rene), (skroebelig_motor, skroebelige)):
            m = stabilitetsmaal(motor, s)
            kurv.append(max(v["median_score_aendring_pp"] for v in m["varianter"].values()))
    vaerst_ren = max(rene)
    bedst_skroebelig = min(skroebelige)
    # Geometrisk midtpunkt: ligger relativt lige langt fra begge, saa marginen er
    # symmetrisk i forholdstal frem for i absolutte point.
    forslag = float(np.sqrt(vaerst_ren * bedst_skroebelig))
    return {
        "n_serier": n_serier,
        "ren_vaerst": float(vaerst_ren),
        "ren_alle": [round(x, 2) for x in rene],
        "skroebelig_bedst": float(bedst_skroebelig),
        "skroebelig_alle": [round(x, 2) for x in skroebelige],
        "forslag_pp": round(forslag, 1),
        "margin_ned": round(forslag / vaerst_ren, 2),
        "margin_op": round(bedst_skroebelig / forslag, 2),
        "gaeldende_pp": STABILITET_TAERSKEL_PP,
    }


def skroebelig_motor(serie: np.ndarray, i: int, vindue: int = 252) -> float:
    """⚠ BEVIDST PARAMETERFOELSOM: scoren skaleres med vinduets laengde.

    Ikke look-ahead — den ser kun bagud og bestaar realtidstesten. Defekten er en
    skaleringsfaktor der afhaenger af tilbagekigets laengde, formet som en
    "annualisering" nogen kunne finde paa at skrive. Den ser plausibel ud i koden og
    goer scoren staerkt afhaengig af et parametervalg der burde vaere ligegyldigt.

    Findes for at vise at stabilitetstesten KAN fejle. Uden den er "stabilitetstesten
    bestod" lige saa intetsigende som enhver anden kontrol der aldrig er set dumpe.
    """
    serie = np.asarray(serie, float)
    start = max(0, i - vindue)
    historik = serie[start:i]
    if len(historik) < 20:
        return float("nan")
    sd = historik.std()
    if sd <= 0:
        return float("nan")
    z = (serie[i] - historik.mean()) / sd
    # DEN DEFEKTE LINJE: vinduets laengde siver ind i selve scoren som et niveauskift.
    # Defekten er med vilje GROV. Et fikstur skal demonstrere UTVETYDIGT at testen kan
    # fejle; det skal ikke efterligne en subtil fejl. En foerste, mere realistisk
    # udgave (z skaleret med sqrt(vindue/252)) blev forkastet fordi klipningen til
    # 0-100 aad forskellen — fiksturet BESTOD dermed stabilitetstesten og var altsaa
    # selv en kontrol der ikke kunne fejle.
    return float(np.clip(50.0 + z * 12.0 + (vindue - 252) / 6.0, 0, 100))


# ═══════════════════════════════════════════════════════════════════════════════
# Maaleredskaber som V-testene deler
# ═══════════════════════════════════════════════════════════════════════════════
def spearman(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    gyldig = np.isfinite(x) & np.isfinite(y)
    if gyldig.sum() < 3:
        return float("nan")
    # Konstante serier har ingen rangorden — spearmanr giver nan, og det er det
    # aerlige svar. Lad den staa frem for at oversaette den til 0: en 0-korrelation
    # betyder "maalt, ingen sammenhaeng", mens nan betyder "kan ikke maales". At
    # skrive det foerste hvor det sidste gaelder, er praecis den slags stille
    # forkerte svar hele denne spec handler om at undgaa.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConstantInputWarning)
        r = spearmanr(x[gyldig], y[gyldig]).statistic
    return float(r)


def bootstrap_forskel(maal, benchmark, udfald, n_resamples: int = 1000,
                      seed: int = 20260808) -> tuple[float, float, float]:
    """Forskellen i Spearman (maal - benchmark) med 95 %-konfidensinterval.

    Returnerer (forskel, ci_lav, ci_hoej). Ligger ci_lav over 0, slaar maalet
    benchmarken med den valgte sikkerhed — det er V-test 1's bestaa-kriterium.
    Parret resampling: samme indeks bruges til begge, saa forskellen maales paa de
    samme dage og ikke paa to uafhaengige stikproever.
    """
    maal = np.asarray(maal, float)
    benchmark = np.asarray(benchmark, float)
    udfald = np.asarray(udfald, float)
    n = len(udfald)
    forskel = spearman(maal, udfald) - spearman(benchmark, udfald)
    rng = np.random.default_rng(seed)
    forskelle = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, n)
        forskelle[i] = spearman(maal[idx], udfald[idx]) - spearman(benchmark[idx], udfald[idx])
    gyldige = forskelle[np.isfinite(forskelle)]
    if len(gyldige) < n_resamples // 2:
        return forskel, float("nan"), float("nan")
    return forskel, float(np.percentile(gyldige, 2.5)), float(np.percentile(gyldige, 97.5))


# ═══════════════════════════════════════════════════════════════════════════════
# Registret
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class Registrering:
    navn: str
    dumpe_beskrivelse: str
    bestaa_beskrivelse: str
    dumpede: bool
    bestod: bool
    egenskab: str | None = None
    dumpe_detalje: str = ""
    bestaa_detalje: str = ""

    @property
    def gyldig(self) -> bool:
        """Kontrollen skal BAADE kunne dumpe og kunne bestaa. Ellers maaler den intet."""
        return self.dumpede and self.bestod


class Falsifikationskrav(Exception):
    """Rejses naar en kontrol ikke har vist at den kan fejle."""


class Fikstuurfejl(Exception):
    """Rejses naar et fikstur bruges som nul for en egenskab det ikke er nul for."""


# ═══════════════════════════════════════════════════════════════════════════════
# H1 — HVILKEN EGENSKAB ER FIKSTURET NUL FOR?
# ═══════════════════════════════════════════════════════════════════════════════
# Der findes ikke én universel nulserie. Hvid stoej, random walk, shufflet og
# konstant er negative for FORSKELLIGE egenskaber, og at traekke en tilfaeldig af dem
# ned fra hylden er en fejl der ser omhyggelig ud.
#
# Den dyre variant: en random walk har lag-1-autokorrelation taet paa 1, fordi
# niveauet knap flytter sig. Bruges den som kendt-negativ i en PREDIKTIV test, vil en
# fuldstaendig korrekt test BESTAA paa den — og man kasserer noget der virker. Den
# fejl peger den forkerte vej, og det er den farligste slags.
#
# Derfor er taksonomien kode og ikke en note: registret vaelger selv fiksturet ud fra
# den egenskab kontrollen maaler, og AFVISER et haandplukket fikstur der ikke er nul
# for netop den egenskab.

EGENSKABER = {
    "prediktiv": "Kan gaarsdagens vaerdi forudsige morgendagens? (lag-1 rangkorrelation) "
                 "— V-test 1 OG V-test 2 maaler denne egenskab",
    # ⚠ INGEN V-TEST I DENNE BYGGEKLODS MAALER 'regimeophold' (Revision I1).
    # Egenskaben stammer fra den FORRIGE regime-motors V4 (opholdstid, skiftfrekvens)
    # og blev slaebt med herover ved en fejl. Volatilitetsspecen har ingen
    # opholdstidstest, og opholdstid indfoeres bevidst IKKE som kriterium her:
    # hysterese hoerer til beslutningslaget hos meta-strategien, ikke til
    # maaleklodsen (jf. C1). Fiksturet beholdes fordi taksonomien skal kunne sige
    # hvad random_walk ER nul for — men registrerer nogen en V-test mod denne
    # egenskab, er det en fejl der skal fanges her og ikke i rapporten.
    "regimeophold": "Findes der vedvarende tilstande med skift imellem, ud over "
                    "mekanisk glathed? — MAALES IKKE af nogen V-test i byggeklods 1",
    "fordeling": "Adskiller fordelingen sig fra den tilfaeldige?",
}

# V-test 3 og 4 sammenligner MOTOREN MED SIG SELV og kan derfor ikke falsificeres
# med en nulserie. De kraever hver sin defekte motor — se afsnittet om H3 nedenfor.
EGENSKABSLOESE_TESTS = {
    "V-test 3 (stabilitet)": "kraever en PARAMETERFOELSOM motor som fikstur",
    "V-test 4 (realtidsgyldighed)": "kraever en LAEKKENDE motor som fikstur",
}

# For hvert fikstur: hvilke egenskaber er det NUL for, og hvilke HAR det.
# `maalt` er den observerede lag-1-autokorrelation — tallet der afgoer sagen, saa
# ingen behoever tage taksonomien paa mit ord.
FIKSTUUR_TAKSONOMI: dict[str, dict] = {
    "hvid_stoej": {
        "funktion": None,               # udfyldes nedenfor
        "nul_for": {"prediktiv", "regimeophold"},
        "har": set(),
        "maalt_autokorr": -0.012,
        "note": "uafhaengige traekninger — nul for alt vi maaler, men aendrer ogsaa "
                "fordelingen, saa den er svagere end en shufflet serie",
    },
    "shufflet_klynget": {
        "funktion": None,
        "nul_for": {"prediktiv", "regimeophold"},
        "har": {"fordeling"},
        "maalt_autokorr": 0.000,
        "note": "DEN SKARPESTE kendt-negative: samme fordeling som den aegte serie, "
                "tidsstruktur fjernet. Det rette nul for V-test 1.",
    },
    "random_walk": {
        "funktion": None,
        "nul_for": {"regimeophold"},
        # ⚠ DEN AFGOERENDE LINJE. En random walk ER prediktiv — trivielt, via
        # niveaupersistens. Bruges den som nul for "prediktiv", kasserer man en
        # korrekt test.
        "har": {"prediktiv", "fordeling"},
        "maalt_autokorr": 0.995,
        "note": "glathed UDEN regimer. Haard mod opholdstids-paastande, FORBUDT som "
                "nul for prediktive tests.",
    },
    "konstant": {
        "funktion": None,
        "nul_for": {"prediktiv", "regimeophold", "fordeling"},
        "har": set(),
        "maalt_autokorr": float("nan"),
        "note": "degenereret. Brugbar til kantsager, ubrugelig som hovedfikstur — en "
                "kontrol kan dumpe her uden at kunne dumpe paa noget realistisk.",
    },
    "klynget": {
        "funktion": None,
        "nul_for": set(),
        "har": {"prediktiv", "regimeophold", "fordeling"},
        "maalt_autokorr": 0.806,
        "note": "KENDT-POSITIV. Aegte volatilitetsklyngning. Dumper en V-test her, er "
                "testen for stram — ikke markedet der mangler struktur.",
    },
}


def _fyld_taksonomi(n: int = 1200) -> None:
    FIKSTUUR_TAKSONOMI["hvid_stoej"]["funktion"] = lambda: hvid_stoej_serie(n)
    FIKSTUUR_TAKSONOMI["shufflet_klynget"]["funktion"] = lambda: shufflet(klynget_serie(n))
    FIKSTUUR_TAKSONOMI["random_walk"]["funktion"] = lambda: random_walk_serie(n)
    FIKSTUUR_TAKSONOMI["konstant"]["funktion"] = lambda: konstant_serie(n)
    FIKSTUUR_TAKSONOMI["klynget"]["funktion"] = lambda: klynget_serie(n)


def nul_fikstur(egenskab: str, n: int = 1200) -> tuple[str, np.ndarray]:
    """Det RETTE nulfikstur for en given egenskab. Vaelg ikke selv — spoerg her."""
    if egenskab not in EGENSKABER:
        raise Fikstuurfejl(f"ukendt egenskab {egenskab!r}; vaelg blandt {sorted(EGENSKABER)}")
    _fyld_taksonomi(n)
    # Foretraek det fikstur der bevarer flest andre egenskaber — jo mere der er
    # uroert, jo skarpere er nullet.
    kandidater = [(navn, sp) for navn, sp in FIKSTUUR_TAKSONOMI.items()
                  if egenskab in sp["nul_for"] and navn != "konstant"]
    kandidater.sort(key=lambda t: -len(t[1]["har"]))
    navn, spec = kandidater[0]
    return navn, spec["funktion"]()


def positiv_fikstur(egenskab: str, n: int = 1200) -> tuple[str, np.ndarray]:
    """Et fikstur der beviseligt HAR egenskaben. Uden det er en dumpet kontrol tom."""
    if egenskab not in EGENSKABER:
        raise Fikstuurfejl(f"ukendt egenskab {egenskab!r}; vaelg blandt {sorted(EGENSKABER)}")
    _fyld_taksonomi(n)
    for navn, spec in FIKSTUUR_TAKSONOMI.items():
        if egenskab in spec["har"] and not spec["nul_for"]:
            return navn, spec["funktion"]()
    raise Fikstuurfejl(f"intet kendt-positivt fikstur for {egenskab!r}")


def bekraeft_nul(fikstuurnavn: str, egenskab: str) -> None:
    """Er dette fikstur overhovedet nul for den egenskab? Rejser hvis ikke.

    Dette er H1 som kode. Kaldet er det der goer at random_walk ikke kan snige sig
    ind som nul for en prediktiv test — hverken ved uopmaerksomhed eller ved at nogen
    om et aar husker reglen forkert.
    """
    spec = FIKSTUUR_TAKSONOMI.get(fikstuurnavn)
    if spec is None:
        raise Fikstuurfejl(f"ukendt fikstur {fikstuurnavn!r}")
    if egenskab in spec["har"]:
        raise Fikstuurfejl(
            f"{fikstuurnavn!r} HAR egenskaben {egenskab!r} (maalt autokorrelation "
            f"{spec['maalt_autokorr']:+.3f}) og kan derfor ikke vaere nulfikstur for "
            f"den. {spec['note']} Brug i stedet "
            f"{nul_fikstur(egenskab)[0]!r}.")
    if egenskab not in spec["nul_for"]:
        raise Fikstuurfejl(
            f"{fikstuurnavn!r} er ikke erklaeret nul for {egenskab!r}. "
            f"Er det nul, saa skriv det i FIKSTUUR_TAKSONOMI med det maalte tal — "
            f"ikke i hovedet paa den der laeser koden.")


class Falsifikationsregister:
    """Enhver V-test registreres her FOER den koeres paa rigtige data.

    Registret koerer selv de to fikstuer og noterer hvad der faktisk skete. Det er
    forskellen paa "vi mener den kan fejle" og "vi har set den fejle".
    """

    def __init__(self) -> None:
        self.poster: list[Registrering] = []

    def kraev_egenskab(self, navn: str, kontrol: Callable[..., bool], egenskab: str,
                       n: int = 1200) -> Registrering:
        """DEN FORETRUKNE VEJ (H1): navngiv egenskaben, saa vaelges fiksturet for dig.

        Kalderen kan ikke gribe forkert ned paa hylden, fordi kalderen ikke vaelger.
        `egenskab` skal staa i EGENSKABER; for V-test 1 er den "prediktiv".
        """
        nul_navn, nul_data = nul_fikstur(egenskab, n)
        pos_navn, pos_data = positiv_fikstur(egenskab, n)
        return self.kraev(
            navn, kontrol, dumper_paa=nul_data, bestaar_paa=pos_data,
            dumpe_beskrivelse=f"{nul_navn} — nul for '{egenskab}' "
                              f"(autokorr {FIKSTUUR_TAKSONOMI[nul_navn]['maalt_autokorr']:+.3f})",
            bestaa_beskrivelse=f"{pos_navn} — har '{egenskab}' "
                               f"(autokorr {FIKSTUUR_TAKSONOMI[pos_navn]['maalt_autokorr']:+.3f})",
            egenskab=egenskab)

    def kraev(self, navn: str, kontrol: Callable[..., bool], dumper_paa, bestaar_paa,
              dumpe_beskrivelse: str = "", bestaa_beskrivelse: str = "",
              egenskab: str | None = None, nul_fikstuurnavn: str | None = None,
              begrundelse: str = "") -> Registrering:
        """`kontrol(data) -> bool` hvor True = bestaaet.

        `dumper_paa` skal give False, `bestaar_paa` skal give True. Sker det ikke,
        er kontrollen ikke brugbar, og det siges hoejt her frem for at blive opdaget
        naar resultatet allerede er rapporteret.

        Vaelger man selv fikstur (`nul_fikstuurnavn`), verificeres det mod
        taksonomien — et fikstur der HAR egenskaben afvises. Ligger valget helt uden
        for taksonomien, kraeves en skreven `begrundelse`; en afvigelse maa gerne
        forekomme, men ikke i tavshed.
        """
        if nul_fikstuurnavn and egenskab:
            bekraeft_nul(nul_fikstuurnavn, egenskab)     # H1: rejser ved forkert valg
        elif egenskab is None and not begrundelse:
            raise Fikstuurfejl(
                f"{navn!r}: angiv enten `egenskab` (saa vaelges fiksturet korrekt) "
                f"eller en skreven `begrundelse` for hvorfor valget ligger uden for "
                f"taksonomien. Et uargumenteret nulfikstur er praecis den fejl H1 "
                f"handler om.")
        try:
            dumpe_resultat = bool(kontrol(dumper_paa))
            d_detalje = "kontrollen returnerede " + ("BESTAAET" if dumpe_resultat else "DUMPET")
        except Exception as e:
            dumpe_resultat, d_detalje = False, f"kontrollen rejste {type(e).__name__}: {e}"
        try:
            bestaa_resultat = bool(kontrol(bestaar_paa))
            b_detalje = "kontrollen returnerede " + ("BESTAAET" if bestaa_resultat else "DUMPET")
        except Exception as e:
            bestaa_resultat, b_detalje = False, f"kontrollen rejste {type(e).__name__}: {e}"

        post = Registrering(
            navn=navn, egenskab=egenskab,
            dumpe_beskrivelse=dumpe_beskrivelse or "kendt-negativt input",
            bestaa_beskrivelse=bestaa_beskrivelse or "kendt-positivt input",
            dumpede=not dumpe_resultat, bestod=bestaa_resultat,
            dumpe_detalje=d_detalje, bestaa_detalje=b_detalje)
        self.poster.append(post)
        return post

    def rapport(self, sti: Path | None = None, rejs: bool = True) -> str:
        L = ["# Falsifikationsregister — kan kontrollerne overhovedet fejle?\n\n",
             f"Koert: {datetime.now().isoformat(timespec='seconds')}\n\n",
             "Revision G. Hver kontrol er koert mod et input der BURDE faa den til at "
             "dumpe, og et der BURDE faa den til at bestaa. En kontrol der ikke kan "
             "begge dele maaler ikke noget, uanset hvad den rapporterer paa rigtige "
             "data.\n\n",
             "| kontrol | dumper paa kendt-negativ | bestaar paa kendt-positiv | brugbar |\n",
             "|---|---|---|---|\n"]
        for p in self.poster:
            L.append(f"| {p.navn} | {'JA' if p.dumpede else '**NEJ**'} | "
                     f"{'JA' if p.bestod else '**NEJ**'} | "
                     f"{'ja' if p.gyldig else '**NEJ**'} |\n")
        L.append("\n## Detaljer\n")
        for p in self.poster:
            L.append(f"\n### {p.navn}\n\n")
            L.append(f"- **kendt-negativ** ({p.dumpe_beskrivelse}): {p.dumpe_detalje}\n")
            L.append(f"- **kendt-positiv** ({p.bestaa_beskrivelse}): {p.bestaa_detalje}\n")
            if not p.gyldig:
                L.append("\n⚠ **Denne kontrol er ikke brugbar.** "
                         + ("Den bestod paa et input der beviseligt ikke indeholder det "
                            "den paastaar at maale — praecis den fejl der ramte den "
                            "forrige regime-motor. " if not p.dumpede else "")
                         + ("Den dumpede paa et input der beviseligt indeholder det den "
                            "maaler efter, saa den ville ogsaa afvise et aegte fund. "
                            if not p.bestod else "")
                         + "Omskriv den foer resultater rapporteres.\n")
        tekst = "".join(L)
        if sti:
            sti.write_text(tekst, encoding="utf-8")
        ubrugelige = [p.navn for p in self.poster if not p.gyldig]
        if ubrugelige and rejs:
            raise Falsifikationskrav(
                "Disse kontroller har ikke vist at de kan fejle: " + ", ".join(ubrugelige))
        return tekst


# ═══════════════════════════════════════════════════════════════════════════════
# Selvtest: fikstuerne skal vaere det de paastaar
# ═══════════════════════════════════════════════════════════════════════════════
def fikstur_egenskaber(n: int = 1500) -> dict[str, float]:
    """Autokorrelation i serien (lag 1) — maalet paa hvad fikstuerne faktisk indeholder.

    En kendt-negativ fikstur der ved et uheld HAR struktur, oedelaegger hele
    garantien i stilhed. Derfor maales de, og tallene staar i rapporten frem for at
    blive antaget.
    """
    return {
        "hvid_stoej": spearman(hvid_stoej_serie(n)[:-1], hvid_stoej_serie(n)[1:]),
        "random_walk": spearman(random_walk_serie(n)[:-1], random_walk_serie(n)[1:]),
        "shufflet_klynget": spearman(shufflet(klynget_serie(n))[:-1],
                                     shufflet(klynget_serie(n))[1:]),
        "klynget": spearman(klynget_serie(n)[:-1], klynget_serie(n)[1:]),
    }
