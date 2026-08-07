"""
vol_percentil.py — percentil mod egen historik, uden at kigge fremad
═══════════════════════════════════════════════════════════════════════════════════
Alle tre lag i regime-determineringen udtrykker deres komponenter som percentiler
mod seriens egen historik. Maskineriet bygges derfor ÉN gang her frem for at blive
gentaget tre steder med tre lidt forskellige fortolkninger af "historik".

DEN AFGOERENDE REGEL, og den eneste der virkelig er svaer:

    pct(serie, d) maales mod dage STRENGT FOER d. Aldrig dagen selv.

Tages dagen selv med, laekker fremtiden ind i fortiden — og et look-ahead-laek goer
ALLE oevrige resultater for gode uden at noget fejler. Det er praecis dét
V-test 4 leder efter, og `motor_med_laek` i vol_falsifikation.py er bygget for at
bevise at testen faktisk fanger det.

TRE REFERENCER (spec v2.0 afsnit 2):

    ekspanderende  alt kendt foer d          PRIMAER
    252            de seneste 252 dage foer d
    504            de seneste 504 dage foer d

Den ekspanderende er primaer af en grund der er let at overse: med en KORT rullende
reference bliver etiketterne rent relative. Motoren ville kalde de vaerste dage i et
roligt aar for "stress", fordi den ikke har set andet. Med en ekspanderende
reference indeholder grundlaget i 2026 baade marts 2020 og hele 2022, saa den 90.
percentil svarer til et niveau der faktisk ER forhoejet.

De to rullende koeres som robusthedsgitter i V3 — de rapporteres, de vaelges ikke
imellem.

BURN-IN: en percentil beregnet paa faa observationer er stoej med to decimaler.
Foer BURNIN_LAG1 gyldige dage returneres None frem for et tal der ser praecist ud.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

# ── Parametre (spec v2.0 afsnit 2) ────────────────────────────────────────────
BURNIN_LAG1 = 252                                  # handelsdage foer en lag 1-pct er gyldig
REFERENCER  = ("ekspanderende", "252", "504")
PRIMAER_REF = "ekspanderende"

# Rullende vinduers laengde. Den ekspanderende har ingen — den er alt hidtil.
VINDUE = {"252": 252, "504": 504}


class Percentilfejl(Exception):
    """Kaldet er ugyldigt — fx en ukendt reference. Aldrig et stille fallback."""


@dataclass(frozen=True)
class Punkt:
    """Én dags percentil, med det grundlag den blev beregnet paa.

    `n` er ikke pynt: en percentil paa 260 observationer og en paa 4.000 er ikke
    lige meget vaerd, og forskellen skal kunne ses bagefter uden at genberegne.
    """
    dag: date
    vaerdi: float
    pct: Optional[float]     # 0-100, eller None foer burn-in
    n: int                   # antal observationer i grundlaget


def percentil_af(sorteret: list[float], x: float) -> float:
    """Hvor stor en andel af `sorteret` ligger under `x`? 0-100.

    Midtpunkts-konvention: vaerdier LIG x taeller med det halve. Uden den ville en
    serie med mange gentagne vaerdier (og det har VIX-familien, der kvoteres i
    hele hundrededele) give systematisk for lave percentiler — alle de identiske
    dage ville taelle som "ikke under".

    `sorteret` SKAL vaere sorteret; det er kalderens ansvar og gjort af hensyn til
    hastighed. Modulet kalder aldrig med andet.
    """
    if not sorteret:
        raise Percentilfejl("tomt grundlag — percentil er udefineret")
    under = bisect.bisect_left(sorteret, x)
    lig = bisect.bisect_right(sorteret, x) - under
    return 100.0 * (under + 0.5 * lig) / len(sorteret)


def beregn(serie: dict[date, float], reference: str = PRIMAER_REF,
           burnin: int = BURNIN_LAG1) -> list[Punkt]:
    """Percentil for hver dag i serien, maalt mod dage STRENGT FOER dagen selv.

    Returnerer én Punkt pr. dag i kronologisk orden. Dage foer burn-in faar
    pct=None — de er med i listen, saa kalderen kan se at dagen fandtes, men
    uden et tal der foregiver praecision.

    Implementationen holder grundlaget sorteret og indsaetter dagens vaerdi FOERST
    EFTER at percentilen er beregnet. Det er dér look-ahead ellers sniger sig ind,
    og raekkefoelgen er derfor ikke en detalje man maa bytte om paa for laesbarhed.
    """
    if reference not in REFERENCER:
        raise Percentilfejl(
            f"ukendt reference {reference!r} — kendte: {', '.join(REFERENCER)}")

    dage = sorted(serie)
    grundlag: list[float] = []          # sorteret, kun dage FOER den aktuelle
    kronologisk: list[float] = []       # samme vaerdier i tidsorden, til vinduet
    ud: list[Punkt] = []

    vindue = VINDUE.get(reference)      # None for ekspanderende

    for d in dage:
        x = serie[d]
        if vindue is not None and len(kronologisk) > vindue:
            # Rullende: fjern de vaerdier der er faldet ud af vinduet. De fjernes
            # fra det SORTEREDE grundlag, ikke fra enden — derfor bisect.
            for gammel in kronologisk[:len(kronologisk) - vindue]:
                i = bisect.bisect_left(grundlag, gammel)
                if i < len(grundlag) and grundlag[i] == gammel:
                    grundlag.pop(i)
            kronologisk = kronologisk[len(kronologisk) - vindue:]

        n = len(grundlag)
        pct = percentil_af(grundlag, x) if n >= burnin else None
        ud.append(Punkt(dag=d, vaerdi=x, pct=pct, n=n))

        # FOERST NU kommer dagen med i grundlaget.
        bisect.insort(grundlag, x)
        kronologisk.append(x)

    return ud


def som_opslag(punkter: Iterable[Punkt]) -> dict[date, Optional[float]]:
    """{dag: percentil}. Bekvemmelighed til sammenvejning."""
    return {p.dag: p.pct for p in punkter}


def foerste_gyldige(punkter: Iterable[Punkt]) -> Optional[date]:
    """Foerste dag med en gyldig percentil — altsaa hvor burn-in er overstaaet."""
    for p in punkter:
        if p.pct is not None:
            return p.dag
    return None
