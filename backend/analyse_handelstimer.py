#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyse_handelstimer.py
=======================
Beskrivende bevaegelses-statistik for MES (Micro E-mini S&P 500) pr. time-paa-
doegnet i DANSK tid — ud fra ~2 aars 1-minuts candles.

Formaal (jf. spec): find hvilke timer paa doegnet der har nok bevaegelse til at
det kan betale sig at handle, i BEGGE retninger. INGEN strategi, ingen entry/
exit, ingen P&L — kun ren beskrivende statistik: hvor meget flytter prisen sig i
denne time, og hvor paalideligt?

Kilde-data: data_harvest/mes_m2k_stitched/MES_1min.csv
  - Kolonner: timestamp, open, high, low, close, volume
  - Tidsstempler er TIDSZONE-BEVIDSTE med eksplicit offset (-04:00 om sommeren /
    -05:00 om vinteren) => America/New_York (ET). Fordi offset er eksplicit, er
    hver raekke et entydigt absolut oejeblik; tz_convert til Europe/Copenhagen er
    derfor eksakt (haandterer dansk sommertid CET<->CEST automatisk).

Output (skrives til OUT_DIR):
  - handelstimer_dansktid.csv   (hoveddeliverable: 24 timer)
  - handelstimer_ugedag.csv     (ugedag x time, langt format)
  - handelstimer_rapport.md     (tabeller + korte konklusioner + split-half)
Plus en paen tabel i terminalen.

