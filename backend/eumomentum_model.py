#!/usr/bin/env python3
"""
eumomentum_model.py — EUMOMENTUM Trin 2: meta-label-model (gradient-boosted trees).
════════════════════════════════════════════════════════════════════════════════════
Bygger paa (a) separabilitets-studiet. Ét spoergsmaal: kan en model der kombinerer ALLE
faktorer (F1-F8) + interaktioner adskille EXTENSION (reverterens taber) fra REVERSION —
robust OUT-OF-SAMPLE — bedre end nogen enkelt faktor gjorde? Og forbedrer det reverterens P&L?

KOEBER INTET. BYGGER INGEN STRATEGI. Rent offline. Roerer ikke handelsstien eller den
validerede eureversion_backtest. Genbruger (a)'s events.csv (features + label + exit_close).

PRAE-REGISTRERET (fastlagt FOER OOS-holdout ses — kan ikke aendres bagefter):
  - Label: 1 = EXTENSION (reverteren rammer stop), 0 = REVERSION/TIMEOUT (reverteren ok).
  - Split: OOS-holdout = ts-dato >= HOLDOUT (default 2025-11-19, samme graense som (a));
    train = foer. OOS roeres KUN til den endelige dom.
  - Model: XGBoost med LAASTE hyperparametre (shallow + regulariseret; INGEN tuning paa OOS).
  - Purged walk-forward CV i train (dag-niveau split + embargo) -> aerlig in-sample-AUC.
  - Dom:
      ROED : OOS-AUC < 0.54                      (ingen adskillelse ud over stoej)
      GUL  : OOS-AUC 0.54-0.58, ELLER adskiller men model-filter forbedrer ikke OOS-reverter-PF
      GROEN: OOS-AUC >= 0.58  OG  CV-AUC-median > 0.54  OG  model-filter LOEFTER OOS-reverter-PF@2bp
             OG filtreret PF@2bp >= 1.25

Koersel:
    python eumomentum_model.py
    python eumomentum_model.py --events eumomentum_separability_output/events.csv --holdout 2025-11-19
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from xgboost import XGBClassifier
except Exception:                       # pragma: no cover
    print("FEJL: xgboost ikke installeret (pip install xgboost)."); sys.exit(2)
from sklearn.metrics import roc_auc_score

FEATURES = ["F1_trend_aligned", "F2_atr_ratio", "F3_rvol", "F4_bar_struct",
            "F5_persistence", "F6_hour_et", "F7_confluence", "F8_abs_gap_bp"]

# LAASTE hyperparametre — praeregistreret, ingen tuning paa OOS. Shallow + regulariseret
# (8 features, ~1300 train-events, ~625 uafhaengige dage -> hold frihedsgrader lave).
XGB_PARAMS = dict(
    max_depth=3, n_estimators=200, learning_rate=0.03,
    subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, min_child_weight=20,
    objective="binary:logistic", eval_metric="auc", tree_method="hist", random_state=0,
)
COST_BP     = 2.0
SKIP_PCTILE = 70        # model-filter: spring de 30 % mest-sandsynlige-extension over (train-70-pct.)


def load_events(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York").dt.date
    df["y"] = (df["label"] == "EXTENSION").astype(int)
    for c in FEATURES + ["close", "exit_close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=FEATURES + ["close", "exit_close"]).reset_index(drop=True)


def make_model(train_y) -> XGBClassifier:
    pos = int(train_y.sum()); neg = int(len(train_y) - pos)
    spw = (neg / pos) if pos > 0 else 1.0     # klasse-ubalance (~20 % extension) — kun fra train
    return XGBClassifier(scale_pos_weight=spw, **XGB_PARAMS)


def purged_wf_auc(tr: pd.DataFrame, n_folds: int = 5, embargo_days: int = 3) -> list[float]:
    """Purged walk-forward CV paa dag-niveau: fold k traener paa dage FOER (val_start - embargo)
    og validerer paa dag-blok k. Labels resolver intradag, saa dag-split + embargo purger
    overlappende label-vinduer. Returnerer AUC pr. fold."""
    dts = np.array(sorted(tr["date"].unique()))
    blocks = np.array_split(dts, n_folds + 1)
    aucs = []
    for k in range(1, n_folds + 1):
        val_dates = set(blocks[k].tolist())
        val_start = blocks[k][0]
        embargo = val_start - timedelta(days=embargo_days)
        tr_mask = tr["date"] < embargo
        va_mask = tr["date"].isin(val_dates)
        if tr_mask.sum() < 100 or va_mask.sum() < 30 or tr.loc[va_mask, "y"].nunique() < 2:
            continue
        m = make_model(tr.loc[tr_mask, "y"])
        m.fit(tr.loc[tr_mask, FEATURES], tr.loc[tr_mask, "y"])
        p = m.predict_proba(tr.loc[va_mask, FEATURES])[:, 1]
        aucs.append(float(roc_auc_score(tr.loc[va_mask, "y"], p)))
    return aucs


def reverter_metrics(df: pd.DataFrame, cost_bp: float = COST_BP) -> dict:
    """Reverter-P&L (modsat momentum): short op-straek / long ned-straek, exit ved label-bar.
    net% = -(momentum-gross%) - cost. Spejler naive_pf i (a), bare omvendt fortegn."""
    if len(df) == 0:
        return {"pf": 0.0, "n": 0, "win": 0.0, "avg": 0.0, "total": 0.0}
    entry = df["close"].to_numpy(); ex = df["exit_close"].to_numpy()
    raw = (ex - entry) / entry * 100.0
    mom_gross = np.where(df["side"].to_numpy() == "up", raw, -raw)
    net = -mom_gross - cost_bp * 0.01
    wins = net[net > 0].sum(); losses = -net[net < 0].sum()
    pf = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0)
    return {"pf": pf, "n": len(df), "win": float((net > 0).mean() * 100),
            "avg": float(net.mean()), "total": float(net.sum())}


def main() -> int:
    ap = argparse.ArgumentParser(description="EUMOMENTUM Trin 2 — meta-label-model (offline)")
    ap.add_argument("--events", default="eumomentum_separability_output/events.csv")
    ap.add_argument("--holdout", default="2025-11-19", help="OOS-holdout starter paa denne ET-dato")
    a = ap.parse_args()

    ev_path = Path(a.events)
    if not ev_path.is_absolute():
        ev_path = Path.cwd() / ev_path
    if not ev_path.exists():
        print(f"FEJL: {ev_path} findes ikke — koer eumomentum_separability.py foerst."); return 2
    holdout = datetime.strptime(a.holdout, "%Y-%m-%d").date()

    df = load_events(ev_path)
    tr = df[df["date"] < holdout].copy()
    oos = df[df["date"] >= holdout].copy()

    def line(s=""): print(s)
    line("=" * 78)
    line("  EUMOMENTUM — Trin 2: meta-label-model (gradient-boosted trees)")
    line(f"  Events: {len(df)}  |  train {len(tr)} (<{holdout})  |  OOS {len(oos)} (>= {holdout})")
    line(f"  Label: EXTENSION=1 (reverteren taber).  train-ext {tr['y'].mean()*100:.1f}%  "
         f"OOS-ext {oos['y'].mean()*100:.1f}%")
    line("=" * 78)

    if tr["y"].nunique() < 2 or oos["y"].nunique() < 2:
        line("FEJL: for faa events / kun én klasse i train eller OOS."); return 1

    # ── Purged walk-forward CV (aerlig in-sample) ──────────────────────
    cv = purged_wf_auc(tr)
    cv_med = float(np.median(cv)) if cv else float("nan")
    line("\n── Purged walk-forward CV (in-sample, dag-niveau + embargo) ──────────")
    line(f"  fold-AUC: {', '.join(f'{x:.3f}' for x in cv) if cv else '(ingen)'}   median {cv_med:.3f}")

    # ── Endelig model paa HELE train, evaluér ÉN gang paa OOS ──────────
    model = make_model(tr["y"])
    model.fit(tr[FEATURES], tr["y"])
    p_oos = model.predict_proba(oos[FEATURES])[:, 1]
    oos = oos.assign(p_ext=p_oos)
    oos_auc = float(roc_auc_score(oos["y"], p_oos))

    line("\n── OOS (roert ÉN gang) ──────────────────────────────────────────────")
    line(f"  OOS-AUC: {oos_auc:.3f}   (0.50 = ingen adskillelse)")
    # Top-decil lift: extension-rate blandt de 10 % hoejeste P(ext) vs OOS-base.
    p0_oos = oos["y"].mean()
    k = max(1, len(oos) // 10)
    top = oos.nlargest(k, "p_ext")
    line(f"  top-decil P(ext): extension-rate {top['y'].mean()*100:.1f}%  "
         f"vs base {p0_oos*100:.1f}%  (lift {(top['y'].mean()-p0_oos)*100:+.1f} pp)")

    # ── Feature-importance (gain) ──────────────────────────────────────
    imp = sorted(zip(FEATURES, model.feature_importances_), key=lambda t: -t[1])
    line("\n── Feature-importance (gain) ────────────────────────────────────────")
    for f, w in imp:
        line(f"  {f:<20} {w:.3f}  {'#' * int(round(w * 60))}")

    # ── Oekonomi: forbedrer model-filter reverterens OOS-P&L? ──────────
    thr = float(np.percentile(model.predict_proba(tr[FEATURES])[:, 1], SKIP_PCTILE))
    all_m = reverter_metrics(oos)
    kept = oos[oos["p_ext"] < thr]
    kept_m = reverter_metrics(kept)
    line("\n── Reverter-oekonomi paa OOS (short op-straek / long ned-straek, @2bp) ──")
    line(f"  {'':22}{'n':>6}{'PF':>8}{'win%':>8}{'avg%':>9}{'total%':>10}")
    line(f"  {'alle events':22}{all_m['n']:>6}{all_m['pf']:>8.2f}{all_m['win']:>7.1f}"
         f"{all_m['avg']:>+9.3f}{all_m['total']:>+10.2f}")
    line(f"  {'model-filtreret':22}{kept_m['n']:>6}{kept_m['pf']:>8.2f}{kept_m['win']:>7.1f}"
         f"{kept_m['avg']:>+9.3f}{kept_m['total']:>+10.2f}   (skip P(ext)>={thr:.2f})")
    pf_improved = kept_m["pf"] > all_m["pf"] and kept_m["n"] >= 30

    # ── DOM (praeregistreret) ──────────────────────────────────────────
    line("\n" + "=" * 78)
    if oos_auc < 0.54:
        verdict = "ROED"
        why = f"OOS-AUC {oos_auc:.3f} < 0.54 — modellen adskiller ikke extension fra reversion OOS"
    elif oos_auc >= 0.58 and cv_med > 0.54 and pf_improved and kept_m["pf"] >= 1.25:
        verdict = "GROEN"
        why = (f"OOS-AUC {oos_auc:.3f} (CV-median {cv_med:.3f}); model-filter loefter reverter-PF "
               f"{all_m['pf']:.2f} -> {kept_m['pf']:.2f} (>=1.25) paa OOS")
    else:
        verdict = "GUL"
        bits = [f"OOS-AUC {oos_auc:.3f}"]
        if not (oos_auc >= 0.58): bits.append("AUC<0.58")
        if not (cv_med > 0.54):   bits.append(f"CV-median {cv_med:.3f}<=0.54")
        if not pf_improved:       bits.append(f"filter forbedrer ikke PF ({all_m['pf']:.2f}->{kept_m['pf']:.2f})")
        elif kept_m["pf"] < 1.25: bits.append(f"filtreret PF {kept_m['pf']:.2f}<1.25")
        why = "; ".join(bits)
    line(f"DOM: {verdict} — {why}")
    line("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
