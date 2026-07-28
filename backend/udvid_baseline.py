#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
udvid_baseline.py
=================
FASE A — regenerér kontrol-samplet (baseline) med ~30.000 barer i stedet for
4.000.

Hvorfor kun baseline: alle lift-/enrichment-tal er forhold mellem to rater, og
det er KONTROL-raten der staar i naevneren. Med 4.000 kontrol-barer hviler
hale-filteret (RVOL >= 4) paa 5-8 hits pr. IS/OOS-halvdel, og et punktestimat
paa "15x" daekker reelt 6x-37x. Flere *events* aendrer intet ved det —
naevneren bliver ikke bedre af at taelleren vokser. Derfor gaar hele indsatsen
i baseline-stoerrelsen.

Metode-garanti: dette script importerer og kalder de SAMME funktioner som
`analyse_store_bevaegelser.py` (TimeframeView, detect_swings,
detect_forward_moves, build_baseline_rows, order_columns). Der er ingen
genimplementering. Som ekstra sikkerhed regenererer scriptet foerst det
oprindelige 4.000-sample og sammenligner det celle for celle med den
eksisterende parquet-fil — er de ikke identiske, stopper det.

Output:
  store_bevaegelser_out/store_bevaegelser_baseline_30k.parquet
  store_bevaegelser_out/store_bevaegelser_baseline_30k.csv

