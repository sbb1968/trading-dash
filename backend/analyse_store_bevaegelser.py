#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyse_store_bevaegelser.py
============================
Indfang store MES-prisbevaegelser (op OG ned) som ét EVENT-DATASAET.

Formaal (jf. spec): i stedet for at aflaese indikatorer candle-for-candle paa
TradingView, laver vi én raekke pr. stor bevaegelse med ALLE indikatorvaerdier
fanget ved bevaegelsens START (setup/entry) og SLUT (udmattelse/exit) — paa
alle fem timeframes. Saa kan moenstrene findes i en tabel i stedet for paa
charten.

Dette script producerer KUN datasaettet. Ingen strategi, ingen backtest, ingen
P&L, ingen forudsigelse — selve moenster-analysen sker bagefter.

Kilde-data: data_harvest/mes_m2k_stitched/MES_1min.csv
  - Tidsstempler er tidszone-BEVIDSTE med eksplicit offset (-04:00 sommer /
    -05:00 vinter) => America/New_York. Hver raekke er derfor et entydigt
    absolut oejeblik, og tz_convert til Europe/Copenhagen er eksakt (haandterer
    dansk sommertid CET<->CEST automatisk). Alt output er i DANSK tid.

Output (skrives til OUT_DIR):
  - store_bevaegelser_events.parquet / .csv   hoved-leverancen (begge metoder)
  - store_bevaegelser_baseline.parquet / .csv kontrol-sample af "almindelige" barer
  - store_bevaegelser_sammenligning.md        metode-overlap + noegletal

Koeres:  python analyse_store_bevaegelser.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from store_bevaegelser_lib import (
    TF_SPEC, TimeframeView, SNAPSHOT_NUMERIC,
    Event, detect_swings, detect_forward_moves, nearest_prior_dot,
    roll_segment_id, DOT_INGEN,
    Z_LEN, ADX_DI_LEN, ADX_SMOOTH, ATR_LEN, CMF_LEN, RVOL_LEN,
    RSI_LEN, STOCH_LEN, WT_CHANNEL_LEN, WT_AVERAGE_LEN, WT_MA_LEN,
)

# Windows-terminalen er cp1252 og kan ikke printe danske tegn/emoji — tving UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# =============================================================================
# KONSTANTER — juster her, ikke i logikken nedenfor
# =============================================================================
HERE      = Path(__file__).resolve().parent
DATA_FILE = HERE / "data_harvest" / "mes_m2k_stitched" / "MES_1min.csv"
# Per-kontrakt-filerne bruges KUN til at finde rul-graenserne (se load_roll_times).
CLEAN_DIR = HERE / "data_harvest" / "mes_m2k_clean"
CLEAN_GLOB = "MES_20*_1min.csv"
OUT_DIR   = HERE / "store_bevaegelser_out"

TARGET_TZ = "Europe/Copenhagen"        # alt output i dansk vaegur-tid

# --- Timeframes ------------------------------------------------------------
# Indikator-suiten beregnes paa ALLE disse (raekkefoelgen styrer kolonne-orden).
TIMEFRAMES     = ["1h", "15m", "5m", "3m", "2m"]
# Bevaegelserne DETEKTERES paa denne (Soerens trade-TF). Skift til "5m"/"3m"/"2m"
# for at koere samme analyse paa en finere skala — snapshottene tages stadig
# paa alle fem.
DETECT_TF      = "15m"

# --- Snapshot-punkter ------------------------------------------------------
PRE_BARS       = 2                     # PRE-snapshot = 2 barer foer start (detekterings-TF)

# --- Metode A: ATR-swing / zigzag -----------------------------------------
PIVOT_LEFT     = 3                     # L=R=3 som i det eksisterende bibliotek
PIVOT_RIGHT    = 3
SWING_ATR_MULT = 1.5                   # ben skal vaere >= 1.5 x ATR ved benets start

# --- Metode B: fremadrettet afkast-taerskel --------------------------------
FWD_N          = 20                    # se 20 barer frem
FWD_ATR_MULT   = 2.0                   # bevaegelse skal vaere >= 2.0 x ATR

# --- Cipher B-prikker ------------------------------------------------------
DOT_LOOKBACK_BARS = 50                 # ingen prik inden for 50 barer => "ingen"

# --- Kontrol-/baseline-sample ---------------------------------------------
BASELINE_N     = 4000                  # antal "almindelige" barer
BASELINE_SEED  = 20260727              # fast seed => reproducerbart udtraek
# Baseline skal vaere MEs normaltilstand. Vi udelukker kun selve event-START-
# barerne, ikke hele event-spaendet: swing-benene daekker naesten hver eneste
# bar i serien (de ligger jo i forlaengelse af hinanden), saa "ikke inde i et
# event" ville efterlade nogle faa hundrede systematisk atypiske rest-barer.
# Radius > 0 fjerner ogsaa naboer til starterne — men det ville skaere praecis
# de oejeblikke vaek der ligner en start mest, og dermed kunstigt forstoerre
# enhver forskel. Default 0.
BASELINE_EXCLUDE_RADIUS = 0

