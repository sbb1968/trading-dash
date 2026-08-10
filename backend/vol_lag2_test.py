"""
vol_lag2_test.py — lag 2's inkrementelle test (spec v2.2 §3)
════════════════════════════════════════════════════════════════════════════════
⚠ SPØRGSMÅLET ER OMFORMULERET, OG DET ER VÆRD AT FORSTÅ HVORFOR.

v2.1 spurgte om lag 2 slog gårsdagens range. Det svar var −0,1323, og det var
et rigtigt svar på et forkert spørgsmål: lag 1's score alene gav +0,6435 mod
benchmarkens +0,6361. Lag 2's berettigelse kan altså ikke være at forudsige
dagens range — det gør lag 1 allerede bedre end noget andet vi har.

Lag 2's berettigelse er at NATTEN TILFØJER NOGET BAGGRUNDSTILSTANDEN IKKE VED.

  Benchmark   lag 1's score (+0,6435), IKKE gårsdagens range (+0,6361)
  Test        Spearman mellem natmålet og RANGRESIDUALET af dagens range
              efter at lag 1's forudsigelse er fjernet
  Bestå       korrelationen er signifikant forskellig fra nul

⚠ RETNINGEN AF FLYTNINGEN. Vi går fra en benchmark på +0,6361 til en på +0,6435
— altså til en HÅRDERE test, efter at have set data. Det er den eneste retning
man må flytte i bagefter. At gøre en test sværere er ikke fejlfinding i eget
favør; at gøre den lettere er.

⚠ HVORFOR RESIDUAL OG IKKE BARE ENKELTKORRELATIONER. Tabellen fra v2.1 —
K1 +0,14, K2 +0,24, K3 +0,64 — siger INTET om hvad komponenterne bidrager oven i
hinanden. To mål kan hver især korrelere 0,6 med målet og være fuldstændig
overflødige over for hinanden. Residualtesten er det eneste der svarer på det
spørgsmål der faktisk stilles.

Ingen vægte vælges her. Består testen, er en sammenvejning en separat beslutning
med sin egen præregistrering. Dumper den, ER lag 2 lag 1's score — og det er et
rent resultat: én komponent færre at vedligeholde, og en ærlig grænse for hvad
der kan vides før åbning.

    python vol_lag2_test.py
"""
from __future__ import annotations

import datetime as dt
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vol_lag1 as l1
import vol_lag2 as l2

FEJL: list[str] = []
BOOTSTRAP_N = 2000
FROE = 20260810


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# ═══════════════════════════════════════════════════════════════════════════════
# Rangresidual
# ═══════════════════════════════════════════════════════════════════════════════

def rangresidual(x: list[float], y: list[float]) -> list[float]:
    """Rangresidualet af y efter x: rangér begge, træk den lineære del fra.

    På ranks er en lineær regression netop det der svarer til Spearman, så
    residualet er "den del af y's rangorden som x ikke forklarer". Det er
    grundlaget for at spørge om en TREDJE serie forklarer noget derudover.
    """
    rx, ry = l2._rang(x), l2._rang(y)
    mx, my = st.fmean(rx), st.fmean(ry)
    sxx = sum((v - mx) ** 2 for v in rx)
    sxy = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(rx)))
    b = sxy / sxx if sxx else 0.0
    return [ry[i] - (my + b * (rx[i] - mx)) for i in range(len(rx))]


