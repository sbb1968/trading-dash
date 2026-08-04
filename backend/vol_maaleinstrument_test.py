"""
vol_maaleinstrument_test.py — J4: hvilken futures-serie maaler natten renest?
═══════════════════════════════════════════════════════════════════════════════════
PRAEREGISTRERET. Kriterierne staar som konstanter nedenfor og er skrevet FOER data
findes. Det er hele pointen: naar ES/RTY er hoestet, skal spoergsmaalet afgoeres af
en regel vi allerede har bundet os til — ikke af hvad tallene saa inspirerer til.

SPOERGSMAALET (E3, J2). Vi HANDLER MES og M2K, men det er ikke givet at de er de
bedste at MAALE paa. Micro-kontrakterne bliver tynde om natten: en bar med tyve
handler har en high og en low der i hoej grad er sat af hvor netop de tyve handler
tilfaeldigt ramte. Det rammer dér hvor det goer mest ondt, fordi overnight-range er
lag 2's tungeste enkeltinput.

MODARGUMENTET ER REELT, og det staar her frem for kun i specen: vi bruger
percentiler, ikke absolutte tal. Er tyndhedsstoejen nogenlunde konstant, forsvinder
den i rangordenen. Bekymringen er at den formentlig IKKE er konstant —
micro-kontrakterne er tyndest paa de stille naetter, hvor den sande volatilitet ogsaa
er lavest, saa stoejgulvet fylder relativt mest netop dér. Og den anden vej: vi
handler MES/M2K, saa fills sker i DERES ordrebog. ES giver et paenere tal, men et der
ligger en tak laengere fra den markedsplads der transakteres paa.

ES/RTY koeber derfor ikke en kendt forbedring. Det koeber MULIGHEDEN FOR AT AFGOERE
om det overhovedet betyder noget. Viser serierne sig praktisk taget identiske, er
svaret "brug MES/M2K, sagen er lukket" — lige saa meget vaerd som det modsatte.

BESLUTNINGSTRAPPEN (J4), i raekkefoelge:
  1. Beregn overnight-range-percentil for begge par over den faelles periode.
  2. Spearman mellem de to percentilserier. >= 0,98  ->  udskiftelige, BRUG MES/M2K.
  3. Ellers: rapportér uenighed pr. decil. Samler den sig i den lave ende, er
     tyndhedshypotesen bekraeftet; er den spredt, er der noget andet paa faerde og
     det skal findes FOER der vaelges.
  4. Afgoerende: hvilken forudsiger naeste dags realiserede RTH-range bedst
     (V-test 1's metrik), med bootstrap-KI paa forskellen. Slaar ES/RTY MES/M2K
     UDEN FOR intervallet -> maal paa ES/RTY. Ellers MES/M2K.

Punkt 4 er det der goer testen praeregistreret frem for en skoenssag: "bedre" er
allerede defineret som bedre forudsigelse af morgendagens range, og det kriterium
gaelder ogsaa naar vi vaelger maaleinstrument.

⚠ INGEN STRATEGIAFKAST INDGAAR. Kun markedsdata og fremtidig realiseret volatilitet.
Kontaminationsregel 1 er derfor ikke i spil — men testen koeres paa DESIGNPERIODEN,
aldrig paa holdout.

STATUS: koerer ikke endnu. ES/RTY 1-min findes ikke (0 filer pr. 2026-08-04). Testen
er bygget og falsificeret paa forhaand, saa den er ét kald naar data er der.

    python vol_maaleinstrument_test.py --a MES,M2K --b ES,RTY
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, time
from pathlib import Path

import numpy as np

from vol_falsifikation import bootstrap_forskel, spearman

# ── PRAEREGISTREREDE KRITERIER — bundet foer data findes ───────────────────────
UDSKIFTELIG_SPEARMAN = 0.98   # >= dette: serierne er udskiftelige, vaelg det vi handler
PCT_VINDUE = 252              # bagudrettet percentilreference, kun dage FOER
MIN_FAELLES_SESSIONER = 250   # under dette er sammenligningen ikke meningsfuld
BOOTSTRAP_N = 1000

# Sessionsgraenser i ET. Overnight = fra RTH-luk til naeste RTH-aabning.
RTH_AABEN = time(9, 30)
RTH_LUK = time(16, 0)

# Designperiodens slut for spor 2. Testen maa IKKE se holdout.
DESIGN_SLUT = date(2024, 12, 31)


def laes_bars(sti: Path) -> list[tuple[datetime, float, float, float]]:
    """(tid, high, low, close) med naive ET-stempler."""
    ud = []
    with sti.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                t = datetime.fromisoformat(r["timestamp"])
                ud.append((t.replace(tzinfo=None) if t.tzinfo else t,
                           float(r["high"]), float(r["low"]), float(r["close"])))
            except (ValueError, KeyError):
                continue
    ud.sort()
    return ud


def overnight_og_rth(bars) -> tuple[dict[date, float], dict[date, float]]:
    """Overnight-range og RTH-range pr. handelsdag, begge i procent af niveauet.

    Overnight for dag d = alle barer fra dagen foer kl. 16:00 til d kl. 09:30.
    Det er praecis det vindue lag 2 skal udtale sig ud fra, og det er lukket foer
    beregningstidspunktet kl. 15:00 dansk.
    """
    overnat: dict[date, list] = {}
    rth: dict[date, list] = {}
    for t, h, l, c in bars:
        d, klok = t.date(), t.time()
        if RTH_AABEN <= klok < RTH_LUK:
            rth.setdefault(d, []).append((h, l, c))
        else:
            # Barer efter luk hoerer til NAESTE handelsdags nat. Barer foer aabning
            # hoerer til dagens egen nat. Weekender falder ud af sig selv, fordi
            # naeste dag med RTH-barer er mandagen.
            ejer = d if klok < RTH_AABEN else None
            if ejer is None:
                overnat.setdefault(("efter", d), []).append((h, l, c))
            else:
                overnat.setdefault(ejer, []).append((h, l, c))

    # Knyt "efter luk"-barer til den foerste efterfoelgende handelsdag.
    handelsdage = sorted(rth)
    for noegle in [k for k in overnat if isinstance(k, tuple)]:
        _, d = noegle
        naeste = next((x for x in handelsdage if x > d), None)
        if naeste is not None:
            overnat.setdefault(naeste, []).extend(overnat[noegle])
        del overnat[noegle]

    def spaend(v):
        if not v:
            return None
        hs = [x[0] for x in v]
        ls = [x[1] for x in v]
        niveau = v[-1][2]
        return (max(hs) - min(ls)) / niveau * 100.0 if niveau > 0 else None

    on = {d: s for d in overnat if (s := spaend(overnat[d])) is not None}
    dag = {d: s for d in rth if (s := spaend(rth[d])) is not None}
    return on, dag


def percentiler(serie: dict[date, float], vindue: int = PCT_VINDUE) -> dict[date, float]:
    """Bagudrettet percentil — kun dage FOER dagen selv. Aldrig look-ahead."""
    dage = sorted(serie)
    ud = {}
    for i, d in enumerate(dage):
        hist = [serie[x] for x in dage[max(0, i - vindue):i]]
        if len(hist) < 20:
            continue
        ud[d] = float(np.mean([h < serie[d] for h in hist]) * 100.0)
    return ud


def uenighed_pr_decil(a: dict, b: dict, faelles: list) -> list[tuple[int, float, int]]:
    """Hvor er de to serier uenige? (decil, median |forskel|, antal dage)."""
    ud = []
    va = np.array([a[d] for d in faelles])
    vb = np.array([b[d] for d in faelles])
    for k in range(10):
        lo, hi = k * 10, (k + 1) * 10
        m = (va >= lo) & (va < hi if k < 9 else va <= 100)
        if m.sum() == 0:
            ud.append((k, float("nan"), 0))
            continue
        ud.append((k, float(np.median(np.abs(va[m] - vb[m]))), int(m.sum())))
    return ud


def afgoer(a_navn: str, b_navn: str, pa: dict, pb: dict,
           udfald: dict, emit) -> dict:
    """Beslutningstrappen. Returnerer resultatet OG hvilket trin der afgjorde det."""
    faelles = sorted(set(pa) & set(pb) & set(udfald))
    faelles = [d for d in faelles if d <= DESIGN_SLUT]
    res = {"a": a_navn, "b": b_navn, "n_faelles": len(faelles)}

    if len(faelles) < MIN_FAELLES_SESSIONER:
        res["konklusion"] = "UAFGJORT — for faa faelles sessioner"
        res["afgjort_paa"] = "trin 0"
        emit(f"Kun {len(faelles)} faelles sessioner i designperioden "
             f"(kraever {MIN_FAELLES_SESSIONER}). Testen udtaler sig ikke.")
        return res

    # ── Trin 2: er de udskiftelige? ───────────────────────────────────────────
    va = [pa[d] for d in faelles]
    vb = [pb[d] for d in faelles]
    r = spearman(va, vb)
    res["spearman"] = r
    emit(f"\nTrin 2 — Spearman mellem percentilserierne: {r:+.4f} "
         f"(graense {UDSKIFTELIG_SPEARMAN})")
    if r >= UDSKIFTELIG_SPEARMAN:
        res["konklusion"] = f"BRUG {a_navn} — serierne er udskiftelige"
        res["afgjort_paa"] = "trin 2"
        emit(f"  -> Udskiftelige. Vaelg det vi HANDLER: {a_navn}. Sagen er lukket, "
             f"og det er et lige saa brugbart svar som det modsatte.")
        return res
    emit("  -> Ikke udskiftelige. Fortsaetter til trin 3.")

    # ── Trin 3: hvor er de uenige? ────────────────────────────────────────────
    emit("\nTrin 3 — uenighed pr. decil af " + a_navn + "-percentilen:")
    deciler = uenighed_pr_decil(pa, pb, faelles)
    res["deciler"] = deciler
    for k, med, n in deciler:
        emit(f"   decil {k*10:>3}-{(k+1)*10:<3}  median |forskel| "
             f"{('—' if np.isnan(med) else f'{med:5.1f}')} pp   ({n} dage)")
    gyldige = [(k, m) for k, m, n in deciler if n > 0 and not np.isnan(m)]
    if gyldige:
        lav = np.mean([m for k, m in gyldige if k < 3])
        hoej = np.mean([m for k, m in gyldige if k >= 3])
        res["uenighed_lav_ende"] = float(lav)
        res["uenighed_resten"] = float(hoej)
        if lav > hoej * 1.5:
            emit(f"  -> Uenigheden samler sig i den LAVE ende ({lav:.1f} mod "
                 f"{hoej:.1f} pp). Tyndhedshypotesen er bekraeftet.")
        else:
            emit(f"  -> Uenigheden er SPREDT ({lav:.1f} mod {hoej:.1f} pp). "
                 f"Noget andet er paa faerde — find det FOER der vaelges.")

    # ── Trin 4: hvem forudsiger morgendagen bedst? ────────────────────────────
    y = [udfald[d] for d in faelles]
    ra = spearman(va, y)
    rb = spearman(vb, y)
    forskel, lav_ki, hoej_ki = bootstrap_forskel(vb, va, y, n_resamples=BOOTSTRAP_N)
    res.update({"spearman_a_mod_udfald": ra, "spearman_b_mod_udfald": rb,
                "forskel": forskel, "ki": [lav_ki, hoej_ki]})
    emit(f"\nTrin 4 — forudsigelse af naeste dags RTH-range (V-test 1's metrik):")
    emit(f"   {a_navn:<10} {ra:+.4f}")
    emit(f"   {b_navn:<10} {rb:+.4f}")
    emit(f"   forskel ({b_navn} - {a_navn}) {forskel:+.4f}  "
         f"95 %-KI [{lav_ki:+.4f}, {hoej_ki:+.4f}]")

    if lav_ki > 0:
        res["konklusion"] = f"MAAL PAA {b_navn} — slaar {a_navn} uden for KI"
        emit(f"  -> {b_navn} slaar {a_navn}, og KI'et udelukker nul. Maal paa {b_navn}.")
    else:
        res["konklusion"] = f"BRUG {a_navn} — {b_navn} slaar den ikke overbevisende"
        emit(f"  -> KI'et udelukker ikke nul. Praeregistreringen siger da {a_navn} "
             f"(det vi handler). Ingen efterrationalisering.")
    res["afgjort_paa"] = "trin 4"
    return res


def koer(par_a: list[str], par_b: list[str], mappe: Path, emit) -> dict | None:
    """Kombinér hvert pars symboler til én percentilserie (gennemsnit pr. dag)."""
    def par_serie(symboler):
        ons, dags = [], []
        for s in symboler:
            p = mappe / f"{s}_1min.csv"
            if not p.exists():
                emit(f"MANGLER: {p}")
                return None, None
            on, dag = overnight_og_rth(laes_bars(p))
            ons.append(on)
            dags.append(dag)
        faelles_on = set.intersection(*[set(o) for o in ons])
        faelles_dag = set.intersection(*[set(d) for d in dags])
        return ({d: float(np.mean([o[d] for o in ons])) for d in faelles_on},
                {d: float(np.mean([x[d] for x in dags])) for d in faelles_dag})

    on_a, dag_a = par_serie(par_a)
    on_b, _ = par_serie(par_b)
    if on_a is None or on_b is None:
        emit("\nTesten kan ikke koeres foer begge par er hoestet i 1-min.")
        return None

    # Udfaldet er NAESTE dags RTH-range — maalt paa det vi HANDLER (par A),
    # uanset hvilket instrument der maales med. Ellers ville vi skifte baade
    # praediktor og maal paa én gang.
    dage = sorted(dag_a)
    udfald = {dage[i]: dag_a[dage[i + 1]] for i in range(len(dage) - 1)}

    return afgoer("+".join(par_a), "+".join(par_b),
                  percentiler(on_a), percentiler(on_b), udfald, emit)


def main() -> int:
    ap = argparse.ArgumentParser(description="J4: vaelg maaleinstrument for lag 2's nat")
    ap.add_argument("--a", default="MES,M2K", help="parret vi HANDLER (default MES,M2K)")
    ap.add_argument("--b", default="ES,RTY", help="kandidatparret (default ES,RTY)")
    ap.add_argument("--mappe", default="data_harvest/mes_m2k_stitched")
    args = ap.parse_args()

    def emit(s=""):
        try:
            print(s, flush=True)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(s.encode(enc, "replace").decode(enc), flush=True)

    emit("J4 — praeregistreret valg af maaleinstrument for lag 2's overnight-range")
    emit("=" * 74)
    r = koer([s.strip().upper() for s in args.a.split(",")],
             [s.strip().upper() for s in args.b.split(",")],
             Path(args.mappe), emit)
    if r is None:
        return 2
    emit("\n" + "=" * 74)
    emit(f"KONKLUSION ({r['afgjort_paa']}): {r['konklusion']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
