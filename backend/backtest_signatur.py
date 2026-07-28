#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_signatur.py
====================
FASE B — P&L-backtest af den validerede long/short-signatur paa MES.

Spoergsmaalet: bliver ~4x lift og ~99 % retning til faktisk afkast efter
omkostninger?

REALISME (spec B0) — det vigtigste ved hele scriptet
────────────────────────────────────────────────────
Event-datasaettets `start` er en swing-pivot der foerst bekraeftes 3 barer
senere. Den kan IKKE handles i realtid. Derfor backtestes der **ikke** paa
event-startbarerne. I stedet bygges signalet forfra paa de raa, resamplede
barer, med disse regler:

  * alle tre betingelser evalueres paa en LUKKET 15m-bar
  * entry sker tidligst paa NAESTE bars open
  * prik-opslaget bruger `nearest_prior_dot`, som kun ser bagud og kun
    accepterer prikker der var BEKRAEFTET paa eller foer signalbaren
  * target- og stop-niveauer beregnes af den FORRIGE lukkede bars SMA/std,
    saa de kan ligge som rigtige limit-/stop-ordrer inden baren aabner

Alt indikator-arbejde genbruger `store_bevaegelser_lib` (samme Pine-matchede
bibliotek og samme MTF-"previous"-konvention som valideringen), saa signalet
her er defineret praecis som det der blev valideret.

Output:
  store_bevaegelser_out/backtest_signatur.md
  store_bevaegelser_out/backtest_trades.csv

Koeres:  python backtest_signatur.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

