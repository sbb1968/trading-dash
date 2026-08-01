#!/usr/bin/env python3
"""
trade_chart.py
──────────────
Handels-chart STAGE B — server-genereret PNG pr. lukket handel for ALLE fire algoer.

For hver handel genskaber vi chartet algoen FAKTISK handlede paa: vi henter bars fra
IBKR med PRAECIS de samme bar-parametre algoen selv bruger (whatToShow/useRTH/barSize +
samme kontrakt-kvalificering). Saa chartet ER pr. definition algoens kontekst — ikke et
eksternt TradingView/TWS-approksimat (loeser IBRX-klassen af tvivl).

Vinduet: bars_before (default 40) FOER entry · selve handlen · bars_after (default 40)
EFTER exit. Plus stop/target-linjer hvor de findes, og et markeret holde-interval.

Rent visnings-/diagnoselag: roerer IKKE handelsstien. Read-only mod journalen + IBKR-
bar-genhentning. Koeres paa en WORKSTATION med TWS forbundet (bars er konto-uafhaengige).

FIDELITY-KERNEN — BAR_PARAMS_BY_SOURCE er verificeret mod hver algos faktiske
reqHistoricalData-kald (algo_buythedip/_confluence2/_trendjoin/_europa_reversion +
strategy_base._fetch_bars + ibkr_connect.get_historical_bars). Aendr KUN efter ny
verifikation mod algoen — mismatch her = IBRX-problemet igen.

PROVENANCE — snapshot-first: naar algoen gemte de bars den faktisk evaluerede i
close-payloadet (chart_bars), tegnes PRAECIS dem (ground truth, immun mod IBKR-revision/
sletning/roll). Ellers gen-hentes bars (rekonstruktion). KILDE-badge viser hvilken.

PRAECISION (fase 1):
  - Markoerer snapper til den bar fillet SKETE i (gulv paa bar-start), ikke naermeste bar.
  - formatDate=2 (epoch UTC) — chartet afhaenger ikke af TWS' tidszone-indstilling.
  - Entry: groen linje fra venstre y-akse -> prik paa entry-baren + prisskilt paa aksen.
    Exit:  magenta linje fra hoejre y-akse -> prik paa exit-baren + prisskilt paa aksen.
    y = eksakt fill-pris; afstand til candlen = slippage.
  - Baand: én kilde (_resolve_bands) for PNG og JSON; genberegnede baand maerkes "(genb.)".
"""
from __future__ import annotations

import io
import logging
import math
from statistics import pstdev
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz

import matplotlib
matplotlib.use("Agg")   # ingen skaerm — server-side PNG
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

import trade_queries

# Europa-reversions z-score-parametre — til at GENBEREGNE baand for aeldre handler der
# mangler de gemte payload-baand (foer baand-logningen). Matcher rule.py 1:1 (pstdev).
try:
    from strategies.europa_reversion.config import LOOKBACK as EUREV_LOOKBACK, ENTRY_Z as EUREV_ENTRY_Z
except Exception:   # pragma: no cover — fald tilbage til de kendte defaults
    EUREV_LOOKBACK, EUREV_ENTRY_Z = 30, 2.0

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")
DK = pytz.timezone("Europe/Copenhagen")

# ── Markoer-farver (entry/exit). Én sandhedskilde. ──
ENTRY_GREEN  = "#00b050"   # tydelig groen — entry-linje, prik, prisskilt
EXIT_MAGENTA = "#e6007e"   # magenta      — exit-linje, prik, prisskilt


