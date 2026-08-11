#!/usr/bin/env python3
"""
trendjoin_shadow_eval.py — A/B: tilføjer nyhedskatalysator-gaten værdi? (OFFLINE)
══════════════════════════════════════════════════════════════════════════════════
Del B af SPEC_trendjoin_forensik. Bygger en MATCHET kontrolgruppe og sammenligner mod
dagens faktiske trades — så man over mange sessioner kan dømme om nyheds-gaten separerer:

  • Treatment = dagens faktiske entries (trade_forensics entry-snaps, source="Trend Join Long").
  • Control   = dagens nyheds-AFVISTE gappers (trendjoin_shadow_candidate-events) — bestod
                change>=3% + pris>=$3, fejlede KUN nyheds-gaten.

For HVER control-kandidat rekonstrueres samme mekanik som live (D2 SMA200, premarket-high,
D3/D1/I1/I2/I3 bar-for-bar fra scan-tid & 10:05 ET frem). Udfald måles med en LET proxy
(IKKE den fulde exit-stige): MFE_R, MAE_R, og om +0.75R blev nået FØR −1R. Samme tre tal
beregnes for treatment, så sammenligningen er æbler-til-æbler.

READ-ONLY mod handel: ingen ordrer, skriver intet til handels-state (kun sin egen summary).
Egen CLIENT_ID = 17 (må IKKE kollidere med backend — stop backend, ELLER skift --client-id).
Historik-pull sker EFTER luk, så det aldrig konkurrerer med live-strategiens feed.

⚠ Kendt drift-risiko: dette script reimplementerer entry-gates + R-proxyen og kan komme ud
  af sync med algo_trendjoin.py. v1 accepterer det; en senere refaktor bør dele de rene
  beslutnings-funktioner. Dom på nyheds-gaten kræver MANGE sessioner (~400 events), ikke én dag.

    python trendjoin_shadow_eval.py --verify     # læs BARE dagens events tilbage (intet IBKR)
    python trendjoin_shadow_eval.py              # fuld eval (kræver TWS; stop backend først)
    python trendjoin_shadow_eval.py --day 2026-06-27

Placering: C:\\Projects\\trading_dash\\backend\\trendjoin_shadow_eval.py
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import date, datetime, time as dtime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

SOURCE = "Trend Join Long"
HOST, PORT, CLIENT_ID = "127.0.0.1", 7497, 17

# Regel-parametre (SKAL matche algo_trendjoin.py — drift-risiko, jf. header)
MIN_GAP_PCT   = 3.0
RVOL_MIN      = 2.0
RVOL_LOOKBACK = 14
SMA_LEN       = 200
STOP_PCT      = 0.01
PARTIAL_R     = 0.75
ENTRY_LO      = 10 * 60 + 5    # 10:05 ET
ENTRY_HI      = 15 * 60 + 30   # 15:30 ET
RTH_OPEN      = 9 * 60 + 30
RTH_CLOSE     = 16 * 60


# ── Journal-læsning (read-only) ──────────────────────────────────────────────
def ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def pts(s):
    if not s:
        return None
    for cut in (None, 26, 19):
        try:
            return datetime.fromisoformat(str(s) if cut is None else str(s)[:cut])
        except ValueError:
            continue
    return None


def read_events(db, source, day):
    """Returnerer (control, treatment) for dagen. control = shadow-kandidat-dicts;
    treatment = entry-snap-dicts (phase=entry m. trendjoin.R)."""
    con = ro(db)
    rows = con.execute(
        "SELECT ts_local, ts_utc, event_type, payload_json FROM events "
        "WHERE source=? ORDER BY id", (source,)).fetchall()
    con.close()
    control, treatment = [], []
    for r in rows:
        t = pts(r["ts_local"]) or pts(r["ts_utc"])
        if not t or t.date() != day:
            continue
        try:
            pl = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except (json.JSONDecodeError, TypeError):
            pl = {}
        if not isinstance(pl, dict):
            continue
        if r["event_type"] == "trendjoin_shadow_candidate":
            control.append(pl)
        elif r["event_type"] == "trade_forensics":
            tj = pl.get("trendjoin") or {}
            if pl.get("phase") == "entry" and "R" in tj:   # entry-snap (ikke exit)
                treatment.append(pl)
    return control, treatment


# ── Bar-hjælpere ─────────────────────────────────────────────────────────────
def _bar_dt_et(b):
    d = b.date
    if isinstance(d, datetime):
        if d.tzinfo is not None and ET is not None:
            return d.astimezone(ET)
        return d.replace(tzinfo=ET) if (ET is not None and d.tzinfo is None) else d
    return d   # date (daglig bar)


def _tmin(dt):
    return dt.hour * 60 + dt.minute


async def fetch_daily(ib, contract, end_dt):
    from ib_async import util  # noqa
    bars = await ib.reqHistoricalDataAsync(
        contract, endDateTime=end_dt, durationStr="1 Y", barSizeSetting="1 day",
        whatToShow="TRADES", useRTH=True, formatDate=1)
    return bars or []


async def fetch_5m(ib, contract, end_dt):
    bars = await ib.reqHistoricalDataAsync(
        contract, endDateTime=end_dt, durationStr="3 D", barSizeSetting="5 mins",
        whatToShow="TRADES", useRTH=False, formatDate=1)
    return bars or []


async def reconstruct(ib, sym, day, end_dt):
    """Hent + udled konteksten for sym på `day`: prior_close/high, sma200, avg_day_vol,
    premarket_high, og RTH 5m-bars (sorteret). None hvis data mangler eller D2 fejler."""
    from ib_async import Stock
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(sym, "SMART", "USD")), timeout=15)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        contract = q[0] if (q and getattr(q[0], "conId", 0)) else None
    except Exception:
        contract = None
    if contract is None:
        return None, "ukvalificeret"

    daily = await fetch_daily(ib, contract, end_dt)
    prior = [b for b in daily if (_bar_dt_et(b).date() if isinstance(_bar_dt_et(b), datetime)
                                  else _bar_dt_et(b)) < day]
    if len(prior) < SMA_LEN + 1:
        return None, "for_faa_daglige"
    closes = [float(b.close) for b in prior]
    sma200 = sum(closes[-SMA_LEN:]) / SMA_LEN
    prior_close = closes[-1]
    prior_high = float(prior[-1].high)
    vols = [float(b.volume) for b in prior[-RVOL_LOOKBACK:] if b.volume]
    avg_day_vol = (sum(vols) / len(vols)) if vols else 0.0
    if not (prior_close > sma200):   # D2 (matched: treatment kræver også D2)
        return None, "d2_fejl"

    bars5 = await fetch_5m(ib, contract, end_dt)
    pm_high = -1.0
    rth = []
    for b in bars5:
        dt = _bar_dt_et(b)
        if not isinstance(dt, datetime) or dt.date() != day:
            continue
        tm = _tmin(dt)
        if tm < RTH_OPEN:
            pm_high = max(pm_high, float(b.high))
        elif RTH_OPEN <= tm < RTH_CLOSE:
            rth.append({"tmin": tm, "o": float(b.open), "h": float(b.high),
                        "l": float(b.low), "c": float(b.close), "v": float(b.volume or 0)})
    rth.sort(key=lambda x: x["tmin"])
    if len(rth) < 3:
        return None, "ingen_rth_bars"
    return {"prior_close": prior_close, "prior_high": prior_high, "sma200": sma200,
            "avg_day_vol": avg_day_vol, "premarket_high": pm_high, "rth": rth}, "ok"


def _rvol(rth, idx, avg_day_vol):
    if avg_day_vol <= 0:
        return None
    cum = sum(b["v"] for b in rth[:idx + 1])
    mins = rth[idx]["tmin"] - RTH_OPEN
    frac = max(min(mins / 390.0, 1.0), 1e-3)
    exp = avg_day_vol * frac
    return (cum / exp) if exp > 0 else None


def find_shadow_entry(ctx, from_tmin):
    """Walk RTH-bars; returnér entry-idx på første bar (>= from_tmin og i 10:05-15:30) hvor
    ALLE gates rammer (D3/D1/I1/I2/I3). None hvis ingen."""
    rth = ctx["rth"]
    pc, ph, pm = ctx["prior_close"], ctx["prior_high"], ctx["premarket_high"]
    hod = -1.0
    for i, b in enumerate(rth):
        t = b["tmin"]
        cond_window = (ENTRY_LO <= t <= ENTRY_HI) and (t >= from_tmin)
        new_hod = b["h"] > hod
        if cond_window:
            price = b["c"]
            ok = (pc > 0 and (price - pc) / pc * 100.0 >= MIN_GAP_PCT
                  and price > ph
                  and (pm <= 0 or price > pm)
                  and new_hod)
            if ok:
                rv = _rvol(rth, i, ctx["avg_day_vol"])
                if rv is None or rv >= RVOL_MIN:
                    return i
        hod = max(hod, b["h"])
    return None


def measure(ctx, entry_idx, entry_price):
    """Proxy-udfald fra entry: MFE_R, MAE_R, hit_partial_first (+0.75R før −1R)."""
    rth = ctx["rth"]
    lod = min(b["l"] for b in rth[:entry_idx + 1])
    stop = lod * (1 - STOP_PCT)
    R = entry_price - stop
    if R <= 0:
        return None
    after = rth[entry_idx + 1:]
    if not after:
        return {"R": R, "mfe_r": 0.0, "mae_r": 0.0, "hit_partial_first": None}
    mfe = max(b["h"] for b in after)
    mae = min(b["l"] for b in after)
    hit = None
    for b in after:
        if b["l"] <= entry_price - R:          # −1R (stop) ramt
            hit = False; break
        if b["h"] >= entry_price + PARTIAL_R * R:  # +0.75R ramt
            hit = True; break
    return {"R": R, "mfe_r": (mfe - entry_price) / R, "mae_r": (entry_price - mae) / R,
            "hit_partial_first": hit}


def _end_dt(day):
    """endDateTime til IBKR: '' for i dag (=nu), ellers dagens efter-luk i ET."""
    today = datetime.now(ET).date() if ET else date.today()
    if day >= today:
        return ""
    if ET is None:
        return f"{day:%Y%m%d} 20:00:00"
    return datetime(day.year, day.month, day.day, 20, 0, tzinfo=ET)


# ── --verify (intet IBKR) ────────────────────────────────────────────────────
def run_verify(db, source, day, emit):
    control, treatment = read_events(db, source, day)
    emit(f"\n  --verify · dag {day} · source='{source}' (ingen IBKR-pull)")
    emit(f"\n  CONTROL (nyheds-afviste shadow-kandidater): {len(control)}")
    for c in control:
        emit(f"     {c.get('symbol','?'):<6} change {c.get('change_pct',0):+.1f}%  "
             f"@ {c.get('scan_et','?')}  · {str(c.get('news_detail',''))[:60]}")
    emit(f"\n  TREATMENT (faktiske entries): {len(treatment)}")
    for t in treatment:
        tj = t.get("trendjoin") or {}
        emit(f"     {t.get('ticker','?'):<6} @ {t.get('time_et','?')}  "
             f"katalysator-alder {tj.get('catalyst_age_min','?')} min  "
             f"· {str(tj.get('catalyst',''))[:50]}")
    if not control and not treatment:
        emit("\n  (tomt — Del A har ikke fanget events for dagen endnu)")


# ── Aggregering + output ─────────────────────────────────────────────────────
def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def summarize(label, results, emit):
    entered = [r for r in results if r.get("entered")]
    n = len(results)
    ne = len(entered)
    emit(f"\n  ── {label}: {n} kandidater · {ne} ville trigge entry ──")
    if not entered:
        emit("     (ingen entries at måle)")
        return
    mfe = _med([r["mfe_r"] for r in entered])
    mae = _med([r["mae_r"] for r in entered])
    yes = sum(1 for r in entered if r["hit_partial_first"] is True)
    no = sum(1 for r in entered if r["hit_partial_first"] is False)
    neither = sum(1 for r in entered if r["hit_partial_first"] is None)
    p = (yes / (yes + no)) if (yes + no) else None
    emit(f"     median MFE_R {mfe:+.2f}" if mfe is not None else "     median MFE_R n/a")
    emit(f"     median MAE_R {mae:+.2f}" if mae is not None else "     median MAE_R n/a")
    emit(f"     +0.75R FØR −1R: {yes} ja / {no} nej / {neither} ingen af delene"
         + (f"  →  P(hit) = {p*100:.0f}%" if p is not None else ""))


async def run_full(db, source, day, args, emit):
    from ib_async import IB
    control, treatment = read_events(db, source, day)
    emit(f"\n  Dag {day}: {len(control)} control (shadow) · {len(treatment)} treatment (entries)")
    if not control and not treatment:
        emit("  (intet at evaluere — Del A har ikke fanget events. Kør efter en session.)")
        return

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde TWS (client-id {args.client_id}): {e}")
        emit("  -> stop backenden (client-id-konflikt) eller vælg en ledig --client-id.")
        return
    emit("  Forbundet til TWS (read-only historik).\n")
    end_dt = _end_dt(day)

    async def eval_one(sym, from_tmin, fixed_entry=None):
        ctx, why = await reconstruct(ib, sym, day, end_dt)
        if ctx is None:
            return {"sym": sym, "entered": False, "reason": why}
        if fixed_entry is None:
            idx = find_shadow_entry(ctx, from_tmin)
            if idx is None:
                return {"sym": sym, "entered": False, "reason": "ingen_trigger"}
            entry_price = ctx["rth"][idx]["c"]
        else:
            # treatment: find bar nærmest entry-tid, brug faktisk entry-pris
            etmin = fixed_entry["tmin"]
            idx = max((i for i, b in enumerate(ctx["rth"]) if b["tmin"] <= etmin), default=None)
            if idx is None:
                return {"sym": sym, "entered": False, "reason": "entry_bar_ikke_fundet"}
            entry_price = fixed_entry["price"]
        m = measure(ctx, idx, entry_price)
        if m is None:
            return {"sym": sym, "entered": False, "reason": "R<=0"}
        return {"sym": sym, "entered": True, "entry_price": entry_price, **m}

    ctrl_res, treat_res = [], []
    try:
        for c in control:
            sym = (c.get("symbol") or "").upper()
            scan_tmin = _scan_tmin(c.get("scan_et"))
            r = await eval_one(sym, max(scan_tmin, ENTRY_LO))
            r["news_detail"] = c.get("news_detail", "")
            ctrl_res.append(r)
            emit(f"   [control]   {sym:<6} {_fmt(r)}")
        for t in treatment:
            sym = (t.get("ticker") or "").upper()
            etmin = _scan_tmin((t.get("time_et") or "").split(" ")[-1])
            r = await eval_one(sym, ENTRY_LO, fixed_entry={"tmin": etmin, "price": t.get("price", 0)})
            treat_res.append(r)
            emit(f"   [treatment] {sym:<6} {_fmt(r)}")
    finally:
        ib.disconnect()

    emit("\n" + "=" * 78)
    emit("  AGGREGAT (proxy-udfald — IKKE fuld exit-stige)")
    emit("=" * 78)
    summarize("TREATMENT (nyheds-godkendt)", treat_res, emit)
    summarize("CONTROL (nyheds-afvist)", ctrl_res, emit)
    # Stratificér control: 'ingen nyhed' vs 'blandet/negativ'
    no_news = [r for r in ctrl_res if "ingen" in (r.get("news_detail") or "").lower()]
    mixed = [r for r in ctrl_res if "blandet" in (r.get("news_detail") or "").lower()
             or "negativ" in (r.get("news_detail") or "").lower()]
    if no_news:
        summarize("CONTROL · ingen nyhed", no_news, emit)
    if mixed:
        summarize("CONTROL · blandet/negativ nyhed", mixed, emit)

    emit("\n  FORUDREGISTRERET LÆSNING: nyheds-gaten tilføjer værdi KUN hvis treatment over "
         "TID\n  separerer fra control (højere P(hit +0.75R før −1R) og/eller højere median "
         "MFE_R).\n  Enkelte dage er for få events — dette er en akkumuleret dom (~400 events).")


def _scan_tmin(hhmmss):
    if not hhmmss:
        return ENTRY_LO
    try:
        parts = [int(x) for x in str(hhmmss).split(":")]
        return parts[0] * 60 + parts[1]
    except (ValueError, IndexError):
        return ENTRY_LO


def _fmt(r):
    if not r.get("entered"):
        return f"ingen entry ({r.get('reason','?')})"
    hp = {True: "→+0.75R", False: "→stop", None: "→åben"}[r["hit_partial_first"]]
    return f"entry ${r['entry_price']:.2f}  MFE {r['mfe_r']:+.2f}R  MAE {r['mae_r']:+.2f}R  {hp}"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Trend Join Long — shadow A/B-eval (offline, read-only)")
    ap.add_argument("--db", default="trading_dash.db")
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default i dag)")
    ap.add_argument("--verify", action="store_true", help="læs BARE dagens events tilbage (intet IBKR)")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    a = ap.parse_args()
    day = date.fromisoformat(a.day) if a.day else (datetime.now(ET).date() if ET else date.today())

    out_dir = Path.cwd() / "trendjoin_shadow_eval_output"
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit(f"  TREND JOIN LONG — SHADOW A/B-EVAL · dag {day} · {'VERIFY' if a.verify else 'FULD'}")
    emit("=" * 78)

    if a.verify:
        run_verify(a.db, a.source, day, emit)
    else:
        try:
            asyncio.run(run_full(a.db, a.source, day, a, emit))
        except KeyboardInterrupt:
            emit("\n  Afbrudt.")

    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"\n  Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
