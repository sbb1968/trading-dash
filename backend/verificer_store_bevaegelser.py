#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verificer_store_bevaegelser.py
==============================
Verifikations-harness for `analyse_store_bevaegelser.py`.

Tre uafhaengige tjek — alle skal bestaa foer datasaettet bruges til analyse:

  TJEK 1  FUTURE LEAK. For et udvalg af events afkortes 1-minuts-serien
          praecis ved start-barens lukketid, hele indikator-suiten genberegnes
          paa den afkortede serie, og START-snapshottet sammenlignes med det
          der staar i event-tabellen. Er der leak, afviger tallene.
          Dette er det vigtigste tjek i hele projektet: hvis START-snapshottet
          kender fremtiden, er ethvert moenster vi finder cirkulaert.

  TJEK 2  MTF-ALIGNMENT. Staar vi paa en 15m-bar der lukker 10:15, skal
          1h-snapshottet komme fra 1h-baren der lukkede 10:00 — ikke fra den
          igangvaerende. Tjekkes eksplicit for alle fem timeframes.

  TJEK 3  UAFHAENGIG INDIKATOR-KONTROL. Et par indikatorer genberegnes med
          en anden (langsom, direkte) implementering og sammenlignes.

Koeres:  python verificer_store_bevaegelser.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