Koeres: python analyse_handelstimer.py
"""
from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Windows-terminal er cp1252 og kan ikke printe emojis/em-dash — tving UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# =============================================================================
# KONSTANTER — juster her, ikke i logikken nedenfor
# =============================================================================
HERE       = Path(__file__).resolve().parent
DATA_FILE  = HERE / "data_harvest" / "mes_m2k_stitched" / "MES_1min.csv"
OUT_DIR    = HERE / "analyse_handelstimer_out"

SRC_TZ_NOTE = "Kilde er tz-bevidst ET (offset i strengen) — parses med utc=True"
TARGET_TZ   = "Europe/Copenhagen"     # ALT output er i dansk vaegur-tid

# --- Enheder --------------------------------------------------------------
POINT_PER_TICK = 0.25                  # MES: 1 tick = 0.25 indekspoint
USD_PER_POINT  = 5.0                   # MES: $5 pr. indekspoint

# --- Data-kvalitet --------------------------------------------------------
MIN_MIN_PER_HOUR = 30                  # <30 minutter i en time => tynd/afbrudt time (markeres, forurener ikke)
MIN_N            = 30                  # <30 observationsdage for en time/celle => "for lidt data" (⚪)

# --- "Store timer" (konsistens-maal) --------------------------------------
# En times range regnes "stor" hvis den overstiger DAGENS EGEN median-range paa
# tvaers af doegnets timer (adaptiv, dagsspecifik skaering). Saet til et fast
# point-tal i stedet ved at saette BIG_HOUR_FIXED_PT (None = brug dags-median).
BIG_HOUR_FIXED_PT = None
CONSISTENCY_MIN   = 0.40               # andel store timer under dette => nedgrader ét trin

# --- Retnings-bias --------------------------------------------------------
BIAS_DEADZONE_PT = 0.5                 # |median netto-afkast| under dette => neutral (ingen bias)

# --- Absolutte trafiklys-graenser (median range i point) ------------------
ABS_GREEN_PT  = 5.0                    # >= 5 pt  => absolut groen-kandidat
ABS_YELLOW_PT = 3.0                    # 3-5 pt   => gul; < 3 pt => roed

# --- Median/gennemsnit-divergens -----------------------------------------
DIVERGENCE_FACTOR = 1.5                # gns_range > median_range * dette => flag (faa ekstreme dage traekker)

# Danske ugedagsnavne (0=mandag ... 6=soendag)
DK_DOW = ["Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Loerdag", "Soendag"]

# De seks maal vi beregner pr. time-bar (kolonnenavn -> visningsnavn)
MEASURES = {
    "range":    "Range",
    "abs_move": "|beveg.|",
    "net":      "Netto-afkast",
    "max_up":   "Max op",
    "max_down": "Max ned",
    "churn":    "Churn/path",
}


# =============================================================================
# 0-1. INDLAES + TIDSZONE-KONVERTERING
# =============================================================================
def load_minute_data() -> pd.DataFrame:
    """Laes 1-min CSV, konverter til dansk tid, tilfoej danske vaegur-felter."""
    df = pd.read_csv(DATA_FILE)
    # Tz-bevidst parse: utc=True samler de blandede offsets (-04/-05) til eet
    # entydigt UTC-instant pr. raekke, hvorefter vi konverterer til dansk tid.
    ts_utc = pd.to_datetime(df["timestamp"], utc=True)
    dk = ts_utc.dt.tz_convert(TARGET_TZ)

    df = df.drop(columns=["timestamp"])
    df["dk"]        = dk
    df["dk_date"]   = dk.dt.date                 # dansk kalenderdato
    df["dk_hour"]   = dk.dt.hour                  # dansk time-paa-doegnet 0-23
    df["dk_dow"]    = dk.dt.dayofweek             # 0=mandag
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


# =============================================================================
# 2-3. BYG TIME-BARER + MAAL PR. TIME-BAR
# =============================================================================
def build_hour_bars(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aggreger 1-min -> time-barer pr. (dansk dato, dansk time).

    Returnerer (gyldige_timebarer, tynde_timebarer). Tynde (n_min < graense)
    holdes separat saa de ikke forurener gennemsnittene.
    """
    # Path/churn: sum af |1-min afkast| (close-til-close) INDEN i hver time.
    # diff() inden for gruppen => foerste minut i timen bliver NaN (droppes af sum).
    df = df.sort_values("dk").copy()
    df["ret1"] = df.groupby(["dk_date", "dk_hour"])["close"].diff().abs()

    g = df.groupby(["dk_date", "dk_hour"], sort=True)
    hb = g.agg(
        o=("open", "first"),
        h=("high", "max"),
        l=("low", "min"),
        c=("close", "last"),
        vol=("volume", "sum"),
        n_min=("close", "size"),
        churn=("ret1", "sum"),
        dow=("dk_dow", "first"),
    ).reset_index()

    # De 6 maal (alle i indekspoint)
    hb["range"]    = hb["h"] - hb["l"]           # 1. raa bredde (retningsloes)
    hb["abs_move"] = (hb["c"] - hb["o"]).abs()   # 2. absolut netto-bevaegelse
    hb["net"]      = hb["c"] - hb["o"]           # 3. netto-afkast (fortegn)
    hb["max_up"]   = hb["h"] - hb["o"]           # 4. max op-traek fra open
    hb["max_down"] = hb["o"] - hb["l"]           # 5. max ned-traek fra open
    # 6. churn er allerede beregnet

    thin  = hb[hb["n_min"] < MIN_MIN_PER_HOUR].copy()
    valid = hb[hb["n_min"] >= MIN_MIN_PER_HOUR].copy()
    return valid, thin


# =============================================================================
# 4. "STORE TIMER" (konsistens)
# =============================================================================
def add_big_flag(valid: pd.DataFrame) -> pd.DataFrame:
    """Flag hver time-bar som 'stor' ift. dagens egen median-range (eller fast pt)."""
    valid = valid.copy()
    if BIG_HOUR_FIXED_PT is not None:
        thr = pd.Series(BIG_HOUR_FIXED_PT, index=valid.index)
    else:
        # dagens median-range paa tvaers af dagens timer (adaptiv skaering)
        thr = valid.groupby("dk_date")["range"].transform("median")
    valid["is_big"] = valid["range"] > thr
    return valid


# =============================================================================
# 4. AGGREGERING PR. DANSK TIME-PAA-DOEGNET
# =============================================================================
def bias_label(median_net: float) -> str:
    if median_net > BIAS_DEADZONE_PT:
        return "LONG"
    if median_net < -BIAS_DEADZONE_PT:
        return "SHORT"
    return "neutral"