def bootstrap_rho(a: list[float], b: list[float], bloklaengde: int,
                  n: int = BOOTSTRAP_N, froe: int = FROE):
    """KI for Spearman(a, b). Blokke fordi volatilitet klynger — nabodage er
    ikke uafhængige, selv når målene ikke overlapper."""
    rng = random.Random(froe)
    N = len(a)
    n_blokke = max(1, N // bloklaengde)
    ud = []
    for _ in range(n):
        idx = []
        for _ in range(n_blokke):
            start = rng.randrange(0, max(1, N - bloklaengde + 1))
            idx.extend(range(start, min(start + bloklaengde, N)))
        ud.append(l2._spearman([a[i] for i in idx], [b[i] for i in idx]))
    ud.sort()
    lav, hoej = ud[int(0.025 * len(ud))], ud[int(0.975 * len(ud))]
    return {"rho": l2._spearman(a, b), "lav": lav, "hoej": hoej,
            "bredde": hoej - lav, "udelukker_nul": lav > 0 or hoej < 0}


def partiel_spearman(x: list[float], y: list[float], z: list[float]) -> tuple:
    """Spearman mellem x og y, med z fjernet fra BEGGE.

    ⚠ HVORFOR IKKE BARE Spearman(x, residual(y|z)). Residualet er ortogonalt på
    rang(z) i Pearson-forstand — det følger af OLS. Men Spearman rangordner
    residualet PÅ NY, og den omrangering er ikke-lineær, så en rest af z
    overlever: målt +0,0526 mod lag 1's egne +0,7118.

    Det er lille, men ikke ligegyldigt her. Natmålet er selv korreleret med
    baggrundstilstanden, så en rest af lag 1 i residualet ville tælle med som om
    natten bidrog — netop den fejl testen skal udelukke. Fjernes z fra begge
    sider, kan resten ikke drive resultatet uanset hvor stor den er.
    """
    return rangresidual(z, x), rangresidual(z, y)


def vis(navn: str, kandidat: list[float], maal_: list[float],
        baggrund: list[float]) -> dict:
    """Partiel Spearman: bidrager `kandidat` til `maal_` ud over `baggrund`?"""
    rk, rm = partiel_spearman(kandidat, maal_, baggrund)
    ud = {"navn": navn, "n": len(rm)}
    print(f"\n  {navn}  (n = {len(rm)})")
    for lgd, noegle, tekst in ((5, "blok5", "blok  5 dage"), (1, "enkelt", "enkeltdage  ")):
        r = bootstrap_rho(rk, rm, lgd)
        ud[noegle] = r
        print(f"     {tekst}   rho {r['rho']:+.4f}   KI [{r['lav']:+.4f}, {r['hoej']:+.4f}]"
              f"   bredde {r['bredde']:.4f}   "
              f"{'≠ nul' if r['udelukker_nul'] else '⚠ RUMMER NUL'}")
    return ud


# ═══════════════════════════════════════════════════════════════════════════════
# Grundlag
# ═══════════════════════════════════════════════════════════════════════════════

def byg_grundlag(slut: dt.date = l2.UDVIKLING_SLUT, port=l2.tilladt_input):
    """(dage, komponenter, lag1-score, mål) for fælles dage.

    ⚠ Kaster hvis `slut` går ud over udviklingsperioden. 2024 er
    design-validering med et budget på tre kørsler for HELE byggeklodsen; 2025+
    er holdout. Disciplinen er en fejl man ikke kan komme udenom, ikke en
    hensigt man husker.
    """
    if slut > l2.UDVIKLING_SLUT:
        raise RuntimeError(
            f"slut={slut} ligger efter udviklingsperioden ({l2.UDVIKLING_SLUT}).")

    dage2 = l2.beregn_lag2(slut=slut, port=port)
    komp = {d.dag: d.komponenter for d in dage2 if d.score is not None}
    lag1 = {d.dag: d.score for d in l1.beregn_lag1(slut=slut) if d.score is not None}
    maal, _ = l2.maal_og_benchmark(slut=slut)

    faelles = sorted(d for d in komp
                     if d in lag1 and d in maal
                     and all(komp[d][k] is not None for k in l2.KOMPONENTER))
    return (faelles,
            {k: [komp[d][k] for d in faelles] for k in l2.KOMPONENTER},
            [lag1[d] for d in faelles],
            [maal[d] for d in faelles])


# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 78)
print("LAG 2 — INKREMENTEL TEST (spec v2.2 §3)")
print("=" * 78)
print(f"config_hash: {l2.config_hash()}   normalisering: {l2.NORMALISERING}")

dage, komp, lag1, maal = byg_grundlag()
print(f"grundlag: {len(dage)} dage · {dage[0]} .. {dage[-1]}")

kraev(dage[-1] <= l2.UDVIKLING_SLUT, "intet efter udviklingsperiodens slut")
try:
    byg_grundlag(slut=dt.date(2024, 6, 30))
    kraev(False, "en slutdato i 2024 blev accepteret")
except RuntimeError:
    kraev(True, "en slutdato efter 2023-12-31 kastes")

# ── 0. Benchmarken ─────────────────────────────────────────────────────────
print("\n0. Benchmarken er lag 1 — og den er hårdere end den gamle")
rho_lag1 = l2._spearman(lag1, maal)
print(f"     lag 1 mod dagens range   {rho_lag1:+.4f}")
kraev(rho_lag1 > 0.6, f"lag 1 forklarer allerede det meste ({rho_lag1:+.4f})")

residual = rangresidual(lag1, maal)
# Den EKSAKTE egenskab OLS giver: residualet er ortogonalt paa rang(lag 1).
rl = l2._rang(lag1)
_ml, _mr = st.fmean(rl), st.fmean(residual)
kov = sum((rl[i] - _ml) * (residual[i] - _mr) for i in range(len(rl)))
kraev(abs(kov) < 1e-6 * len(rl),
      f"residualet er ortogonalt paa rang(lag 1) — kovarians {kov:.2e}")
# ⚠ Resten som omrangeringen efterlader RAPPORTERES frem for at blive gemt vaek.
rest = l2._spearman(lag1, residual)
print(f"     rest af lag 1 efter omrangering: {rest:+.4f} (mod lag 1's egne "
      f"{rho_lag1:+.4f})")
print(f"     — fjernes helt af partiel_spearman, som traekker lag 1 ud af BEGGE sider")

# ── 1. Falsificér testen FØRST ─────────────────────────────────────────────
print("\n1. ⚠ Falsificér testen, før den bruges til noget")

rng = random.Random(FROE)
shufflet = komp["nat_pctl"][:]
rng.shuffle(shufflet)
neg = vis("KENDT-NEGATIV (shufflet natmål)", shufflet, maal, lag1)
kraev(not neg["blok5"]["udelukker_nul"],
      "en shufflet serie forklarer IKKE residualet")

pos_rng = random.Random(FROE + 1)
spred = st.pstdev(residual)
snyd = [r + pos_rng.gauss(0, spred * 0.8) for r in residual]
pos = vis("KENDT-POSITIV (residual + støj)", snyd, maal, lag1)
kraev(pos["blok5"]["udelukker_nul"] and pos["blok5"]["rho"] > 0,
      "noget der beviseligt forklarer residualet, FANGES — testen kan sige ja")

# ── 2. Look-ahead ──────────────────────────────────────────────────────────
print("\n2. ⚠ Look-ahead — den lækkende udgave skal fanges")
d_l, k_l, l1_l, m_l = byg_grundlag(port=l2.tilladt_input_laekkende)
res_l = rangresidual(l1_l, m_l)
laek = vis("LÆKKENDE (K2 = dagens egen range)", k_l["igaar_range_pctl"], m_l, l1_l)
kraev(laek["blok5"]["rho"] > 0.2,
      f"lækket springer i vejret ({laek['blok5']['rho']:+.4f}) — testen ER "
      f"følsom over for look-ahead")

# ── 3. De ægte komponenter ─────────────────────────────────────────────────
print("\n3. Bidrager natten ud over baggrunden?")
k1 = vis("K1 — natbevægelse", komp["nat_pctl"], maal, lag1)

print("\n   Og gårsdagens range? (samme vej — medtages kun hvis den består)")
k2 = vis("K2 — gårsdagens range", komp["igaar_range_pctl"], maal, lag1)

k1_ok = k1["blok5"]["udelukker_nul"]
k2_ok = k2["blok5"]["udelukker_nul"]

# ── 4. Fordeling ───────────────────────────────────────────────────────────
print("\n4. Fordeling (spec v2.1 §6 — grundlag for sammenvævningen)")
alle = l2.beregn_lag2(slut=l2.UDVIKLING_SLUT)
f = l2.fordeling(alle)
print(f"     n {f['n']} · middel {f['middel']} · spredning {f['spredning']} · "
      f"andel 40-60: {f['andel_40_60']} %")

# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("DOM")
for navn, r, ok in (("K1 natbevægelse", k1, k1_ok), ("K2 gårsdagens range", k2, k2_ok)):
    b = r["blok5"]
    print(f"  {navn:22} rho {b['rho']:+.4f}  KI [{b['lav']:+.4f}, {b['hoej']:+.4f}]  "
          f"{'BIDRAGER' if ok else 'bidrager ikke'}")
if not (k1_ok or k2_ok):
    print("\n  → LAG 2 ER LAG 1'S SCORE. Natten tilfører intet på dagsskala.")
    print("    Det er et rent resultat: én komponent færre at vedligeholde, og")
    print("    en ærlig grænse for hvad der kan vides før åbning.")
else:
    print("\n  → Mindst én komponent bidrager. Sammenvejningen er en SEPARAT")
    print("    beslutning med sin egen præregistrering — ingen vægte vælges her.")
print("=" * 78)

if FEJL:
    print(f"\n{len(FEJL)} FEJL:")
    for x in FEJL:
        print("  -", x)
    sys.exit(1)
print("\nAlle kontroller grønne.")