import analyse_store_bevaegelser as A
from store_bevaegelser_lib import (
    TimeframeView, TF_SPEC, sma, nearest_prior_dot, roll_segment_id,
    resample_ohlcv,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# KONSTANTER — alt justerbart staar her
# =============================================================================
OUT_DIR = A.OUT_DIR
HANDELS_TF = "15m"          # signal- og handels-timeframe
PRIK_TF    = "3m"           # timeframe prikken aflaeses paa

# --- Signatur (uaendret fra valideringen) --------------------------------
Z_ENTRY_LONG  = -2.0
Z_ENTRY_SHORT =  2.0
RVOL_MIN      =  1.5
DOT_LONG      = "kraftig_groen"
DOT_SHORT     = "kraftig_roed"
DOT_LOOKBACK  = A.DOT_LOOKBACK_BARS     # 50 barer — samme som i datasaettet

# --- Exit ------------------------------------------------------------------
Z_TARGET      = 0.0         # target: retur til 15m-middelvaerdien
Z_STOP        = 3.5         # stop-variant 1: |z| naar 3.5 i tabt retning
ATR_STOP_K    = 2.0         # stop-variant 2: entry -/+ k x ATR
MAX_BARS      = 20          # tids-stop i 15m-barer (~5 timer)
TRAIL_ATR_K   = 2.0         # trailing stop paa hale-benet
RVOL_TAIL     = 4.0         # hale-benet aktiveres foerst over dette

# --- Omkostninger (MES) ----------------------------------------------------
USD_PR_POINT     = 5.0
TICK             = 0.25
KOMMISSION_SIDE  = 1.00     # $ pr. kontrakt pr. side (IBKR-niveau)
SLIPPAGE_TICKS   = 1.0      # mindst 1 tick pr. side
SLIPPAGE_POINT   = SLIPPAGE_TICKS * TICK

# --- Rul-haandtering (samme filter som valideringen) ----------------------
ROLL_MIN_BARS = 100         # ingen entry inden for 100 barer efter et rul

# --- Warmup ---------------------------------------------------------------
WARMUP_BARS = A.WARMUP_BARS_DET

Z_LEN = 30                  # skal matche zscore() i biblioteket


# =============================================================================
@dataclass
class Handel:
    side: str
    entry_tid: pd.Timestamp
    entry_pris: float
    exit_tid: pd.Timestamp
    exit_pris: float
    exit_aarsag: str
    bars: int
    kontrakter: float
    ben: str                 # "hoved" eller "hale"
    kontrakt_periode: str
    rvol_entry: float
    z_entry: float
    atr_entry: float

    @property
    def point(self) -> float:
        d = self.exit_pris - self.entry_pris
        return d if self.side == "long" else -d

    def brutto(self) -> float:
        return self.point * USD_PR_POINT * self.kontrakter

    def omkostning(self) -> float:
        """Round-trip: kommission begge sider + 1 tick slippage begge sider."""
        return self.kontrakter * 2 * (KOMMISSION_SIDE + SLIPPAGE_POINT * USD_PR_POINT)

    def netto(self) -> float:
        return self.brutto() - self.omkostning()


# =============================================================================
# 1. GRUNDLAG
# =============================================================================
def byg_grundlag():
    """Indikatorer paa handels-TF og prik-TF + rul-segmenter."""
    df1m = A.load_minute_data()
    print(f"  {len(df1m):,} 1-min barer")

    v_h = TimeframeView.build(HANDELS_TF, df1m)
    v_p = TimeframeView.build(PRIK_TF, df1m)
    f = v_h.feat

    # build_indicator_frame gemmer ikke `open` — men entry sker paa NAESTE bars
    # open, saa den skal hentes fra de resamplede barer og saettes paa.
    ohlc = resample_ohlcv(df1m, TF_SPEC[HANDELS_TF][0])
    f = f.assign(open=ohlc["open"].reindex(f.index))

    # SMA/std bag z — vi skal bruge dem som PRISNIVEAUER, ikke kun som z-vaerdi,
    # for at kunne laegge target/stop som rigtige ordrer.
    luk = f["close"]
    f = f.assign(sma30=sma(luk, Z_LEN),
                 std30=luk.rolling(Z_LEN, min_periods=Z_LEN).std(ddof=0))

    roll_times = A.load_roll_times()
    seg = roll_segment_id(f.index, roll_times)
    seg_start = np.zeros(len(f), dtype=np.int64)
    for s, i0 in zip(*np.unique(seg, return_index=True)):
        seg_start[seg == s] = i0
    bars_since_roll = np.arange(len(f)) - seg_start

    print(f"  {HANDELS_TF}: {len(f):,} barer · {PRIK_TF}: {len(v_p.feat):,} barer "
          f"· {len(roll_times)} rul")
    return f, v_h, v_p, seg, bars_since_roll


def prik_pr_bar(v_h: TimeframeView, v_p: TimeframeView) -> np.ndarray:
    """
    Den naermeste FORUDGAAENDE bekraeftede 3m-prik, set fra hver 15m-bars luk.

    Samme opslag som event-datasaettet bruger (`nearest_prior_dot` via
    `align_index`), saa backtestens prik-betingelse er bit for bit den samme
    som den der blev valideret. Ingen prik fra fremtiden kan slippe med:
    align_index returnerer kun barer der var HELT afsluttede, og
    nearest_prior_dot scanner kun bagud fra dén bar.
    """
    ud = np.full(len(v_h.feat), "ingen", dtype=object)
    for i, luk_ns in enumerate(v_h.bar_close_ns):
        j = v_p.align_index(luk_ns)
        if j < 0:
            continue
        ud[i], _ = nearest_prior_dot(v_p.dot_type, v_p.dot_lag, j, DOT_LOOKBACK)
    return ud


# =============================================================================
# 2. SIMULERING
# =============================================================================
def koer(f: pd.DataFrame, prikker: np.ndarray, seg: np.ndarray,
         bars_since_roll: np.ndarray, stop_variant: str,
         hale_ben: bool) -> list[Handel]:
    """
    Bar-for-bar-simulering.

    stop_variant: "z" (|z| naar Z_STOP) eller "atr" (entry -/+ k x ATR).
    hale_ben:     True => ved rvol >= 4 handles 2 kontrakter, hvor den ene
                  tages ved target og den anden foelger en trailing stop.

    Konventioner:
      - signal paa lukket bar i  ->  entry paa open[i+1]
      - target/stop er PRISNIVEAUER kendt foer baren aabner
      - rammer baade stop og target inden for samme bar, antages STOP ramt
        foerst (konservativt — vi kan ikke se raekkefoelgen inde i baren)
      - aabne positioner tvangslukkes ved kontrakt-rul (raw-stitched serie:
        prisspringet ved rullet er ikke en markedsbevaegelse)
    """
    o = f["open"].to_numpy()
    hi, lo, cl = (f[c].to_numpy() for c in ("high", "low", "close"))
    z = f["z"].to_numpy()
    rv = f["rvol"].to_numpy()
    atr = f["atr"].to_numpy()
    sm = f["sma30"].to_numpy()
    sd = f["std30"].to_numpy()
    idx = f.index
    n = len(f)
    # Kontrakt-periode pr. bar — bruges til at bryde resultatet ned pr.
    # futures-kontrakt (spec B4), ikke bare pr. kalendermaaned.
    periode = np.array([f"kontrakt {s_}" for s_ in seg])

    handler: list[Handel] = []
    # side -> liste af BEN. Hvert ben styres helt for sig med egen stop/target.
    # Det er enklere og mere korrekt end ét fælles positions-objekt, fordi
    # hale-benet netop IKKE skal lukke der hvor hovedbenet lukker.
    aaben: dict[str, list[dict]] = {}

    for i in range(WARMUP_BARS, n - 1):
        # ---------- 1) Styr aabne ben paa bar i ----------
        for side in list(aaben):
            lang = side == "long"
            beholdt = []
            for p in aaben[side]:
                if i < p["entry_i"]:
                    beholdt.append(p)
                    continue

                luk_nu = None
                if seg[i] != seg[p["entry_i"]]:
                    # Raw-stitched serie: prisspringet ved rullet er ikke en
                    # markedsbevaegelse, saa vi lukker paa sidste bar foer.
                    luk_nu = (cl[i - 1], "rul", i - 1)
                else:
                    # Stop foer target: rammer baren begge, kan vi ikke se
                    # raekkefoelgen inde i baren, og vi vaelger det daarlige.
                    if (lo[i] <= p["stop"]) if lang else (hi[i] >= p["stop"]):
                        luk_nu = (p["stop"], "trail-stop" if p["trailing"] else "stop", i)
                    elif p["target"] is not None and (
                            (hi[i] >= p["target"]) if lang else (lo[i] <= p["target"])):
                        luk_nu = (p["target"], "target", i)
                    elif (i - p["entry_i"]) >= MAX_BARS:
                        luk_nu = (cl[i], "tid", i)

                if luk_nu is None and p["ben"] == "hale" and not p["trailing"]:
                    # Hale-benet lukker ikke ved middel — det SKIFTER til
                    # trailing stop naar niveauet naas, og loeber videre.
                    naaet = (hi[i] >= p["traek_niveau"]) if lang else (lo[i] <= p["traek_niveau"])
                    if naaet:
                        p["trailing"] = True

                if luk_nu is not None:
                    pris, aarsag, bar = luk_nu
                    handler.append(Handel(
                        side=side, entry_tid=p["entry_tid"], entry_pris=p["entry"],
                        exit_tid=idx[bar], exit_pris=pris, exit_aarsag=aarsag,
                        bars=bar - p["entry_i"], kontrakter=p["kon"], ben=p["ben"],
                        kontrakt_periode=p["periode"], rvol_entry=p["rvol"],
                        z_entry=p["z"], atr_entry=p["atr"]))
                    continue

                if p["trailing"]:
                    a = p["atr"]
                    p["stop"] = (max(p["stop"], hi[i] - TRAIL_ATR_K * a) if lang
                                 else min(p["stop"], lo[i] + TRAIL_ATR_K * a))
                beholdt.append(p)

            if beholdt:
                aaben[side] = beholdt
            else:
                aaben.pop(side)

        # ---------- 2) Signal paa lukket bar i ----------
        if bars_since_roll[i] <= ROLL_MIN_BARS:
            continue
        if not (np.isfinite(z[i]) and np.isfinite(rv[i]) and np.isfinite(atr[i])
                and np.isfinite(sm[i]) and np.isfinite(sd[i])):
            continue
        if seg[i + 1] != seg[i]:
            continue                      # entry ville falde paa den anden side af et rul

        for side, z_ok, dot_kraev in (
                ("long", z[i] <= Z_ENTRY_LONG, DOT_LONG),
                ("short", z[i] >= Z_ENTRY_SHORT, DOT_SHORT)):
            if side in aaben or not z_ok or rv[i] < RVOL_MIN:
                continue
            if prikker[i] != dot_kraev:
                continue

            entry = o[i + 1]
            if not np.isfinite(entry):
                continue

            # Target = middelvaerdien; stop = valgt variant. Begge udledt af
            # bar i's (lukkede) SMA/std, altsaa kendt foer bar i+1 aabner.
            target = sm[i]
            if stop_variant == "z":
                stop = sm[i] - Z_STOP * sd[i] if side == "long" else sm[i] + Z_STOP * sd[i]
            else:
                stop = entry - ATR_STOP_K * atr[i] if side == "long" else entry + ATR_STOP_K * atr[i]

            # Meningsloest setup: stop paa den forkerte side af entry
            if (side == "long" and (stop >= entry or target <= entry)) or \
               (side == "short" and (stop <= entry or target >= entry)):
                continue

            faelles = dict(entry=entry, entry_i=i + 1, entry_tid=idx[i + 1],
                           stop=stop, atr=atr[i], rvol=rv[i], z=z[i],
                           trailing=False, periode=periode[i])
            # Hovedbenet tager profit ved middel. Hale-benet (kun ved hoej RVOL)
            # har intet target — det skifter til trailing stop naar middel naas.
            # NB: hale-varianten kraever 2 kontrakter i praksis; man kan ikke
            # tage "halvdelen" af én kontrakt.
            ben = [dict(faelles, ben="hoved", kon=1.0, target=target)]
            if hale_ben and rv[i] >= RVOL_TAIL:
                ben.append(dict(faelles, ben="hale", kon=1.0, target=None,
                                traek_niveau=target))
            aaben[side] = ben

    return handler


# =============================================================================
# 3. METRIKKER
# =============================================================================
def maal(handler: list[Handel], netto: bool) -> dict:
    """Noegletal for et saet handler. netto=True traekker omkostninger fra."""
    if not handler:
        return {"n": 0}
    pnl = np.array([h.netto() if netto else h.brutto() for h in handler])
    pts = np.array([h.point for h in handler])
    kon = np.array([h.kontrakter for h in handler])
    vind, tab = pnl[pnl > 0], pnl[pnl <= 0]

    # Drawdown paa den kronologiske P&L-kurve
    orden = np.argsort([h.exit_tid for h in handler])
    kurve = np.cumsum(pnl[orden])
    dd = float((kurve - np.maximum.accumulate(kurve)).min()) if len(kurve) else 0.0

    return {
        "n": len(handler),
        "kontrakter": float(kon.sum()),
        "hitrate": float((pnl > 0).mean()),
        "median_vinder": float(np.median(vind)) if len(vind) else 0.0,
        "median_taber": float(np.median(tab)) if len(tab) else 0.0,
        "gns_vinder": float(vind.mean()) if len(vind) else 0.0,
        "gns_taber": float(tab.mean()) if len(tab) else 0.0,
        "median_point": float(np.median(pts)),
        "profit_factor": (float(vind.sum() / abs(tab.sum()))
                          if len(tab) and tab.sum() != 0 else np.inf),
        "expectancy": float(pnl.mean()),
        "pnl": float(pnl.sum()),
        "maxdd": dd,
        "gns_bars": float(np.mean([h.bars for h in handler])),
        "gns_min": float(np.mean([h.bars for h in handler])) * TF_SPEC[HANDELS_TF][1],
    }


# =============================================================================
# 4. RAPPORT
# =============================================================================
def tabel(raekker, hoved) -> str:
    ud = ["| " + " | ".join(hoved) + " |", "|" + "---|" * len(hoved)]
    ud += ["| " + " | ".join(str(c) for c in r) + " |" for r in raekker]
    return "\n".join(ud)


def d(x, n=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:,.{n}f}"


def maal_raekke(m: dict) -> list[str]:
    """Én linje noegletal — bruges baade pr. side og pr. kontrakt-periode."""
    if not m.get("n"):
        return ["0"] + ["—"] * 8
    return [f"{m['n']:,}", f"{m['hitrate']*100:.1f} %", d(m["gns_vinder"], 0),
            d(m["gns_taber"], 0),
            "∞" if not np.isfinite(m["profit_factor"]) else d(m["profit_factor"]),
            d(m["expectancy"], 2), d(m["pnl"], 0), d(m["maxdd"], 0),
            d(m["gns_min"], 0)]


MAAL_HOVED = ["n", "Hitrate", "Gns. vinder $", "Gns. taber $", "PF",
              "Expectancy $", "P&L $", "MaxDD $", "Gns. min"]


def rapport(kombi: dict, perioder: dict) -> str:
    md = ["# P&L-backtest af long/short-signaturen", "",
          "Genereret af `backtest_signatur.py`. Validerings-backtest af den",
          "signatur der bestod IS/OOS-testen — ikke en parameterjagt.", "",
          "**Signal (live-triggerbart, ingen look-ahead):**", "", "```",
          f"LONG   z_15m <= {Z_ENTRY_LONG} & rvol_15m >= {RVOL_MIN} "
          f"& seneste bekraeftede 3m-prik == {DOT_LONG}",
          f"SHORT  z_15m >= {Z_ENTRY_SHORT} & rvol_15m >= {RVOL_MIN} "
          f"& seneste bekraeftede 3m-prik == {DOT_SHORT}",
          "",
          "Evalueres paa LUKKET 15m-bar -> entry paa naeste bars open.",
          f"Target  retur til 15m-middel (SMA{Z_LEN}), lagt som limit foer baren aabner",
          f"Stop    variant z: middel -/+ {Z_STOP} x std   |   variant atr: entry -/+ {ATR_STOP_K} x ATR",
          f"Tid     luk efter {MAX_BARS} barer ({MAX_BARS*15} min)",
          f"Omkost. {KOMMISSION_SIDE:.2f} $/side kommission + {SLIPPAGE_TICKS:.0f} tick "
          f"slippage/side = {2*(KOMMISSION_SIDE+SLIPPAGE_POINT*USD_PR_POINT):.2f} $ round-trip",
          "```", "",
          "**Vigtigt om realismen.** Event-datasaettets startbar er en swing-pivot",
          "der foerst bekraeftes 3 barer senere — den kan ikke handles i realtid.",
          "Denne backtest bruger den derfor ikke. Signalet er bygget forfra paa de",
          "raa barer og evalueret paa lukkede barer. Til gengaeld betyder det at",
          "handlerne her IKKE er de samme events som blev valideret; de er hvad",
          "signaturen faktisk ville have udloest live.", ""]

    # ---------- Hovedtabel: alle varianter ----------
    md += ["---", "", "## 1. Alle varianter, samlet", "",
           "Hver raekke er én komplet backtest. `netto` = efter kommission og "
           "slippage.", ""]
    r = []
    for (stop_v, hale), h in kombi.items():
        for netto in (False, True):
            m = maal(h, netto)
            r.append([f"stop={stop_v}", "hale" if hale else "kun hoved",
                      "netto" if netto else "brutto"] + maal_raekke(m))
    md += [tabel(r, ["Stop", "Ben", "Omkost."] + MAAL_HOVED), ""]

    # ---------- Pr. side ----------
    md += ["---", "", "## 2. Pr. side (netto)", ""]
    for (stop_v, hale), h in kombi.items():
        if hale:
            continue
        r = []
        for side in ("long", "short"):
            r.append([side] + maal_raekke(maal([x for x in h if x.side == side], True)))
        md += [f"**stop={stop_v}, kun hovedben**", "",
               tabel(r, ["Side"] + MAAL_HOVED), ""]

    # ---------- Exit-aarsager ----------
    md += ["---", "", "## 3. Hvorfor blev handlerne lukket?", ""]
    for (stop_v, hale), h in kombi.items():
        if hale:
            continue
        tot = len(h)
        r = []
        for aarsag in ("target", "stop", "trail-stop", "tid", "rul"):
            g = [x for x in h if x.exit_aarsag == aarsag]
            if not g:
                continue
            pnl = sum(x.netto() for x in g)
            r.append([aarsag, f"{len(g):,}", f"{len(g)/tot*100:.1f} %",
                      d(np.median([x.point for x in g]), 2), d(pnl, 0)])
        md += [f"**stop={stop_v}**", "",
               tabel(r, ["Aarsag", "n", "Andel", "Median point", "P&L netto $"]), ""]

    # ---------- Pr. kontrakt-periode ----------
    md += ["---", "", "## 4. Pr. futures-kontrakt (netto, kun hovedben)", "",
           "Spec B4: afkastet maa ikke haenge paa én periode eller ét regime.", ""]
    for stop_v in ("z", "atr"):
        h = kombi[(stop_v, False)]
        r = []
        for p in perioder:
            g = [x for x in h if x.kontrakt_periode == p]
            if not g:
                continue
            for side in ("long", "short", "begge"):
                gg = g if side == "begge" else [x for x in g if x.side == side]
                if not gg:
                    continue
                m = maal(gg, True)
                r.append([p, side, f"{m['n']}", f"{m['hitrate']*100:.0f} %",
                          d(m["expectancy"], 2), d(m["pnl"], 0)])
        md += [f"**stop={stop_v}**", "",
               tabel(r, ["Periode", "Side", "n", "Hitrate", "Expectancy $", "P&L $"]), ""]

    # ---------- Break-even paa omkostninger ----------
    md += ["---", "", "## 5. Hvor meget aeder omkostningerne?", "",
           "Det afgoerende regnestykke: bruttoen pr. handel mod round-trip-",
           "omkostningen. Er bruttoen mindre, findes der ingen omkostningsstruktur",
           "der redder strategien — kun en anden exit-model kan.", ""]
    r = []
    for (stop_v, hale), h in kombi.items():
        if not h:
            continue
        brutto = sum(x.brutto() for x in h) / len(h)
        faktisk = sum(x.omkostning() for x in h) / len(h)
        r.append([f"stop={stop_v}", "hale" if hale else "kun hoved",
                  d(brutto, 2), d(faktisk, 2),
                  f"{brutto/5/0.25:.1f}" if brutto > 0 else "—",
                  "JA" if brutto > faktisk else "**NEJ**"])
    md += [tabel(r, ["Stop", "Ben", "Brutto/handel $", "Omkostning/handel $",
                     "Break-even i ticks (round-trip)", "Daekker bruttoen omkostningen?"]), "",
           "Til sammenligning koster ét tick $1,25, og round-trip med 1 tick",
           f"slippage pr. side + ${KOMMISSION_SIDE:.2f} kommission pr. side er "
           f"${2*(KOMMISSION_SIDE+SLIPPAGE_POINT*USD_PR_POINT):.2f}.", ""]

    # Foelsomhed: hvor billigt skulle det vaere?
    md += ["**Foelsomhed — netto expectancy ved forskellige omkostningsniveauer:**", ""]
    niveauer = [0.0, 1.0, 2.0, 3.0, 4.5, 6.0]
    r = []
    for (stop_v, hale), h in kombi.items():
        if not h:
            continue
        brutto = sum(x.brutto() for x in h) / len(h)
        r.append([f"stop={stop_v}", "hale" if hale else "kun hoved"]
                 + [d(brutto - c, 2) for c in niveauer])
    md += [tabel(r, ["Stop", "Ben"] + [f"${c:.2f}" for c in niveauer]), "",
           "Kun kolonnerne helt til venstre er positive — og de svarer til at",
           "handle uden kommission og uden slippage.", ""]

    # ---------- Forbehold ----------
    md += ["---", "", "## 6. Forbehold og fortolkning", "",
           "1. **z-stoppet er strukturelt skaevt** — og det er en egenskab ved",
           "   selve reglen, ikke ved implementeringen. Stoppet ligger fast ved",
           "   |z| = 3.5, mens signalet kan fyre hvor som helst fra |z| = 2.0 og",
           "   nedefter. Fyrer det ved |z| = 3.2, ligger stoppet 0,3 std vaek, og",
           "   handlen stoppes naesten med det samme: **47 % af alle stops i",
           "   z-varianten rammer paa selve entry-baren**. ATR-stoppet giver en",
           "   konstant risiko pr. handel og er derfor den variant der siger noget",
           "   om signalet. Jeg har ikke aendret z-reglen — det ville vaere den",
           "   parameterjagt specen forbyder — men den skal ikke laeses som et",
           "   ligevaerdigt alternativ.",
           "2. **~99 % retningstraef betoed aldrig 99 % vinderhandler.** Det tal",
           "   blev maalt BLANDT event-starter, altsaa betinget af at en",
           "   bevaegelse allerede var defineret. Det rigtige tal at forvente er",
           "   praecisionen (~30-40 %), og backtestens hitrate paa 27-45 % ligger",
           "   praecis der. Valideringen og backtesten modsiger ikke hinanden.",
           "3. **Handlerne her er ikke de validerede events.** Signalet fyrer paa",
           "   ~1.340 barer, hvoraf kun ~380 er event-starter. Resten er de ~70 %",
           "   hvor signaturen fyrer uden at en stor bevaegelse foelger. En",
           "   backtest skal tage dem alle med — det er dem der betaler regningen.",
           "4. **Kun ét exit-design er afproevet.** At target = middelvaerdien er",
           "   statistisk velbegrundet (bevaegelserne doer der), men et signal kan",
           "   have edge og alligevel tabe med et bestemt stop/target-forhold.",
           "   Resultatet her afviser dette design — ikke enhver anvendelse af",
           "   signaturen.",
           "5. **Ingen sizing, ingen filtrering paa tid/regime.** Fast 1 kontrakt,",
           "   alle timer, alle dage. Expectancy pr. handel er rapporteret, saa",
           "   sizing kan laegges ovenpaa senere.", ""]

    return md


def main() -> None:
    print("── FASE B: P&L-backtest af signaturen ─────────────────────────")
    f, v_h, v_p, seg, bars_since_roll = byg_grundlag()

    print("Slaar 3m-prikker op pr. 15m-bar (samme opslag som valideringen) …")
    prikker = prik_pr_bar(v_h, v_p)
    n_sig_l = int(((f["z"].to_numpy() <= Z_ENTRY_LONG)
                   & (f["rvol"].to_numpy() >= RVOL_MIN) & (prikker == DOT_LONG)).sum())
    n_sig_s = int(((f["z"].to_numpy() >= Z_ENTRY_SHORT)
                   & (f["rvol"].to_numpy() >= RVOL_MIN) & (prikker == DOT_SHORT)).sum())
    print(f"  raa signalbarer: long {n_sig_l:,} · short {n_sig_s:,}")

    kombi = {}
    for stop_v in ("z", "atr"):
        for hale in (False, True):
            h = koer(f, prikker, seg, bars_since_roll, stop_v, hale)
            kombi[(stop_v, hale)] = h
            m = maal(h, True)
            print(f"  stop={stop_v:<4} hale={str(hale):<5} "
                  f"n={m.get('n',0):<5} netto P&L ${m.get('pnl',0):>9,.0f}  "
                  f"expectancy ${m.get('expectancy',0):>7.2f}  "
                  f"PF {m.get('profit_factor',float('nan')):.2f}")

    perioder = sorted({h.kontrakt_periode for h in kombi[("z", False)]})
    md = rapport(kombi, perioder)
    (OUT_DIR / "backtest_signatur.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    raekker = []
    for (stop_v, hale), h in kombi.items():
        for x in h:
            raekker.append({**asdict(x), "stop_variant": stop_v, "hale_ben": hale,
                            "point": x.point, "brutto": x.brutto(),
                            "omkostning": x.omkostning(), "netto": x.netto()})
    pd.DataFrame(raekker).to_csv(OUT_DIR / "backtest_trades.csv", index=False)

    for p in (OUT_DIR / "backtest_signatur.md", OUT_DIR / "backtest_trades.csv"):
        print(f"  {p.name:<32} {p.stat().st_size/1024:8.1f} kB")


if __name__ == "__main__":
    main()
