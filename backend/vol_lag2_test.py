"""
vol_lag2_test.py — lag 2's prædiktive test
════════════════════════════════════════════════════════════════════════════════
Spørgsmålet er ikke om lag 2 hænger sammen med morgendagens uro. Det gør næsten
alt der har med volatilitet at gøre. Spørgsmålet er om det slår **gårsdagens
range** — den naive antagelse at i dag ligner i går.

  Mål        RTH-range på dag d, normaliseret ved gårsdagens luk
  Benchmark  RTH-range på dag d−1, samme normalisering
  Bestå      lag 2 slår benchmarken, og forskellens KI udelukker nul

⚠ K2 ER BENCHMARKEN. Testen spørger altså reelt om K1 (natbevægelse) og K3
(baggrundstilstand) tilføjer noget ud over gårsdagens range. Gør de ikke det, er
det rigtige svar at bruge gårsdagens range alene — et gyldigt udfald, ikke en
fejl der skal bortforklares.

────────────────────────────────────────────────────────────────────────────────
⚠ BLOKKENE HER ER IKKE NØDVENDIGE AF SAMME GRUND SOM I LAG 1

I lag 1 var målet beregnet over et OVERLAPPENDE vindue: dag d's og dag d+1's mål
delte observationer, og enkeltdags-bootstrap ville derfor have undervurderet
usikkerheden groft. Blokke var strengt nødvendige.

Her deler dag d's range ingen observationer med dag d+1's. Blokke er ikke
nødvendige af den grund — men volatilitet KLYNGER, så nabodage er alligevel ikke
uafhængige. Derfor rapporteres BEGGE: blok på 5 dage og enkeltdage. Er de næsten
ens, er sagen ligetil. Er de meget forskellige, bærer klyngningen mere end
antaget, og det skal frem frem for at blive valgt bort.

Skriv altid begge tal, så ingen senere generaliserer forkert fra ét lag til et andet.

    python vol_lag2_test.py
"""
from __future__ import annotations

import datetime as dt
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vol_lag2 as l2

FEJL: list[str] = []
BOOTSTRAP_N = 2000
FROE = 20260810


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# ═══════════════════════════════════════════════════════════════════════════════
# Grundlag — udviklingsperioden håndhæves i KODE
# ═══════════════════════════════════════════════════════════════════════════════

def byg_grundlag(slut: dt.date = l2.UDVIKLING_SLUT, port=l2.tilladt_input):
    """(dage, score, benchmark, mål) for fælles dage.

    ⚠ Kaster hvis `slut` går ud over udviklingsperioden. Iterationsdisciplinen er
    ikke en hensigt man husker — den er en fejl man ikke kan komme udenom.
    """
    if slut > l2.UDVIKLING_SLUT:
        raise RuntimeError(
            f"slut={slut} ligger efter udviklingsperioden ({l2.UDVIKLING_SLUT}). "
            f"2024 er design-validering med et budget på tre kørsler for HELE "
            f"byggeklodsen, og 2025+ er holdout. Ingen af dem røres her.")

    dage2 = l2.beregn_lag2(slut=slut, port=port)
    score = {d.dag: d.score for d in dage2 if d.score is not None}
    maal, bench = l2.maal_og_benchmark(slut=slut)

    faelles = sorted(set(score) & set(maal) & set(bench))
    return (faelles,
            [score[d] for d in faelles],
            [bench[d] for d in faelles],
            [maal[d] for d in faelles])


# ═══════════════════════════════════════════════════════════════════════════════
# Bootstrap
# ═══════════════════════════════════════════════════════════════════════════════

