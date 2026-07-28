#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
valider_signaturer.py
=====================
IS/OOS-validering af de to entry-signaturer paa MES (long og short).

Formaal: vi har fundet to signaturer der paa HELE datasaettet ser staerke ud
(~4x lift, retning ~99 %, reversion til middel). Spoergsmaalet her er ét:
**holder edgen ud af proeve?** Alle noegletal genberegnes derfor paa en
tidlig (in-sample) og en sen (out-of-sample) halvdel hver for sig, long og
short adskilt, saa et kurve-tilpasset moenster afsloerer sig ved at forsvinde
i OOS.

Ingen P&L, ingen entry/exit-simulering, ingen taerskel-optimering, ingen ML.
Ren signal-validering: ja/nej + tal.

Input (allerede produceret af analyse_store_bevaegelser.py):
  store_bevaegelser_out/store_bevaegelser_events.parquet    events m. START/SLUT-snapshots
  store_bevaegelser_out/store_bevaegelser_baseline.parquet  kontrol-barer m. START-snapshots

Output:
  store_bevaegelser_out/signatur_validering_IS_OOS.md   rapport, IS vs OOS side om side
  store_bevaegelser_out/signatur_validering.csv         raa tal i langt format

Koeres:  python valider_signaturer.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# KONSTANTER — praeregistreret. Ret dem IKKE efter at have set resultatet.
# =============================================================================
HERE    = Path(__file__).resolve().parent
OUT_DIR = HERE / "store_bevaegelser_out"
EVENTS  = OUT_DIR / "store_bevaegelser_events.parquet"
BASELINE = OUT_DIR / "store_bevaegelser_baseline.parquet"
MIN_FILE = HERE / "data_harvest" / "mes_m2k_stitched" / "MES_1min.csv"

# --- Rul-filter (samme paa events OG baseline) ----------------------------
ROLL_MIN = 100                 # bars_since_roll skal vaere > dette

# --- Signatur-definitioner (maalt ved event-START) ------------------------
Z_LONG      = -2.0             # z_15m_start <= -2
Z_SHORT     =  2.0             # z_15m_start >= +2
RVOL_HI     =  1.5             # "hoej RVOL"
RVOL_TAIL   =  4.0             # hale-/stor-bevaegelses-filter
DOT_LONG    = "kraftig_groen"  # dot_type_3m_start
DOT_SHORT   = "kraftig_roed"
SIZE_THRESHOLDS = [1.5, 3.0, 4.0, 6.0, 8.0]     # ATR

SIDER = {                      # side -> (forventet retning, z-fortegn, prik)
    "long":  ("up",   "under", DOT_LONG),
    "short": ("down", "over",  DOT_SHORT),
}

# --- Prevalens-estimat ----------------------------------------------------
# Spec: est. total 15m-barer ~= hverdage x 23 timer x 4 barer.
BARER_PR_HVERDAG = 23 * 4
BAR_SENSITIVITET = 0.25        # +/-25 % foelsomhed paa total-barer

# --- Bestaa/dumpe-kriterier (defineret FOER koersel) ----------------------
OOS_MIN_ENRICHMENT = 3.0       # fuld signatur
OOS_MIN_RETNING    = 0.95      # 95 % i forventet retning
OOS_PRAECISION_TOL = 0.25      # skal ligge inden for IS-praecision +/-25 %

# --- Robusthedsgitter (rapporteres, optimeres IKKE) -----------------------
GRID_Z    = [1.5, 2.0, 2.5]                     # absolut vaerdi; spejles pr. side
GRID_RVOL = [1.25, 1.5, 2.0]
GRID_DOT  = {                                   # label -> tilladte prik-typer
    "kun kraftig":   {"long": [DOT_LONG],              "short": [DOT_SHORT]},
    "kraftig + alm": {"long": [DOT_LONG, "alm_groen"], "short": [DOT_SHORT, "alm_roed"]},
}

Z_CRIT = 1.959964              # 95 % normal-fraktil

# --- Flagning af IS→OOS-aendringer ----------------------------------------
# Specen: "Flag enhver metrik der kun er staerk i IS og forsvinder i OOS."
# Et fald paa 30 %+ regnes som svaekket, en stigning paa 43 %+ (= 1/0,7) som
# styrket. Symmetrisk, saa vi ikke kun leder efter daarlige nyheder.
SVAEKKET_GRAENSE = 0.70
STYRKET_GRAENSE  = 1.0 / SVAEKKET_GRAENSE


