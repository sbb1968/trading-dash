"""
vol_lag1_test.py — forudsiger lag 1 morgendagens volatilitet bedre end i gaars?
═══════════════════════════════════════════════════════════════════════════════════
Dette er byggeklodsens foerste rigtige svar. Alt hidtil har vaeret infrastruktur og
metode; her faar vi at vide om percentiltilgangen overhovedet baerer.

    MAAL       realiseret vol paa SPY over de NAESTE 20 handelsdage (d+1 .. d+20)
    BENCHMARK  samme maal over de FOREGAAENDE 20 dage — altsaa rv20 paa dag d
    METODE     Spearman for hver mod maalet, derefter bootstrap paa FORSKELLEN
    BESTAA     lag 1 slaar benchmarken, og forskellens KI udelukker nul

Benchmarken er ikke tilfaeldigt valgt. Volatilitet klynger — i gaars vol er en
staerk forudsigelse af morgendagens, og enhver model skal slaa netop dét for at
have tilfoejet noget. En test mod "ingen viden" ville vaere let at bestaa og
intet vaerd.

⚠ BLOKBOOTSTRAP ER OBLIGATORISK, ikke en forfining.
Maalet er beregnet over 20 OVERLAPPENDE dage, saa to nabodage deler 19 af 20
observationer. Et bootstrap der resampler ENKELTDAGE behandler dem som uafhaengige
og undervurderer spredningen dramatisk — den reelle maengde uafhaengig information
er omkring en tyvendedel af antallet af raekker. Resultatet ville vaere smalle
intervaller og selvsikre konklusioner paa et grundlag der ikke baerer dem.
Modulet demonstrerer forskellen frem for at paastaa den.

⚠ FALSIFIKATION FOERST (Revision G). Testen koeres paa to konstruerede maal foer
den roerer lag 1:
    et beviseligt VAERDILOEST maal maa IKKE slaa benchmarken
    benchmarken maa IKKE slaa et maal der beviseligt er BEDRE
Uden begge retninger ved vi ikke om testen maaler noget eller bare altid siger
det samme.

⚠ UDVIKLINGSPERIODEN ER HAARD. Intet kald her kan laese data efter 2023-12-31.
De tre design-valideringskoersler paa 2024 er et faelles budget for HELE
byggeklodsen og maa ikke braendes paa en lag 1-udgave der senere aendres.

    python vol_lag1_test.py
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import vol_lag1 as l1
import vol_percentil as vp
import vol_serier as vs

# ── Haard graense (spec v2.0 afsnit 6) ────────────────────────────────────────
UDVIKLING_SLUT = date(2023, 12, 31)

HORISONT   = 20        # handelsdage frem — maalets vindue
BLOK       = 20        # bootstrap-bloklaengde >= horisonten
BLOK_ALT   = 40        # anden bloklaengde, saa resultatet ikke hviler paa én
BOOTSTRAP  = 2000
KI         = 95        # konfidensniveau i procent
FROE       = 20260807  # fast, saa koerslen er reproducerbar


class Testfejl(Exception):
    """Testen kan ikke koeres paa en maade der er til at stole paa."""


@dataclass
class Resultat:
    navn: str
    n: int
    rho_maal: float          # Spearman: kandidat mod fremtidig vol
    rho_bench: float         # Spearman: benchmark mod fremtidig vol
    forskel: float
    ki_lav: float
    ki_hoej: float
    blok: int

    @property
    def ki_bredde(self) -> float:
        return self.ki_hoej - self.ki_lav

    @property
    def bestaar(self) -> bool:
        """Slaar kandidaten benchmarken, OG udelukker intervallet nul?"""
        return self.forskel > 0 and self.ki_lav > 0

    def konklusion(self) -> str:
        """K2's skelnen: 'de er lige gode' er IKKE det samme som 'vi ved det ikke'."""
        if self.bestaar:
            return "POSITIVT FUND — kandidaten slaar benchmarken"
        if self.ki_lav <= 0 <= self.ki_hoej:
            snaevert = self.ki_bredde < 0.05
            return ("AFGJORT: ingen forskel (snaevert KI)" if snaevert
                    else "UAFKLARET: KI rummer nul OG er bredt — vi VED det ikke")
        return "NEGATIVT FUND — benchmarken er bedst"


# ═══════════════════════════════════════════════════════════════════════════════
# Statistik
# ═══════════════════════════════════════════════════════════════════════════════
def rang(x: Sequence[float]) -> list[float]:
    """Gennemsnitsrang ved bindinger. Uden det ville gentagne vaerdier faa
    vilkaarlig rangorden og goere Spearman afhaengig af sorteringens stabilitet."""
    par = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(par):
        j = i
        while j + 1 < len(par) and x[par[j + 1]] == x[par[i]]:
            j += 1
        snit = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[par[k]] = snit
        i = j + 1
    return r


def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n < 3:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    kov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return kov / math.sqrt(va * vb)


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    return pearson(rang(a), rang(b))