# --- Opvarmning ------------------------------------------------------------
# 1h-indikatorerne skal have historik nok (MACD 26/9, ADX 14+14, stoch 14+14).
# 500 x 15m = 125 timer => ~125 1h-barer. Koster ~5 dage ud af 2 aar.
WARMUP_BARS_DET = 500

# --- Metode-sammenligning --------------------------------------------------
MATCH_TOL_BARS = 3                     # samme retning + start inden for +/-3 barer = match
TOP_MISS_N     = 10                    # hvor mange "missede" events der listes i rapporten

# Barer efter et kontrakt-rul hvor rullende vinduer stadig blander den gamle
# og den nye kontrakts priser. 100 x 15m = 25 timer, hvilket daekker det
# laengste vindue vi bruger (MACD's langsomme EMA) med god margin.
ROLL_SUSPECT_BARS = 100

# --- TradingView-fidelitetstjek -------------------------------------------
TV_KONTROL_N   = 10                    # antal tidsstempler til manuel TV-aflaesning
# Vaelg barer i den amerikanske aabningstid (dansk tid), hvor der er rigelig
# volumen og barerne er nemme at finde igen paa charten.
TV_TIME_VINDUE = (15, 21)              # dansk time, start- og slut-inklusivt

# Danske ugedagsnavne (0=mandag ... 6=soendag)
DK_DOW = ["Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag", "Loerdag", "Soendag"]

# Snapshot-punkter der gemmes pr. event
PUNKTER_EVENT    = ["pre", "start", "end"]
PUNKTER_BASELINE = ["pre", "start"]      # baseline har ingen "slut" — den er ikke en bevaegelse


