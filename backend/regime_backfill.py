#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regime_backfill.py — Fase 1: backfill af metrik-tidsserien (ikke etiketterne).
════════════════════════════════════════════════════════════════════════════════

Én raekke pr. handelsdag, beregnet som om dagen var "i dag". Det er denne serie
percentilerne (fase 2), valideringen (fase 4) og senere switch-designet bygger paa.

DET CENTRALE FIX (spec fase 1.1): vinduer ankres i KALENDEREN, ikke i cachen.
v1's `make_windows()` tog "de sidste 30 dage der findes i cachen", hvilket er
grunden til at rapporten kunne vaere dateret 15/7 med data til 5/6 uden at nogen
opdagede det. Her er vinduet for dag d = de op til WINDOW_DAYS seneste
kalender-handelsdage <= d, og daekningen maales eksplicit.

Metrikkerne genbruges UAENDRET fra regime_fingerprint.py (de er look-ahead-testede).
m6 (halt) udgaar indtil halt-loggen findes.

Rent offline. Design-mode-vagten haandhaever DESIGN_END.

Koeres:  python regime_backfill.py
Output:  regime_v2_output/regime_metrics_daily.parquet + .csv
         regime_v2_output/regime_v1_falsifikation.md
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import regime_fingerprint as RF
from regime_guard import guard, DESIGN_END

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════════
# KONSTANTER — spejler spec'ens config-blok
# ═══════════════════════════════════════════════════════════════════
BACKEND = Path(__file__).resolve().parent
OUT_DIR = BACKEND / "regime_v2_output"

WINDOW_DAYS = 30
STEP_DAYS = 1
MIN_COVERAGE = 0.80

FUT = ["ES", "NQ", "RTY"]
PAIRS = [("ES", "RTY"), ("ES", "NQ"), ("NQ", "RTY")]


# ═══════════════════════════════════════════════════════════════════
def byg_kalender(daily: dict) -> list[date]:
    """Handelsdags-kalender = foreningen af futures-dage (mest komplette kilde)."""
    alle = set()
    for lbl, rows in daily.items():
        alle.update(r[0] for r in rows)
    return sorted(alle)


def vindue(kalender: list[date], i: int) -> list[date]:
    """De op til WINDOW_DAYS seneste kalender-handelsdage til og med kalender[i]."""
    return kalender[max(0, i - WINDOW_DAYS + 1): i + 1]


def daekning(dage_i_vindue: list[date], kilde_dage: set) -> float:
    """Andel af vinduets dage kilden faktisk har data for."""
    if not dage_i_vindue:
        return 0.0
    return sum(1 for d in dage_i_vindue if d in kilde_dage) / len(dage_i_vindue)