def blokbootstrap(kandidat: Sequence[float], bench: Sequence[float],
                  maal: Sequence[float], blok: int, gentag: int = BOOTSTRAP,
                  froe: int = FROE) -> tuple[float, float]:
    """KI for FORSKELLEN i Spearman (kandidat minus benchmark), blokvis.

    Blokke af SAMMENHAENGENDE dage traekkes med tilbagelaegning. Det bevarer
    naboskabet mellem overlappende maalvinduer — netop dét et enkeltdags-bootstrap
    oedelaegger, og som ellers giver kunstigt smalle intervaller.

    PARRET: de samme dage indgaar i begge korrelationer i hver traekning. Det er
    forskellen vi maaler, og den maales praecist selvom hver enkelt korrelation er
    usikker (Revision K2).
    """
    n = len(maal)
    if n < blok * 3:
        raise Testfejl(f"for faa observationer ({n}) til blokke paa {blok}")
    rng = random.Random(froe)
    antal_blokke = math.ceil(n / blok)
    forskelle = []
    for _ in range(gentag):
        idx = []
        for _ in range(antal_blokke):
            start = rng.randrange(0, n - blok + 1)
            idx.extend(range(start, start + blok))
        idx = idx[:n]
        k = [kandidat[i] for i in idx]
        b = [bench[i] for i in idx]
        m = [maal[i] for i in idx]
        d = spearman(k, m) - spearman(b, m)
        if not math.isnan(d):
            forskelle.append(d)
    forskelle.sort()
    lo = forskelle[int((100 - KI) / 2 / 100 * len(forskelle))]
    hi = forskelle[min(int((1 - (100 - KI) / 2 / 100) * len(forskelle)),
                       len(forskelle) - 1)]
    return lo, hi


def evaluer(navn: str, kandidat: Sequence[float], bench: Sequence[float],
            maal: Sequence[float], blok: int = BLOK) -> Resultat:
    rk = spearman(kandidat, maal)
    rb = spearman(bench, maal)
    lo, hi = blokbootstrap(kandidat, bench, maal, blok)
    return Resultat(navn, len(maal), rk, rb, rk - rb, lo, hi, blok)


# ═══════════════════════════════════════════════════════════════════════════════
# Datagrundlag
# ═══════════════════════════════════════════════════════════════════════════════
def fremtidig_vol(afkast: dict[date, float], horisont: int = HORISONT
                  ) -> dict[date, float]:
    """Annualiseret std. af log-afkast over d+1 .. d+horisont.

    Bemaerk at dagen selv IKKE er med: maalet er hvad der sker EFTER vi har
    udtalt os. Tages d med, forudsiger vi delvist noget vi allerede har set.
    """
    dage = sorted(afkast)
    ud = {}
    for i in range(len(dage) - horisont):
        v = [afkast[dage[j]] for j in range(i + 1, i + 1 + horisont)]
        m = sum(v) / horisont
        var = sum((x - m) ** 2 for x in v) / (horisont - 1)
        ud[dage[i]] = math.sqrt(var) * math.sqrt(l1.HANDELSDAGE_PR_AAR)
    return ud


def byg_grundlag(slut: date = UDVIKLING_SLUT):
    """(dage, score, benchmark, maal) paa de dage hvor ALLE tre findes.

    ⚠ `slut` haardkodes til udviklingsperioden. Bliver den sat senere, kastes der
    — de tre design-valideringskoersler paa 2024 er et faelles budget for hele
    byggeklodsen og maa ikke braendes her.
    """
    if slut > UDVIKLING_SLUT:
        raise Testfejl(
            f"slut={slut} ligger efter udviklingsperioden ({UDVIKLING_SLUT}). "
            f"Design-validering paa 2024 er et faelles budget paa TRE koersler for "
            f"hele byggeklodsen — de maa ikke bruges paa lag 1 alene.")

    lag1 = {d.dag: d.score for d in l1.beregn_lag1(slut=slut) if d.score is not None}
    spy = vs.laes_serie("SPY")
    afk = l1.log_afkast(spy)
    bench = l1.realiseret_vol(afk, l1.RV_KORT)      # rv20 paa dag d
    maal = fremtidig_vol(afk)                        # d+1 .. d+20

    dage = sorted(set(lag1) & set(bench) & set(maal))
    dage = [d for d in dage if d <= slut]
    return (dage,
            [lag1[d] for d in dage],
            [bench[d] for d in dage],
            [maal[d] for d in dage])