def aggregate_by_hour(valid: pd.DataFrame) -> pd.DataFrame:
    """Median/gns/n + andel store + p90 + bias pr. dansk time (0-23)."""
    rows = []
    for hour in range(24):
        sub = valid[valid["dk_hour"] == hour]
        n = len(sub)
        row = {"dk_hour": hour, "n": n}
        for key in MEASURES:
            row[f"{key}_med"] = sub[key].median() if n else np.nan
            row[f"{key}_avg"] = sub[key].mean()   if n else np.nan
        row["range_p90"]   = sub["range"].quantile(0.90) if n else np.nan
        row["andel_store"] = sub["is_big"].mean() if n else np.nan
        row["bias"]        = bias_label(row["net_med"]) if n else "—"
        rows.append(row)
    return pd.DataFrame(rows)


# =============================================================================
# 5. TRAFIKLYS-VURDERING
# =============================================================================
def rel_terciles(agg: pd.DataFrame) -> dict[int, int]:
    """Relativ rang: del de gyldige timer (n>=MIN_N) i terciler efter median-range.
    Oeverste tredjedel -> 3 (groen-kandidat), midterste -> 2, nederste -> 1."""
    ok = agg[agg["n"] >= MIN_N].copy()
    if ok.empty:
        return {}
    ranks = ok["range_med"].rank(method="first")
    q = ranks / (len(ok) + 1e-9)
    level = {}
    for h, qq in zip(ok["dk_hour"], q):
        level[int(h)] = 3 if qq > 2/3 else (2 if qq > 1/3 else 1)
    return level


def assess_level(median_range: float, andel_store: float, n: float,
                 rel_level: int | None) -> int:
    """0=⚪ for lidt data, 1=🔴, 2=🟡, 3=🟢. Groen kraever BAADE absolut OG relativt.
    Konsistens (lav andel store timer) nedgraderer ét trin."""
    if not n or n < MIN_N or pd.isna(median_range):
        return 0
    # absolut
    if median_range >= ABS_GREEN_PT:
        abs_level = 3
    elif median_range >= ABS_YELLOW_PT:
        abs_level = 2
    else:
        abs_level = 1
    level = min(abs_level, rel_level if rel_level else 1)
    # konsistens-modifikator
    if andel_store is not None and not pd.isna(andel_store) \
            and andel_store < CONSISTENCY_MIN and level > 1:
        level -= 1
    return level


EMOJI = {0: "⚪", 1: "🔴", 2: "🟡", 3: "🟢"}


def level_text(level: int, andel_store: float, downgraded: bool) -> str:
    if level == 0:
        return "for lidt data / lukket"
    base = {1: "for stille", 2: "moderat", 3: "staerk"}[level]
    if level == 3:
        return "staerk, konsistent"
    if level == 2:
        return "moderat" + (", inkonsistent" if downgraded else "")
    return "for stille" + (", inkonsistent" if downgraded else "")


def add_assessment(agg: pd.DataFrame) -> pd.DataFrame:
    agg = agg.copy()
    rel = rel_terciles(agg)
    levels, texts, downgr = [], [], []
    for _, r in agg.iterrows():
        h = int(r["dk_hour"])
        rl = rel.get(h)
        lvl = assess_level(r["range_med"], r["andel_store"], r["n"], rl)
        # var den nedgraderet pga. konsistens?
        raw = assess_level(r["range_med"], 1.0, r["n"], rl)  # uden konsistens-straf
        dg = (lvl < raw)
        levels.append(lvl)
        downgr.append(dg)
        texts.append(f"{EMOJI[lvl]} {level_text(lvl, r['andel_store'], dg)}")
        _ = downgr
    agg["level"] = levels
    agg["vurdering"] = texts
    return agg


