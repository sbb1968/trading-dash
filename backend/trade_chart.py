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
EFTER exit. Markoerer: PRAECIS groen pil ved entry, PRAECIS roed pil ved exit. Plus
stop/target-linjer hvor de findes, og et markeret holde-interval.

Rent visnings-/diagnoselag: roerer IKKE handelsstien. Read-only mod journalen + IBKR-
bar-genhentning. Koeres paa en WORKSTATION med TWS forbundet (bars er konto-uafhaengige).

FIDELITY-KERNEN — BAR_PARAMS_BY_SOURCE er verificeret mod hver algos faktiske
reqHistoricalData-kald (algo_buythedip/_confluence2/_trendjoin/_europa_reversion +
strategy_base._fetch_bars + ibkr_connect.get_historical_bars). Aendr KUN efter ny
verifikation mod algoen — mismatch her = IBRX-problemet igen.
"""
from __future__ import annotations

import io
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz

import matplotlib
matplotlib.use("Agg")   # ingen skaerm — server-side PNG
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

import trade_queries

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")
DK = pytz.timezone("Europe/Copenhagen")


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
    """Spejler strategy_base._parse_bar's tz-haandtering: naive bars lokaliseres til ET,
    tz-aware konverteres til ET (IBKR intraday-bars i TWS-tidszonen)."""
    if raw_dt is None:
        return None
    if isinstance(raw_dt, str):
        try:
            raw_dt = datetime.fromisoformat(raw_dt)
        except ValueError:
            return None
    if not isinstance(raw_dt, datetime):
        # date (daglige bars) — ikke relevant for intraday-chart
        return None
    return ET.localize(raw_dt) if raw_dt.tzinfo is None else raw_dt.astimezone(ET)


def _duration_str(seconds: float) -> str:
    """IBKR durationStr: sekunder hvis <= 86400, ellers dage (afrundet op + buffer)."""
    secs = int(seconds) + 120   # lille buffer
    if secs <= 86400:
        return f"{secs} S"
    return f"{math.ceil(secs / 86400) + 1} D"


# ═══════════════════════════════════════════════════════════════════
# Bar-genhentning — PRAECIS algoens parametre + endDateTime ved exit
# ═══════════════════════════════════════════════════════════════════
async def fetch_trade_bars(conn, symbol: str, source: str,
                           entry_dt_utc: datetime, exit_dt_utc: datetime,
                           bars_before: int = 40, bars_after: int = 40) -> pd.DataFrame:
    """Genhent bars for handels-vinduet med algoens egne bar-parametre.

    Kvalificerer kontrakten SOM algoen (conn._resolve_contract — samme helper
    get_historical_bars bruger), og kalder reqHistoricalDataAsync DIREKTE saa vi kan
    saette endDateTime (get_historical_bars hardkoder ""=nu, ubrugeligt for historik).

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
        contract = await conn._resolve_contract(symbol)
        raw = await conn.ib.reqHistoricalDataAsync(
            contract,
            endDateTime    = end_dt,           # tz-aware UTC — ib_async formaterer
            durationStr    = duration,
            barSizeSetting = p["bar_size"],
            whatToShow     = p["what_to_show"],
            useRTH         = p["use_rth"],
            formatDate     = 1,
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

    # Find entry-/exit-bar ved naermeste indeks, og skaer til vinduet.
    entry_et = entry_dt_utc.astimezone(ET)
    exit_et = exit_dt_utc.astimezone(ET)
    entry_idx = df.index.get_indexer([entry_et], method="nearest")[0]
    exit_idx = df.index.get_indexer([exit_et], method="nearest")[0]
    lo = max(0, entry_idx - bars_before)
    hi = min(len(df), exit_idx + bars_after + 1)
    return df.iloc[lo:hi]


# ═══════════════════════════════════════════════════════════════════
# Rendering — candlestick + PRAECIS groen entry-pil / roed exit-pil
# ═══════════════════════════════════════════════════════════════════
def _nearest_pos(index, target_et) -> int:
    return int(index.get_indexer([target_et], method="nearest")[0])


def render_trade_png(df: pd.DataFrame, trade: dict) -> bytes:
    """Tegn candlestick-chart for handlen og returnér PNG-bytes.

    - PRAECIS GROEN pil (^) ved (entry-bar, entry_price)
    - PRAECIS ROED pil (v) ved (exit-bar, exit_price)
    - markeret holde-interval mellem entry og exit
    - stop/target-linjer hvor de findes
    - tidsakse i DANSK tid (CET/CEST); ET noteres i titlen
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
    entry_pos = _nearest_pos(df.index, entry_et)
    exit_pos = _nearest_pos(df.index, exit_et)

    # Bredden skaleres efter antal bars -> KONSTANT candle-bredde uanset handelsvarighed.
    # (En 5-timers hold blev "gnidret" naar ~400 1-min bars blev presset ind paa samme
    # bredde som en 18-min handel.) Frontend fylder hoejden og scroller vandret for lange
    # handler; korte centreres. ~0.11 in/bar @130dpi ≈ 14 px/bar.
    width_in = max(14.0, min(60.0, n * 0.11))
    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(width_in, 9), sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.06})

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

    # ── Stop / target-linjer hvor de findes ──
    if isinstance(stop, (int, float)) and stop:
        ax.axhline(stop, color="#d32f2f", linestyle="--", linewidth=1.2, alpha=0.8, zorder=4)
        ax.text(n - 0.5, stop, f" stop {stop:.2f}", color="#d32f2f", va="center",
                ha="left", fontsize=12)
    if isinstance(target, (int, float)) and target:
        ax.axhline(target, color="#2e7d32", linestyle="--", linewidth=1.2, alpha=0.8, zorder=4)
        ax.text(n - 0.5, target, f" target {target:.2f}", color="#2e7d32", va="center",
                ha="left", fontsize=12)

    # ── PRAECIS groen entry-pil + roed exit-pil (paa eksakt fill-pris) ──
    if isinstance(entry_price, (int, float)):
        ax.annotate("", xy=(entry_pos, entry_price),
                    xytext=(entry_pos, entry_price - (df["High"].max() - df["Low"].min()) * 0.07),
                    arrowprops=dict(arrowstyle="-|>", color="#00c853", lw=2.4), zorder=6)
        ax.scatter([entry_pos], [entry_price], marker="^", s=170, color="#00c853",
                   edgecolors="black", linewidths=0.6, zorder=7, label=f"entry {entry_price:.2f}")
    if isinstance(exit_price, (int, float)):
        ax.annotate("", xy=(exit_pos, exit_price),
                    xytext=(exit_pos, exit_price + (df["High"].max() - df["Low"].min()) * 0.07),
                    arrowprops=dict(arrowstyle="-|>", color="#d50000", lw=2.4), zorder=6)
        ax.scatter([exit_pos], [exit_price], marker="v", s=170, color="#d50000",
                   edgecolors="black", linewidths=0.6, zorder=7, label=f"exit {exit_price:.2f}")

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
    fig.suptitle(
        f"{symbol} · {source} · {side} · P&L {pnl_s} · {exit_reason} · {dato}",
        fontsize=17, fontweight="bold", y=0.98)
    ax.set_title(
        f"{p['bar_size']} {p['what_to_show']} useRTH={p['use_rth']}  ·  "
        f"entry {entry_et.strftime('%H:%M')} ET / {entry_et.astimezone(DK).strftime('%H:%M')} DK  →  "
        f"exit {exit_et.strftime('%H:%M')} ET / {exit_et.astimezone(DK).strftime('%H:%M')} DK  ·  "
        f"x-akse i dansk tid",
        fontsize=12, color="#555", pad=10)

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
    df = await fetch_trade_bars(conn, trade["symbol"], trade.get("source", ""),
                                entry_dt, exit_dt, bars_before, bars_after)
    if df.empty:
        return None
    return render_trade_png(df, trade)


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
    df = await fetch_trade_bars(conn, trade["symbol"], trade.get("source", ""),
                                entry_dt, exit_dt, bars_before, bars_after)
    if df.empty:
        return None
    bars = [{
        "time": int(ts.astimezone(timezone.utc).timestamp()),   # UTC-epoch (Lightweight Charts)
        "open": round(df["Open"].iloc[i], 4), "high": round(df["High"].iloc[i], 4),
        "low": round(df["Low"].iloc[i], 4), "close": round(df["Close"].iloc[i], 4),
        "volume": int(df["Volume"].iloc[i]),
    } for i, ts in enumerate(df.index)]
    return {
        "bars": bars,
        "entry": {"time": int(entry_dt.timestamp()), "price": trade.get("entry_price")},
        "exit":  {"time": int(exit_dt.timestamp()),  "price": trade.get("exit_price")},
        "levels": {"stop": trade.get("current_stop"), "target": trade.get("current_target")},
        "meta": {"symbol": trade.get("symbol"), "source": trade.get("source"),
                 "side": trade.get("side"), "pnl": trade.get("pnl"),
                 "exit_reason": trade.get("exit_reason")},
    }