# ═══════════════════════════════════════════════════════════════════════════════
# Falsifikation — FOER testen roerer lag 1
# ═══════════════════════════════════════════════════════════════════════════════
def falsificer(bench, maal, skriv=print) -> bool:
    """Kan testen overhovedet fejle? Begge retninger skal vises (Revision G).

    Returnerer True hvis testen opfoerer sig som den skal.
    """
    ok = True
    rng = random.Random(FROE)

    # ── Retning 1: et VAERDILOEST maal maa IKKE slaa benchmarken ──────────────
    # H1: den rette kendt-negative for en PRAEDIKTIV egenskab er en SHUFFLET
    # udgave af den aegte serie — samme fordeling, tidsstruktur fjernet. IKKE en
    # random walk, som er staerkt positiv for niveaupersistens og derfor ville
    # faa en korrekt test til at se defekt ud.
    vaerdiloes = list(bench)
    rng.shuffle(vaerdiloes)
    r1 = evaluer("shufflet benchmark (kendt-negativ)", vaerdiloes, bench, maal)
    skriv(f"   kendt-NEGATIV : forskel {r1.forskel:+.4f}  "
          f"KI [{r1.ki_lav:+.4f}, {r1.ki_hoej:+.4f}]  -> {r1.konklusion()}")
    if r1.bestaar:
        skriv("   ⛔ TESTEN ER DEFEKT: et shufflet maal 'slog' benchmarken.")
        ok = False

    # ── Retning 2: benchmarken maa IKKE slaa et BEDRE maal ───────────────────
    # Det bedre maal er maalet selv plus stoej — det kender fremtiden og SKAL
    # vinde. Bestaar den ikke, mangler testen styrke og kan ikke opdage en aegte
    # forbedring heller.
    bedre = [m + rng.gauss(0, 0.02) for m in maal]
    r2 = evaluer("maalet selv + stoej (kendt-positiv)", bedre, bench, maal)
    skriv(f"   kendt-POSITIV : forskel {r2.forskel:+.4f}  "
          f"KI [{r2.ki_lav:+.4f}, {r2.ki_hoej:+.4f}]  -> {r2.konklusion()}")
    if not r2.bestaar:
        skriv("   ⛔ TESTEN MANGLER STYRKE: den kunne ikke se et maal der kender "
              "fremtiden.")
        ok = False

    return ok


def bootstrap_sammenligning(kand, bench, maal, skriv=print) -> None:
    """Vis HVORFOR blokbootstrap er obligatorisk frem for at paastaa det."""
    lo1, hi1 = blokbootstrap(kand, bench, maal, blok=1)
    lo2, hi2 = blokbootstrap(kand, bench, maal, blok=BLOK)
    skriv(f"   enkeltdage (blok=1)   KI [{lo1:+.4f}, {hi1:+.4f}]  bredde {hi1-lo1:.4f}")
    skriv(f"   blokke  (blok={BLOK})     KI [{lo2:+.4f}, {hi2:+.4f}]  bredde {hi2-lo2:.4f}")
    skriv(f"   -> enkeltdags-KI er {(hi2-lo2)/(hi1-lo1):.1f}x for SMALT. "
          f"Maalvinduerne overlapper 19 af 20 dage.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Lag 1's praediktive test")
    ap.add_argument("--slut", default=UDVIKLING_SLUT.isoformat())
    args = ap.parse_args()
    slut = date.fromisoformat(args.slut)

    print("=" * 74)
    print("  LAG 1 — PRAEDIKTIV TEST (spec v2.0 afsnit 5)")
    print(f"  udviklingsperiode til {slut} · config_hash {l1.config_hash()}")
    print("=" * 74)

    dage, score, bench, maal = byg_grundlag(slut)
    print(f"\n  {len(dage)} dage: {dage[0]} -> {dage[-1]}")
    print(f"  maal      : realiseret vol d+1..d+{HORISONT} paa SPY")
    print(f"  benchmark : rv{l1.RV_KORT} paa dag d")

    print("\n── FALSIFIKATION (foer lag 1 roeres) ──────────────────────────────")
    if not falsificer(bench, maal):
        print("\n  Testen er ikke troværdig. Lag 1 vurderes IKKE.")
        return 1
    print("   ✅ testen kan fejle i begge retninger")

    print("\n── HVORFOR BLOKBOOTSTRAP ──────────────────────────────────────────")
    bootstrap_sammenligning(score, bench, maal)

    print("\n── LAG 1 ──────────────────────────────────────────────────────────")
    for blok in (BLOK, BLOK_ALT):
        r = evaluer("lag 1", score, bench, maal, blok=blok)
        print(f"   blok={blok:3d}  Spearman lag1 {r.rho_maal:+.4f} · "
              f"benchmark {r.rho_bench:+.4f} · forskel {r.forskel:+.4f}")
        print(f"            KI{KI} [{r.ki_lav:+.4f}, {r.ki_hoej:+.4f}]  "
              f"bredde {r.ki_bredde:.4f}")
        print(f"            {r.konklusion()}")

    r = evaluer("lag 1", score, bench, maal, blok=BLOK)
    print("\n" + "=" * 74)
    if r.bestaar:
        print("  BESTAAET — lag 1 tilfoejer noget ud over rv20.")
        return 0
    print("  IKKE BESTAAET.")
    print("  Svaret er da at bruge rv20 som lag 1-maal og skrive det aabent.")
    print("  Det er ikke en fiasko, men den billigst mulige opdagelse af at fire")
    print("  komponenter ikke tilfoejer noget ud over den ene.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