# =============================================================================
# 6. UGEDAG x TIME
# =============================================================================
def aggregate_by_dow_hour(valid: pd.DataFrame) -> pd.DataFrame:
    """Median-range + n + bias pr. (ugedag, time).

    Farve = RELATIV skala paa tvaers af ugens celler (terciler efter median-range):
    oeverste tredjedel groen, midterste gul, nederste roed. Relativ er det rette
    valg til et heatmap ('hvor i ugen ligger bevaegelsen'); den absolutte cost-floor
    (5/3 pt) klarer stort set alle MES-timer og ville farve alt groent.
    """
    rows = []
    for dow in sorted(valid["dow"].unique()):
        for hour in range(24):
            sub = valid[(valid["dow"] == dow) & (valid["dk_hour"] == hour)]
            n = len(sub)
            if n == 0:
                continue
            rows.append({
                "dow": int(dow), "ugedag": DK_DOW[int(dow)], "dk_hour": hour,
                "n": n, "range_med": sub["range"].median(),
                "abs_move_med": sub["abs_move"].median(),
                "net_med": sub["net"].median(), "bias": bias_label(sub["net"].median()),
            })
    dfc = pd.DataFrame(rows)
    if dfc.empty:
        dfc["level"] = []; dfc["vurdering"] = []
        return dfc

    # Relative terciler paa tvaers af cellerne med nok data
    ok = dfc[dfc["n"] >= MIN_N]
    if len(ok) >= 3:
        q1, q2 = ok["range_med"].quantile([1/3, 2/3])
    else:
        q1 = q2 = ok["range_med"].median() if len(ok) else 0.0

    def cell_level(r):
        if r["n"] < MIN_N:
            return 0
        if r["range_med"] > q2:
            return 3
        if r["range_med"] > q1:
            return 2
        return 1

    dfc["level"] = dfc.apply(cell_level, axis=1)
    dfc["vurdering"] = dfc["level"].map(EMOJI)
    return dfc


# =============================================================================
# 8. SPLIT-HALF ROBUSTHED (aar 1 vs aar 2)
# =============================================================================
def split_half_levels(valid: pd.DataFrame) -> pd.DataFrame:
    """Kør vurderingen paa foerste vs. anden halvdel af perioden; sammenlign."""
    dates = pd.to_datetime(pd.Series(sorted(valid["dk_date"].unique())))
    midpoint = dates.iloc[len(dates) // 2].date()

    def half_levels(mask):
        v = add_big_flag(valid[mask])
        a = add_assessment(aggregate_by_hour(v))
        return dict(zip(a["dk_hour"], a["level"]))

    m1 = valid["dk_date"] < midpoint
    m2 = valid["dk_date"] >= midpoint
    l1, l2 = half_levels(m1), half_levels(m2)

    rows = []
    for h in range(24):
        a, b = l1.get(h, 0), l2.get(h, 0)
        rows.append({
            "dk_hour": h, "level_h1": a, "level_h2": b,
            "robust": (a == b),
            "flag": ("groen kun i én halvdel" if (3 in (a, b) and a != b) else
                     ("skift" if a != b else "")),
        })
    return pd.DataFrame(rows), midpoint


# =============================================================================
# 7. OUTPUT — terminal, CSV, markdown
# =============================================================================
def hour_label(h: int) -> str:
    return f"{h:02d}:00-{(h+1) % 24:02d}:00"


def fmt(x, dec=1):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:.{dec}f}"


def build_main_table(agg: pd.DataFrame) -> pd.DataFrame:
    """Hoveddeliverable-tabellen (24 raekker) med visningskolonner."""
    out = pd.DataFrame()
    out["Time (DK)"]        = agg["dk_hour"].map(hour_label)
    out["n (dage)"]         = agg["n"].astype(int)
    out["Median range (pt)"] = agg["range_med"].round(2)
    out["Range ($)"]        = (agg["range_med"] * USD_PER_POINT).round(0)
    out["Median |beveg.| (pt)"] = agg["abs_move_med"].round(2)
    out["Gns. afkast (pt,±)"]   = agg["net_avg"].round(2)
    out["Median max-op (pt)"]   = agg["max_up_med"].round(2)
    out["Median max-ned (pt)"]  = agg["max_down_med"].round(2)
    out["Churn (pt)"]           = agg["churn_med"].round(2)
    out["Range p90 (pt)"]       = agg["range_p90"].round(2)
    out["Andel store"]          = (agg["andel_store"] * 100).round(0)
    out["Bias"]                 = agg["bias"]
    out["Vurdering"]            = agg["vurdering"]
    return out


def print_terminal(main: pd.DataFrame, meta: dict):
    print("\n" + "=" * 100)
    print("  MES — BEVAEGELSE PR. TIME (DANSK TID)   |   ren beskrivende statistik, ingen strategi")
    print("=" * 100)
    print(f"  Data: {meta['rows']:,} 1-min bars  |  {meta['start']} -> {meta['slut']}  "
          f"|  {meta['days']} handelsdage")
    print(f"  Enhed: 1 pt = ${USD_PER_POINT:.0f}  |  median primaer, n vist  |  tz: {TARGET_TZ}")
    print("-" * 100)
    with pd.option_context("display.max_rows", None, "display.width", 200,
                           "display.max_columns", None):
        print(main.to_string(index=False))
    print("-" * 100)