# ── FIDELITY-KERNEN: bar-parametre pr. journal-source (= algoens self.name) ──
#    Verificeret 2026-06-30 mod algoernes reqHistoricalData-kald:
#    - BuyTheDip / Konfluens 2: strategy_base._fetch_bars(1 min, TRADES), use_rth=None
#      -> get_historical_bars defaulter aktier til useRTH=True (RTH).
#    - Trend Join Long: _fetch_bars(5 mins, TRADES), use_rth=None -> RTH.
#    - Europa-reversion: futures (MES m.fl.), 15 mins, TRADES, useRTH=False
#      (europaeisk session 02-08 ET ligger UDEN for US RTH).
BAR_PARAMS_BY_SOURCE: dict[str, dict] = {
    "BuyTheDip":        {"what_to_show": "TRADES", "use_rth": True,  "bar_size": "1 min",   "bar_minutes": 1},
    "Konfluens 2":      {"what_to_show": "TRADES", "use_rth": True,  "bar_size": "1 min",   "bar_minutes": 1},
    "Trend Join Long":  {"what_to_show": "TRADES", "use_rth": True,  "bar_size": "5 mins",  "bar_minutes": 5},
    "Europa-reversion": {"what_to_show": "TRADES", "use_rth": False, "bar_size": "15 mins", "bar_minutes": 15},
}
# Fallback for ukendt/aeldre source — 1-min RTH TRADES (de fleste aktie-algoer).
_DEFAULT_PARAMS = {"what_to_show": "TRADES", "use_rth": True, "bar_size": "1 min", "bar_minutes": 1}


def params_for(source: str) -> dict:
    return BAR_PARAMS_BY_SOURCE.get(source, _DEFAULT_PARAMS)


# ═══════════════════════════════════════════════════════════════════
# Journal-laesning (GENBRUG trade_queries — samme sti som strategi-rapporten)
# ═══════════════════════════════════════════════════════════════════
async def load_closed_trades(db, *, start: Optional[str] = None, end: Optional[str] = None,
                             sources: Optional[list[str]] = None) -> list[dict]:
    """Lukkede handler (exit_time_utc IS NOT NULL) for trade-listen i vinduet.

    Returnerer en let liste (ikke fuld payload) sorteret nyeste foerst. Filtrér paa
    kilde-liste i Python (list_trades tager én source ad gangen)."""
    rows = await trade_queries.list_trades(db, date_from=start, date_to=end,
                                           status="closed", limit=1000)
    src_set = set(sources) if sources else None
    out = []
    for t in rows:
        if src_set is not None and t.get("source") not in src_set:
            continue
        out.append({
            "trade_id":    t.get("trade_id"),
            "symbol":      t.get("symbol"),
            "source":      t.get("source"),
            "side":        t.get("side"),
            "entry_time_utc": t.get("entry_time_utc"),
            "entry_time_et":  t.get("entry_time_et"),
            "exit_time_utc":  t.get("exit_time_utc"),
            "exit_time_et":   t.get("exit_time_et"),
            "entry_price": t.get("entry_price"),
            "exit_price":  t.get("exit_price"),
            "pnl":         t.get("pnl"),
            "exit_reason": t.get("exit_reason"),
            "current_stop":   t.get("current_stop"),
            "current_target": t.get("current_target"),
            # Kontekst til trade-listen: gør det muligt at læse en handel uden at
            # klikke den op. entry_reason bærer strategiens egen begrundelse
            # (K2: "score=2, bricks=VBGEK··"), som er dét man sammenligner på.
            "shares":       t.get("shares"),
            "pnl_pct":      t.get("pnl_pct"),
            "variant":      t.get("variant"),
            "entry_reason": t.get("entry_reason"),
        })
    return out