# =============================================================================
# 1. INDLAES 1-MINUTS DATA
# =============================================================================
def load_minute_data() -> pd.DataFrame:
    """
    Laes 1-min CSV og returnér OHLCV med tz-bevidst UTC-indeks.

    Vi regner internt i UTC og konverterer foerst til dansk tid ved output.
    Grunden: resampling til 2/3/5/15/60 min skal have stabile bucket-graenser.
    ET-offset er altid et HELT antal timer fra UTC, og alle fem timeframes
    gaar op i en time, saa UTC-buckets falder paa praecis samme graenser som
    boersens — ogsaa hen over sommertids-skift.
    """
    df = pd.read_csv(DATA_FILE)
    ts = pd.to_datetime(df["timestamp"], utc=True)
    df = df.drop(columns=["timestamp"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.index = pd.DatetimeIndex(ts)
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def load_roll_times() -> list[pd.Timestamp]:
    """
    Find kontrakt-rullene i den kontinuerlige serie.

    MES_1min.csv er RAW-stitched: `curate_futures_data.stitch_continuous`
    saetter per-kontrakt-filerne efter hinanden UDEN back-adjustment, saa
    prisen springer med kontraktens carry-spread ved hvert kvartals-rul.
    Rul-graensen er sidste tidsstempel i hver udloebende kontrakt.

    Findes per-kontrakt-mappen ikke, returneres en tom liste (og analysen
    koerer videre uden rul-vagt — det siges tydeligt i rapporten).
    """
    parts = sorted(CLEAN_DIR.glob(CLEAN_GLOB))
    if len(parts) < 2:
        return []
    slut = []
    for p in parts:
        ts = pd.to_datetime(pd.read_csv(p, usecols=["timestamp"])["timestamp"],
                            utc=True)
        slut.append(ts.max())
    return sorted(slut)[:-1]      # sidste kontrakt har ingen rul efter sig


# =============================================================================
# 2. SNAPSHOTS
# =============================================================================
def snapshot_into(out: dict, views: dict[str, TimeframeView],
                  known_at_ns: int, punkt: str) -> None:
    """
    Skriv ét snapshot (alle indikatorer, alle 5 TF) ind i raekke-dict'en `out`.

    `known_at_ns` er LUKKETIDEN for den bar snapshottet hoerer til. Alt hentes
    via TimeframeView.align_index, som kun returnerer barer der var HELT
    afsluttet paa det tidspunkt — det er den future-leak-sikre "previous"-
    konvention fra codebase.md.

    Kolonnenavne: {indikator}_{tf}_{punkt}, fx z_2m_start, adx_1h_start,
    dot_type_2m_start, bars_to_dot_2m_start, cmf_15m_end.
    """
    for tf in TIMEFRAMES:
        view = views[tf]
        i = view.align_index(known_at_ns)

        if i < 0:
            for name in SNAPSHOT_NUMERIC:
                out[f"{name}_{tf}_{punkt}"] = np.nan
            out[f"dot_type_{tf}_{punkt}"] = DOT_INGEN
            out[f"bars_to_dot_{tf}_{punkt}"] = np.nan
            continue

        row = view.num[i]
        for j, name in enumerate(SNAPSHOT_NUMERIC):
            out[f"{name}_{tf}_{punkt}"] = row[j]

        dot, bars_to = nearest_prior_dot(view.dot_type, view.dot_lag, i,
                                         DOT_LOOKBACK_BARS)
        out[f"dot_type_{tf}_{punkt}"] = dot
        out[f"bars_to_dot_{tf}_{punkt}"] = bars_to


def dk_felter(ts_utc: pd.Timestamp) -> dict:
    """Dansk tidsstempel + ugedag + time-paa-doegnet."""
    dk = ts_utc.tz_convert(TARGET_TZ)
    return {
        "start_ts_dk": dk,
        "ugedag": DK_DOW[dk.dayofweek],
        "dansk_time": int(dk.hour),
    }


def build_event_rows(events: list[Event], views: dict[str, TimeframeView],
                     det_index: pd.DatetimeIndex, det_close_ns: np.ndarray,
                     det_minutes: int, bars_since_roll: np.ndarray) -> pd.DataFrame:
    """Byg den brede event-tabel — én raekke pr. bevaegelse."""
    rows = []
    for k, e in enumerate(events):
        row: dict = {
            "event_id": f"{e.metode}_{k:06d}",
            "metode": e.metode,
            "retning": e.retning,
            **dk_felter(det_index[e.start_idx]),
            "end_ts_dk": det_index[e.end_idx].tz_convert(TARGET_TZ),
            "varighed_bars": int(e.end_idx - e.start_idx),
            "varighed_min": int((e.end_idx - e.start_idx) * det_minutes),
            "start_pris": e.start_pris,
            "slut_pris": e.slut_pris,
            "size_pt": e.size_pt,
            "size_atr": e.size_atr,
            "detect_tf": DETECT_TF,
            "bars_since_roll": int(bars_since_roll[e.start_idx]),
        }

        # PRE: PRE_BARS barer FOER start-baren (samme detekterings-TF).
        pre_idx = e.start_idx - PRE_BARS
        snapshot_into(row, views,
                      int(det_close_ns[pre_idx]) if pre_idx >= 0 else -1, "pre")
        # START: alt til og med start-barens lukning — intet derefter.
        snapshot_into(row, views, int(det_close_ns[e.start_idx]), "start")
        # SLUT: udmattelses-baren. Her ER fremadrettet info med vilje.
        snapshot_into(row, views, int(det_close_ns[e.end_idx]), "end")

        rows.append(row)

    return pd.DataFrame(rows)


def build_baseline_rows(bar_idx: np.ndarray, views: dict[str, TimeframeView],
                        det_index: pd.DatetimeIndex, det_close_ns: np.ndarray,
                        bars_since_roll: np.ndarray) -> pd.DataFrame:
    """
    Kontrol-sample: helt almindelige barer med samme START/PRE-snapshots.

    Uden baseline kan vi ikke sige om fx "lav z ved start" faktisk er saerligt
    for store bevaegelser, eller bare er normaltilstanden paa MES.

    Baseline har ingen "end"-kolonner — en tilfaeldig bar har ingen naturlig
    udmattelse, og et vilkaarligt valgt slut-punkt ville vaere et skjult valg.
    """
    rows = []
    for k, i in enumerate(bar_idx):
        i = int(i)
        row: dict = {
            "event_id": f"baseline_{k:06d}",
            "metode": "baseline",
            "retning": "none",
            **dk_felter(det_index[i]),
            "detect_tf": DETECT_TF,
            "bars_since_roll": int(bars_since_roll[i]),
        }
        pre_idx = i - PRE_BARS
        snapshot_into(row, views,
                      int(det_close_ns[pre_idx]) if pre_idx >= 0 else -1, "pre")
        snapshot_into(row, views, int(det_close_ns[i]), "start")
        rows.append(row)
    return pd.DataFrame(rows)


# =============================================================================
# 3. KOLONNE-ORDEN
# =============================================================================
META_COLS = [
    "event_id", "metode", "retning", "start_ts_dk", "end_ts_dk",
    "ugedag", "dansk_time", "varighed_bars", "varighed_min",
    "start_pris", "slut_pris", "size_pt", "size_atr", "detect_tf",
    "bars_since_roll",
]


def order_columns(df: pd.DataFrame, punkter: list[str]) -> pd.DataFrame:
    """Meta foerst, derefter snapshots grupperet punkt -> timeframe -> indikator."""
    cols = [c for c in META_COLS if c in df.columns]
    for punkt in punkter:
        for tf in TIMEFRAMES:
            for name in SNAPSHOT_NUMERIC:
                cols.append(f"{name}_{tf}_{punkt}")
            cols.append(f"dot_type_{tf}_{punkt}")
            cols.append(f"bars_to_dot_{tf}_{punkt}")
    cols = [c for c in cols if c in df.columns]
    return df[cols + [c for c in df.columns if c not in cols]]


# =============================================================================
# 4. METODE-SAMMENLIGNING
# =============================================================================
def sammenlign_metoder(sw: list[Event], fw: list[Event]) -> dict:
    """
    Hvor enige er de to definitioner?

    Match = samme retning OG start inden for +/-MATCH_TOL_BARS barer.
    Vi taeller fra begge sider, fordi matchet ikke er 1:1 (én swing kan
    daekke flere fwd-events og omvendt).
    """
    def starts(evs, retning):
        return np.array(sorted(e.start_idx for e in evs if e.retning == retning))

    matched_sw, matched_fw = set(), set()
    for retning in ("up", "down"):
        s_fw = starts(fw, retning)
        for a, e in enumerate(sw):
            if e.retning != retning or len(s_fw) == 0:
                continue
            p = int(np.searchsorted(s_fw, e.start_idx))
            for q in (p - 1, p):
                if 0 <= q < len(s_fw) and abs(int(s_fw[q]) - e.start_idx) <= MATCH_TOL_BARS:
                    matched_sw.add(a)
                    break

        s_sw = starts(sw, retning)
        for b, e in enumerate(fw):
            if e.retning != retning or len(s_sw) == 0:
                continue
            p = int(np.searchsorted(s_sw, e.start_idx))
            for q in (p - 1, p):
                if 0 <= q < len(s_sw) and abs(int(s_sw[q]) - e.start_idx) <= MATCH_TOL_BARS:
                    matched_fw.add(b)
                    break

    return {
        "sw_matched": len(matched_sw),
        "fw_matched": len(matched_fw),
        "sw_only": [e for a, e in enumerate(sw) if a not in matched_sw],
        "fw_only": [e for b, e in enumerate(fw) if b not in matched_fw],
    }


def _fordelingstabel(df: pd.DataFrame, col: str, orden=None) -> str:
    """Lille markdown-tabel: antal events pr. vaerdi af `col`, delt paa metode."""
    piv = df.pivot_table(index=col, columns="metode", values="event_id",
                         aggfunc="count", fill_value=0)
    if orden is not None:
        piv = piv.reindex([o for o in orden if o in piv.index])
    lines = ["| " + col + " | " + " | ".join(str(c) for c in piv.columns) + " |",
             "|" + "---|" * (len(piv.columns) + 1)]
    for idx, r in piv.iterrows():
        lines.append(f"| {idx} | " + " | ".join(str(int(v)) for v in r.values) + " |")
    return "\n".join(lines)


def skriv_sammenligning(ev: pd.DataFrame, sw: list[Event], fw: list[Event],
                        cmp_res: dict, det_index: pd.DatetimeIndex,
                        n_bars_det: int, n_baseline: int,
                        roll_info: str) -> str:
    """Byg sammenlignings-rapporten som markdown."""
    def ts(idx: int) -> str:
        return det_index[idx].tz_convert(TARGET_TZ).strftime("%Y-%m-%d %H:%M")

    def blok(evs: list[Event], titel: str) -> str:
        if not evs:
            return f"**{titel}:** ingen.\n"
        top = sorted(evs, key=lambda e: -e.size_atr)[:TOP_MISS_N]
        out = [f"**{titel}** (de {len(top)} stoerste af {len(evs)}):", "",
               "| start (dansk tid) | retning | size_pt | size_atr | varighed_bars |",
               "|---|---|---|---|---|"]
        for e in top:
            out.append(f"| {ts(e.start_idx)} | {e.retning} | {e.size_pt:.2f} | "
                       f"{e.size_atr:.2f} | {e.end_idx - e.start_idx} |")
        return "\n".join(out) + "\n"

    n_sw, n_fw = len(sw), len(fw)
    sw_up = sum(1 for e in sw if e.retning == "up")
    fw_up = sum(1 for e in fw if e.retning == "up")

    # Rul-forurening, konkret: |z| = sqrt(Z_LEN-1) er den matematisk stoerst
    # mulige z-vaerdi og opstaar kun naar alle foregaaende closes er ens.
    z_maks = np.sqrt(Z_LEN - 1)
    z_ekstrem = ev["z_15m_start"].abs() > (z_maks - 1e-6)
    n_z_ekstrem = int(z_ekstrem.sum())
    n_z_roll = int((z_ekstrem & (ev["bars_since_roll"] <= ROLL_SUSPECT_BARS)).sum())
    n_total = len(ev)
    n_roll_naer = int((ev["bars_since_roll"] <= ROLL_SUSPECT_BARS).sum())
    pct_roll_naer = 100.0 * n_roll_naer / max(n_total, 1)

    md = f"""# Store MES-bevaegelser — metode-sammenligning

Genereret af `analyse_store_bevaegelser.py`. Rent beskrivende datasaet —
ingen strategi, ingen backtest, ingen forudsigelse.

**Kilde:** `{DATA_FILE.relative_to(HERE)}`
**Detekterings-timeframe:** {DETECT_TF} ({n_bars_det:,} barer efter {WARMUP_BARS_DET} barers opvarmning)
**Periode (dansk tid):** {ev['start_ts_dk'].min()} → {ev['start_ts_dk'].max()}
**Snapshot-timeframes:** {", ".join(TIMEFRAMES)}
**Kontrakt-rul:** {roll_info}

---

## 1. Antal events

| | Metode A (swing) | Metode B (fwd) |
|---|---|---|
| Parametre | pivots L=R={PIVOT_LEFT}, ben >= {SWING_ATR_MULT} x ATR | {FWD_N} barer frem, >= {FWD_ATR_MULT} x ATR |
| Events i alt | {n_sw:,} | {n_fw:,} |
| — op | {sw_up:,} | {fw_up:,} |
| — ned | {n_sw - sw_up:,} | {n_fw - fw_up:,} |
| Median size_atr | {np.median([e.size_atr for e in sw]) if sw else float('nan'):.2f} | {np.median([e.size_atr for e in fw]) if fw else float('nan'):.2f} |
| Median varighed (barer) | {np.median([e.end_idx - e.start_idx for e in sw]) if sw else float('nan'):.1f} | {np.median([e.end_idx - e.start_idx for e in fw]) if fw else float('nan'):.1f} |

Baseline-sample (almindelige, ikke-event barer): **{n_baseline:,}**.

---

## 2. Overlap

Match = samme retning OG start inden for +/-{MATCH_TOL_BARS} barer. Matchet er
ikke 1:1 (én swing kan daekke flere fwd-events), derfor taelles begge veje.

| | matchet | kun denne metode | andel matchet |
|---|---|---|---|
| Metode A (swing) | {cmp_res['sw_matched']:,} | {len(cmp_res['sw_only']):,} | {100 * cmp_res['sw_matched'] / max(n_sw, 1):.1f} % |
| Metode B (fwd)   | {cmp_res['fw_matched']:,} | {len(cmp_res['fw_only']):,} | {100 * cmp_res['fw_matched'] / max(n_fw, 1):.1f} % |

**Hvorfor de er uenige — de to definitioner spoerger om forskellige ting:**

- *Swing* skaerer prisen i skiftende ben og spoerger "hvor stort blev dette
  ben?". Starten er en bekraeftet pivot — altsaa et vendepunkt.
- *Fwd* spoerger for HVER bar "kom der 2 x ATR inden for 20 barer?". Starten er
  en vilkaarlig bar, ikke noedvendigvis et vendepunkt. Den fanger derfor ogsaa
  fortsaettelser midt i et ben, og misser lange, langsomme ben der bruger mere
  end {FWD_N} barer paa at levere bevaegelsen.

{blok(cmp_res['sw_only'], "Kun fundet af Metode A (swing)")}
{blok(cmp_res['fw_only'], "Kun fundet af Metode B (fwd)")}
---

## 3. Fordeling paa dansk time-paa-doegnet

{_fordelingstabel(ev, "dansk_time")}

---

## 4. Fordeling paa ugedag

{_fordelingstabel(ev, "ugedag", DK_DOW)}

---

## 5. Stoerrelse

| metode | retning | n | median size_pt | median size_atr | 90%-fraktil size_atr |
|---|---|---|---|---|---|
"""
    for m in ("swing", "fwd"):
        for r in ("up", "down"):
            g = ev[(ev["metode"] == m) & (ev["retning"] == r)]
            if len(g) == 0:
                continue
            md += (f"| {m} | {r} | {len(g):,} | {g['size_pt'].median():.2f} | "
                   f"{g['size_atr'].median():.2f} | {g['size_atr'].quantile(0.90):.2f} |\n")

    md += f"""
---

## 6. Indikator-parametre brugt

z={Z_LEN} (population-std) · ADX({ADX_DI_LEN},{ADX_SMOOTH}) · ATR({ATR_LEN}, Wilder) ·
CMF({CMF_LEN}) · RVOL({RVOL_LEN}, foregaaende barer) · RSI({RSI_LEN}) ·
MACD(12,26,9) · StochRSI({STOCH_LEN}/{RSI_LEN}/3/3, log-skala) ·
WaveTrend({WT_CHANNEL_LEN},{WT_AVERAGE_LEN},{WT_MA_LEN}) · VWAP ankret 18:00 ET (CME-doegnet).

---

## 7. Forbehold der skal med til analysen

1. **Prik-kategorierne er den eneste antagelse.** Cipher B's .pine-fil ligger
   ikke i repoet; formlerne er taget fra `docs_src/market_cipher_b_teknisk.md`
   (skrevet direkte ud af Pine-scriptet). Mapningen af de tre groen/roed-styrker
   (`udvandet` = almindeligt WT-kryds, `alm` = divergens-prik, `kraftig` = stor
   cirkel i overkoebt/oversolgt) er udledt af dokumentets egen styrke-beskrivelse
   og skal bekraeftes visuelt i TradingView-tjekket. Aendres den, aendres kun
   `_DOT_PRIORITY` i `store_bevaegelser_lib.py`.
2. **`bars_to_dot` taeller til der hvor prikken TEGNES**, men prikken tages kun
   med hvis den var BEKRAEFTET paa eller foer start-baren. Divergens-prikker
   har 2 barers bekraeftelses-forsinkelse (5-bars fraktal); krydsprikker har
   ingen. Ingen future leak, men tallet passer med det man taeller paa charten.
3. **START-snapshottet bruger kun data til og med start-barens lukning** — paa
   alle fem timeframes, via "previous"-konventionen. Bevaegelsens stoerrelse og
   retning er fremadrettet, men det er labelen, ikke en feature.
4. **Metode A's startpunkt er en pivot**, som foerst bekraeftes {PIVOT_RIGHT} barer
   senere. Selve *definitionen* af hvor benet begynder er dermed fremadskuende.
   Det er i orden for moenster-beskrivelse, men et handelssystem kan ikke
   handle paa den bar i realtid.
5. **Baseline har ingen `_end`-kolonner.** En tilfaeldig bar har ingen naturlig
   udmattelse; et vilkaarligt slut-punkt ville vaere et skjult valg. Baseline
   er alle detekterings-barer der IKKE er en event-start (ikke "alle barer
   uden for et event" — swing-benene ligger i forlaengelse af hinanden og
   daekker naesten hele serien).
6. **Kontrakt-rul — laes denne foer du analyserer.** Den kontinuerlige
   MES-serie er raw-stitched, ikke back-adjusted: prisen springer med
   carry-spreadet ved hvert kvartals-rul. Events der SPAENDER over et rul er
   fjernet ({roll_info}). Men indikatorerne er ogsaa forurenede i barerne
   EFTER et rul, hvor de rullende vinduer stadig indeholder den gamle
   kontrakts priser. Det kan ses direkte i data: {n_z_ekstrem} events har
   |`z_15m_start`| = √29 = 5.385 — den stoerst MULIGE z naar de 29
   foregaaende closes er identiske, hvilket kun sker i den doede aabning af
   en ny kontrakt. {n_z_roll} af dem ligger inden for {ROLL_SUSPECT_BARS}
   barer efter et rul.
   **Anbefalet filter:** `bars_since_roll > {ROLL_SUSPECT_BARS}` — fjerner
   {n_roll_naer} af {n_total} events ({pct_roll_naer:.1f} %).
7. **Degenereret volatilitet.** I doede nattetimer kan ATR falde under ét
   tick (0.25 pt). Alt der divideres med ATR eksploderer saa. `vwap_dist_atr`
   saettes til `na` under den graense, og events med start-ATR under ét tick
   detekteres slet ikke.
8. **Overnight- og weekend-huller er IKKE filtreret.** Et ben kan spaende hen
   over en session-pause. `varighed_min` er vaegur-tid, ikke handelstid.
"""
    return md


# =============================================================================
# 4b. TRADINGVIEW-FIDELITETSTJEK
# =============================================================================
# Kolonner der skal aflaeses i TradingViews Data Window, i den raekkefoelge de
# staar i tabellerne. `stoch_k` og WT'erne skal midlertidigt tilfoejes som
# data-window-plots i Cipher B for at kunne aflaeses.
TV_KOLONNER = ["close", "atr", "vwap", "vwap_dist_atr", "z", "adx", "cmf",
               "rvol", "rsi", "stoch_k", "wt1", "wt2", "wt_diff",
               "macd_line", "macd_hist"]


def byg_tv_kontrol(views: dict[str, TimeframeView], det_index: pd.DatetimeIndex,
                   det_close_ns: np.ndarray, n_det: int) -> tuple[str, pd.DataFrame]:
    """
    Lav kontrolarket til det manuelle TradingView-tjek (spec afsnit 6).

    Vi kan ikke laese TradingViews Data Window herfra, saa i stedet leverer vi
    Python-siden faerdig: 10 tidsstempler spredt over hele historikken, med
    alle vaerdier paa alle fem timeframes. Saa er tjekket en ren sammenligning
    — saet charten paa den paagaeldende TF, hold musen over baren, laes af.
    """
    dk = det_index.tz_convert(TARGET_TZ)
    kand = np.flatnonzero(
        (np.arange(n_det) >= WARMUP_BARS_DET)
        & (dk.hour >= TV_TIME_VINDUE[0]) & (dk.hour <= TV_TIME_VINDUE[1])
        & (dk.dayofweek < 5)
    )
    # Jaevnt spredt over historikken, ikke tilfaeldigt — saa daekker de ogsaa
    # forskellige kontrakter og volatilitets-regimer.
    valgte = kand[np.linspace(0, len(kand) - 1, TV_KONTROL_N).astype(int)]

    lange_raekker = []
    md = ["# TradingView-fidelitetstjek — kontrolark", "",
          "Python-siden er udfyldt. Aflaes de samme barer i TradingViews",
          "**Data Window** (MES, samme kontrakt-serie) og skriv vaerdierne ved",
          "siden af.", "",
          "**Foer du gaar i gang:** Cipher B viser ikke WT1/WT2/wt_diff/Stoch i",
          "Data Window som standard. Tilfoej dem midlertidigt som `plot(...)` i",
          "indikatoren, saa de kan aflaeses. Cockpittets ①–⑩ daekker allerede",
          "ATR, VWAP-afstand, RVOL, z, CMF og ADX.", "",
          "**Maal:** pris-afledte (close, ATR, VWAP) inden for faa ticks;",
          "oscillatorer (RSI, Stoch, WT, MACD) inden for ~1 %.",
          "**Stoerste risiko: WaveTrend.** Bekraeft wt1/wt2 specifikt — hele",
          "prik-logikken haenger paa dem.", "",
          "Afviger noget: ret Python'en til kilden er ramt, ikke omvendt.", ""]

    for tf in TIMEFRAMES:
        view = views[tf]
        md += [f"---", "", f"## Timeframe {tf}", "",
               "| # | bar-start (dansk tid) | bar-start (ET) | "
               + " | ".join(TV_KOLONNER) + " |",
               "|---|---|---|" + "---|" * len(TV_KOLONNER)]
        for k, i in enumerate(valgte, 1):
            j = view.align_index(int(det_close_ns[i]))
            if j < 0:
                continue
            bar_ts = view.feat.index[j]
            vals = [view.feat[c].iloc[j] for c in TV_KOLONNER]
            md.append(
                f"| {k} | {bar_ts.tz_convert(TARGET_TZ):%Y-%m-%d %H:%M} | "
                f"{bar_ts.tz_convert('America/New_York'):%Y-%m-%d %H:%M} | "
                + " | ".join("na" if not np.isfinite(v) else f"{v:.4f}" for v in vals)
                + " |")
            lange_raekker.append({
                "nr": k, "tf": tf,
                "bar_start_dk": bar_ts.tz_convert(TARGET_TZ),
                "bar_start_et": bar_ts.tz_convert("America/New_York"),
                **{c: v for c, v in zip(TV_KOLONNER, vals)},
                "dot_type": view.feat["dot_type"].iloc[j],
            })
        md.append("")

    md += ["---", "", "## Prik-kategorier paa de samme barer", "",
           "Tjek visuelt at kategorien passer med den prik Cipher B rent",
           "faktisk tegner. Det er den ene antagelse i pipelinen der ikke kan",
           "udledes af dokumentationen — se `PRIK_MAPNING` i",
           "`store_bevaegelser_lib.py`.", "",
           "| # | bar-start (dansk tid) | " + " | ".join(TIMEFRAMES) + " |",
           "|---|---|" + "---|" * len(TIMEFRAMES)]
    for k, i in enumerate(valgte, 1):
        celler = []
        for tf in TIMEFRAMES:
            view = views[tf]
            j = view.align_index(int(det_close_ns[i]))
            celler.append(view.feat["dot_type"].iloc[j] if j >= 0 else "na")
        md.append(f"| {k} | {det_index[i].tz_convert(TARGET_TZ):%Y-%m-%d %H:%M} | "
                  + " | ".join(celler) + " |")

    return "\n".join(md) + "\n", pd.DataFrame(lange_raekker)


# =============================================================================
# 5. MAIN
# =============================================================================
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("── Store MES-bevaegelser ──────────────────────────────────────")
    print(f"Laeser {DATA_FILE.name} …")
    df1m = load_minute_data()
    print(f"  {len(df1m):,} 1-min barer  "
          f"{df1m.index[0].tz_convert(TARGET_TZ)} → {df1m.index[-1].tz_convert(TARGET_TZ)} (dansk tid)")

    # --- Indikatorer paa alle fem timeframes ------------------------------
    views: dict[str, TimeframeView] = {}
    for tf in TIMEFRAMES:
        views[tf] = TimeframeView.build(tf, df1m)
        n_dots = int((views[tf].dot_type != DOT_INGEN).sum())
        print(f"  {tf:>3}: {len(views[tf].feat):>7,} barer · {n_dots:>6,} Cipher B-prikker")

    det = views[DETECT_TF]
    det_bars = det.feat[["close", "high", "low"]].copy()
    det_index = det.feat.index
    det_close_ns = det.bar_close_ns
    det_minutes = TF_SPEC[DETECT_TF][1]
    n_det = len(det_bars)

    # --- Kontrakt-rul ------------------------------------------------------
    roll_times = load_roll_times()
    seg = roll_segment_id(det_index, roll_times)
    # Barer siden seneste rul — til at filtrere rul-forurenede indikatorer fra.
    seg_start = np.zeros(n_det, dtype=np.int64)
    _, first = np.unique(seg, return_index=True)
    for s, f in zip(np.unique(seg), first):
        seg_start[seg == s] = f
    bars_since_roll = np.arange(n_det) - seg_start
    print(f"\n{len(roll_times)} kontrakt-rul fundet "
          f"(raw-stitched serie => prisspring ved hvert rul)")

    # --- Detektér bevaegelser ---------------------------------------------
    print(f"Detekterer bevaegelser paa {DETECT_TF} …")
    sw_all = detect_swings(det_bars, det.feat["atr"],
                           PIVOT_LEFT, PIVOT_RIGHT, SWING_ATR_MULT)
    fw_all = detect_forward_moves(det_bars, det.feat["atr"], FWD_N, FWD_ATR_MULT)

    def behold(evs: list[Event]) -> tuple[list[Event], int, int]:
        """Skær opvarmning og rul-krydsende events fra. Returnér (beholdt, n_warm, n_roll)."""
        warm = [e for e in evs if e.start_idx >= WARMUP_BARS_DET]
        keep = [e for e in warm if seg[e.start_idx] == seg[e.end_idx]]
        return keep, len(evs) - len(warm), len(warm) - len(keep)

    sw, sw_warm, sw_roll = behold(sw_all)
    fw, fw_warm, fw_roll = behold(fw_all)
    print(f"  Metode A (swing): {len(sw):,} events "
          f"(kasseret: {sw_warm} i opvarmning, {sw_roll} paa tvaers af rul)")
    print(f"  Metode B (fwd):   {len(fw):,} events "
          f"(kasseret: {fw_warm} i opvarmning, {fw_roll} paa tvaers af rul)")
    roll_info = (f"{len(roll_times)} rul · {sw_roll + fw_roll} rul-krydsende "
                 f"events fjernet" if roll_times else
                 "per-kontrakt-filer ikke fundet — INGEN rul-vagt aktiv")

    # --- Byg event-tabellen -----------------------------------------------
    print("\nBygger snapshots (5 timeframes x 3 punkter pr. event) …")
    ev_sw = build_event_rows(sw, views, det_index, det_close_ns, det_minutes,
                             bars_since_roll)
    ev_fw = build_event_rows(fw, views, det_index, det_close_ns, det_minutes,
                             bars_since_roll)
    ev = pd.concat([ev_sw, ev_fw], ignore_index=True)
    ev = order_columns(ev, PUNKTER_EVENT)
    print(f"  {len(ev):,} raekker x {len(ev.columns)} kolonner")

    # --- Baseline ----------------------------------------------------------
    # "Almindelig" = enhver bar der ikke selv er en event-START (se konstanten
    # BASELINE_EXCLUDE_RADIUS for hvorfor vi ikke udelukker hele event-spaend).
    er_start = np.zeros(n_det, dtype=bool)
    r = BASELINE_EXCLUDE_RADIUS
    for e in sw + fw:
        er_start[max(0, e.start_idx - r):e.start_idx + r + 1] = True
    kandidater = np.flatnonzero(~er_start)
    kandidater = kandidater[kandidater >= WARMUP_BARS_DET]

    rng = np.random.default_rng(BASELINE_SEED)
    n_take = min(BASELINE_N, len(kandidater))
    valgte = np.sort(rng.choice(kandidater, size=n_take, replace=False))
    print(f"\nBaseline: {n_take:,} tilfaeldige ikke-start barer "
          f"(ud af {len(kandidater):,} mulige) …")
    bl = build_baseline_rows(valgte, views, det_index, det_close_ns,
                             bars_since_roll)
    bl = order_columns(bl, PUNKTER_BASELINE)

    # --- Skriv output ------------------------------------------------------
    p_ev_pq = OUT_DIR / "store_bevaegelser_events.parquet"
    p_ev_csv = OUT_DIR / "store_bevaegelser_events.csv"
    p_bl_pq = OUT_DIR / "store_bevaegelser_baseline.parquet"
    p_bl_csv = OUT_DIR / "store_bevaegelser_baseline.csv"
    p_md = OUT_DIR / "store_bevaegelser_sammenligning.md"
    p_tv_md = OUT_DIR / "store_bevaegelser_tv_kontrol.md"
    p_tv_csv = OUT_DIR / "store_bevaegelser_tv_kontrol.csv"

    ev.to_parquet(p_ev_pq, index=False)
    bl.to_parquet(p_bl_pq, index=False)
    # CSV til hurtigt kig: tz-bevidste tidsstempler skrives som ISO med offset.
    ev.to_csv(p_ev_csv, index=False)
    bl.to_csv(p_bl_csv, index=False)

    cmp_res = sammenlign_metoder(sw, fw)
    md = skriv_sammenligning(ev, sw, fw, cmp_res, det_index,
                             n_det - WARMUP_BARS_DET, len(bl), roll_info)
    p_md.write_text(md, encoding="utf-8")

    tv_md, tv_df = byg_tv_kontrol(views, det_index, det_close_ns, n_det)
    p_tv_md.write_text(tv_md, encoding="utf-8")
    tv_df.to_csv(p_tv_csv, index=False)

    print("\nSkrevet til", OUT_DIR)
    for p in (p_ev_pq, p_ev_csv, p_bl_pq, p_bl_csv, p_md, p_tv_md, p_tv_csv):
        print(f"  {p.name:<42} {p.stat().st_size / 1e6:8.2f} MB")

    # --- Kort terminal-opsummering ----------------------------------------
    print("\n── Overlap ────────────────────────────────────────────────────")
    print(f"  Metode A matchet af B: {cmp_res['sw_matched']:,} / {len(sw):,} "
          f"({100 * cmp_res['sw_matched'] / max(len(sw), 1):.1f} %)")
    print(f"  Metode B matchet af A: {cmp_res['fw_matched']:,} / {len(fw):,} "
          f"({100 * cmp_res['fw_matched'] / max(len(fw), 1):.1f} %)")
    print("\n── Prik-fordeling ved START (15m) ─────────────────────────────")
    print(ev.groupby("metode")["dot_type_15m_start"].value_counts().to_string())


if __name__ == "__main__":
    main()