def divergence_flags(agg: pd.DataFrame) -> list[str]:
    out = []
    for _, r in agg.iterrows():
        if r["n"] >= MIN_N and r["range_med"] and not pd.isna(r["range_med"]):
            if r["range_avg"] > r["range_med"] * DIVERGENCE_FACTOR:
                out.append(hour_label(int(r["dk_hour"])))
    return out


def best_worst(agg: pd.DataFrame) -> tuple[list, list]:
    ok = agg[agg["n"] >= MIN_N].sort_values("range_med", ascending=False)
    best = [(hour_label(int(r.dk_hour)), r.range_med, r.bias, r.vurdering)
            for r in ok.head(5).itertuples()]
    worst = [(hour_label(int(r.dk_hour)), r.range_med, r.vurdering)
             for r in ok.tail(5).itertuples()]
    return best, worst


def write_reports(main, agg, dow, split, midpoint, thin, meta):
    OUT_DIR.mkdir(exist_ok=True)

    # --- CSV 1: hoveddeliverable ---
    main_path = OUT_DIR / "handelstimer_dansktid.csv"
    main.to_csv(main_path, index=False, encoding="utf-8-sig")

    # --- CSV 2: ugedag x time (langt format) ---
    dow_path = OUT_DIR / "handelstimer_ugedag.csv"
    dow_out = dow.copy()
    dow_out["Time (DK)"] = dow_out["dk_hour"].map(hour_label)
    dow_out = dow_out[["ugedag", "Time (DK)", "dk_hour", "n", "range_med",
                       "abs_move_med", "net_med", "bias", "vurdering"]]
    dow_out.to_csv(dow_path, index=False, encoding="utf-8-sig")

    # --- MD-rapport ---
    best, worst = best_worst(agg)
    div = divergence_flags(agg)
    md = []
    md.append("# MES — handelstimer i dansk tid\n")
    md.append(f"*Ren beskrivende bevaegelses-statistik pr. time. Ingen strategi, ingen P&L.*\n")
    md.append(f"- **Data:** {meta['rows']:,} 1-min bars, {meta['start']} → {meta['slut']} "
              f"({meta['days']} handelsdage)\n")
    md.append(f"- **Enhed:** MES, 1 point = ${USD_PER_POINT:.0f} (1 tick = {POINT_PER_TICK} pt)\n")
    md.append(f"- **Tidszone:** alt i {TARGET_TZ} (dansk vaegur). Kilde er ET (tz-bevidst); "
              "konverteret med rigtig tz — dansk sommertid haandteret automatisk.\n")
    md.append("\n> **Note om sommertid:** USA og EU skifter sommertid paa forskellige "
              "datoer. I 2-3 uger om aaret daekker en 'dansk time' derfor et lidt andet "
              "markedsoejeblik end resten af aaret (fx US-aabningen rykker en dansk time "
              "i de uger). Det er korrekt: du handler efter dit eget vaegur, og det er "
              "praecis hvad tz-konverteringen giver.\n")

    md.append("\n## Bedste 3-5 timer at handle (dansk tid)\n")
    for lbl, mr, bias, vur in best:
        md.append(f"- **{lbl}** — median range {mr:.1f} pt (${mr*USD_PER_POINT:.0f}), "
                  f"bias {bias} — {vur}\n")
    md.append("\n## Undgaa typisk (mindst bevaegelse)\n")
    for lbl, mr, vur in worst:
        md.append(f"- {lbl} — median range {mr:.1f} pt — {vur}\n")

    md.append("\n## Alle 24 timer\n\n")
    md.append(main.to_markdown(index=False))
    md.append("\n")

    md.append("\n## Ugedag x time — median range (pt) + trafiklys\n\n")
    md.append(dow_matrix_md(dow))
    md.append("\n")

    md.append(f"\n## Split-half robusthed (foer/efter {midpoint})\n\n")
    md.append("Samme vurdering koert paa hver halvdel. En times 'groen' skal helst "
              "findes i begge halvdele for at vaere robust.\n\n")
    md.append(split_md(split))
    md.append("\n")

    if div:
        md.append("\n## Median/gennemsnit-divergens (faa ekstreme dage traekker gns. op)\n\n")
        md.append("Timer hvor gns-range > "
                  f"{DIVERGENCE_FACTOR}x median-range — brug medianen, ikke gns.: "
                  + ", ".join(div) + "\n")

    md.append("\n## Datakvalitet\n")
    md.append(f"- Tynde time-barer (<{MIN_MIN_PER_HOUR} min, typisk CME-vedligeholdelse "
              f"~23:00 DK + weekend-kanter): {len(thin):,} styk — holdt UDE af gennemsnittene.\n")
    md.append(f"- Timer med n < {MIN_N} dage vurderes ⚪ (for lidt data).\n")
    md.append("- CME: handel soendag aften → fredag; daglig vedligeholdelsespause "
              "17:00-18:00 ET (~23:00 DK). Weekend = lukket, ikke 'stille'.\n")

    (OUT_DIR / "handelstimer_rapport.md").write_text("".join(md), encoding="utf-8")
    return main_path, dow_path, OUT_DIR / "handelstimer_rapport.md"