def blokbootstrap(score, bench, maal, bloklaengde: int, n: int = BOOTSTRAP_N,
                  froe: int = FROE) -> tuple[float, float, float]:
    """KI for (rho_score − rho_bench). Returnerer (median, lav, høj) i 95 %-KI."""
    rng = random.Random(froe)
    N = len(maal)
    n_blokke = max(1, N // bloklaengde)
    forskelle = []
    for _ in range(n):
        idx = []
        for _ in range(n_blokke):
            start = rng.randrange(0, max(1, N - bloklaengde + 1))
            idx.extend(range(start, min(start + bloklaengde, N)))
        s = [score[i] for i in idx]
        b = [bench[i] for i in idx]
        m = [maal[i] for i in idx]
        forskelle.append(l2._spearman(s, m) - l2._spearman(b, m))
    forskelle.sort()
    return (forskelle[len(forskelle) // 2],
            forskelle[int(0.025 * len(forskelle))],
            forskelle[int(0.975 * len(forskelle))])


def koer_test(score, bench, maal, navn: str, vis: bool = True) -> dict:
    rho_s = l2._spearman(score, maal)
    rho_b = l2._spearman(bench, maal)
    ud = {"navn": navn, "n": len(maal), "rho_score": rho_s, "rho_bench": rho_b,
          "forskel": rho_s - rho_b}
    for lgd, noegle in ((5, "blok5"), (1, "enkelt")):
        med, lav, hoej = blokbootstrap(score, bench, maal, lgd)
        ud[noegle] = {"median": med, "lav": lav, "hoej": hoej,
                      "bredde": hoej - lav, "udelukker_nul": lav > 0}
    if vis:
        print(f"\n  {navn}  (n = {len(maal)})")
        print(f"     Spearman lag 2   {rho_s:+.4f}")
        print(f"     Spearman bench   {rho_b:+.4f}")
        print(f"     forskel          {rho_s - rho_b:+.4f}")
        for noegle, tekst in (("blok5", "blok  5 dage"), ("enkelt", "enkeltdage  ")):
            k = ud[noegle]
            print(f"     {tekst}   KI [{k['lav']:+.4f}, {k['hoej']:+.4f}]  "
                  f"bredde {k['bredde']:.4f}  "
                  f"{'udelukker nul' if k['udelukker_nul'] else '⚠ RUMMER NUL'}")
    return ud


# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("LAG 2 — PRÆDIKTIV TEST")
print("=" * 78)
print(f"config_hash: {l2.config_hash()}   grænser: {l2.GRAENSER}")

dage, score, bench, maal = byg_grundlag()
print(f"grundlag: {len(dage)} dage · {dage[0]} .. {dage[-1]}")

kraev(dage[-1] <= l2.UDVIKLING_SLUT, "intet efter udviklingsperiodens slut")
try:
    byg_grundlag(slut=dt.date(2024, 6, 30))
    kraev(False, "en slutdato i 2024 blev accepteret")
except RuntimeError:
    kraev(True, "en slutdato efter 2023-12-31 kastes — disciplinen er i koden")

# ── 1. Falsificér testen FØRST ─────────────────────────────────────────────
print("\n1. ⚠ Falsificér testen, før den bruges til noget")
print("   Kan den ikke skelne et værdiløst mål fra et bedre, måler den intet.")

# KENDT-NEGATIV: en SHUFFLET udgave af den ægte score — samme fordeling, samme
# autokorrelationsfrie indhold, men uden nogen sammenhæng med målet. Ikke en
# random walk: den ville have en anden fordeling og teste noget andet.
rng = random.Random(FROE)
shufflet = score[:]
rng.shuffle(shufflet)
neg = koer_test(shufflet, bench, maal, "KENDT-NEGATIV (shufflet lag 2)")
kraev(not neg["blok5"]["udelukker_nul"] or neg["forskel"] < 0,
      "en shufflet score slår IKKE benchmarken")

# KENDT-POSITIV: målet selv plus støj. SKAL slå benchmarken klart.
pos_rng = random.Random(FROE + 1)
spred = st.pstdev(maal)
snyd = [m + pos_rng.gauss(0, spred * 0.5) for m in maal]
pos = koer_test(snyd, bench, maal, "KENDT-POSITIV (mål + støj)")
kraev(pos["blok5"]["udelukker_nul"] and pos["forskel"] > 0,
      "et beviseligt bedre mål SLÅR benchmarken — testen kan sige ja")

# ── 2. Look-ahead-porten ───────────────────────────────────────────────────
print("\n2. ⚠ Look-ahead — den lækkende udgave skal fanges")
print("   Lækket ville få resultatet til at se glimrende ud, ikke forkert.")
d_l, s_l, b_l, m_l = byg_grundlag(port=l2.tilladt_input_laekkende)
laek = koer_test(s_l, b_l, m_l, "LÆKKENDE (bruger dag d's range)")
kraev(laek["forskel"] > 0.05,
      f"den lækkende udgave springer i vejret ({laek['forskel']:+.4f}) — "
      f"testen ER følsom over for look-ahead")

# ── 3. Den ægte test ───────────────────────────────────────────────────────
print("\n3. Den ægte test")
aegte = koer_test(score, bench, maal, "LAG 2")

bestaaet = aegte["forskel"] > 0 and aegte["blok5"]["udelukker_nul"]
kraev(True, f"resultat registreret: forskel {aegte['forskel']:+.4f}")

print("\n   Bootstrap-varianterne mod hinanden:")
b5, b1 = aegte["blok5"], aegte["enkelt"]
print(f"     blok 5 dage   bredde {b5['bredde']:.4f}")
print(f"     enkeltdage    bredde {b1['bredde']:.4f}")
forhold = b5["bredde"] / b1["bredde"] if b1["bredde"] else 0
print(f"     forhold       {forhold:.2f}×")
if forhold > 1.3:
    print("     → klyngningen bærer mere end enkeltdags-bootstrap antager.")
else:
    print("     → næsten ens; målet overlapper ikke, som ventet.")

# ── 4. Fordeling til sammenvævningen ───────────────────────────────────────
print("\n4. Fordeling (spec §6 — grundlag for sammenvævningen)")
alle = l2.beregn_lag2(slut=l2.UDVIKLING_SLUT)
f = l2.fordeling(alle)
print(f"     n {f['n']} · middel {f['middel']} · spredning {f['spredning']} · "
      f"min {f['min']} · max {f['max']}")
print(f"     andel mellem 40 og 60: {f['andel_40_60']} %")
print(f"     klasser: {f['klassefordeling']}")
kraev(f["n"] > 2000, f"{f['n']} dage med score")

# ── 5. Diagnose: HVORFOR ────────────────────────────────────────────────────
print("\n5. Diagnose — hvor bliver signalet af?")
print("   ⚠ Dette er diagnose, ikke optimering. Det præregistrerede resultat")
print("   ovenfor står uændret; her måles kun HVOR forskellen kommer fra.")

komp = {d.dag: d.komponenter for d in l2.beregn_lag2(slut=l2.UDVIKLING_SLUT)
        if d.score is not None}
maal_d, bench_d = l2.maal_og_benchmark(slut=l2.UDVIKLING_SLUT)
f_d = sorted(set(komp) & set(maal_d) & set(bench_d))
m_d = [maal_d[d] for d in f_d]
print(f"\n     {'benchmark (rå range d−1 / luk d−2)':38} "
      f"{l2._spearman([bench_d[d] for d in f_d], m_d):+.4f}")
for navn in l2.KOMPONENTER:
    par = [(komp[d][navn], maal_d[d]) for d in f_d if komp[d][navn] is not None]
    print(f"     {navn:38} "
          f"{l2._spearman([x for x, _ in par], [y for _, y in par]):+.4f}")

# ⚠ SPECENS §2 SIGER "K2 ER BENCHMARKEN SELV". Det er den ikke som skrevet:
# benchmarken normaliserer ved forrige luk, K2 ved typisk range, og forskellen
# er 0,64 mod 0,24. Divisionen med typisk range fjerner NIVEAUET — en dag på
# 1,0× typisk betyder noget helt forskelligt i et roligt og et uroligt regime,
# og målet er absolut. Percentileringen koster derimod næsten intet (0,012).
print("\n     Samme størrelse, to normaliseringer:")
d_v, s_v, b_v, m_v = None, None, None, None
for norm in ("typisk_range", "forrige_luk"):
    dv = l2.beregn_lag2(slut=l2.UDVIKLING_SLUT, normalisering=norm)
    sc = {d.dag: d.score for d in dv if d.score is not None}
    fv = sorted(set(sc) & set(maal_d) & set(bench_d))
    mv = [maal_d[d] for d in fv]
    sv = [sc[d] for d in fv]
    bv = [bench_d[d] for d in fv]
    r = koer_test(sv, bv, mv, f"VARIANT normalisering={norm}", vis=False)
    print(f"       {norm:14} forskel {r['forskel']:+.4f}  "
          f"KI(blok5) [{r['blok5']['lav']:+.4f}, {r['blok5']['hoej']:+.4f}]  "
          f"{'slår benchmarken' if r['blok5']['udelukker_nul'] else 'gør ikke'}")

kraev(True, "diagnose registreret — beslutningen om normalisering hører til v2.2")

print("\n" + "=" * 78)
print(f"DOM: lag 2 {'BESTÅR' if bestaaet else 'BESTÅR IKKE'} — forskel "
      f"{aegte['forskel']:+.4f}, KI(blok5) "
      f"[{b5['lav']:+.4f}, {b5['hoej']:+.4f}]")
if not bestaaet:
    print("     Gårsdagens range alene er da det rigtige svar. Det er et gyldigt")
    print("     udfald, og det må ikke laves om ved at justere vægte bagefter.")
print("=" * 78)

if FEJL:
    print(f"\n{len(FEJL)} FEJL:")
    for x in FEJL:
        print("  -", x)
    sys.exit(1)
print("\nAlle kontroller grønne.")