# =============================================================================
# 1. INDLAESNING OG SPLIT
# =============================================================================
def load(baseline_sti: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Laes events + baseline, anvend rul-filteret, og dedupliker events.

    Dedup: swing- og fwd-metoden finder tit den samme bevaegelse, saa den samme
    start-bar optraeder to gange. Til antals- og prevalens-beregninger skal
    hver (start-bar, retning) kun taelle ÉN gang — ellers ville events der
    begge metoder fanger, vaegte dobbelt.
    """
    ev = pd.read_parquet(EVENTS)
    bl = pd.read_parquet(baseline_sti or BASELINE)

    ev = ev[ev["bars_since_roll"] > ROLL_MIN].copy()
    bl = bl[bl["bars_since_roll"] > ROLL_MIN].copy()

    # Deterministisk dedup: sortér foerst, saa samme raekke altid vinder.
    ev = (ev.sort_values(["start_ts_dk", "retning", "metode", "event_id"])
            .drop_duplicates(["start_ts_dk", "retning"], keep="first")
            .reset_index(drop=True))
    return ev, bl


def kalender_split(ev: pd.DataFrame, bl: pd.DataFrame) -> pd.Timestamp:
    """
    Kronologisk 50/50-split: midtpunktet af kalenderspaendet.

    Bemaerk: specen siger baade "median-datoen" og "tidligste 50 % af
    kalendertiden". De to falder her inden for faa dage af hinanden (data er
    jaevnt fordelt over de to aar), saa valget er uden betydning — vi bruger
    kalender-midtpunktet og rapporterer begge datoer i rapporten.
    """
    t0 = min(ev["start_ts_dk"].min(), bl["start_ts_dk"].min())
    t1 = max(ev["start_ts_dk"].max(), bl["start_ts_dk"].max())
    return t0 + (t1 - t0) / 2


def aar_split(ev: pd.DataFrame, bl: pd.DataFrame) -> pd.Timestamp:
    """Kontrol-split: aar-1 vs aar-2 regnet fra foerste observation."""
    t0 = min(ev["start_ts_dk"].min(), bl["start_ts_dk"].min())
    return t0 + pd.Timedelta(days=365)


def kontrakt_perioder(ev: pd.DataFrame, bl: pd.DataFrame) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """
    Del historikken op efter futures-kontrakt (kvartals-rul).

    Baggrund: specens kontrol-split "aar-1 vs aar-2" er degenereret paa dette
    datasaet — serien spaender praecis to aar, saa aars-graensen falder inden
    for ét doegn af 50/50-kalendergraensen. De to splits er altsaa det SAMME
    split og kan ikke bekraefte hinanden.

    Per kontrakt giver derimod ~8 uafhaengige perioder. Er enrichment stabil
    paa tvaers af dem, er det et reelt robusthedstjek: en edge der kun findes
    i én kontrakt (fx ét volatilitets-regime) afsloerer sig her.
    """
    from analyse_store_bevaegelser import load_roll_times

    t0 = min(ev["start_ts_dk"].min(), bl["start_ts_dk"].min())
    # +1 s: alle udsnit bruger [a, b), saa den oeverste graense skal ligge en
    # anelse efter sidste observation for at faa den med.
    t1 = max(ev["start_ts_dk"].max(), bl["start_ts_dk"].max()) + pd.Timedelta(seconds=1)
    tz = t0.tz
    graenser = [t0] + [pd.Timestamp(r).tz_convert(tz) for r in load_roll_times()] + [t1]
    graenser = sorted(g for g in graenser if t0 <= g <= t1)

    ud = []
    for a, b in zip(graenser, graenser[1:]):
        if a >= b:
            continue
        ud.append((f"{a:%Y-%m}→{b:%Y-%m}", a, b))
    return ud


def hverdage(t0: pd.Timestamp, t1: pd.Timestamp) -> int:
    """Antal hverdage i spaendet [t0, t1) — bruges til prevalens-naevneren."""
    return int(np.busday_count(t0.date(), t1.date()))


def faktiske_15m_barer(t0: pd.Timestamp, t1: pd.Timestamp) -> int:
    """
    Kontroltal: det FAKTISKE antal 15m-barer i spaendet.

    Specens estimat (hverdage x 92) er et skoen; her tæller vi dem. Bruges kun
    som krydstjek i rapporten — hovedtallene foelger specens estimat.
    """
    df = pd.read_csv(MIN_FILE, usecols=["timestamp"])
    ts = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Europe/Copenhagen")
    bars = pd.Series(1, index=pd.DatetimeIndex(ts)).resample("15min").sum()
    bars = bars[bars > 0]
    return int(((bars.index >= t0) & (bars.index < t1)).sum())


# =============================================================================
# 2. SIGNATUR
# =============================================================================
def signatur(d: pd.DataFrame, side: str, rvol_min: float = RVOL_HI,
             z_abs: float = 2.0, prikker: list[str] | None = None) -> pd.Series:
    """
    Boolean-maske: fyrer signaturen paa disse raekker?

    Alle tre betingelser maales ved event-START (kolonner *_start), saa masken
    kan bruges paa baade events og kontrol-barer med samme kode — det er
    forudsaetningen for at enrichment overhovedet er meningsfuld.
    """
    z = d["z_15m_start"]
    r = d["rvol_15m_start"]
    dot = d["dot_type_3m_start"]
    if prikker is None:
        prikker = [DOT_LONG if side == "long" else DOT_SHORT]

    z_ok = (z <= -z_abs) if side == "long" else (z >= z_abs)
    return z_ok & (r >= rvol_min) & dot.isin(prikker)


# =============================================================================
# 3. STATISTIK-HJAELPERE
# =============================================================================
def wilson(k: int, n: int) -> tuple[float, float]:
    """
    Wilson-konfidensinterval for en andel.

    Bruges frem for det normale interval fordi flere af raterne her er meget
    smaa (kontrol-raten for hale-filteret er ~0,3 %, dvs. en haandfuld hits).
    Det normale interval bryder sammen der; Wilson goer ikke.
    """
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + Z_CRIT ** 2 / n
    c = p + Z_CRIT ** 2 / (2 * n)
    s = Z_CRIT * np.sqrt(p * (1 - p) / n + Z_CRIT ** 2 / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def ratio_ci(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float, float]:
    """
    Forhold mellem to andele (p1/p2) med 95 %-interval — Katz' log-metode.

    Returnerer (ratio, nedre, oevre). Dette er tallet vi kalder "enrichment"
    eller "lift": hvor mange gange hyppigere signaturen er paa event-starter
    end paa kontrol-barer.

    Intervallet er ikke pynt. Naar naevneren hviler paa en halv snes hits
    (hale-filteret), er et punktestimat paa "12x" i praksis foreneligt med alt
    fra 5x til 30x — og saa skal man ikke laese det som 12.
    """
    if n1 == 0 or n2 == 0 or k1 == 0 or k2 == 0:
        p1 = k1 / n1 if n1 else np.nan
        p2 = k2 / n2 if n2 else np.nan
        return (p1 / p2 if (p2 and np.isfinite(p2)) else np.nan, np.nan, np.nan)
    p1, p2 = k1 / n1, k2 / n2
    logr = np.log(p1 / p2)
    se = np.sqrt((1 - p1) / (k1) + (1 - p2) / (k2))
    return (p1 / p2, float(np.exp(logr - Z_CRIT * se)), float(np.exp(logr + Z_CRIT * se)))


def fmt_ci(v: float, lo: float, hi: float, enhed: str = "x") -> str:
    """'4.30x [3.2-5.8]' — punktestimat med interval, eller '—' hvis udefineret."""
    if not np.isfinite(v):
        return "—"
    if not np.isfinite(lo):
        return f"{v:.2f}{enhed} [for faa obs.]"
    return f"{v:.2f}{enhed} [{lo:.1f}–{hi:.1f}]"


# =============================================================================
# 4. METRIKKER PR. HALVDEL PR. SIDE
# =============================================================================
def beregn(ev: pd.DataFrame, bl: pd.DataFrame, side: str,
           t0: pd.Timestamp, t1: pd.Timestamp) -> dict:
    """
    Alle noegletal for én halvdel og én side.

    Nomenklatur (vigtig for at laese rapporten):
      E  = antal retnings-event-starter i den forventede retning
      K  = heraf hvor mange signaturen fyrer paa
      nb = antal kontrol-barer signaturen fyrer paa, ud af Nb i alt
      B  = estimeret totalt antal 15m-barer i halvdelens datospaend

    enrichment = (K/E) / (nb/Nb)
    praecision = K / (kontrol-rate x B)     <- "fyrer den, hvor tit foelger et event?"
    basisrate  = E / B
    lift       = praecision / basisrate

    NB: lift og enrichment er ALGEBRAISK det samme tal —
        praecision/basisrate = [K/(r*B)] / [E/B] = (K/E)/r = enrichment.
        B gaar altsaa ud. Det betyder at +/-25 %-foelsomheden paa antal barer
        rammer praecisionen, men IKKE liftet. Konklusionen om lift er derfor
        uafhaengig af hvor godt vi gaetter antallet af barer.
    """
    forventet, _, _ = SIDER[side]
    dir_ev = ev[ev["retning"] == forventet]

    m_dir = signatur(dir_ev, side)
    m_all = signatur(ev, side)
    m_bl = signatur(bl, side)

    E, K = len(dir_ev), int(m_dir.sum())
    Nb, nb = len(bl), int(m_bl.sum())

    # --- Retningstraef: blandt ALLE fyrende event-starter (begge retninger) --
    fyrer_alle = ev[m_all]
    n_sig = len(fyrer_alle)
    n_ret = int((fyrer_alle["retning"] == forventet).sum())
    ret_hit = n_ret / n_sig if n_sig else np.nan
    ret_lo, ret_hi = wilson(n_ret, n_sig)

    # --- Enrichment ------------------------------------------------------
    enr, enr_lo, enr_hi = ratio_ci(K, E, nb, Nb)
    kontrol_rate = nb / Nb if Nb else np.nan

    # --- Praecision + basisrate ------------------------------------------
    B = hverdage(t0, t1) * BARER_PR_HVERDAG
    est_fyringer = kontrol_rate * B if np.isfinite(kontrol_rate) else np.nan
    praecision = K / est_fyringer if est_fyringer else np.nan
    basisrate = E / B if B else np.nan
    # Foelsomhed: faerre barer -> faerre estimerede fyringer -> hoejere praecision
    praec_lav = K / (kontrol_rate * B * (1 + BAR_SENSITIVITET)) if est_fyringer else np.nan
    praec_hoej = K / (kontrol_rate * B * (1 - BAR_SENSITIVITET)) if est_fyringer else np.nan

    # --- Exit-profil (kun paa de fyrende retnings-events) -----------------
    f = dir_ev[m_dir]
    if len(f):
        exit_profil = {
            "median_size_atr": float(f["size_atr"].median()),
            "median_size_pt": float(f["size_pt"].median()),
            "median_varighed_min": float(f["varighed_min"].median()),
            "median_z_start": float(f["z_15m_start"].median()),
            "median_z_end": float(f["z_15m_end"].median()),
            "andel_overshoot": float(((f["z_15m_end"] >= 2.0) if side == "long"
                                      else (f["z_15m_end"] <= -2.0)).mean()),
            "andel_swing": float((f["metode"] == "swing").mean()),
        }
    else:
        exit_profil = {k: np.nan for k in (
            "median_size_atr", "median_size_pt", "median_varighed_min",
            "median_z_start", "median_z_end", "andel_overshoot", "andel_swing")}

    # --- Stoerrelses-taerskel-tabeller (fuld signatur + hale-filter) -------
    def taerskel_tabel(rvol_min: float) -> list[dict]:
        mb = signatur(bl, side, rvol_min=rvol_min)
        nb_t, r_t = int(mb.sum()), (mb.mean() if len(bl) else np.nan)
        md = signatur(dir_ev, side, rvol_min=rvol_min)
        raekker = []
        for T in SIZE_THRESHOLDS:
            stor = dir_ev["size_atr"] >= T
            E_T = int(stor.sum())
            K_T = int((md & stor).sum())
            est = r_t * B if np.isfinite(r_t) else np.nan
            lift, lo, hi = ratio_ci(K_T, E_T, nb_t, Nb)
            raekker.append({
                "taerskel_atr": T, "E_T": E_T, "K_T": K_T,
                "kontrol_hits": nb_t,
                "praecision": (K_T / est) if est else np.nan,
                "basisrate": (E_T / B) if B else np.nan,
                "lift": lift, "lift_lo": lo, "lift_hi": hi,
            })
        return raekker

    return {
        "side": side, "forventet": forventet,
        "t0": t0, "t1": t1, "hverdage": hverdage(t0, t1), "B": B,
        "E": E, "K": K, "Nb": Nb, "nb": nb,
        "n_signaler": n_sig, "retning_hit": ret_hit,
        "retning_lo": ret_lo, "retning_hi": ret_hi,
        "event_rate": (K / E) if E else np.nan,
        "kontrol_rate": kontrol_rate,
        "enrichment": enr, "enr_lo": enr_lo, "enr_hi": enr_hi,
        "praecision": praecision, "praec_lav": praec_lav, "praec_hoej": praec_hoej,
        "basisrate": basisrate,
        **exit_profil,
        "tab_fuld": taerskel_tabel(RVOL_HI),
        "tab_hale": taerskel_tabel(RVOL_TAIL),
    }


# =============================================================================
# 5. ROBUSTHEDSGITTER
# =============================================================================
def gitter(ev: pd.DataFrame, bl: pd.DataFrame, side: str) -> list[dict]:
    """
    Enrichment + retningstraef for nabo-vaerdier af de tre taerskler.

    Formaalet er IKKE at finde den bedste kombination — det ville vaere
    kurve-tilpasning, og vi ville med garanti finde ét felt der ser flot ud.
    Formaalet er at se om edgen er STABIL paa tvaers af naboer. Falder den fra
    4x til 1x ved at rykke z fra -2.0 til -2.5, hviler den paa et knivskarpt
    valg og er ikke til at stole paa.
    """
    forventet = SIDER[side][0]
    dir_ev = ev[ev["retning"] == forventet]
    ud = []
    for z in GRID_Z:
        for rv in GRID_RVOL:
            for dot_label, dot_map in GRID_DOT.items():
                prikker = dot_map[side]
                md = signatur(dir_ev, side, rvol_min=rv, z_abs=z, prikker=prikker)
                ma = signatur(ev, side, rvol_min=rv, z_abs=z, prikker=prikker)
                mb = signatur(bl, side, rvol_min=rv, z_abs=z, prikker=prikker)
                K, E = int(md.sum()), len(dir_ev)
                nb, Nb = int(mb.sum()), len(bl)
                enr, lo, hi = ratio_ci(K, E, nb, Nb)
                fyrer = ev[ma]
                ud.append({
                    "side": side, "z": z, "rvol": rv, "prik": dot_label,
                    "n_signaler": len(fyrer),
                    "retning_hit": ((fyrer["retning"] == forventet).mean()
                                    if len(fyrer) else np.nan),
                    "K": K, "kontrol_hits": nb,
                    "enrichment": enr, "enr_lo": lo, "enr_hi": hi,
                })
    return ud


# =============================================================================
# 6. DOM
# =============================================================================
def doem(is_m: dict, oos_m: dict) -> tuple[bool, list[tuple[str, bool, str]]]:
    """
    Bestaa/dumpe efter de praeregistrerede kriterier. Ingen efterrationalisering.
    Returnerer (bestaaet, [(kriterium, opfyldt, tekst)]).
    """
    k = []

    enr = oos_m["enrichment"]
    k.append(("Enrichment >= 3x (OOS)", bool(np.isfinite(enr) and enr >= OOS_MIN_ENRICHMENT),
              f"OOS {fmt_ci(enr, oos_m['enr_lo'], oos_m['enr_hi'])} "
              f"(IS {fmt_ci(is_m['enrichment'], is_m['enr_lo'], is_m['enr_hi'])})"))

    r = oos_m["retning_hit"]
    k.append(("Retningstraef >= 95 % (OOS)", bool(np.isfinite(r) and r >= OOS_MIN_RETNING),
              f"OOS {r*100:.1f} % [{oos_m['retning_lo']*100:.1f}–{oos_m['retning_hi']*100:.1f}] "
              f"(IS {is_m['retning_hit']*100:.1f} %)"))

    pi, po = is_m["praecision"], oos_m["praecision"]
    lo, hi = pi * (1 - OOS_PRAECISION_TOL), pi * (1 + OOS_PRAECISION_TOL)
    retning_ord = "over" if (np.isfinite(po) and po > hi) else \
                  "under" if (np.isfinite(po) and po < lo) else "inden for"
    k.append(("Praecision inden for IS +/-25 %",
              bool(np.isfinite(po) and lo <= po <= hi),
              f"OOS {po*100:.1f} % ligger {retning_ord} IS-intervallet "
              f"{lo*100:.1f}–{hi*100:.1f} % (IS {pi*100:.1f} %)"))

    lifts = [r_["lift"] for r_ in oos_m["tab_hale"]]
    mono = all(b >= a - 1e-9 for a, b in zip(lifts, lifts[1:])
               if np.isfinite(a) and np.isfinite(b))
    k.append(("RVOL -> stoerrelse monotont stigende (OOS)", bool(mono),
              "lift pr. taerskel: " + " → ".join(
                  "—" if not np.isfinite(x) else f"{x:.1f}x" for x in lifts)))

    return all(x[1] for x in k), k


def diagnostik(is_m: dict, oos_m: dict) -> list[str]:
    """
    Supplerende aflaesninger — AENDRER ikke dommen.

    De praeregistrerede kriterier er bindende og staar uroert. Men et binaert
    bestaa/dump skjuler *hvordan* et kriterium fejlede, og de to ting er ikke
    lige alvorlige: en praecision der falder under IS er en svaekkelse, en der
    stiger over er det modsatte. Derfor dette afsnit.
    """
    ud = []

    # --- Faldt praecisionen, eller steg den? -----------------------------
    pi, po = is_m["praecision"], oos_m["praecision"]
    if np.isfinite(pi) and np.isfinite(po):
        d = (po / pi - 1) * 100
        lo, hi = pi * (1 - OOS_PRAECISION_TOL), pi * (1 + OOS_PRAECISION_TOL)
        if po < lo:
            hale = ("Det er et FALD ud over tolerancen — praecis den svaekkelse "
                    "kriteriet blev skrevet for at fange.")
        elif po > hi:
            hale = ("Bemaerk retningen: praecisionen er **hoejere** ud af proeve. "
                    "Kriteriet er tosidet som praeregistreret og fejler derfor, "
                    "men specens egen parentes siger \"dvs. ikke et kollaps\" — "
                    "og et kollaps er det modsatte af det der er sket.")
        else:
            hale = ("Inden for tolerancen" +
                    (" (svagt fald)." if d < 0 else " (svag stigning)."))
        ud.append(f"**Praecision OOS mod IS:** {po*100:.1f} % mod {pi*100:.1f} % "
                  f"({d:+.0f} %). {hale}")

    # --- Er hale-liftet stigende, bortset fra stoej? ---------------------
    lifts = [r_["lift"] for r_ in oos_m["tab_hale"]]
    gyldige = [(i, x) for i, x in enumerate(lifts) if np.isfinite(x)]
    if len(gyldige) >= 3:
        xs = np.array([i for i, _ in gyldige], dtype=float)
        ys = np.array([x for _, x in gyldige], dtype=float)
        rho = float(np.corrcoef(np.argsort(np.argsort(xs)),
                                np.argsort(np.argsort(ys)))[0, 1])
        brud = [i for i, (a, b) in enumerate(zip(ys, ys[1:])) if b < a - 1e-9]
        ud.append(
            f"**Hale-lift, samlet tendens:** {ys[0]:.1f}x → {ys[-1]:.1f}x "
            f"(x{ys[-1]/ys[0]:.1f}), rang-korrelation {rho:+.2f}, "
            f"{len(brud)} nedadgaaende trin ud af {len(ys)-1}. "
            + ("Monotont." if not brud else
               "Kriteriet kraever STRENG monotoni og fejler paa de enkelte trin, "
               "men den samlede retning er opad. Med en haandfuld kontrol-hits "
               "bag hvert punkt er et enkelt dyk fuldt foreneligt med ren stoej."))

    # --- Kan IS og OOS overhovedet skelnes? ------------------------------
    if all(np.isfinite(x) for x in (is_m["enr_lo"], is_m["enr_hi"],
                                    oos_m["enr_lo"], oos_m["enr_hi"])):
        overlap = not (is_m["enr_hi"] < oos_m["enr_lo"] or oos_m["enr_hi"] < is_m["enr_lo"])
        ud.append(
            f"**Kan IS og OOS skelnes?** Enrichment IS "
            f"{is_m['enrichment']:.2f}x [{is_m['enr_lo']:.1f}–{is_m['enr_hi']:.1f}] "
            f"mod OOS {oos_m['enrichment']:.2f}x "
            f"[{oos_m['enr_lo']:.1f}–{oos_m['enr_hi']:.1f}]. "
            + ("Intervallerne overlapper kraftigt — forskellen mellem de to "
               "halvdele er ikke statistisk paaviselig. Det udelukker ikke en "
               "aegte svaekkelse, men data kan ikke vise en."
               if overlap else
               "Intervallerne overlapper IKKE — der er en reel forskel mellem "
               "halvdelene."))
    return ud


# =============================================================================
# 7. RAPPORT
# =============================================================================
def degradering(is_m: dict, oos_m: dict) -> list[dict]:
    """
    Systematisk IS→OOS-sammenligning af hver enkelt metrik.

    Dette er specens "flag enhver metrik der kun er staerk i IS og forsvinder i
    OOS". De praeregistrerede bestaa/dumpe-kriterier tjekker kun fire ting; en
    signatur kan bestaa dem alle og stadig have mistet netop den egenskab man
    ville bruge den til. Her sammenlignes ALT, ogsaa det kriterierne ikke
    spoerger om.
    """
    poster = [("Enrichment (fuld signatur)", "enrichment"),
              ("Retningstraef", "retning_hit"),
              ("Praecision", "praecision"),
              ("Median size_atr", "median_size_atr"),
              ("Median size_pt", "median_size_pt"),
              ("Median varighed (min)", "median_varighed_min"),
              ("Andel overshoot til modsat baand", "andel_overshoot")]

    ud = []
    for navn, noegle in poster:
        a, b = is_m.get(noegle), oos_m.get(noegle)
        ud.append({"metrik": navn, "is": a, "oos": b,
                   "ratio": (b / a) if (a not in (None, 0) and np.isfinite(a)
                                        and np.isfinite(b)) else np.nan})

    for blok, label in (("tab_fuld", "Lift"), ("tab_hale", "Lift m. RVOL≥4")):
        for ra, rb in zip(is_m[blok], oos_m[blok]):
            a, b = ra["lift"], rb["lift"]
            ud.append({"metrik": f"{label} ved ≥ {ra['taerskel_atr']} ATR",
                       "is": a, "oos": b,
                       "ratio": (b / a) if (a and np.isfinite(a) and np.isfinite(b))
                                 else np.nan})

    for r in ud:
        v = r["ratio"]
        r["flag"] = ("—" if not np.isfinite(v) else
                     "⚠ SVAEKKET" if v < SVAEKKET_GRAENSE else
                     "↑ styrket" if v > STYRKET_GRAENSE else "stabil")
    return ud


def tabel(raekker: list[list[str]], hoved: list[str]) -> str:
    ud = ["| " + " | ".join(hoved) + " |", "|" + "---|" * len(hoved)]
    ud += ["| " + " | ".join(r) + " |" for r in raekker]
    return "\n".join(ud)


def pct(x: float, d: int = 1) -> str:
    return "—" if not np.isfinite(x) else f"{x*100:.{d}f} %"


def num(x: float, d: int = 2) -> str:
    return "—" if not np.isfinite(x) else f"{x:.{d}f}"


def rapport(res: dict, grids: dict, splits: dict, faktisk: dict,
            kontrakt: dict, foer: dict | None = None) -> str:
    md = ["# IS/OOS-validering af long/short-signaturerne", "",
          "Genereret af `valider_signaturer.py`. Ren signal-validering — ingen",
          "P&L, ingen entry/exit-simulering, ingen taerskel-optimering.", "",
          "**Signaturer (praeregistreret, uaendret gennem hele koerslen):**", "",
          "```",
          f"LONG  = (z_15m_start <= {Z_LONG}) & (rvol_15m_start >= {RVOL_HI}) "
          f"& (dot_type_3m_start == \"{DOT_LONG}\")   # positiv = retning \"up\"",
          f"SHORT = (z_15m_start >= {Z_SHORT}) & (rvol_15m_start >= {RVOL_HI}) "
          f"& (dot_type_3m_start == \"{DOT_SHORT}\")    # positiv = retning \"down\"",
          f"Rul-filter: bars_since_roll > {ROLL_MIN} (paa BAADE events og baseline)",
          "```", ""]

    # ---------------- DOM ----------------
    md += ["---", "", "## 0. Dom", "",
           "Kriterierne blev defineret foer koerslen. Ingen af dem er justeret bagefter.", ""]
    for side in ("long", "short"):
        i_m, o_m = res[("IS/OOS", "IS", side)], res[("IS/OOS", "OOS", side)]
        ok, krit = doem(i_m, o_m)
        n_fejl = sum(1 for _, v, _ in krit if not v)
        md += [f"### {side.upper()} — **{'BESTAAET' if ok else 'DUMPET'}** "
               f"({4 - n_fejl}/4 kriterier opfyldt)", "",
               tabel([[k, "✔" if v else "✘", t] for k, v, t in krit],
                     ["Kriterium", "", "Tal"]), "",
               "**Hvordan fejlede den?** (aendrer ikke dommen — kriterierne staar uroert)", ""]
        md += [f"- {linje}" for linje in diagnostik(i_m, o_m)]
        md += [""]

    # ---------------- DEGRADERING ----------------
    md += ["---", "", "## 0b. Hvad aendrede sig fra IS til OOS?", "",
           "Hver metrik, ikke kun de fire kriterier. Flag: fald paa 30 %+ =",
           "⚠ SVAEKKET, stigning paa 43 %+ = ↑ styrket.", ""]
    for side in ("long", "short"):
        d = degradering(res[("IS/OOS", "IS", side)], res[("IS/OOS", "OOS", side)])
        r = [[x["metrik"], num(x["is"]), num(x["oos"]),
              "—" if not np.isfinite(x["ratio"]) else f"{x['ratio']:.2f}", x["flag"]]
             for x in d]
        n_svag = sum(1 for x in d if x["flag"].endswith("SVAEKKET"))
        n_staerk = sum(1 for x in d if x["flag"].endswith("styrket"))
        md += [f"### {side.upper()} — {n_svag} svaekket, {n_staerk} styrket "
               f"af {len(d)} metrikker", "",
               tabel(r, ["Metrik", "IS", "OOS", "OOS/IS", "Flag"]), ""]

    # ---------------- SYNTESE ----------------
    d_l = degradering(res[("IS/OOS", "IS", "long")], res[("IS/OOS", "OOS", "long")])
    d_s = degradering(res[("IS/OOS", "IS", "short")], res[("IS/OOS", "OOS", "short")])
    par = [(a["ratio"], b["ratio"]) for a, b in zip(d_l, d_s)
           if np.isfinite(a["ratio"]) and np.isfinite(b["ratio"])]
    modsat = sum(1 for x, y in par if (x - 1) * (y - 1) < 0)

    # Pooled long+short.
    #
    # Taelleren er ligetil: K_long + K_short, altsaa hvor mange retnings-events
    # der fyrede den MATCHENDE signatur.
    #
    # Naevneren er ikke. Man kan ikke bare laegge kontrol-hits sammen: en
    # kontrol-bar har ingen retning, saa "fyrede den matchende signatur?" er
    # ikke defineret for den. Laegger man long- og short-hits sammen, taeller
    # man begge signaturer paa hver bar, mens taelleren kun taeller den ene —
    # og enrichment halveres kunstigt.
    #
    # Det rigtige er den RETNINGS-VEJEDE kontrol-rate: hvor tit ville den
    # matchende signatur fyre, hvis baren fik tildelt en retning med samme
    # fordeling som events har (~51 % op / 49 % ned).
    pool = {}
    for lab in ("IS", "OOS"):
        l, s = res[("IS/OOS", lab, "long")], res[("IS/OOS", lab, "short")]
        K, E = l["K"] + s["K"], l["E"] + s["E"]
        Nb = l["Nb"]
        w_up, w_dn = l["E"] / E, s["E"] / E
        nb_eff = w_up * l["nb"] + w_dn * s["nb"]
        pool[lab] = (K, E, nb_eff, Nb) + ratio_ci(K, E, int(round(nb_eff)), Nb)

    # Teksten herunder udledes af tallene, ikke skrevet i haanden. Med det lille
    # kontrol-sample pegede long og short hver sin vej og lignede et regimeskift;
    # med det store goer de ikke. Havde teksten staaet fast, ville rapporten
    # paastaa det modsatte af sine egne tabeller.
    r_l = res[("IS/OOS", "OOS", "long")]["enrichment"] / res[("IS/OOS", "IS", "long")]["enrichment"]
    r_s = res[("IS/OOS", "OOS", "short")]["enrichment"] / res[("IS/OOS", "IS", "short")]["enrichment"]
    r_p = pool["OOS"][4] / pool["IS"][4]
    modsat_hoved = (r_l - 1) * (r_s - 1) < 0
    vej = lambda r: "op" if r > 1 else "ned"

    if modsat_hoved:
        overskrift = "## 0c. Syntese: long og short bevaeger sig modsat"
        brod = [
            f"Af de {len(par)} sammenlignelige metrikker gaar **{modsat}** den ENE "
            f"vej for long og den ANDEN for short — og det gaelder ogsaa "
            f"hovedtallet: long {vej(r_l)} (x{r_l:.2f}), short {vej(r_s)} (x{r_s:.2f}).", "",
            "To uafhaengige signaler ville ikke svinge i modfase saa systematisk.",
            "Den enkle forklaring er at det ikke er signalernes kvalitet der",
            "aendrer sig, men **markedet**: den ene halvdel gav de bedste",
            "op-bevaegelser, den anden de bedste ned-bevaegelser.", "",
            "Konsekvens: *ingen af siderne er vist regime-uafhaengig*. Det der ser",
            "stabilt ud, er de to tilsammen:", ""]
    else:
        overskrift = "## 0c. Syntese: begge sider bevaeger sig samme vej"
        brod = [
            f"Hovedtallet gaar **samme vej for begge sider**: long {vej(r_l)} "
            f"(x{r_l:.2f}), short {vej(r_s)} (x{r_s:.2f}). "
            f"({modsat} af {len(par)} mindre metrikker peger hver sin vej, men "
            f"det er stoej i enkeltmaal, ikke i hovedtallet.)", "",
            "**Det er en anden konklusion end med det lille kontrol-sample.** Der",
            "faldt long og steg short, hvilket lignede et regimeskift hvor den",
            "ene side tog over for den anden. Det moenster overlever ikke et",
            "stoerre kontrol-sample — det var stoej i naevneren, ikke et signal i",
            "markedet.", "",
            "Konsekvens: der er ikke belaeg for at behandle long og short som",
            "regime-modsaetninger. Til sammenligning det samlede tal:", ""]

    md += ["---", "", overskrift, ""] + brod + [
           tabel([[
               "Fyrer (K) / retnings-events (E)", f"{pool['IS'][0]}/{pool['IS'][1]:,}",
               f"{pool['OOS'][0]}/{pool['OOS'][1]:,}"],
               ["Kontrol-hits (retnings-vejet)",
                f"{pool['IS'][2]:.1f}/{pool['IS'][3]:,}",
                f"{pool['OOS'][2]:.1f}/{pool['OOS'][3]:,}"],
               ["**Enrichment (long+short samlet)**",
                fmt_ci(pool["IS"][4], pool["IS"][5], pool["IS"][6]),
                fmt_ci(pool["OOS"][4], pool["OOS"][5], pool["OOS"][6])]],
               ["Metrik", "IS", "OOS"]), "",
           f"Samlet: {pool['IS'][4]:.2f}x → {pool['OOS'][4]:.2f}x (x{r_p:.2f}). "
           f"Long alene x{r_l:.2f}, short alene x{r_s:.2f}."
           + (" Det samlede signal er markant mere stabilt end hver af halvdelene "
              "— netop fordi de to udligner hinanden. Edgen er aegte, men "
              "fordelingen mellem long og short svinger med regimet."
              if abs(r_p - 1) < 0.6 * (abs(r_l - 1) + abs(r_s - 1)) / 2 else
              " Poolingen stabiliserer altsaa ikke noget her: alle tre flytter sig "
              "omtrent lige meget og samme vej. Det er hvad man forventer naar "
              "udsvingene er stikproeve-stoej og ikke modsatrettede regimer."), ""]

    # ---------------- CI-KRYMPNING (kun med stor baseline) ----------------
    if foer is not None:
        md += ["---", "", "## 0d. Krympede konfidensintervallerne?", "",
               f"Samme events, samme kriterier — kun kontrol-samplet er skiftet "
               f"fra {foer['n_baseline']:,} til {res[('IS/OOS','IS','long')]['Nb'] + res[('IS/OOS','OOS','long')]['Nb']:,} "
               "barer. Det er naevneren i alle lift-tal, saa det er her praecisionen",
               "af hele analysen bestemmes.", ""]
        for side in ("long", "short"):
            r = []
            for lab in ("IS", "OOS"):
                a, b = foer["res"][("IS/OOS", lab, side)], res[("IS/OOS", lab, side)]
                bredde_a = (a["enr_hi"] - a["enr_lo"]) if np.isfinite(a["enr_lo"]) else np.nan
                bredde_b = (b["enr_hi"] - b["enr_lo"]) if np.isfinite(b["enr_lo"]) else np.nan
                r.append([f"{lab} enrichment", f"{a['nb']}", fmt_ci(a["enrichment"], a["enr_lo"], a["enr_hi"]),
                          f"{b['nb']}", fmt_ci(b["enrichment"], b["enr_lo"], b["enr_hi"]),
                          "—" if not np.isfinite(bredde_a) else f"{bredde_b/bredde_a:.2f}"])
                # hale-lift ved den stoerste taerskel
                ha = a["tab_hale"][-1]
                hb = b["tab_hale"][-1]
                wa = (ha["lift_hi"] - ha["lift_lo"]) if np.isfinite(ha["lift_lo"]) else np.nan
                wb = (hb["lift_hi"] - hb["lift_lo"]) if np.isfinite(hb["lift_lo"]) else np.nan
                r.append([f"{lab} hale-lift ≥ {ha['taerskel_atr']} ATR",
                          f"{ha['kontrol_hits']}", fmt_ci(ha["lift"], ha["lift_lo"], ha["lift_hi"]),
                          f"{hb['kontrol_hits']}", fmt_ci(hb["lift"], hb["lift_lo"], hb["lift_hi"]),
                          "—" if not np.isfinite(wa) else f"{wb/wa:.2f}"])
            md += [f"### {side.upper()}", "",
                   tabel(r, ["Metrik", "Lille: hits", "Lille: estimat",
                             "Stor: hits", "Stor: estimat", "CI-bredde stor/lille"]), ""]

        # --- Regime-spoergsmaalet: kan IS og OOS nu skelnes? --------------
        md += ["### Er IS/OOS-forskellen nu statistisk paaviselig?", "",
               "Dette er spoergsmaalet Fase A blev sat i vaerk for at afgoere.",
               "Kriteriet er enkelt: overlapper IS- og OOS-intervallet stadig?", ""]
        r = []
        for side in ("long", "short"):
            for navn, noegle, idx in (("Enrichment", None, None),
                                      ("Hale-lift ≥ 8 ATR", "tab_hale", -1)):
                i_m, o_m = res[("IS/OOS", "IS", side)], res[("IS/OOS", "OOS", side)]
                if noegle is None:
                    ai, bi, ao, bo = i_m["enr_lo"], i_m["enr_hi"], o_m["enr_lo"], o_m["enr_hi"]
                    vi, vo = i_m["enrichment"], o_m["enrichment"]
                else:
                    ai, bi = i_m[noegle][idx]["lift_lo"], i_m[noegle][idx]["lift_hi"]
                    ao, bo = o_m[noegle][idx]["lift_lo"], o_m[noegle][idx]["lift_hi"]
                    vi, vo = i_m[noegle][idx]["lift"], o_m[noegle][idx]["lift"]
                if not all(np.isfinite(x) for x in (ai, bi, ao, bo)):
                    dom = "kan ikke afgoeres"
                else:
                    dom = ("**JA — adskilt**" if (bi < ao or bo < ai)
                           else "NEJ — overlapper")
                r.append([f"{side} · {navn}", fmt_ci(vi, ai, bi), fmt_ci(vo, ao, bo), dom])
        md += [tabel(r, ["Metrik", "IS", "OOS", "Paaviselig forskel?"]), ""]

    # ---------------- SPLIT ----------------
    md += ["---", "", "## 1. Splittet", ""]
    for navn, (graense, halvdele) in splits.items():
        md += [f"**{navn}** — graense {graense:%Y-%m-%d %H:%M}", ""]
        r = []
        for h, (t0, t1, n_up, n_dn, n_bl) in halvdele.items():
            r.append([h, f"{t0:%Y-%m-%d}", f"{t1:%Y-%m-%d}",
                      f"{hverdage(t0, t1)}", f"{n_up:,}", f"{n_dn:,}", f"{n_bl:,}"])
        md += [tabel(r, ["Halvdel", "Fra", "Til", "Hverdage", "Events op",
                         "Events ned", "Kontrol-barer"]), ""]

    md += ["**Krydstjek af prevalens-naevneren.** Specen estimerer antal 15m-barer",
           "som hverdage x 23 t x 4. Jeg har ogsaa talt de faktiske barer i",
           "1-min-filen:", ""]
    r = [[h, f"{v['est']:,}", f"{v['fakt']:,}", f"{(v['est']/v['fakt']-1)*100:+.1f} %"]
         for h, v in faktisk.items()]
    md += [tabel(r, ["Halvdel", "Estimat (spec)", "Faktisk", "Afvigelse"]), "",
           "Estimatet rammer inden for et par procent. Og som noteret i koden gaar antallet",
           "af barer alligevel ud af lift-beregningen — det paavirker kun",
           "praecisionen, ikke liftet.", ""]

    # ---------------- HOVEDTABEL ----------------
    md += ["---", "", "## 2. IS vs. OOS, side om side", ""]
    for side in ("long", "short"):
        i, o = res[("IS/OOS", "IS", side)], res[("IS/OOS", "OOS", side)]
        md += [f"### {side.upper()} (forventet retning: {i['forventet']})", "",
               tabel([
                   ["Retnings-event-starter (E)", f"{i['E']:,}", f"{o['E']:,}"],
                   ["Heraf fyrer signaturen (K)", f"{i['K']:,}", f"{o['K']:,}"],
                   ["Signaler i alt (begge retn.)", f"{i['n_signaler']:,}", f"{o['n_signaler']:,}"],
                   ["**Retningstraef**", pct(i["retning_hit"]), pct(o["retning_hit"])],
                   ["Event-rate (K/E)", pct(i["event_rate"], 2), pct(o["event_rate"], 2)],
                   ["Kontrol-rate", f"{pct(i['kontrol_rate'], 2)} ({i['nb']}/{i['Nb']})",
                    f"{pct(o['kontrol_rate'], 2)} ({o['nb']}/{o['Nb']})"],
                   ["**Enrichment**", fmt_ci(i["enrichment"], i["enr_lo"], i["enr_hi"]),
                    fmt_ci(o["enrichment"], o["enr_lo"], o["enr_hi"])],
                   ["Praecision", pct(i["praecision"]), pct(o["praecision"])],
                   ["Praecision ±25 % barer", f"{pct(i['praec_lav'])} – {pct(i['praec_hoej'])}",
                    f"{pct(o['praec_lav'])} – {pct(o['praec_hoej'])}"],
                   ["Basisrate", pct(i["basisrate"]), pct(o["basisrate"])],
                   ["Lift (= enrichment)", num(i["enrichment"]) + "x", num(o["enrichment"]) + "x"],
               ], ["Metrik", "IS", "OOS"]), "",
               "**Exit-profil** (kun de fyrende events i forventet retning):", "",
               tabel([
                   ["Median size_atr", num(i["median_size_atr"]), num(o["median_size_atr"])],
                   ["Median size_pt", num(i["median_size_pt"], 1), num(o["median_size_pt"], 1)],
                   ["Median varighed (min)", num(i["median_varighed_min"], 0), num(o["median_varighed_min"], 0)],
                   ["Median z ved START", num(i["median_z_start"]), num(o["median_z_start"])],
                   ["Median z ved SLUT", num(i["median_z_end"]), num(o["median_z_end"])],
                   ["Andel der overshooter til modsat baand", pct(i["andel_overshoot"]), pct(o["andel_overshoot"])],
                   ["Andel swing-metode (resten fwd)", pct(i["andel_swing"]), pct(o["andel_swing"])],
               ], ["Metrik", "IS", "OOS"]), ""]

        for navn, noegle in (("Fuld signatur", "tab_fuld"),
                             (f"Signatur + RVOL >= {RVOL_TAIL} (hale-filter)", "tab_hale")):
            md += [f"**Stoerrelses-taerskler — {navn}**", ""]
            r = []
            for a, b in zip(i[noegle], o[noegle]):
                r.append([f"≥ {a['taerskel_atr']} ATR",
                          f"{a['K_T']}/{a['E_T']}", pct(a["praecision"]),
                          fmt_ci(a["lift"], a["lift_lo"], a["lift_hi"]),
                          f"{b['K_T']}/{b['E_T']}", pct(b["praecision"]),
                          fmt_ci(b["lift"], b["lift_lo"], b["lift_hi"])])
            md += [tabel(r, ["Taerskel", "IS K/E", "IS praec.", "IS lift",
                             "OOS K/E", "OOS praec.", "OOS lift"]), "",
                   f"*Kontrol-hits bag naevneren: IS {i[noegle][0]['kontrol_hits']}, "
                   f"OOS {o[noegle][0]['kontrol_hits']} af hhv. {i['Nb']} og {o['Nb']} "
                   f"kontrol-barer.*", ""]

    # ---------------- AAR-SPLIT ----------------
    md += ["---", "", "## 3. Kontrol-split: aar-1 vs. aar-2", "",
           "⚠ **Dette split er degenereret paa dette datasaet.** Serien spaender",
           "praecis to aar (2024-06-28 → 2026-07-01), saa graensen mellem aar 1 og",
           "aar 2 falder inden for ét doegn af 50/50-kalendergraensen. De to splits",
           "er i praksis det SAMME split — se de identiske n i afsnit 1. Det kan",
           "derfor ikke bekraefte IS/OOS-resultatet; det gentager det.",
           "Det uafhaengige robusthedstjek ligger i stedet i afsnit 3b (pr. kontrakt).", ""]
    for side in ("long", "short"):
        a1, a2 = res[("aar", "aar-1", side)], res[("aar", "aar-2", side)]
        md += [f"### {side.upper()}", "",
               tabel([
                   ["Retnings-events (E)", f"{a1['E']:,}", f"{a2['E']:,}"],
                   ["Fyrer (K)", f"{a1['K']:,}", f"{a2['K']:,}"],
                   ["Retningstraef", pct(a1["retning_hit"]), pct(a2["retning_hit"])],
                   ["Kontrol-rate", f"{pct(a1['kontrol_rate'], 2)} ({a1['nb']}/{a1['Nb']})",
                    f"{pct(a2['kontrol_rate'], 2)} ({a2['nb']}/{a2['Nb']})"],
                   ["**Enrichment**", fmt_ci(a1["enrichment"], a1["enr_lo"], a1["enr_hi"]),
                    fmt_ci(a2["enrichment"], a2["enr_lo"], a2["enr_hi"])],
                   ["Praecision", pct(a1["praecision"]), pct(a2["praecision"])],
                   ["Median z START → SLUT",
                    f"{num(a1['median_z_start'])} → {num(a1['median_z_end'])}",
                    f"{num(a2['median_z_start'])} → {num(a2['median_z_end'])}"],
               ], ["Metrik", "Aar 1", "Aar 2"]), ""]

    # ---------------- PR. KONTRAKT ----------------
    md += ["---", "", "## 3b. Robusthed pr. futures-kontrakt", "",
           "Otte fulde kvartals-kontrakter (+ en 12-dages rest til sidst, som er",
           "for kort til at laese noget ud af). Her ses om edgen findes hele vejen",
           "igennem, eller kun i enkelte regimer. **Dette er det egentlige",
           "uafhaengige robusthedstjek**, nu hvor aar-splittet viste sig at vaere",
           "det samme som IS/OOS-splittet.", "",
           "Enkelt-vaerdierne er stoejende (2–12 kontrol-hits bag hver). Det der",
           "taeller er om fortegnet holder hele vejen — ikke niveauet i den",
           "enkelte periode.", ""]
    for side in ("long", "short"):
        md += [f"### {side.upper()}", ""]
        r = []
        fulde = []
        for navn, m in kontrakt[side]:
            stub = m["hverdage"] < 30
            r.append([navn + (" *(rest)*" if stub else ""), f"{m['E']:,}", f"{m['K']}",
                      f"{m['nb']}/{m['Nb']}", num(m["enrichment"]) + "x",
                      pct(m["retning_hit"], 0) if np.isfinite(m["retning_hit"]) else "—"])
            if not stub and np.isfinite(m["enrichment"]):
                fulde.append(m["enrichment"])
        md += [tabel(r, ["Kontrakt-periode", "E", "K", "Kontrol-hits",
                         "Enrichment", "Retningstraef"]), ""]
        if fulde:
            md += [f"**Over de {len(fulde)} fulde kontrakter:** enrichment fra "
                   f"{min(fulde):.2f}x til {max(fulde):.2f}x, median "
                   f"{np.median(fulde):.2f}x. "
                   f"{sum(1 for x in fulde if x > 1):d}/{len(fulde)} over 1x, "
                   f"{sum(1 for x in fulde if x >= 3):d}/{len(fulde)} over 3x.", ""]

    # ---------------- GITTER ----------------
    md += ["---", "", "## 4. Robusthedsgitter", "",
           "Enrichment (og retningstraef) for nabo-vaerdier. **Dette er ikke en",
           "soegning efter den bedste kombination** — det ville vaere kurve-",
           "tilpasning. Det er et tjek af om edgen overlever smaa rykninger i",
           "taersklerne. Kig efter et plateau, ikke efter et maksimum.", ""]
    for side in ("long", "short"):
        md += [f"### {side.upper()}", ""]
        r = []
        for gi, go in zip(grids[("IS", side)], grids[("OOS", side)]):
            z_vis = f"{-gi['z']:+.1f}" if side == "long" else f"{gi['z']:+.1f}"
            mark = " ←" if (abs(gi["z"] - 2.0) < 1e-9 and abs(gi["rvol"] - RVOL_HI) < 1e-9
                            and gi["prik"] == "kun kraftig") else ""
            r.append([f"{z_vis}{mark}", f"{gi['rvol']}", gi["prik"],
                      f"{gi['K']}", num(gi["enrichment"]) + "x", pct(gi["retning_hit"], 0),
                      f"{go['K']}", num(go["enrichment"]) + "x", pct(go["retning_hit"], 0)])
        md += [tabel(r, ["z", "RVOL", "Prik", "IS K", "IS enrich.", "IS retn.",
                         "OOS K", "OOS enrich.", "OOS retn."]), "",
               "← = den praeregistrerede signatur.", ""]

    # ---------------- FORBEHOLD ----------------
    md += ["---", "", "## 5. Forbehold", "",
           "1. **Kontrol-raten er den svageste led.** Enrichment er et forhold",
           "   mellem to rater, og naevneren bygger paa et sample af",
           "   kontrol-barer. For hale-filteret (RVOL ≥ 4) er der kun en",
           "   haandfuld kontrol-hits pr. halvdel. Derfor staar der",
           "   konfidensintervaller paa alle lift-tal — laes bredden, ikke kun",
           "   punktestimatet. Et \"12x\" med interval 4–35 er ikke en maaling af",
           "   12, det er \"et sted mellem beskedent og enormt\".",
           "2. **Baseline er 4.000 barer, ikke alle barer.** Kontrol-raten er et",
           "   stikproeve-estimat. Vil man snaevre intervallerne ind, er det",
           "   baseline-samplet der skal vokse — flere events hjaelper ikke.",
           "3. **Events er ikke uafhaengige.** Swing- og fwd-metoden finder tit",
           "   den samme bevaegelse; vi deduplikerer paa (start-bar, retning), men",
           "   naboliggende events overlapper stadig i tid. De effektive",
           "   frihedsgrader er derfor lavere end n antyder, og intervallerne",
           "   herover er i den forstand optimistiske.",
           "4. **Retningstraeffet er delvist indbygget.** Et event ER en",
           "   bevaegelse; signaturen maales paa event-starter. At ~99 % gaar den",
           "   forventede vej siger at signaturen skelner retning godt — ikke at",
           "   99 % af alle fyringer i markedet foelges af en bevaegelse. Det tal",
           "   er praecisionen (~30 %), ikke retningstraeffet.",
           "5. **Ingen omkostninger, intet slip.** Dette er signal-validering.",
           "   Om edgen overlever kurtage og slippage er et P&L-spoergsmaal, og",
           "   det er eksplicit ikke stillet her.", ""]
    return "\n".join(md) + "\n"


# =============================================================================
# 8. MAIN
# =============================================================================
def koer_alt(ev: pd.DataFrame, bl: pd.DataFrame, tael_barer: bool = True) -> dict:
    """
    Beregn HELE metrik-saettet for ét (events, baseline)-par.

    Udskilt fra main() saa den samme beregning kan koeres mod to forskellige
    kontrol-samples (lille og stor baseline) og stilles op mod hinanden.
    Events er de samme i begge tilfaelde — det er kun naevneren der aendres.
    """
    t_min = min(ev["start_ts_dk"].min(), bl["start_ts_dk"].min())
    # +1 s af samme grund som i kontrakt_perioder: udsnit er [a, b).
    t_max = max(ev["start_ts_dk"].max(), bl["start_ts_dk"].max()) + pd.Timedelta(seconds=1)
    g_kal = kalender_split(ev, bl)
    g_aar = aar_split(ev, bl)
    med = ev["start_ts_dk"].median()
    print(f"  periode {t_min:%Y-%m-%d} → {t_max:%Y-%m-%d}")
    print(f"  kalender-midtpunkt {g_kal:%Y-%m-%d} · event-median {med:%Y-%m-%d} "
          f"(forskel {abs((g_kal - med).days)} dage)")

    res, splits = {}, {}
    for navn, graense, labels in (("IS/OOS", g_kal, ("IS", "OOS")),
                                  ("aar", g_aar, ("aar-1", "aar-2"))):
        halvdele = {}
        for lab, (a, b) in zip(labels, ((t_min, graense), (graense, t_max))):
            e = ev[(ev["start_ts_dk"] >= a) & (ev["start_ts_dk"] < b)]
            b_ = bl[(bl["start_ts_dk"] >= a) & (bl["start_ts_dk"] < b)]
            halvdele[lab] = (a, b, int((e["retning"] == "up").sum()),
                             int((e["retning"] == "down").sum()), len(b_))
            for side in ("long", "short"):
                res[(navn, lab, side)] = beregn(e, b_, side, a, b)
        splits[navn] = (graense, halvdele)

    # Krydstjek: faktiske 15m-barer pr. IS/OOS-halvdel
    print("\nTaeller faktiske 15m-barer (krydstjek af prevalens-naevneren) …")
    faktisk = {}
    for lab, (a, b, *_r) in splits["IS/OOS"][1].items():
        faktisk[lab] = {"est": hverdage(a, b) * BARER_PR_HVERDAG,
                        "fakt": faktiske_15m_barer(a, b)}
        print(f"  {lab}: estimat {faktisk[lab]['est']:,} · faktisk {faktisk[lab]['fakt']:,}")

    # Robusthed pr. futures-kontrakt (det uafhaengige kontrol-split)
    kontrakt = {"long": [], "short": []}
    perioder = kontrakt_perioder(ev, bl)
    print(f"\n{len(perioder)} kontrakt-perioder …")
    for navn, a, b in perioder:
        e = ev[(ev["start_ts_dk"] >= a) & (ev["start_ts_dk"] < b)]
        b_ = bl[(bl["start_ts_dk"] >= a) & (bl["start_ts_dk"] < b)]
        for side in ("long", "short"):
            kontrakt[side].append((navn, beregn(e, b_, side, a, b)))

    # Robusthedsgitter paa IS og OOS
    grids = {}
    for lab in ("IS", "OOS"):
        a, b, *_ = splits["IS/OOS"][1][lab]
        e = ev[(ev["start_ts_dk"] >= a) & (ev["start_ts_dk"] < b)]
        b_ = bl[(bl["start_ts_dk"] >= a) & (bl["start_ts_dk"] < b)]
        for side in ("long", "short"):
            grids[(lab, side)] = gitter(e, b_, side)

    return {"res": res, "splits": splits, "faktisk": faktisk,
            "kontrakt": kontrakt, "grids": grids, "n_baseline": len(bl)}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, default=None,
                    help="alternativ baseline-parquet (default: den lille)")
    ap.add_argument("--sammenlign-med", type=Path, default=None,
                    help="anden baseline at vise CI-krympning imod")
    ap.add_argument("--suffix", default="",
                    help="suffiks paa output-filnavne, fx _30k")
    args = ap.parse_args()

    print("── IS/OOS-validering af signaturerne ──────────────────────────")
    ev, bl = load(args.baseline)
    print(f"Efter rul-filter (bars_since_roll > {ROLL_MIN}) og dedup:")
    print(f"  events {len(ev):,} unikke (start-bar, retning) · "
          f"kontrol-barer {len(bl):,}"
          + (f"  [{args.baseline.name}]" if args.baseline else ""))

    alt = koer_alt(ev, bl)
    res, grids = alt["res"], alt["grids"]
    splits, faktisk, kontrakt = alt["splits"], alt["faktisk"], alt["kontrakt"]

    foer = None
    if args.sammenlign_med:
        print(f"\nKoerer samme beregning mod {args.sammenlign_med.name} "
              f"til CI-sammenligning …")
        _, bl_lille = load(args.sammenlign_med)
        foer = koer_alt(ev, bl_lille, tael_barer=False)

    # --- Skriv rapport + raa tal -----------------------------------------
    md = rapport(res, grids, splits, faktisk, kontrakt, foer)
    # Uden suffiks beholdes det oprindelige filnavn (saa commit bb92296's
    # rapport ikke skifter navn); med suffiks bruges specens navn.
    p_md = OUT_DIR / (f"signatur_validering{args.suffix}.md" if args.suffix
                      else "signatur_validering_IS_OOS.md")
    p_md.write_text(md, encoding="utf-8")

    raekker = []
    for (split, halvdel, side), m in res.items():
        for k, v in m.items():
            if k in ("tab_fuld", "tab_hale"):
                for r_ in v:
                    for kk, vv in r_.items():
                        if kk == "taerskel_atr":
                            continue
                        raekker.append({"split": split, "halvdel": halvdel, "side": side,
                                        "blok": "hale" if k == "tab_hale" else "fuld",
                                        "taerskel_atr": r_["taerskel_atr"],
                                        "metrik": kk, "vaerdi": vv})
            elif not isinstance(v, (pd.Timestamp, str)):
                raekker.append({"split": split, "halvdel": halvdel, "side": side,
                                "blok": "hoved", "taerskel_atr": np.nan,
                                "metrik": k, "vaerdi": v})
    for side in ("long", "short"):
        for x in degradering(res[("IS/OOS", "IS", side)], res[("IS/OOS", "OOS", side)]):
            raekker.append({"split": "degradering", "halvdel": "OOS/IS", "side": side,
                            "blok": x["flag"], "taerskel_atr": np.nan,
                            "metrik": x["metrik"], "vaerdi": x["ratio"]})
    for (lab, side), g in grids.items():
        for row in g:
            for kk in ("n_signaler", "retning_hit", "K", "kontrol_hits",
                       "enrichment", "enr_lo", "enr_hi"):
                raekker.append({"split": "gitter", "halvdel": lab, "side": side,
                                "blok": f"z{row['z']}_rvol{row['rvol']}_{row['prik']}",
                                "taerskel_atr": np.nan, "metrik": kk, "vaerdi": row[kk]})
    p_csv = OUT_DIR / f"signatur_validering{args.suffix}.csv"
    pd.DataFrame(raekker).to_csv(p_csv, index=False)

    # --- Terminal-opsummering --------------------------------------------
    print("\n" + "=" * 66)
    for side in ("long", "short"):
        ok, krit = doem(res[("IS/OOS", "IS", side)], res[("IS/OOS", "OOS", side)])
        print(f"  {side.upper():<6} {'BESTAAET' if ok else 'DUMPET'}")
        for navn, v, tekst in krit:
            print(f"      {'✔' if v else '✘'} {navn:<38} {tekst}")
    print("=" * 66)
    for p in (p_md, p_csv):
        print(f"  {p.name:<38} {p.stat().st_size/1024:7.1f} kB")


if __name__ == "__main__":
    main()
