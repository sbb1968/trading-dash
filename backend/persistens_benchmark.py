#!/usr/bin/env python3
r"""
persistens_benchmark.py — den naive benchmark, bygget FØR nogen kandidat findes
════════════════════════════════════════════════════════════════════════════════
`spec_persistens.md` §1.2 og §9. Benchmarken er *gårsdagens ER som prædiktor
for morgendagens ER*, og dens tal skrives ned før den første kandidatprædiktor
eksisterer i koden.

⚠ HVORFOR REKKEFØLGEN ER BINDENDE. Skriver man kandidaten først og benchmarken
bagefter, kalibrerer man — uden at ville det — mod en benchmark man allerede
kender styrken af. Samme regel som volatilitetsspecens §0.2.

⚠ OG BENCHMARKEN SKAL VÆRE DET STÆRKESTE SIMPLE ALTERNATIV, ikke det svageste.
"I går trendede det, så antag trend i dag" er faktisk en god gætning i et
marked med volatilitetsklyngning. Slår vi den ikke, har vi ikke tilføjet noget,
og så ER den svaret.

────────────────────────────────────────────────────────────────────────────────
MÅLET (spec §1.1), præcist som det er implementeret:

    ER(d) = |luk − aabning| / Σ|serie(i) − serie(i−1)|

    serie = [aabning, luk_1, luk_2, … luk_N]   for dagens 5-min RTH-barer

⚠ AABNINGEN ER MED I NÆVNEREN. Uden den ville bevægelsen fra sessionens åbning
til første bars luk tælle i tælleren men ikke i nævneren, og ER kunne overstige
1 på en dag der åbnede med et spring. Med den er ER ∈ [0, 1] pr. konstruktion —
og et mål der pr. definition ligger i sit interval, kan kontrolleres.

⚠ 5-MIN ER LÅST (spec §2.2). 1-min domineres af mikrostruktur: bid-ask-bounce
blæser nævneren op og får alt til at ligne chop. Robusthedsgitteret 1/5/15
rapporteres, men valget er ikke frit.

⚠ HALVE DAGE UDELADES (spec §2.1) — den vigtigste tekniske fælde i specen.
ER's nævner er en SUM OVER BARER. En fuld session har 78 5-min-barer, en halv
har 42. Færre barer → mindre sum → MEKANISK højere ER. Halve dage ville altså
fremstå som trend-dage uanset hvad markedet gjorde — og de klumper sæsonmæssigt
(dagen efter Thanksgiving, 3. juli, juleaftensdag), så artefaktet ville ligne et
ægte fund om november og december.

    python persistens_benchmark.py
    python persistens_benchmark.py --bar 15        # robusthedsgitteret
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

HER = Path(__file__).resolve().parent
CACHE = HER / "vol_cache"

from nyse_kalender import er_halv_dag, er_handelsdag       # noqa: E402

# ── Dataopdeling (spec §5) — håndhæves, ikke huskes ─────────────────────────
UDVIKLING_SLUT = date(2023, 12, 31)

RTH_FRA, RTH_TIL = 9 * 60 + 30, 16 * 60
BEN = ["SPY", "IWM"]          # rapportér begge; Russell kan afvige fra S&P


# ── Indlæsning ──────────────────────────────────────────────────────────────
def femmin_pr_dag(instrument: str, minutter: int) -> dict[date, list[tuple[float, float]]]:
    """{dag: [(aabning, luk), …]} for RTH-barer af `minutter`.

    ⚠ SPANDENE FØLGER URET, ikke bar 0 — 09:30, 09:35, 09:40. Talte vi barer,
    ville ét manglende minut forskyde alle spande resten af dagen.
    """
    import pytz
    ET = pytz.timezone("America/New_York")
    p = CACHE / f"{instrument}_1min.csv"
    if not p.exists():
        raise SystemExit(f"⚠ findes ikke: {p}")

    spand: dict[date, dict[int, list]] = defaultdict(dict)
    with p.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                u = datetime.fromisoformat(r["timestamp"])
            except ValueError:
                continue
            if u.tzinfo is None:
                u = u.replace(tzinfo=timezone.utc)
            e = u.astimezone(ET)
            m = e.hour * 60 + e.minute
            if not (RTH_FRA <= m < RTH_TIL):
                continue
            try:
                o, c = float(r["open"]), float(r["close"])
            except (ValueError, KeyError):
                continue
            k = (m - RTH_FRA) // minutter
            d = e.date()
            v = spand[d].get(k)
            if v is None:
                spand[d][k] = [m, o, m, c]        # [tidligst, open, senest, close]
            else:
                if m < v[0]:
                    v[0], v[1] = m, o
                if m > v[2]:
                    v[2], v[3] = m, c
    return {d: [(v[1], v[3]) for _, v in sorted(ks.items())]
            for d, ks in spand.items()}


def er_pr_dag(barer: dict[date, list[tuple[float, float]]],
              minutter: int) -> tuple[dict[date, float], dict]:
    """ER pr. dag + en revision af hvad der blev udeladt og hvorfor."""
    ventet = (RTH_TIL - RTH_FRA) // minutter          # 78 ved 5 min
    ud: dict[date, float] = {}
    revision = {"halve": 0, "ikke_handelsdag": 0, "for_faa_barer": 0,
                "nul_naevner": 0, "brugt": 0, "afvigende_antal": defaultdict(int)}
    for d, b in barer.items():
        if not er_handelsdag(d):
            revision["ikke_handelsdag"] += 1
            continue
        if er_halv_dag(d):
            revision["halve"] += 1
            continue
        # ⚠ B2: barantallet skal være KONSTANT hen over historikken. Skifter det
        # uden at kalenderen kender dagen, er sessionsdefinitionen ændret — og
        # så måler ER noget andet i den ene ende af serien end i den anden.
        revision["afvigende_antal"][len(b)] += 1
        if len(b) < ventet:
            revision["for_faa_barer"] += 1
            continue
        serie = [b[0][0]] + [x[1] for x in b]         # aabning + alle lukninger
        naevner = sum(abs(serie[i] - serie[i - 1]) for i in range(1, len(serie)))
        if naevner <= 0:
            revision["nul_naevner"] += 1
            continue
        ud[d] = abs(serie[-1] - serie[0]) / naevner
        revision["brugt"] += 1
    return ud, revision


def naeste_handelsdag(d: date) -> date:
    from datetime import timedelta
    n = d + timedelta(days=1)
    for _ in range(12):                      # laengste US-helligdagsbro
        if er_handelsdag(n):
            return n
        n += timedelta(days=1)
    return n


def naboliste(er: dict[date, float], dage: list[date]):
    """Par KUN faktiske nabo-handelsdage.

    ⚠ FOERSTE UDGAVE PAREDE NABOINDGANGE I DEN FILTREREDE LISTE. Naar en halv
    dag udelades, bliver "i gaar" da forrige handelsdag FOER den — altsaa to
    dage tilbage. Det ramte 32 af 2.991 par (1,1 %).
    #
    ⚠ Og de 32 er ikke tilfaeldige: halve dage klumper om helligdage, saa de
    forkerte par er SYSTEMATISK udvalgte. Med 1,1 % kan de ikke vende et
    fortegn med det KI — men en rettelse foretaget EFTER kandidattallene
    foreligger, kan ikke skelnes fra tuning. Derfor nu.
    """
    par, sprunget = [], 0
    haves = set(dage)
    for i in range(len(dage) - 1):
        d, e = dage[i], dage[i + 1]
        if naeste_handelsdag(d) != e:
            sprunget += 1
            continue
        par.append((er[d], er[e]))
    return par, sprunget


# ── Statistik ───────────────────────────────────────────────────────────────
def spearman(x: list[float], y: list[float]) -> float:
    def rang(v):
        par = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(par):                      # gennemsnitsrang ved bindinger
            j = i
            while j + 1 < len(par) and v[par[j + 1]] == v[par[i]]:
                j += 1
            m = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[par[k]] = m
            i = j + 1
        return r
    rx, ry = rang(x), rang(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def blok_bootstrap(x: list[float], y: list[float], blok: int,
                   n_gentag: int = 1000, froe: int = 20260817) -> tuple[float, float]:
    """KI95 for Spearman med blok-resampling.

    ⚠ BLOKKE, IKKE ENKELTDAGE. Lag 1's test målte at enkeltdags-KI var 3,1×
    for smalt, fordi målvinduerne overlapper. Her overlapper ER(d) og ER(d+1)
    ikke i tid — men persistens er netop klyngedannende, så nabodage er
    stærkt afhængige alligevel. Blokken bevarer den afhængighed.
    """
    rnd = random.Random(froe)
    n = len(x)
    n_blok = max(1, n // blok)
    ud = []
    for _ in range(n_gentag):
        xi, yi = [], []
        for _ in range(n_blok):
            s = rnd.randrange(0, max(1, n - blok))
            xi += x[s:s + blok]
            yi += y[s:s + blok]
        if len(xi) > 10:
            ud.append(spearman(xi, yi))
    ud.sort()
    return ud[int(0.025 * len(ud))], ud[int(0.975 * len(ud))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bar", type=int, default=5, help="barstørrelse i minutter")
    ap.add_argument("--alle-perioder", action="store_true",
                    help="⚠ bryder dataopdelingen — kun til diagnostik")
    a = ap.parse_args()

    print("═" * 78)
    print(f"PERSISTENS-BENCHMARK — gårsdagens ER som prædiktor for morgendagens")
    print(f"{a.bar}-min barer · udviklingsperiode → {UDVIKLING_SLUT}")
    print("═" * 78)

    for inst in BEN:
        barer = femmin_pr_dag(inst, a.bar)
        er, rev = er_pr_dag(barer, a.bar)

        # ⚠ DATAOPDELINGEN HÅNDHÆVES I KODE (spec §5), ikke i disciplin.
        if not a.alle_perioder:
            er = {d: v for d, v in er.items() if d <= UDVIKLING_SLUT}

        dage = sorted(er)
        par, sprunget = naboliste(er, dage)
        x = [p[0] for p in par]
        y = [p[1] for p in par]

        print(f"\n── {inst} ──")
        print(f"   dage i alt {sum(rev['afvigende_antal'].values()):>6}"
              f" · brugt {rev['brugt']:>6}"
              f" · halve udeladt {rev['halve']:>4}"
              f" · for få barer {rev['for_faa_barer']:>4}")
        antal = sorted(rev["afvigende_antal"].items(), key=lambda t: -t[1])[:3]
        print(f"   barer pr. session: " +
              " · ".join(f"{k}→{v} dage" for k, v in antal))
        if len(antal) > 1 and antal[1][1] > 0.02 * rev["brugt"]:
            print(f"   ⚠ barantallet er IKKE konstant — se spec §2.1/B2")
        print(f"   ER: median {st.median(er.values()):.4f}"
              f" · middel {st.mean(er.values()):.4f}"
              f" · spredning {st.pstdev(er.values()):.4f}")
        print(f"   par (d → d+1): {len(par):,}"
              f"   · {sprunget} par sprunget over (ikke nabo-handelsdage)")

        rho = spearman(x, y)
        print(f"\n   Spearman ER(d) → ER(d+1)   rho {rho:+.4f}")
        for blok in (20, 40):
            lo, hi = blok_bootstrap(x, y, blok)
            udenfor = "≠ nul" if (lo > 0 or hi < 0) else "⚠ RUMMER NUL"
            print(f"      blok {blok:>3}   KI95 [{lo:+.4f}, {hi:+.4f}]"
                  f"   bredde {hi-lo:.4f}   {udenfor}")

    print("\n" + "═" * 78)
    print("Det er BENCHMARKEN. Ingen kandidatprædiktor findes endnu i koden.")
    print("")
    print("⚠ BEDØM PÅ |rho|, IKKE PÅ rho (Revision A). Med en NEGATIV benchmark")
    print("  ville 'slå benchmarkens Spearman' være opfyldt af et møntkast:")
    print("  0,00 vinder over -0,08 med 0,08. En kandidat på +0,05 vinder med")
    print("  0,13 uden at være værd at handle på — en kontrol hvis udfald er")
    print("  strukturelt gunstigt.")
    print("")
    print("  Benchmarkens BRUGBARE styrke er |rho|. En stabil negativ")
    print("  sammenhæng er lige så anvendelig som en positiv af samme")
    print("  størrelse; man vender fortegnet i aflæsningen.")
    print("  Fortegnsstabilitet hen over gitteret 1/5/15-min er et")
    print("  SELVSTÆNDIGT krav — skifter fortegnet, er prædiktoren ubrugelig")
    print("  uanset |rho|.")
    print("═" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
