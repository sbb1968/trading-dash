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

    from vol_falsifikation import Falsifikationsregister, hvid_stoej_serie
    reg = Falsifikationsregister()
    reg.kraev("V-test 1", vtest1, dumper_paa=hvid_stoej_serie(500),
              bestaar_paa=klynget_serie(500))
    reg.rapport()          # skriver vol_falsifikation.md — og raiser hvis noget mangler

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
    dumpe_detalje: str = ""
    bestaa_detalje: str = ""

    @property
    def gyldig(self) -> bool:
        """Kontrollen skal BAADE kunne dumpe og kunne bestaa. Ellers maaler den intet."""
        return self.dumpede and self.bestod


class Falsifikationskrav(Exception):
    """Rejses naar en kontrol ikke har vist at den kan fejle."""


class Falsifikationsregister:
    """Enhver V-test registreres her FOER den koeres paa rigtige data.

    Registret koerer selv de to fikstuer og noterer hvad der faktisk skete. Det er
    forskellen paa "vi mener den kan fejle" og "vi har set den fejle".
    """

    def __init__(self) -> None:
        self.poster: list[Registrering] = []

    def kraev(self, navn: str, kontrol: Callable[..., bool], dumper_paa, bestaar_paa,
              dumpe_beskrivelse: str = "", bestaa_beskrivelse: str = "") -> Registrering:
        """`kontrol(data) -> bool` hvor True = bestaaet.

        `dumper_paa` skal give False, `bestaar_paa` skal give True. Sker det ikke,
        er kontrollen ikke brugbar, og det siges hoejt her frem for at blive opdaget
        naar resultatet allerede er rapporteret.
        """
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
            navn=navn,
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