def _parse_iso_utc(s: Optional[str]) -> Optional[datetime]:
    """ISO-string (journalens isoformat, tz-aware UTC) -> tz-aware UTC datetime."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _bar_ts_to_et(raw_dt) -> Optional[datetime]:
    """Bar-timestamp -> tz-aware ET.

    Med formatDate=2 leverer IBKR entydige timestamps (epoch-sekunder eller tz-aware
    UTC-datetimes, afhaengigt af ib_async-version). Begge konverteres direkte. Snapshot-
    stien leverer ISO-strenge (tz-aware ET) — de gaar gennem fromisoformat-grenen. Naive
    datetimes BOER ikke forekomme laengere; sker det, lokaliserer vi til ET som foer, men
    logger en advarsel — for saa afhaenger chartet af TWS' tidszone-indstilling.
    """
    if raw_dt is None:
        return None
    if isinstance(raw_dt, (int, float)):
        return datetime.fromtimestamp(float(raw_dt), tz=timezone.utc).astimezone(ET)
    if isinstance(raw_dt, str):
        try:
            raw_dt = datetime.fromisoformat(raw_dt)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(raw_dt), tz=timezone.utc).astimezone(ET)
            except ValueError:
                return None
    if not isinstance(raw_dt, datetime):
        # date (daglige bars) — ikke relevant for intraday-chart
        return None
    if raw_dt.tzinfo is None:
        logger.warning("[trade_chart] naiv bar-timestamp trods formatDate=2 — lokaliseres "
                       "til ET. Verificér TWS' tidszone-indstilling.")
        return ET.localize(raw_dt)
    return raw_dt.astimezone(ET)


def _duration_str(seconds: float) -> str:
    """IBKR durationStr: sekunder hvis <= 86400, ellers dage (afrundet op + buffer)."""
    secs = int(seconds) + 120   # lille buffer
    if secs <= 86400:
        return f"{secs} S"
    return f"{math.ceil(secs / 86400) + 1} D"


def _bar_pos(index, target_et: datetime) -> int:
    """Position paa den bar der INDEHOLDER target_et.

    IBKR-bar-timestamps er barens START-tid. Baren der indeholder et fill er derfor den
    SIDSTE bar hvis start <= fill-tid — IKKE den naermeste. (Naermeste snappede et fill
    kl. 15:40:50 til 15:41-baren: en bar der ikke var begyndt da vi fyldte. Paa 15-min
    bars kunne fejlen vaere 7,5 min = forkert candle.)
    """
    pos = int(index.searchsorted(target_et, side="right")) - 1
    return max(0, min(pos, len(index) - 1))


def _trim_to_window(df: pd.DataFrame, entry_dt_utc: datetime, exit_dt_utc: datetime,
                    bars_before: int, bars_after: int) -> pd.DataFrame:
    """Skaer en ET-indekseret OHLCV-DataFrame til [entry-bar - before, exit-bar + after]
    ved INDEKS (robust mod overnight-gaps). Delt af re-fetch og snapshot-stien. Bruger
    gulv-snap (_bar_pos) saa vinduet centreres om den bar fillet SKETE i."""
    if df.empty:
        return df
    entry_idx = _bar_pos(df.index, entry_dt_utc.astimezone(ET))
    exit_idx = _bar_pos(df.index, exit_dt_utc.astimezone(ET))
    lo = max(0, entry_idx - bars_before)
    hi = min(len(df), exit_idx + bars_after + 1)
    return df.iloc[lo:hi]


def bars_from_snapshot(trade: dict) -> pd.DataFrame:
    """Byg OHLCV-DataFrame fra det oejebliksbillede algoen gemte ved close
    (payload['chart_bars'] = [ts_iso, o, h, l, c, v]).

    Dette er GROUND TRUTH — de faerdige bars algoen faktisk evaluerede — og
    kraever INGEN gen-hentning fra IBKR: immun over for historik-revisioner,
    sletning af udloebne futures og kontrakt-roll. Tom DataFrame hvis intet
    snapshot findes (aeldre handler foer snapshot-logningen -> kalderen
    falder tilbage til re-fetch)."""
    payload = trade.get("payload") or {}
    raw = payload.get("chart_bars") or []
    rows = []
    for item in raw:
        try:
            ts = _bar_ts_to_et(item[0])        # tz-aware ET
            if ts is None:
                continue
            rows.append({"dt_et": ts, "Open": float(item[1]), "High": float(item[2]),
                         "Low": float(item[3]), "Close": float(item[4]),
                         "Volume": float(item[5]) if len(item) > 5 else 0.0})
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("dt_et").sort_index()


# ═══════════════════════════════════════════════════════════════════
# Bar-genhentning — PRAECIS algoens parametre + endDateTime ved exit
# ═══════════════════════════════════════════════════════════════════
async def fetch_trade_bars(conn, symbol: str, source: str,
                           entry_dt_utc: datetime, exit_dt_utc: datetime,
                           bars_before: int = 40, bars_after: int = 40) -> pd.DataFrame:
    """Genhent bars for handels-vinduet med algoens egne bar-parametre.

    Kvalificerer kontrakten pr. HANDELSDATOEN (conn.resolve_contract_asof) saa
    futures-candles matcher den kontrakt-maaned handlen faktisk laa paa (ikke
    dagens front-maaned efter en roll), og kalder reqHistoricalDataAsync DIREKTE
    saa vi kan saette endDateTime (get_historical_bars hardkoder ""=nu, ubrugeligt
    for historik).

    Henter et rundhaandet vindue der ender lidt efter exit, parser til en ET-indekseret
    OHLCV-DataFrame, og skaerer til [entry-bar - bars_before, exit-bar + bars_after] ved
    INDEX (robust mod overnight-gaps i RTH). Tom DataFrame ved fejl/ingen data."""
    if conn is None or not getattr(conn, "connected", False):
        return pd.DataFrame()
    p = params_for(source)
    bar_min = p["bar_minutes"]

    # Vindue: fra (entry - before) til (exit + after), + buffer. endDateTime = exit + buffer.
    end_dt = exit_dt_utc + timedelta(minutes=bar_min * (bars_after + 3))
    span_start = entry_dt_utc - timedelta(minutes=bar_min * (bars_before + 3))
    duration = _duration_str((end_dt - span_start).total_seconds())

    try:
        # Futures roller: en handel fra fx 16. juni laa paa juni-kontrakten, men
        # dagens front-maaned er september (anden pris-serie). resolve_contract_asof
        # kvalificerer den kontrakt der var front-maaned PAA handelsdatoen, saa
        # candlesticks matcher entry/exit-priserne. Aktier: uaendret.
        contract = await conn.resolve_contract_asof(symbol, entry_dt_utc)
        raw = await conn.ib.reqHistoricalDataAsync(
            contract,
            endDateTime    = end_dt,           # tz-aware UTC — ib_async formaterer
            durationStr    = duration,
            barSizeSetting = p["bar_size"],
            whatToShow     = p["what_to_show"],
            useRTH         = p["use_rth"],
            formatDate     = 2,          # epoch UTC — uafhaengigt af TWS' tidszone
        )
    except Exception as e:
        logger.error(f"[trade_chart] bar-genhentning fejlede {symbol}/{source}: {e}")
        return pd.DataFrame()

    rows = []
    for b in (raw or []):
        ts = _bar_ts_to_et(b.date)
        if ts is None:
            continue
        rows.append({"dt_et": ts, "Open": float(b.open), "High": float(b.high),
                     "Low": float(b.low), "Close": float(b.close),
                     "Volume": float(b.volume) if b.volume else 0.0})
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("dt_et").sort_index()
    # Find entry-/exit-bar (baren fillet SKETE i), og skaer til vinduet.
    return _trim_to_window(df, entry_dt_utc, exit_dt_utc, bars_before, bars_after)


# ═══════════════════════════════════════════════════════════════════
# Baand — én sandhedskilde for PNG og JSON
# ═══════════════════════════════════════════════════════════════════
def _resolve_bands(df: pd.DataFrame, trade: dict, entry_pos: int):
    """(mean, upper, lower, recomputed) — payload-baand hvis de findes, ellers genberegnet
    for Europa-reversion med rule.py's formel (population-std over de LOOKBACK closes der
    slutter paa signal-baren = baren FOER fill). Én sandhedskilde for baade PNG og JSON.

    NB: genberegning er en APPROKSIMATION. Nye handler logger baandene i payload ved entry
    (ground truth); for dem returneres recomputed=False.
    """
    payload = trade.get("payload") or {}
    mean = payload.get("mean")
    upper = payload.get("upper_band")
    lower = payload.get("lower_band")
    if mean is not None:
        return mean, upper, lower, False

    if trade.get("source") == "Europa-reversion" and entry_pos >= EUREV_LOOKBACK:
        window = [float(c) for c in df["Close"].iloc[entry_pos - EUREV_LOOKBACK:entry_pos]]
        if len(window) >= 2:
            ma = sum(window) / len(window)
            sd = pstdev(window)
            if sd > 0:
                return ma, ma + EUREV_ENTRY_Z * sd, ma - EUREV_ENTRY_Z * sd, True
    return None, None, None, False


def _step_series(traj, idx, value_i: int, entry_pos: int, exit_pos: int):
    """Byg (xs, ys) til en steps-post-linje fra stop_trajectory ([ts_iso, stop, target]).

    value_i=1 -> stop, value_i=2 -> target. Hvert punkt placeres paa den bar det gjaldt fra
    (gulv-snap), og sidste vaerdi forlaenges til exit-baren saa linjen spaender hele holdet.
    Et trailing stop bliver dermed en TRAPPE (som det faktisk var), ikke én flad linje.
    None hvis trajektorien er tom / mangler den vaerdi (kalderen tegner flad fallback).
    """
    pts = []
    for item in (traj or []):
        try:
            ts = _bar_ts_to_et(item[0])
            v = item[value_i] if len(item) > value_i else None
            if ts is None or not isinstance(v, (int, float)):
                continue
            pts.append((_bar_pos(idx, ts), float(v)))
        except Exception:
            continue
    if not pts:
        return None
    pts.sort(key=lambda p: p[0])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if xs[-1] < exit_pos:      # forlaeng sidste niveau til exit-baren
        xs.append(exit_pos)
        ys.append(ys[-1])
    return xs, ys


# ═══════════════════════════════════════════════════════════════════
# Rendering — candlestick + PRAECISE entry/exit-markoerer (linje + prik + prisskilt)
# ═══════════════════════════════════════════════════════════════════
def render_trade_png(df: pd.DataFrame, trade: dict, provenance: str = "refetch") -> bytes:
    """Tegn candlestick-chart for handlen og returnér PNG-bytes.

    - Entry: GROEN linje fra venstre y-akse -> prik paa entry-baren + prisskilt paa aksen
    - Exit:  MAGENTA linje fra hoejre y-akse -> prik paa exit-baren + prisskilt paa aksen
    - markeret holde-interval mellem entry og exit
    - stop/target-linjer hvor de findes
    - tidsakse i DANSK tid (CET/CEST); ET noteres i titlen

    provenance: "snapshot" = de bars algoen faktisk evaluerede (ground truth) /
    "refetch" = gen-hentet fra IBKR-historik (rekonstrueret) — vises i undertitlen.
    """
    symbol = trade.get("symbol", "?")
    source = trade.get("source", "?")
    side = (trade.get("side") or "").upper()
    pnl = trade.get("pnl")
    exit_reason = trade.get("exit_reason") or "?"
    entry_price = trade.get("entry_price")
    exit_price = trade.get("exit_price")
    stop = trade.get("current_stop")
    target = trade.get("current_target")

    entry_et = (_parse_iso_utc(trade.get("entry_time_utc")) or df.index[0].astimezone(timezone.utc)).astimezone(ET)
    exit_et = (_parse_iso_utc(trade.get("exit_time_utc")) or df.index[-1].astimezone(timezone.utc)).astimezone(ET)

    # X-akse i dansk tid (positionel: candlesticks tegnes paa heltal 0..N-1 saa gaps lukkes)
    idx_dk = [ts.astimezone(DK) for ts in df.index]
    n = len(df)
    x = list(range(n))
    entry_pos = _bar_pos(df.index, entry_et)
    exit_pos = _bar_pos(df.index, exit_et)

    # Faste x-graenser: markoer-linjerne skal ramme akserne PRAECIS.
    x_lo, x_hi = -0.5, n - 0.5

    # Bredden skaleres efter antal bars -> KONSTANT candle-bredde uanset handelsvarighed.
    # (En 5-timers hold blev "gnidret" naar ~400 1-min bars blev presset ind paa samme
    # bredde som en 18-min handel.) Frontend fylder hoejden og scroller vandret for lange
    # handler; korte centreres. ~0.11 in/bar @130dpi ≈ 14 px/bar.
    width_in = max(14.0, min(60.0, n * 0.11))
    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(width_in, 9), sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.06})
    ax.set_xlim(x_lo, x_hi)

    # ── Candlesticks (ren matplotlib — ingen ekstra dep) ──
    width = 0.6
    for i in range(n):
        o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
        up = c >= o
        col = "#26a69a" if up else "#ef5350"
        ax.plot([i, i], [l, h], color=col, linewidth=0.8, zorder=2)
        ax.add_patch(plt.Rectangle((i - width / 2, min(o, c)), width, max(abs(c - o), 1e-9),
                                   facecolor=col, edgecolor=col, zorder=3))
        axv.bar(i, df["Volume"].iloc[i], width=width, color=col, alpha=0.5, zorder=2)

    # ── Holde-interval (markeret) ──
    ax.axvspan(entry_pos, exit_pos, color="#90caf9", alpha=0.12, zorder=1,
               label="holde-interval")

    # ── Stop / target som STEP-linje (labels ind fra hoejre — ellers kolliderer de med
    #    exit-prisskiltet). Har handlen en stop_trajectory (nye handler), tegnes trappen
    #    som stoppet faktisk flyttede sig; ellers flad linje paa slutvaerdien (gamle handler).
    traj = (trade.get("payload") or {}).get("stop_trajectory") or []
    stop_steps = _step_series(traj, df.index, 1, entry_pos, exit_pos)
    tgt_steps  = _step_series(traj, df.index, 2, entry_pos, exit_pos)

    if stop_steps:
        xs, ys = stop_steps
        ax.step(xs, ys, where="post", color="#d32f2f", linestyle="--", linewidth=1.4,
                alpha=0.85, zorder=4)
        ax.text(x_hi - 0.4, ys[-1], f"stop {ys[-1]:.2f} ", color="#d32f2f", va="bottom",
                ha="right", fontsize=12, zorder=5)
    elif isinstance(stop, (int, float)) and stop:
        ax.axhline(stop, color="#d32f2f", linestyle="--", linewidth=1.2, alpha=0.8, zorder=4)
        ax.text(x_hi - 0.4, stop, f"stop {stop:.2f} ", color="#d32f2f", va="bottom",
                ha="right", fontsize=12, zorder=5)

    if tgt_steps:
        xs, ys = tgt_steps
        ax.step(xs, ys, where="post", color="#2e7d32", linestyle="--", linewidth=1.4,
                alpha=0.85, zorder=4)
        ax.text(x_hi - 0.4, ys[-1], f"target {ys[-1]:.2f} ", color="#2e7d32", va="bottom",
                ha="right", fontsize=12, zorder=5)
    elif isinstance(target, (int, float)) and target:
        ax.axhline(target, color="#2e7d32", linestyle="--", linewidth=1.2, alpha=0.8, zorder=4)
        ax.text(x_hi - 0.4, target, f"target {target:.2f} ", color="#2e7d32", va="bottom",
                ha="right", fontsize=12, zorder=5)

    # ── Europa-reversion: midter- + ydre bånd (én kilde: _resolve_bands; PNG == JSON) ──
    band_mean, band_upper, band_lower, bands_recomputed = _resolve_bands(df, trade, entry_pos)
    _bbox = dict(facecolor="white", edgecolor="none", alpha=0.6, pad=1.0)
    _gb = " (genb.)" if bands_recomputed else ""
    if isinstance(band_mean, (int, float)):
        ax.axhline(band_mean, color="#1565c0", linestyle="-", linewidth=1.4, alpha=0.85, zorder=4)
        ax.text(0.4, band_mean, f"middel {band_mean:.2f}{_gb}", color="#1565c0", va="bottom",
                ha="left", fontsize=11, bbox=_bbox, zorder=5)
    if isinstance(band_upper, (int, float)):
        ax.axhline(band_upper, color="#6a1b9a", linestyle="--", linewidth=1.2, alpha=0.75, zorder=4)
        ax.text(0.4, band_upper, f"øvre bånd {band_upper:.2f}{_gb}", color="#6a1b9a", va="bottom",
                ha="left", fontsize=11, bbox=_bbox, zorder=5)
    if isinstance(band_lower, (int, float)):
        ax.axhline(band_lower, color="#6a1b9a", linestyle="--", linewidth=1.2, alpha=0.75, zorder=4)
        ax.text(0.4, band_lower, f"nedre bånd {band_lower:.2f}{_gb}", color="#6a1b9a", va="bottom",
                ha="left", fontsize=11, bbox=_bbox, zorder=5)

    # ── Entry/exit-markoerer: vandret linje fra aksen ind til den PRAECISE candle ──
    # Entry: GROEN linje fra VENSTRE y-akse -> prik paa entry-baren. Prisskilt paa aksen.
    # Exit:  MAGENTA linje fra HOEJRE y-akse -> prik paa exit-baren. Prisskilt paa aksen.
    # y = den EKSAKTE fill-pris. Ligger prikken uden for candlens krop/veger er det
    # SLIPPAGE — ikke en tegnefejl. Det skal kunne ses.
    if isinstance(entry_price, (int, float)):
        ax.plot([x_lo, entry_pos], [entry_price, entry_price],
                color=ENTRY_GREEN, linewidth=2.4, solid_capstyle="butt", zorder=6,
                label=f"entry {entry_price:.2f}")
        ax.plot([entry_pos], [entry_price], marker="o", markersize=9, linestyle="none",
                markerfacecolor=ENTRY_GREEN, markeredgecolor="black", markeredgewidth=0.7,
                zorder=8)
        ax.text(x_lo, entry_price, f" {entry_price:.2f} ", color="white", fontsize=11.5,
                fontweight="bold", va="center", ha="right", zorder=9, clip_on=False,
                bbox=dict(facecolor=ENTRY_GREEN, edgecolor="none", pad=2.0))

    if isinstance(exit_price, (int, float)):
        ax.plot([exit_pos, x_hi], [exit_price, exit_price],
                color=EXIT_MAGENTA, linewidth=2.4, solid_capstyle="butt", zorder=6,
                label=f"exit {exit_price:.2f}")
        ax.plot([exit_pos], [exit_price], marker="o", markersize=9, linestyle="none",
                markerfacecolor=EXIT_MAGENTA, markeredgecolor="black", markeredgewidth=0.7,
                zorder=8)
        ax.text(x_hi, exit_price, f" {exit_price:.2f} ", color="white", fontsize=11.5,
                fontweight="bold", va="center", ha="left", zorder=9, clip_on=False,
                bbox=dict(facecolor=EXIT_MAGENTA, edgecolor="none", pad=2.0))

    # ── Akser / labels (dansk tid) ──
    n_ticks = max(8, int(width_in * 1.2))   # flere ticks paa brede (scrollende) charts
    tick_step = max(1, n // n_ticks)
    ticks = list(range(0, n, tick_step))
    axv.set_xticks(ticks)
    axv.set_xticklabels([idx_dk[i].strftime("%H:%M") for i in ticks], rotation=0, fontsize=11)
    ax.tick_params(axis="y", labelsize=11)
    axv.tick_params(axis="y", labelsize=10)
    ax.set_ylabel("Pris ($)", fontsize=12)
    axv.set_ylabel("Volumen", fontsize=11)
    ax.grid(True, alpha=0.15, zorder=0)
    axv.grid(True, alpha=0.15, zorder=0)
    ax.legend(loc="best", fontsize=12, framealpha=0.85)

    pnl_s = f"${pnl:+,.2f}" if isinstance(pnl, (int, float)) else "?"
    dato = entry_et.strftime("%Y-%m-%d")
    p = params_for(source)
    if provenance == "snapshot":
        kilde, kilde_col = "øjebliksbillede (præcis)", "#2e7d32"
    else:
        kilde, kilde_col = "rekonstrueret", "#c62828"
    fig.suptitle(
        f"{symbol} · {source} · {side} · P&L {pnl_s} · {exit_reason} · {dato}",
        fontsize=17, fontweight="bold", y=0.98)
    ax.set_title(
        f"{p['bar_size']} {p['what_to_show']} useRTH={p['use_rth']}  ·  "
        f"entry {entry_et.strftime('%H:%M')} ET / {entry_et.astimezone(DK).strftime('%H:%M')} DK  →  "
        f"exit {exit_et.strftime('%H:%M')} ET / {exit_et.astimezone(DK).strftime('%H:%M')} DK  ·  "
        f"x-akse i dansk tid",
        fontsize=12, color="#555", pad=10)
    # KILDE-badge (oeverst til hoejre): saa vi ALDRIG forveksler ground truth med rekonstruktion.
    ax.text(0.995, 1.015, f"KILDE: {kilde}", transform=ax.transAxes, ha="right", va="bottom",
            fontsize=11, fontweight="bold", color=kilde_col)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════
# Koordinator — fra trade_id til PNG (bruges af endpointet)
# ═══════════════════════════════════════════════════════════════════
async def build_trade_png(db, conn, trade_id: str,
                          bars_before: int = 40, bars_after: int = 40) -> Optional[bytes]:
    """Hent handlen, genhent bars med fidelity, render PNG. None hvis handel/bars mangler."""
    trade = await trade_queries.get_trade_by_id(db, trade_id)
    if not trade:
        return None
    entry_dt = _parse_iso_utc(trade.get("entry_time_utc"))
    exit_dt = _parse_iso_utc(trade.get("exit_time_utc"))
    if not (entry_dt and exit_dt and trade.get("symbol")):
        return None
    # Snapshot-first: hvis algoen gemte de bars den faktisk evaluerede, tegner vi
    # PRAECIS dem (ground truth). Ellers falder vi tilbage til gen-hentning.
    df = bars_from_snapshot(trade)
    provenance = "snapshot"
    if df.empty:
        df = await fetch_trade_bars(conn, trade["symbol"], trade.get("source", ""),
                                    entry_dt, exit_dt, bars_before, bars_after)
        provenance = "refetch"
    else:
        df = _trim_to_window(df, entry_dt, exit_dt, bars_before, bars_after)
    if df.empty:
        return None
    return render_trade_png(df, trade, provenance=provenance)


async def build_trade_bars_json(db, conn, trade_id: str,
                                bars_before: int = 40, bars_after: int = 40) -> Optional[dict]:
    """Stage A: bars + markoerer + niveauer som JSON til Lightweight Charts."""
    trade = await trade_queries.get_trade_by_id(db, trade_id)
    if not trade:
        return None
    entry_dt = _parse_iso_utc(trade.get("entry_time_utc"))
    exit_dt = _parse_iso_utc(trade.get("exit_time_utc"))
    if not (entry_dt and exit_dt and trade.get("symbol")):
        return None
    # Snapshot-first (som PNG-stien): ground-truth bars naar de findes.
    df = bars_from_snapshot(trade)
    provenance = "snapshot"
    if df.empty:
        df = await fetch_trade_bars(conn, trade["symbol"], trade.get("source", ""),
                                    entry_dt, exit_dt, bars_before, bars_after)
        provenance = "refetch"
    else:
        df = _trim_to_window(df, entry_dt, exit_dt, bars_before, bars_after)
    if df.empty:
        return None
    bars = [{
        "time": int(ts.astimezone(timezone.utc).timestamp()),   # UTC-epoch (Lightweight Charts)
        "open": round(df["Open"].iloc[i], 4), "high": round(df["High"].iloc[i], 4),
        "low": round(df["Low"].iloc[i], 4), "close": round(df["Close"].iloc[i], 4),
        "volume": int(df["Volume"].iloc[i]),
    } for i, ts in enumerate(df.index)]
    # Baand fra SAMME kilde som PNG (_resolve_bands) — gulv-snappet entry-bar.
    entry_pos = _bar_pos(df.index, entry_dt.astimezone(ET))
    band_mean, band_upper, band_lower, bands_recomputed = _resolve_bands(df, trade, entry_pos)
    return {
        "bars": bars,
        "entry": {"time": int(entry_dt.timestamp()), "price": trade.get("entry_price")},
        "exit":  {"time": int(exit_dt.timestamp()),  "price": trade.get("exit_price")},
        "levels": {"stop": trade.get("current_stop"), "target": trade.get("current_target"),
                   "mean": band_mean, "upper_band": band_upper, "lower_band": band_lower},
        "stop_trajectory": (trade.get("payload") or {}).get("stop_trajectory") or [],
        "bands_recomputed": bands_recomputed,
        "meta": {"symbol": trade.get("symbol"), "source": trade.get("source"),
                 "side": trade.get("side"), "pnl": trade.get("pnl"),
                 "exit_reason": trade.get("exit_reason"),
                 "provenance": provenance},   # "snapshot"=praecis / "refetch"=rekonstrueret
    }