Koeres:  python udvid_baseline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import analyse_store_bevaegelser as A
from store_bevaegelser_lib import (
    TimeframeView, TF_SPEC, detect_swings, detect_forward_moves, roll_segment_id,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# KONSTANTER
# =============================================================================
BASELINE_N_STOR = 30_000        # maal 20-40k jf. spec
UD_PARQUET = A.OUT_DIR / "store_bevaegelser_baseline_30k.parquet"
UD_CSV     = A.OUT_DIR / "store_bevaegelser_baseline_30k.csv"
GAMMEL     = A.OUT_DIR / "store_bevaegelser_baseline.parquet"


# =============================================================================
def byg_grundlag():
    """
    Genskab praecis det grundlag `analyse_store_bevaegelser.main()` arbejder paa:
    indikator-views pr. timeframe, detekterings-timeframens barer,
    bars_since_roll, og de to metoders events (efter opvarmnings- og rul-frafald).
    """
    df1m = A.load_minute_data()
    print(f"  {len(df1m):,} 1-min barer indlaest")

    views = {tf: TimeframeView.build(tf, df1m) for tf in A.TIMEFRAMES}
    det = views[A.DETECT_TF]
    det_bars = det.feat[["close", "high", "low"]].copy()
    det_index = det.feat.index
    n_det = len(det_bars)

    # bars_since_roll — identisk med hovedscriptet
    roll_times = A.load_roll_times()
    seg = roll_segment_id(det_index, roll_times)
    seg_start = np.zeros(n_det, dtype=np.int64)
    for s, f in zip(*np.unique(seg, return_index=True)):
        seg_start[seg == s] = f
    bars_since_roll = np.arange(n_det) - seg_start

    sw_all = detect_swings(det_bars, det.feat["atr"],
                           A.PIVOT_LEFT, A.PIVOT_RIGHT, A.SWING_ATR_MULT)
    fw_all = detect_forward_moves(det_bars, det.feat["atr"], A.FWD_N, A.FWD_ATR_MULT)
    sw = [e for e in sw_all if e.start_idx >= A.WARMUP_BARS_DET
          and seg[e.start_idx] == seg[e.end_idx]]
    fw = [e for e in fw_all if e.start_idx >= A.WARMUP_BARS_DET
          and seg[e.start_idx] == seg[e.end_idx]]
    print(f"  {A.DETECT_TF}: {n_det:,} barer · events swing {len(sw):,} / fwd {len(fw):,}")

    return views, det, det_index, n_det, bars_since_roll, sw, fw


def kandidatpulje(n_det: int, sw, fw) -> np.ndarray:
    """Ikke-event-start barer efter opvarmning — samme regel som hovedscriptet."""
    er_start = np.zeros(n_det, dtype=bool)
    r = A.BASELINE_EXCLUDE_RADIUS
    for e in sw + fw:
        er_start[max(0, e.start_idx - r):e.start_idx + r + 1] = True
    kand = np.flatnonzero(~er_start)
    return kand[kand >= A.WARMUP_BARS_DET]


def traek(kand: np.ndarray, n: int, views, det_index, det, bars_since_roll) -> pd.DataFrame:
    """Traek n kontrol-barer og byg raekkerne — samme seed og samme funktioner."""
    rng = np.random.default_rng(A.BASELINE_SEED)
    n_take = min(n, len(kand))
    valgte = np.sort(rng.choice(kand, size=n_take, replace=False))
    bl = A.build_baseline_rows(valgte, views, det_index, det.bar_close_ns,
                               bars_since_roll)
    return A.order_columns(bl, A.PUNKTER_BASELINE)


def verificer_metode(ny_4k: pd.DataFrame) -> bool:
    """
    Regenerér det gamle 4.000-sample og sammenlign med filen paa disken.

    Bestaar dette, ved vi at replikationen er eksakt — og dermed at
    30k-samplet er trukket med praecis samme metode som det lille.
    """
    if not GAMMEL.exists():
        print("  ⚠ gammel baseline findes ikke — springer metode-tjek over")
        return True
    gl = pd.read_parquet(GAMMEL)
    if list(gl.columns) != list(ny_4k.columns) or len(gl) != len(ny_4k):
        print(f"  ✘ form afviger: {gl.shape} mod {ny_4k.shape}")
        return False

    afvig = []
    for c in gl.columns:
        a, b = gl[c], ny_4k[c]
        if pd.api.types.is_float_dtype(a):
            lig = np.isclose(a.to_numpy(), b.to_numpy(), rtol=1e-12,
                             atol=1e-12, equal_nan=True).all()
        else:
            lig = a.equals(b)
        if not lig:
            afvig.append(c)
    if afvig:
        print(f"  ✘ {len(afvig)} kolonner afviger, fx {afvig[:5]}")
        return False
    print(f"  ✔ 4.000-sample regenereret identisk ({len(gl.columns)} kolonner) "
          f"— metoden er eksakt den samme")
    return True


def sanity(gl: pd.DataFrame, ny: pd.DataFrame) -> None:
    """
    Spec-sanity: samme kolonnesaet, og samme fordelinger paa bars_since_roll,
    tid og ugedag. Kun n maa vaere anderledes.
    """
    print("\n── Sanity: stor baseline mod lille ────────────────────────────")
    print(f"  kolonner: {len(gl.columns)} mod {len(ny.columns)} "
          f"{'✔' if list(gl.columns) == list(ny.columns) else '✘'}")

    for navn, ga, na in (
        ("bars_since_roll (median)", gl["bars_since_roll"].median(),
         ny["bars_since_roll"].median()),
        ("bars_since_roll (gns.)", gl["bars_since_roll"].mean(),
         ny["bars_since_roll"].mean()),
    ):
        print(f"  {navn:<26} {ga:8.1f} mod {na:8.1f}")

    # Fordelinger: max absolut afvigelse i andele pr. kategori
    for kol in ("dansk_time", "ugedag"):
        a = gl[kol].value_counts(normalize=True)
        b = ny[kol].value_counts(normalize=True)
        d = (a - b).abs().max()
        print(f"  {kol:<26} stoerste andels-afvigelse {d*100:5.2f} "
              f"pct.point {'✔' if d < 0.02 else '⚠'}")

    # Signatur-fyringsrater — det tal der faktisk bruges i valideringen
    for side, dot in (("long", "kraftig_groen"), ("short", "kraftig_roed")):
        def rate(d):
            z, r_, p = d["z_15m_start"], d["rvol_15m_start"], d["dot_type_3m_start"]
            m = ((z <= -2) if side == "long" else (z >= 2)) & (r_ >= 1.5) & (p == dot)
            return m.mean(), int(m.sum())
        ra, ka = rate(gl)
        rb, kb = rate(ny)
        print(f"  kontrol-rate {side:<14} {ra*100:5.2f} % (n={ka}) mod "
              f"{rb*100:5.2f} % (n={kb})")


def main() -> None:
    print("── FASE A: udvid baseline ─────────────────────────────────────")
    views, det, det_index, n_det, bars_since_roll, sw, fw = byg_grundlag()

    kand = kandidatpulje(n_det, sw, fw)
    print(f"  kandidatpulje (ikke-event-start, efter opvarmning): {len(kand):,}")

    print("\nMetode-tjek — regenererer det gamle 4.000-sample …")
    ny_4k = traek(kand, A.BASELINE_N, views, det_index, det, bars_since_roll)
    if not verificer_metode(ny_4k):
        raise SystemExit("Replikationen matcher ikke den eksisterende baseline — stopper.")

    n_maal = min(BASELINE_N_STOR, len(kand))
    andel = n_maal / len(kand)
    print(f"\nTraekker {n_maal:,} kontrol-barer ({andel*100:.0f} % af puljen) …")
    if andel > 0.5:
        print(f"  NB: vi traekker over halvdelen af hele puljen. Konfidens-")
        print(f"      intervallerne i valideringen regnes som om puljen var")
        print(f"      uendelig, saa de bliver en anelse for BREDE — ikke for")
        print(f"      smalle. Konservativt, altsaa.")
    stor = traek(kand, n_maal, views, det_index, det, bars_since_roll)

    stor.to_parquet(UD_PARQUET, index=False)
    stor.to_csv(UD_CSV, index=False)

    sanity(pd.read_parquet(GAMMEL), stor)

    print(f"\nSkrevet:")
    for p in (UD_PARQUET, UD_CSV):
        print(f"  {p.name:<44} {p.stat().st_size/1e6:7.2f} MB")


if __name__ == "__main__":
    main()
