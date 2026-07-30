#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regime_daekning.py — Fase 0.2: cache-daekningskort for regime-motor v2.
════════════════════════════════════════════════════════════════════════

Svarer paa ét spoergsmaal: HVOR LANGT RAEKKER BACKFILLEN FAKTISK?

Motoren i fase 1 skal producere én raekke pr. handelsdag. Hvor mange dage der
reelt kan beregnes, afhaenger af hver kildes daekning — og af at et vindue paa
WINDOW_DAYS dage skal have mindst MIN_COVERAGE daekning for at taelle. Det er
billigere at faa det tal frem nu end at opdage det efter fase 1-3 er bygget.

Rent offline, kun stdlib + guard. Laeser samme kilder som regime_fingerprint.py.

Koeres:  python regime_daekning.py
Output:  regime_v2_output/regime_data_daekning.md
"""
from __future__ import annotations

import csv
import glob
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime
from pathlib import Path

from regime_guard import guard, DESIGN_END

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════════
# KONSTANTER — spejler spec'ens config-blok
# ═══════════════════════════════════════════════════════════════════
BACKEND = Path(__file__).resolve().parent
BAR_CACHE = BACKEND / "bar_cache"
HARVEST = BACKEND / "data_harvest"
OUT_DIR = BACKEND / "regime_v2_output"

WINDOW_DAYS = 30
MIN_COVERAGE = 0.80
PCTL_BURNIN_FUT = 120
PCTL_BURNIN_EQ = 20

RTH_START, RTH_END = dtime(9, 30), dtime(16, 0)
FUT = ["ES", "NQ", "RTY"]

# Metrik -> hvilken kilde den kommer fra. Afgoerende for at kunne sige hvilke
# AKSER der overhovedet kan beregnes naar en kilde mangler.
METRIK_KILDE = {
    "m1_gap_follow_through": "aktier", "m2_intraday_autocorr": "aktier",
    "m3_atr_ekspansion": "aktier", "m4_hod_morgen": "aktier",
    "m5_dispersion": "aktier", "m6_halt": "(mangler helt)",
    "m7_daily_autocorr": "futures", "m8_overnight_ratio": "futures",
    "m9_term_ratio": "futures", "m10_spread": "futures",
}

# Akse -> komponenter (jf. spec fase 2.2)
AKSER = {
    "A_retning":    ["m7_daily_autocorr", "m2_intraday_autocorr", "m1_gap_follow_through"],
    "A_dispersion": ["m5_dispersion"],
    "A_vol":        ["m3_atr_ekspansion", "m9_term_ratio"],
}


# ═══════════════════════════════════════════════════════════════════
# Indlaesning
# ═══════════════════════════════════════════════════════════════════
def _num(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def futures_dage(label: str) -> list[date]:
    """Handelsdage i {label}_1day.csv."""
    p = HARVEST / f"{label}_1day.csv"
    if not p.exists():
        return []
    ud = []
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                ud.append(date.fromisoformat(r["timestamp"][:10]))
            except (ValueError, KeyError):
                continue
    return sorted(set(ud))


def stitched_dage(navn: str) -> list[date]:
    """Handelsdage i mes_m2k_stitched/{navn}_15min.csv."""
    p = HARVEST / "mes_m2k_stitched" / f"{navn}_15min.csv"
    if not p.exists():
        return []
    ud = set()
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                ud.add(datetime.fromisoformat(r["timestamp"]).date())
            except (ValueError, KeyError):
                continue
    return sorted(ud)


def aktie_cache() -> tuple[dict[str, set], dict[date, set]]:
    """(ticker -> dage), (dag -> tickere) for RTH 1-min-barer i bar_cache."""
    pr_tk: dict[str, set] = defaultdict(set)
    for fp in glob.glob(str(BAR_CACHE / "*_1min.csv")):
        tk = Path(fp).name.split("_", 1)[0]
        try:
            with open(fp, newline="") as f:
                for r in csv.DictReader(f):
                    try:
                        dt = datetime.fromisoformat(r["timestamp"])
                    except (ValueError, KeyError):
                        continue
                    if RTH_START <= dt.timetz().replace(tzinfo=None) < RTH_END:
                        pr_tk[tk].add(dt.date())
        except OSError:
            continue
    pr_dag: dict[date, set] = defaultdict(set)
    for tk, ds in pr_tk.items():
        for d in ds:
            pr_dag[d].add(tk)
    return dict(pr_tk), dict(pr_dag)


def univers() -> dict[date, set]:
    """Point-in-time-univers fra historical_universe_midcap_*.json."""
    ud: dict[date, set] = defaultdict(set)
    for fp in sorted(glob.glob(str(BACKEND / "historical_universe_midcap_*.json"))):
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        for k, v in d.items():
            try:
                ud[date.fromisoformat(k)].update(v)
            except ValueError:
                continue
    return dict(ud)


# ═══════════════════════════════════════════════════════════════════
# Analyse
# ═══════════════════════════════════════════════════════════════════
def huller(dage: list[date], max_gap: int = 5) -> list[tuple[date, date, int]]:
    """Kalenderhuller stoerre end max_gap dage — fanger doede perioder."""
    ud = []
    for a, b in zip(dage, dage[1:]):
        n = (b - a).days
        if n > max_gap:
            ud.append((a, b, n))
    return ud


def backfill_raekkevidde(dage: list[date], kalender: list[date]) -> dict:
    """Hvor mange dage i `kalender` kan faa et GYLDIGT vindue af denne kilde?

    Et vindue for dag d = de op til WINDOW_DAYS seneste KALENDER-handelsdage
    <= d. Daekning = andel af dem kilden har data for. Under MIN_COVERAGE
    bliver metrikken NaN den dag (spec fase 1.3) — det er dét der afgoer den
    reelle raekkevidde, ikke hvornaar den foerste bar ligger.
    """
    har = set(dage)
    gyldige = []
    for i, d in enumerate(kalender):
        vindue = kalender[max(0, i - WINDOW_DAYS + 1): i + 1]
        if len(vindue) < WINDOW_DAYS:
            continue                      # ufuldstaendigt vindue i starten
        dk = sum(1 for x in vindue if x in har) / WINDOW_DAYS
        if dk >= MIN_COVERAGE:
            gyldige.append(d)
    return {"n_gyldige": len(gyldige),
            "foerste": gyldige[0] if gyldige else None,
            "sidste": gyldige[-1] if gyldige else None}


def md_tabel(raekker, hoved) -> str:
    ud = ["| " + " | ".join(hoved) + " |", "|" + "---|" * len(hoved)]
    ud += ["| " + " | ".join(str(c) for c in r) + " |" for r in raekker]
    return "\n".join(ud)


def _d(x) -> str:
    return "—" if x is None else str(x)


# ═══════════════════════════════════════════════════════════════════
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("── Fase 0.2: cache-daekningskort ──────────────────────────────")

    kilder: dict[str, list[date]] = {}
    for lbl in FUT:
        kilder[f"{lbl}_1day"] = futures_dage(lbl)
    for navn in ("MES", "M2K"):
        kilder[f"{navn}_15min"] = stitched_dage(navn)

    pr_tk, pr_dag = aktie_cache()
    kilder["aktier_bar_cache"] = sorted(pr_dag)
    uni = univers()
    kilder["aktie_univers"] = sorted(uni)

    for k, v in kilder.items():
        print(f"  {k:<20} {len(v):>5} dage"
              + (f"  {v[0]} .. {v[-1]}" if v else "  (tom)"))

    # Kalenderen: foreningen af futures-handelsdage (den mest komplette kilde)
    kalender = sorted(set().union(*(set(kilder[f"{l}_1day"]) for l in FUT)))
    kal_design = [d for d in kalender if d <= DESIGN_END]

    # --- Aktie-siden: den reelle begraensning ---------------------------
    # Metrikkerne loeber over universet, ikke over bar_cache (se
    # smallcap_metrics: days_in_win = dage der findes i uni). Bredden i
    # cachen er derfor irrelevant paa dage uden universliste.
    uni_design = [d for d in kilder["aktie_univers"] if d <= DESIGN_END]
    aktie_effektiv = sorted(set(kilder["aktie_univers"]) & set(pr_dag))
    aktie_eff_design = [d for d in aktie_effektiv if d <= DESIGN_END]

    # --- Backfill-raekkevidde pr. kilde --------------------------------
    rk = {}
    for k, v in kilder.items():
        rk[k] = backfill_raekkevidde([d for d in v if d <= DESIGN_END], kal_design)
    rk["aktie_effektiv"] = backfill_raekkevidde(aktie_eff_design, kal_design)

    # --- Akse-tilgaengelighed ------------------------------------------
    akse_status = {}
    for akse, komp in AKSER.items():
        aktie_k = [c for c in komp if METRIK_KILDE[c] == "aktier"]
        fut_k = [c for c in komp if METRIK_KILDE[c] == "futures"]
        n_fut_ok = rk["ES_1day"]["n_gyldige"] > 0
        n_eq_ok = rk["aktie_effektiv"]["n_gyldige"] > 0
        tilg = []
        if fut_k and n_fut_ok:
            tilg += fut_k
        if aktie_k and n_eq_ok:
            tilg += aktie_k
        akse_status[akse] = {"komponenter": komp, "tilgaengelige": tilg,
                             "aktie_kun": komp == aktie_k}

    # ── Rapport ────────────────────────────────────────────────────────
    md = ["# Regime-motor v2 — cache-dækningskort (fase 0.2)", "",
          f"Genereret af `regime_daekning.py`. `DESIGN_END = {DESIGN_END}`.",
          "Kun markedsdata; ingen strategi-afkast læst.", "",
          "---", "", "## 1. Kilder — rå dækning", "",
          md_tabel([[k, len(v), _d(v[0] if v else None), _d(v[-1] if v else None),
                     len([d for d in v if d <= DESIGN_END])]
                    for k, v in kilder.items()],
                   ["Kilde", "Handelsdage", "Første", "Sidste", "Heraf ≤ DESIGN_END"]), "",
          "## 2. Huller (kalendergab > 5 dage)", ""]

    h_raekker = []
    for k, v in kilder.items():
        hh = huller(v)
        if hh:
            for a, b, n in hh[:3]:
                h_raekker.append([k, str(a), str(b), n])
            if len(hh) > 3:
                h_raekker.append([k, f"… +{len(hh)-3} flere", "", ""])
    md += [md_tabel(h_raekker, ["Kilde", "Fra", "Til", "Dage"]) if h_raekker
           else "Ingen huller over 5 dage i nogen kilde.", ""]

    md += ["---", "", "## 3. Reel backfill-rækkevidde",
           "",
           f"Et vindue kræver {WINDOW_DAYS} handelsdage med mindst "
           f"{MIN_COVERAGE:.0%} dækning (spec fase 1.3). Tabellen viser hvor mange",
           "dage der derfor kan få en **gyldig** metrik — ikke hvor mange barer der findes.", "",
           md_tabel([[k, r["n_gyldige"], _d(r["foerste"]), _d(r["sidste"])]
                     for k, r in rk.items()],
                    ["Kilde", "Gyldige dage (≤ DESIGN_END)", "Første", "Sidste"]), ""]

    md += ["---", "", "## 4. Aktie-siden — hvorfor den er den bindende begrænsning", "",
           f"- `bar_cache` indeholder **{len(pr_tk)} tickere** over "
           f"**{len(pr_dag)} handelsdage** ({min(pr_dag)} .. {max(pr_dag)}).",
           f"- Men aktie-metrikkerne løber over **universlisten**, ikke over cachen "
           f"(`smallcap_metrics`: `days_in_win` hentes fra `uni`).",
           f"- Universlisten dækker kun **{len(kilder['aktie_univers'])} datoer** "
           f"({_d(kilder['aktie_univers'][0] if kilder['aktie_univers'] else None)} .. "
           f"{_d(kilder['aktie_univers'][-1] if kilder['aktie_univers'] else None)}).",
           f"- Effektive aktie-dage (univers ∩ cache): **{len(aktie_effektiv)}**, "
           f"heraf **{len(aktie_eff_design)}** ≤ DESIGN_END.",
           f"- Med {WINDOW_DAYS}-dages vindue og {MIN_COVERAGE:.0%}-krav giver det "
           f"**{rk['aktie_effektiv']['n_gyldige']} gyldige dage** i design-perioden.", ""]

    # Aar-fordeling af cache-bredden — afgoer om bagud-rekonstruktion er mulig
    pr_aar = Counter()
    navne_pr_aar = defaultdict(set)
    for d, tks in pr_dag.items():
        pr_aar[d.year] += 1
        navne_pr_aar[d.year].update(tks)
    md += ["**Cache-bredde pr. år** (afgør om universet kan rekonstrueres bagud):", "",
           md_tabel([[y, pr_aar[y], len(navne_pr_aar[y])] for y in sorted(pr_aar)],
                    ["År", "Handelsdage", "Tickere med data"]), ""]

    md += ["---", "", "## 5. Konsekvens for akserne (spec fase 2.2)", "",
           md_tabel([[a, ", ".join(s["komponenter"]),
                      ", ".join(s["tilgaengelige"]) or "**INGEN**",
                      f"{len(s['tilgaengelige'])}/{len(s['komponenter'])}"]
                     for a, s in akse_status.items()],
                    ["Akse", "Komponenter (spec)", "Beregnelige i design", "Dækning"]), ""]

    md += ["---", "", "## 6. Metrik → kilde", "",
           md_tabel([[m, k] for m, k in METRIK_KILDE.items()], ["Metrik", "Kilde"]), "",
           "---", "", "## 7. Vagt-status", "", "```", guard.rapport(), "```", ""]

    p = OUT_DIR / "regime_data_daekning.md"
    p.write_text("\n".join(md), encoding="utf-8")

    print(f"\n  Kalender (futures-forening): {len(kalender)} dage, "
          f"heraf {len(kal_design)} <= DESIGN_END")
    print(f"  Aktie-univers <= DESIGN_END: {len(uni_design)} datoer")
    print(f"  Aktie effektiv (univers ∩ cache) <= DESIGN_END: {len(aktie_eff_design)}")
    print(f"\n  GYLDIGE BACKFILL-DAGE (<= DESIGN_END):")
    for k, r in rk.items():
        print(f"    {k:<20} {r['n_gyldige']:>5}")
    print(f"\n  Skrevet: {p.relative_to(BACKEND)}")


if __name__ == "__main__":
    main()