def backfill() -> pd.DataFrame:
    """Beregn alle metrikker for hver handelsdag i design-perioden."""
    print("Indlaeser kilder …")
    daily = {lbl: RF.load_futures_daily(lbl) for lbl in FUT}
    for lbl, rows in daily.items():
        # Design-vagt: skaer alt efter DESIGN_END fra allerede ved indlaesning.
        beholdt = set(guard.filter_dates([r[0] for r in rows], f"{lbl}_1day"))
        daily[lbl] = [r for r in rows if r[0] in beholdt]
        print(f"  {lbl}_1day: {len(daily[lbl])} dage")

    sc = RF.load_smallcap_1min()
    uni_raw = RF.load_universe()
    # Universet er dato-streng -> tickers; filtrér gennem vagten.
    uni = {}
    for k, v in uni_raw.items():
        try:
            d = date.fromisoformat(k)
        except ValueError:
            continue
        if guard.tillader(d):
            uni[k] = v
    guard.filter_dates([date.fromisoformat(k) for k in uni_raw
                        if _er_dato(k)], "aktie_univers")
    print(f"  aktie-univers: {len(uni)} datoer (efter design-snit)")

    kalender = byg_kalender(daily)
    kilde_dage = {lbl: {r[0] for r in rows} for lbl, rows in daily.items()}
    uni_dage = {date.fromisoformat(k) for k in uni}

    print(f"\nBackfiller {len(kalender)} handelsdage "
          f"({kalender[0]} .. {kalender[-1]}) …")

    raekker = []
    for i in range(0, len(kalender), STEP_DAYS):
        d = kalender[i]
        vin = vindue(kalender, i)
        if len(vin) < WINDOW_DAYS:
            continue                       # ufuldstaendigt vindue i seriens start
        w0, w1 = vin[0], vin[-1]
        row: dict = {"dato": d, "vindue_start": w0, "vindue_slut": w1,
                     "vindue_dage": len(vin)}

        # ── Futures pr. indeks (m7, m8, m9) ────────────────────────────
        m7_liste = []
        for lbl in FUT:
            dk = daekning(vin, kilde_dage[lbl])
            row[f"daekning_{lbl}"] = round(dk, 4)
            if dk < MIN_COVERAGE:
                # Spec 1.3: hellere NaN end et tal beregnet paa et amputeret vindue
                for k in ("m7_daily_autocorr", "m7_continuation_rate",
                          "m8_overnight_intraday_ratio", "m9_term_ratio_short_long"):
                    row[f"{k}_{lbl}"] = np.nan
                continue
            m = RF.futures_daily_metrics(daily[lbl], w0, w1)
            # Spec 1.4: m7 GEMMES PR. INDEKS — gennemsnittet skjulte at NQ
            # trendede mens ES/RTY reverterede.
            for k in ("m7_daily_autocorr", "m7_continuation_rate",
                      "m8_overnight_intraday_ratio", "m9_term_ratio_short_long"):
                row[f"{k}_{lbl}"] = m.get(k)
            if m.get("m7_daily_autocorr") is not None:
                m7_liste.append(m["m7_daily_autocorr"])

        row["m7_median_indeks"] = float(np.median(m7_liste)) if m7_liste else np.nan
        row["m7_spread_indeks"] = (float(max(m7_liste) - min(m7_liste))
                                   if len(m7_liste) >= 2 else np.nan)
        row["n_indeks_ok"] = len(m7_liste)

        # ── Spreads (m10) ──────────────────────────────────────────────
        for a, b in PAIRS:
            if min(daekning(vin, kilde_dage[a]), daekning(vin, kilde_dage[b])) < MIN_COVERAGE:
                row[f"m10_VR5_{a}-{b}"] = np.nan
                row[f"m10_half_life_{a}-{b}"] = np.nan
                continue
            m = RF.spread_metrics(daily[a], daily[b], w0, w1)
            row[f"m10_VR5_{a}-{b}"] = m.get("m10_VR5")
            row[f"m10_half_life_{a}-{b}"] = m.get("m10_half_life_bars")

        # ── Aktier (m1-m5) ─────────────────────────────────────────────
        dk_eq = daekning(vin, uni_dage)
        row["daekning_aktier"] = round(dk_eq, 4)
        eq_felter = ("m1_gap_follow_through_rate", "m2_intraday_autocorr_5min",
                     "m3_atr_expansion_ratio", "m4_hod_morning_dominated",
                     "m5_name_dispersion_pct", "m5_breadth_pct_green")
        if dk_eq < MIN_COVERAGE:
            for k in eq_felter:
                row[k] = np.nan
            row["aktier_status"] = "UNDER_MIN_COVERAGE"
        else:
            m = RF.smallcap_metrics(sc, uni, w0, w1)
            for k in eq_felter:
                v = m.get(k)
                row[k] = float(v) if isinstance(v, bool) else v
            row["aktier_status"] = "OK"

        raekker.append(row)
        if len(raekker) % 200 == 0:
            print(f"    … {len(raekker)} dage")

    df = pd.DataFrame(raekker)
    print(f"  faerdig: {len(df)} raekker")
    return df