import analyse_store_bevaegelser as A
from store_bevaegelser_lib import (
    TF_SPEC, TimeframeView, SNAPSHOT_NUMERIC, to_ns,
    resample_ohlcv, build_indicator_frame, zscore, cmf, rvol, wavetrend,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

N_LEAK_SAMPLES = 12          # antal events der genberegnes fra bunden
N_ALIGN_SAMPLES = 500        # antal events hvor MTF-alignment tjekkes
SEED = 4711

# TJEK 1/2 sammenligner den SAMME kode med sig selv paa en afkortet serie —
# der skal resultatet vaere bit-identisk.
TOL_REL = 1e-9
# TJEK 3 sammenligner to FORSKELLIGE implementeringer. pandas' rullende std
# bruger en online-algoritme, min reference en to-pas-beregning; de er
# matematisk ens, men afviger i sidste ciffer. 1e-6 fanger enhver reel
# formelfejl og ignorerer flydende-komma-stoej.
TOL_IMPL = 1e-6


def _rel_diff(a: float, b: float) -> float:
    """Relativ forskel, robust naar begge er ~0."""
    if not np.isfinite(a) and not np.isfinite(b):
        return 0.0
    if not np.isfinite(a) or not np.isfinite(b):
        return np.inf
    skala = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / skala


# =============================================================================
# TJEK 1 — FUTURE LEAK
# =============================================================================
def tjek_future_leak(df1m: pd.DataFrame, ev: pd.DataFrame) -> bool:
    """
    Genberegn START-snapshottet paa en serie der er hugget af ved start-barens
    lukketid, og sammenlign med det gemte snapshot.

    Bemaerk: EMA/RMA-baserede indikatorer er rekursive, saa en afkortet serie
    giver PRAECIS samme vaerdi paa den sidste bar som den fulde serie gjorde —
    hvis og kun hvis der ikke er kigget fremad nogen steder. Det er derfor
    testen er skarp: enhver form for leak (centrerede vinduer, ffill bagud,
    resample der inkluderer en uafsluttet bar) giver en afvigelse.
    """
    print("\n" + "=" * 72)
    print("TJEK 1 — FUTURE LEAK i START-snapshottet")
    print("=" * 72)

    rng = np.random.default_rng(SEED)
    # Vaelg events spredt ud over historikken, og hop de foerste over (opvarmning).
    kand = ev[ev["start_ts_dk"] > ev["start_ts_dk"].min() + pd.Timedelta(days=30)]
    valgte = kand.iloc[rng.choice(len(kand), size=N_LEAK_SAMPLES, replace=False)]

    det_min = TF_SPEC[A.DETECT_TF][1]
    alle_ok = True
    vaerste = ("", 0.0)

    for _, row in valgte.iterrows():
        start_ts = pd.Timestamp(row["start_ts_dk"]).tz_convert("UTC")
        # Lukketiden for start-baren: alt til og med denne maa bruges.
        luk = start_ts + pd.Timedelta(minutes=det_min)

        # Hug serien af — intet efter start-barens lukning eksisterer.
        trunk = df1m[df1m.index < luk]
        luk_ns = to_ns(pd.DatetimeIndex([luk]))[0]

        n_afvig = 0
        for tf in A.TIMEFRAMES:
            bars = resample_ohlcv(trunk, TF_SPEC[tf][0])
            feat = build_indicator_frame(bars)
            bar_close = to_ns(feat.index + pd.Timedelta(minutes=TF_SPEC[tf][1]))
            i = int(np.searchsorted(bar_close, luk_ns, side="right")) - 1
            if i < 0:
                continue

            for name in SNAPSHOT_NUMERIC:
                gemt = row[f"{name}_{tf}_start"]
                frisk = feat[name].iloc[i]
                d = _rel_diff(float(gemt), float(frisk))
                if d > TOL_REL:
                    n_afvig += 1
                    if d > vaerste[1]:
                        vaerste = (f"{name}_{tf} @ {start_ts}", d)

        status = "OK" if n_afvig == 0 else f"AFVIGER ({n_afvig} felter)"
        if n_afvig:
            alle_ok = False
        print(f"  {row['event_id']:<16} {row['start_ts_dk']}  {status}")

    if alle_ok:
        print(f"\n  ✔ Ingen leak. {N_LEAK_SAMPLES} events x {len(A.TIMEFRAMES)} TF x "
              f"{len(SNAPSHOT_NUMERIC)} indikatorer genberegnet paa afkortede serier"
              f" — alt identisk.")
    else:
        print(f"\n  ✘ LEAK ELLER FEJL. Vaerste afvigelse: {vaerste[0]}  rel={vaerste[1]:.3e}")
    return alle_ok


# =============================================================================
# TJEK 2 — MTF-ALIGNMENT ("previous"-konventionen)
# =============================================================================
def tjek_alignment(views: dict[str, TimeframeView], ev: pd.DataFrame) -> bool:
    """
    For hvert testet event: den bar vi har hentet fra timeframe X skal vaere
    HELT afsluttet paa start-barens lukketid, og den NAESTE bar paa X maa ikke
    vaere det. Det er praecis definitionen paa "seneste faerdige bar".
    """
    print("\n" + "=" * 72)
    print("TJEK 2 — MTF-alignment (aldrig en uafsluttet HTF-bar)")
    print("=" * 72)

    det_min = TF_SPEC[A.DETECT_TF][1]
    rng = np.random.default_rng(SEED + 1)
    valgte = ev.iloc[rng.choice(len(ev), size=min(N_ALIGN_SAMPLES, len(ev)),
                                replace=False)]

    fejl = 0
    eksempel_vist = False
    for _, row in valgte.iterrows():
        luk = pd.Timestamp(row["start_ts_dk"]).tz_convert("UTC") + pd.Timedelta(minutes=det_min)
        luk_ns = to_ns(pd.DatetimeIndex([luk]))[0]

        linje = []
        for tf in A.TIMEFRAMES:
            v = views[tf]
            i = v.align_index(luk_ns)
            if i < 0:
                continue
            # Den valgte bar skal vaere afsluttet ...
            if v.bar_close_ns[i] > luk_ns:
                fejl += 1
            # ... og den naeste maa ikke vaere det (ellers valgte vi for tidligt).
            if i + 1 < len(v.bar_close_ns) and v.bar_close_ns[i + 1] <= luk_ns:
                fejl += 1
            # Sanity: den gemte close skal matche den bar vi peger paa.
            if _rel_diff(float(row[f"close_{tf}_start"]),
                         float(v.feat['close'].iloc[i])) > TOL_REL:
                fejl += 1
            linje.append(f"{tf}: bar {v.feat.index[i].tz_convert(A.TARGET_TZ):%H:%M}"
                         f"→luk {pd.Timestamp(v.bar_close_ns[i]).tz_localize('UTC').tz_convert(A.TARGET_TZ):%H:%M}")

        if not eksempel_vist:
            print(f"  Eksempel — {A.DETECT_TF}-start {row['start_ts_dk']:%Y-%m-%d %H:%M} "
                  f"(lukker {luk.tz_convert(A.TARGET_TZ):%H:%M}):")
            for s in linje:
                print(f"      {s}")
            eksempel_vist = True

    if fejl == 0:
        print(f"\n  ✔ {len(valgte)} events x {len(A.TIMEFRAMES)} TF: altid seneste "
              f"FAERDIGE bar, og close matcher.")
    else:
        print(f"\n  ✘ {fejl} alignment-fejl.")
    return fejl == 0


# =============================================================================
# TJEK 3 — UAFHAENGIG INDIKATOR-KONTROL
# =============================================================================
def tjek_indikatorer(df1m: pd.DataFrame) -> bool:
    """
    Genberegn tre indikatorer med en langsom, direkte implementering (rene
    loekker / definitionen skrevet ud) og sammenlign med bibliotekets.
    Fanger vektoriserings-fejl som TJEK 1 og 2 ikke ser.
    """
    print("\n" + "=" * 72)
    print("TJEK 3 — uafhaengig genberegning af z, CMF, RVOL og WaveTrend")
    print("=" * 72)

    bars = resample_ohlcv(df1m[df1m.index >= "2026-01-01"], "15min")
    h, l, c, v = bars["high"], bars["low"], bars["close"], bars["volume"]
    ok = True

    # --- z-score: direkte definition med population-std ---------------------
    z_lib = zscore(c, 30).to_numpy()
    cv = c.to_numpy()
    z_ref = np.full(len(cv), np.nan)
    for i in range(29, len(cv)):
        w = cv[i - 29:i + 1]
        sd = np.sqrt(((w - w.mean()) ** 2).mean())      # ddof=0
        z_ref[i] = (cv[i] - w.mean()) / sd if sd else np.nan
    d = np.nanmax(np.abs(z_lib - z_ref))
    print(f"  z(30)        max abs afvigelse: {d:.3e}   {'OK' if d < TOL_IMPL else 'FEJL'}")
    ok &= d < TOL_IMPL

    # --- CMF: direkte definition -------------------------------------------
    cmf_lib = cmf(h, l, c, v, 20).to_numpy()
    hv, lv, vv = h.to_numpy(), l.to_numpy(), v.to_numpy()
    mult = np.where(hv - lv != 0, ((cv - lv) - (hv - cv)) / (hv - lv), 0.0)
    mfv = mult * vv
    cmf_ref = np.full(len(cv), np.nan)
    for i in range(19, len(cv)):
        sv = vv[i - 19:i + 1].sum()
        cmf_ref[i] = mfv[i - 19:i + 1].sum() / sv if sv else np.nan
    d = np.nanmax(np.abs(cmf_lib - cmf_ref))
    print(f"  CMF(20)      max abs afvigelse: {d:.3e}   {'OK' if d < TOL_IMPL else 'FEJL'}")
    ok &= d < TOL_IMPL

    # --- RVOL: skal EKSKLUDERE den aktuelle bar ----------------------------
    rv_lib = rvol(v, 20).to_numpy()
    rv_ref = np.full(len(cv), np.nan)
    for i in range(20, len(cv)):
        base = vv[i - 20:i].mean()                       # de 20 FOREGAAENDE
        rv_ref[i] = vv[i] / base if base else np.nan
    d = np.nanmax(np.abs(rv_lib - rv_ref))
    print(f"  RVOL(20)     max abs afvigelse: {d:.3e}   {'OK' if d < TOL_IMPL else 'FEJL'}")
    ok &= d < TOL_IMPL

    # --- WaveTrend: rekursiv EMA skrevet ud bar for bar --------------------
    # NB: paa foerste bar er ap == esa, saa de == 0 og ci er udefineret (na).
    # Bibliotekets ema() er pandas .ewm(adjust=False), som HOLDER den forrige
    # vaerdi hen over en NaN. Referencen skal goere det samme — ellers tester
    # vi NaN-politik i stedet for formlen.
    wt1_lib, wt2_lib = wavetrend(h, l, c)
    ap = ((hv + lv + cv) / 3.0)

    def ema_loop(x, n):
        a = 2.0 / (n + 1.0)
        out = np.full(len(x), np.nan)
        prev = np.nan
        for i in range(len(x)):
            if np.isnan(x[i]):
                out[i] = prev            # hold forrige vaerdi hen over na
                continue
            prev = x[i] if np.isnan(prev) else x[i] * a + prev * (1 - a)
            out[i] = prev
        return out

    esa = ema_loop(ap, 9)
    de = ema_loop(np.abs(ap - esa), 9)
    ci = np.where(de != 0, (ap - esa) / (0.015 * de), np.nan)
    wt1_ref = ema_loop(ci, 12)
    wt2_ref = pd.Series(wt1_ref).rolling(3, min_periods=3).mean().to_numpy()

    d1 = np.nanmax(np.abs(wt1_lib.to_numpy() - wt1_ref))
    d2 = np.nanmax(np.abs(wt2_lib.to_numpy() - wt2_ref))
    print(f"  WaveTrend wt1 max abs afvigelse: {d1:.3e}   {'OK' if d1 < TOL_IMPL else 'FEJL'}")
    print(f"  WaveTrend wt2 max abs afvigelse: {d2:.3e}   {'OK' if d2 < TOL_IMPL else 'FEJL'}")
    ok &= (d1 < TOL_IMPL) and (d2 < TOL_IMPL)

    return bool(ok)


# =============================================================================
def main() -> int:
    print("── Verifikation af store_bevaegelser-pipelinen ─────────────────")
    ev = pd.read_parquet(A.OUT_DIR / "store_bevaegelser_events.parquet")
    print(f"Event-tabel: {len(ev):,} raekker x {len(ev.columns)} kolonner")

    df1m = A.load_minute_data()
    views = {tf: TimeframeView.build(tf, df1m) for tf in A.TIMEFRAMES}

    r1 = tjek_future_leak(df1m, ev)
    r2 = tjek_alignment(views, ev)
    r3 = tjek_indikatorer(df1m)

    print("\n" + "=" * 72)
    print(f"  TJEK 1 future leak      : {'BESTAAET' if r1 else 'FEJLET'}")
    print(f"  TJEK 2 MTF-alignment    : {'BESTAAET' if r2 else 'FEJLET'}")
    print(f"  TJEK 3 indikator-kontrol: {'BESTAAET' if r3 else 'FEJLET'}")
    print("=" * 72)
    return 0 if (r1 and r2 and r3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