def dow_matrix_md(dow: pd.DataFrame) -> str:
    """Matrix: raekker=timer 0-23, kolonner=ugedage. Celle = median range + emoji."""
    order = [d for d in range(7) if d in dow["dow"].values]
    cols = [DK_DOW[d] for d in order]
    lines = ["| Time (DK) | " + " | ".join(cols) + " |",
             "|---|" + "|".join(["---"] * len(cols)) + "|"]
    for h in range(24):
        cells = []
        for d in order:
            c = dow[(dow["dow"] == d) & (dow["dk_hour"] == h)]
            if c.empty:
                cells.append("—")
            else:
                r = c.iloc[0]
                cells.append(f"{EMOJI[int(r['level'])]} {r['range_med']:.1f} (n{int(r['n'])})")
        lines.append(f"| {hour_label(h)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def split_md(split: pd.DataFrame) -> str:
    lines = ["| Time (DK) | 1. halvdel | 2. halvdel | Robust? | Flag |",
             "|---|---|---|---|---|"]
    for _, r in split.iterrows():
        lines.append(f"| {hour_label(int(r['dk_hour']))} | {EMOJI[int(r['level_h1'])]} "
                     f"| {EMOJI[int(r['level_h2'])]} | {'ja' if r['robust'] else 'NEJ'} "
                     f"| {r['flag']} |")
    return "\n".join(lines)


# =============================================================================
# MAIN
# =============================================================================
def main():
    print(f"Laeser {DATA_FILE} ...")
    df = load_minute_data()
    meta = {
        "rows":  len(df),
        "start": str(df["dk"].min())[:16],
        "slut":  str(df["dk"].max())[:16],
        "days":  df["dk_date"].nunique(),
    }

    valid, thin = build_hour_bars(df)
    valid = add_big_flag(valid)

    agg = add_assessment(aggregate_by_hour(valid))
    main_tbl = build_main_table(agg)

    dow = aggregate_by_dow_hour(valid)
    split, midpoint = split_half_levels(valid)

    print_terminal(main_tbl, meta)

    best, worst = best_worst(agg)
    print("\n  BEDSTE TIMER (dansk tid):")
    for lbl, mr, bias, vur in best:
        print(f"    {lbl}  median range {mr:5.1f} pt (${mr*USD_PER_POINT:4.0f})  bias {bias:7s}  {vur}")
    print("\n  MINDST BEVAEGELSE:")
    for lbl, mr, vur in worst:
        print(f"    {lbl}  median range {mr:5.1f} pt  {vur}")

    n_robust = int(split["robust"].sum())
    print(f"\n  Split-half: {n_robust}/24 timer med samme trafiklys i begge halvdele "
          f"(skillelinje {midpoint}).")

    p1, p2, p3 = write_reports(main_tbl, agg, dow, split, midpoint, thin, meta)
    print(f"\n  Skrevet:\n    {p1}\n    {p2}\n    {p3}\n")


if __name__ == "__main__":
    main()