def _er_dato(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


# ═══════════════════════════════════════════════════════════════════
# v1-falsifikation (spec 1.6)
# ═══════════════════════════════════════════════════════════════════
def v1_falsifikation(df: pd.DataFrame) -> str:
    """Koer v1's `_primary_regime`-kaskade hen over serien og rapportér fordelingen.

    ÆRLIGT FORBEHOLD: v1's kaskade kraever aktie-metrikker paa ALLE tre grene.
    Paa dage uden aktie-data kan kaskaden kun returnere "Blandet / uklart" —
    ikke fordi markedet var blandet, men fordi inputtet manglede. Testen er
    derfor kun meningsfuld paa de dage hvor aktie-siden faktisk er beregnet.
    """
    def v1_label(r) -> str:
        disp = r.get("m5_name_dispersion_pct")
        mp = r.get("m7_median_indeks")
        ft = r.get("m1_gap_follow_through_rate")
        ac = r.get("m2_intraday_autocorr_5min")
        morn = r.get("m4_hod_morning_dominated")
        ok = lambda x: x is not None and not (isinstance(x, float) and np.isnan(x))
        if ok(disp) and ok(mp) and disp > 3.0 and mp < 0.05:
            return "Stock-picking (relativ vaerdi)"
        if ok(ft) and ok(ac) and ft > 0.55 and ac > 0.05 and bool(morn):
            return "Momentum-fortsaettelse"
        if ok(ft) and ok(ac) and ac < -0.05 and ft < 0.45:
            return "Intraday mean-reversion"
        return "Blandet / uklart"

    df = df.copy()
    df["v1_label"] = df.apply(v1_label, axis=1)
    beregnelig = df["aktier_status"] == "OK"

    md = ["# v1-falsifikation — kan den gamle kaskade skelne?", "",
          "Genereret af `regime_backfill.py` (spec fase 1.6). Formålet er at",
          "efterprøve statusrapportens påstand om at v1 kun kan producere én",
          "etiket — **før** vi bygger noget nyt oven på den antagelse.", "",
          f"Serie: {len(df)} handelsdage, {df['dato'].min()} .. {df['dato'].max()}",
          f"(design-snit {DESIGN_END}).", "",
          "---", "", "## Resultat", ""]

    n_ok = int(beregnelig.sum())
    if n_ok == 0:
        md += ["**Kaskaden kan ikke evalueres på denne serie.**", "",
               "v1's tre etiket-grene kræver alle mindst én aktie-metrik:", "",
               "| Gren | Kræver |", "|---|---|",
               "| Stock-picking | `m5_dispersion` (aktier) + `m7` (futures) |",
               "| Momentum | `m1_followthrough` + `m2_autocorr` + `m4_hod` (alle aktier) |",
               "| Intraday mean-reversion | `m1_followthrough` + `m2_autocorr` (aktier) |", "",
               f"På **0 af {len(df)} dage** i design-perioden er aktie-metrikkerne",
               "beregnelige (se dækningskortet, fase 0.2). Kaskaden falder derfor",
               "igennem til `Blandet / uklart` hver eneste dag — men det siger noget",
               "om **datadækningen**, ikke om kaskadens evne til at skelne.", "",
               "**Statusrapportens påstand står derfor uafkræftet, ikke bekræftet.**",
               "Den oprindelige observation (4 vinduer → 1 etiket) er stadig det",
               "eneste belæg, og den blev målt på de få dage hvor aktie-data fandtes.", ""]
    else:
        vc = df.loc[beregnelig, "v1_label"].value_counts()
        md += [f"Aktie-metrikker beregnelige på **{n_ok} af {len(df)}** dage.",
               "Fordeling på de beregnelige dage:", "",
               "| Etiket | Dage | Andel |", "|---|---|---|"]
        for lab, n in vc.items():
            md += [f"| {lab} | {n} | {n/n_ok*100:.1f} % |"]
        skift = int((df.loc[beregnelig, "v1_label"] !=
                     df.loc[beregnelig, "v1_label"].shift()).sum()) - 1
        md += ["", f"Antal etiket-skift: **{max(skift,0)}**", "",
               ("**Kaskaden kan skelne** — påstanden er afkræftet og v2 skal "
                "begrundes anderledes." if len(vc) > 1 else
                "**Kaskaden producerer én etiket** — påstanden bekræftet."), ""]

    md += ["---", "", "## Fordeling over HELE serien (inkl. dage uden aktie-data)", "",
           "Medtaget for fuldstændighedens skyld. Domineres af `Blandet / uklart`",
           "som ren dataartefakt — læs den ikke som et regime-udsagn.", "",
           "| Etiket | Dage |", "|---|---|"]
    for lab, n in df["v1_label"].value_counts().items():
        md += [f"| {lab} | {n} |"]
    return "\n".join(md) + "\n"


# ═══════════════════════════════════════════════════════════════════
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("── Fase 1: backfill af metrik-tidsserien ──────────────────────")
    print(guard.rapport().splitlines()[0])

    df = backfill()

    p_pq = OUT_DIR / "regime_metrics_daily.parquet"
    p_csv = OUT_DIR / "regime_metrics_daily.csv"
    df.to_parquet(p_pq, index=False)
    df.to_csv(p_csv, index=False)

    md = v1_falsifikation(df)
    p_md = OUT_DIR / "regime_v1_falsifikation.md"
    p_md.write_text(md, encoding="utf-8")

    print("\n── Daekning i den faerdige serie ──────────────────────────────")
    for k in ("m7_median_indeks", "m5_name_dispersion_pct",
              "m9_term_ratio_short_long_ES", "m10_VR5_ES-RTY"):
        if k in df:
            n = int(df[k].notna().sum())
            print(f"  {k:<32} {n:>5} / {len(df)} dage")
    print(f"  aktier_status=OK                 "
          f"{int((df.get('aktier_status') == 'OK').sum()):>5} / {len(df)} dage")

    print("\n" + guard.rapport())
    print("\nSkrevet:")
    for p in (p_pq, p_csv, p_md):
        print(f"  {p.relative_to(BACKEND)}  ({p.stat().st_size/1024:.1f} kB)")


if __name__ == "__main__":
    main()
