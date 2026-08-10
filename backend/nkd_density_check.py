#!/usr/bin/env python3
"""
nkd_density_check.py — pre-backtest densitets- og struktur-tjek for NKD (CME Nikkei)
=====================================================================================
PORTEN foer vi bygger NKD-strategiens backtest. Svarer paa to ting empirisk,
saa vi ikke backtester paa data der ikke findes — eller i en session hvor NKD ikke
mean-reverter:

  1) DENSITET: hvor taet ligger 15-min bars hen over doegnet? Specielt:
       - EU-morgen-vinduet (02:00-08:00 ET = 08:00-14:00 dansk),
         hvor NKD er i tynd natte-Globex (Japan lukket, US ikke aabnet).
       - NKD's egen LIKVIDE US-session (09:30-17:00 ET = 15:30-23:00 dansk).
     Histogrammet viser begge paa een gang -> billedet afgoer hvilken session der
     overhovedet har nok bars til at handle.

  2) DATA-INTEGRITET: huller. Reglen beregner z paa SAMMENHAENGENDE bars; manglende
     bars midt i en session korrumperer det rullende MA20/std20. Vi bucketer afstanden
     mellem fortloebende bars (15 min = taet, >15 min uden for sessions-grns = hul).

  3) BONUS — REVERSION PLAUSIBEL? lag-1 autokorr af 15-min afkast, beregnet SEPARAT
     for EU-vinduet og US-sessionen (kun inden for sammenhaengende runs, saa natte-
     gabet ikke forurener). <0 = mean-revert (det vi haaber), >0 = trend, ~0 = stoej.

Henter NKD FRONT-kontrakt (CME, USD) paa samme maade som futures_preflight_check.py
(reqContractDetails + naermeste udloeb, 10339-sikkert — IKKE ContFuture), saa det
matcher harvest-pipelinen. useRTH=False som strategien.

Read-only: handler ikke, sender ingen ordrer, aendrer intet. Egen client-id (default
46), saa den IKKE kolliderer med en koerende backend/strategi paa samme TWS.
Kun HISTORIK -> kraever intet realtids-abonnement og kan koeres naar som helst.
Undgaa dog at koere praecis i det minut en strategi fyrer entries (pacing).

Python 3.14: event-loop-fix. ib_async. Kun stdlib derudover.

Brug (paa algoserveren, fra backend/):
    python nkd_density_check.py
    python nkd_density_check.py --days 400 --client-id 46 --port 7497

Output i ./nkd_density_check_output/: summary.txt

Placering: C:\\projects\\trading_dash\\backend\\nkd_density_check.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

from ib_async import IB, Future

# ── Konfiguration ─────────────────────────────────────────────────────────────
HOST            = "127.0.0.1"
PORT            = 7497
CLIENT_ID       = 45
BAR_SIZE        = "15 mins"     # 15-min timeframe
CHUNK_DAYS      = 30
SLEEP_BETWEEN   = 0.6
PACING_WAIT     = 60
DEFAULT_DAYS    = 400           # nok til at ramme front-kontraktens naturlige bund

# Vinduer i ET (time, minut). EU = EU-morgen-vindue; US = NKD's likvide CME-session.
EU_START_ET = (2, 0);  EU_END_ET = (8, 0)     # 08:00-14:00 dansk
US_START_ET = (9, 30); US_END_ET = (17, 0)    # 15:30-23:00 dansk


def _et(dt) -> datetime:
    """Normaliser en bar-dato til tz-aware ET."""
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(ET) if ET else dt
    if ET is not None:
        return dt.replace(tzinfo=ET)
    return dt


def _in_window(dt: datetime, start, end) -> bool:
    m = dt.hour * 60 + dt.minute
    return (start[0] * 60 + start[1]) <= m < (end[0] * 60 + end[1])


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None, n
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None, n
    return cov / math.sqrt(vx * vy), n


def _window_autocorr(bars, start, end, max_gap_min=20):
    """lag-1 autokorr af 15-min afkast inden for sammenhaengende runs i vinduet."""
    runs, cur, prev_ts = [], [], None
    for b in bars:
        dt = b["et"]
        if _in_window(dt, start, end):
            if prev_ts is not None and (dt - prev_ts).total_seconds() <= max_gap_min * 60:
                cur.append(b["close"])
            else:
                if len(cur) >= 3:
                    runs.append(cur)
                cur = [b["close"]]
            prev_ts = dt
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur, prev_ts = [], None
    if len(cur) >= 3:
        runs.append(cur)

    xs, ys = [], []
    for closes in runs:
        rets = [closes[i] / closes[i - 1] - 1.0
                for i in range(1, len(closes)) if closes[i - 1] > 0]
        for i in range(1, len(rets)):
            xs.append(rets[i - 1])
            ys.append(rets[i])
    return _pearson(xs, ys)


async def qualify_front(ib, emit):
    """Naermeste ikke-udloebne NKD-front (CME, USD). 10339-sikkert via contract details."""
    base = Future(symbol="NKD", exchange="CME", currency="USD")
    details = await asyncio.wait_for(ib.reqContractDetailsAsync(base), timeout=15)
    if not details:
        emit("   FEJL: ingen kontrakt-detaljer for NKD@CME (USD).")
        return None, None

    def parse_exp(s: str):
        s = (s or "").strip()
        try:
            if len(s) >= 8:
                return datetime.strptime(s[:8], "%Y%m%d").date()
            return datetime.strptime(s[:6] + "01", "%Y%m%d").date()
        except ValueError:
            return None

    today = datetime.now().date()
    cands = []
    for d in details:
        exp = parse_exp(d.contract.lastTradeDateOrContractMonth)
        if exp and exp >= today:
            cands.append((exp, d))
    if not cands:
        emit("   FEJL: fandt kun udloebne NKD-kontrakter.")
        return None, None
    cands.sort(key=lambda t: t[0])
    exp, det = cands[0]
    c = det.contract
    q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=15)
    # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl — en tom skal
    # med conId=0 ville ellers glide videre og give 'ingen data' i stedet for
    # 'kontrakten findes ikke'. Se ibkr_kvalificer.
    c = q[0] if (q and getattr(q[0], "conId", 0)) else c
    if not getattr(c, "conId", 0):
        raise RuntimeError(f"kontrakten kunne ikke kvalificeres (conId=0): {c}")
    emit(f"   KVALIFICERING: {c.localSymbol}  conId={c.conId}  udloeb {c.lastTradeDateOrContractMonth}"
         f"  mult={c.multiplier}  {c.currency}  minTick={det.minTick}  ({c.exchange})")
    return c, exp


async def pull_15m(ib, contract, max_days, emit):
    """15-min bars useRTH=False, walk bagud i 30 D-chunks. Dedup paa tidsstempel."""
    by_ts = {}
    end_str = ""
    oldest = None
    target = datetime.now(timezone.utc) - timedelta(days=max_days)
    chunks = max_days // CHUNK_DAYS + 4
    for ci in range(chunks):
        bars = None
        for attempt in range(2):
            try:
                bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=f"{CHUNK_DAYS} D",
                    barSizeSetting=BAR_SIZE, whatToShow="TRADES", useRTH=False,
                    formatDate=1), timeout=60)
                break
            except Exception as e:
                if "pacing" in str(e).lower():
                    emit(f"   (pacing — venter {PACING_WAIT}s)")
                    await asyncio.sleep(PACING_WAIT)
                    continue
                if attempt == 0:
                    await asyncio.sleep(3)
                    continue
                emit(f"   reqHistoricalData fejl: {e}")
                bars = None
        if not bars:
            emit(f"   chunk {ci + 1}: tom -> bunden for front-kontrakten er naaet.")
            break
        chunk_oldest = None
        for b in bars:
            dt = _et(b.date)
            dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt
            by_ts[dt_utc] = {"et": dt, "close": float(b.close),
                             "volume": int(b.volume) if b.volume else 0}
            if chunk_oldest is None or dt_utc < chunk_oldest:
                chunk_oldest = dt_utc
        newest_seen = max(by_ts).astimezone(ET) if by_ts else None
        emit(f"   chunk {ci + 1}: +{len(bars)} bars (aeldste i chunk {chunk_oldest.astimezone(ET):%Y-%m-%d})  "
             f"| total {len(by_ts)}")
        if chunk_oldest is None or (oldest is not None and chunk_oldest >= oldest):
            break
        oldest = chunk_oldest
        if oldest <= target or len(bars) < 30:
            break
        end_str = (oldest - timedelta(seconds=1)).strftime("%Y%m%d %H:%M:%S") + " UTC"
        await asyncio.sleep(SLEEP_BETWEEN)
    return [by_ts[k] for k in sorted(by_ts)]


def analyze(bars, lines):
    def emit(s=""):
        print(s)
        lines.append(s)

    if len(bars) < 10:
        emit("   FOR FAA BARS til analyse.")
        return

    oldest = bars[0]["et"]
    newest = bars[-1]["et"]
    emit("")
    emit("=" * 78)
    emit("  DENSITETS-RAPPORT")
    emit("=" * 78)
    emit(f"  Total: {len(bars)} 15-min bars")
    emit(f"  Spaen: {oldest:%Y-%m-%d %H:%M} -> {newest:%Y-%m-%d %H:%M} ET  "
         f"({(newest - oldest).days} kalenderdage)")

    # ── Histogram pr. ET-time ──
    by_hour = defaultdict(int)
    for b in bars:
        by_hour[b["et"].hour] += 1
    maxc = max(by_hour.values()) if by_hour else 1
    emit("")
    emit("  Bars pr. ET-time  ([EU]=EU-morgen-vindue  [US]=NKD likvid):")
    for h in range(24):
        c = by_hour.get(h, 0)
        bar = "#" * int(round(40 * c / maxc)) if maxc else ""
        if 2 <= h < 8:
            tag = " [EU]"
        elif 9 <= h < 17:
            tag = " [US]"
        else:
            tag = ""
        emit(f"   {h:02d}:00  {c:6d}  {bar}{tag}")

    # ── EU vs US pr. dag ──
    eu_days, us_days = defaultdict(int), defaultdict(int)
    for b in bars:
        d = b["et"].date()
        if _in_window(b["et"], EU_START_ET, EU_END_ET):
            eu_days[d] += 1
        if _in_window(b["et"], US_START_ET, US_END_ET):
            us_days[d] += 1
    eu_avg = (sum(eu_days.values()) / len(eu_days)) if eu_days else 0.0
    us_avg = (sum(us_days.values()) / len(us_days)) if us_days else 0.0
    emit("")
    emit("  Pr. handelsdag (forventet hvis taet: EU ~24 bars/dag, US ~30 bars/dag):")
    emit(f"   EU-vindue (02:00-08:00 ET): {sum(eu_days.values())} bars over {len(eu_days)} dage "
         f"-> snit {eu_avg:.1f} bars/dag")
    emit(f"   US-session (09:30-17:00 ET): {sum(us_days.values())} bars over {len(us_days)} dage "
         f"-> snit {us_avg:.1f} bars/dag")

    # ── Hul-analyse ──
    deltas = [(bars[i]["et"] - bars[i - 1]["et"]).total_seconds() / 60.0
              for i in range(1, len(bars))]
    contig = sum(1 for d in deltas if 14 <= d <= 16)
    small = sum(1 for d in deltas if 16 < d <= 90)
    sess = sum(1 for d in deltas if 90 < d <= 1080)
    big = sum(1 for d in deltas if d > 1080)
    largest_intra = max((d for d in deltas if d <= 1080), default=0.0)
    emit("")
    emit("  Hul-analyse (afstand mellem fortloebende bars):")
    emit(f"   15 min (taet):              {contig:6d}  ({100*contig/len(deltas):.1f}%)")
    emit(f"   16-90 min (intra-hul):      {small:6d}  <- manglende bars i en session = tynd likviditet")
    emit(f"   90 min-18t (sessionsgrns):  {sess:6d}")
    emit(f"   >18t (weekend/helligdag):   {big:6d}")
    emit(f"   stoerste intra-dag-hul:     {largest_intra:.0f} min")

    # ── Reversion plausibel? ──
    eu_ac, eu_n = _window_autocorr(bars, EU_START_ET, EU_END_ET)
    us_ac, us_n = _window_autocorr(bars, US_START_ET, US_END_ET)

    def fmt_ac(ac, n):
        if ac is None:
            return f"utilstraekkelige par (n={n})"
        sign = "MEAN-REVERT" if ac < -0.02 else ("TREND" if ac > 0.02 else "stoej (~0)")
        return f"{ac:+.3f}  [{sign}]  (n={n} par)"

    emit("")
    emit("  lag-1 autokorr af 15-min afkast (<0 = mean-revert, >0 = trend):")
    emit(f"   EU-vindue:  {fmt_ac(eu_ac, eu_n)}")
    emit(f"   US-session: {fmt_ac(us_ac, us_n)}")

    # ── Verdikt ──
    emit("")
    emit("=" * 78)
    emit("  VERDIKT")
    emit("=" * 78)
    eu_dense = eu_avg >= 18.0
    us_dense = us_avg >= 22.0
    emit(f"  EU-vindue (EU-morgen):          "
         f"{'TAET NOK' if eu_dense else 'FOR TYNDT'} ({eu_avg:.1f} bars/dag) "
         f"+ {fmt_ac(eu_ac, eu_n).split('[')[1].rstrip(']') if eu_ac is not None else 'n/a'}".rstrip())
    emit(f"  US-session (NKD likvid):        "
         f"{'TAET NOK' if us_dense else 'FOR TYNDT'} ({us_avg:.1f} bars/dag) "
         f"+ {fmt_ac(us_ac, us_n).split('[')[1].rstrip(']') if us_ac is not None else 'n/a'}".rstrip())
    emit("")
    if eu_dense and (eu_ac is not None and eu_ac < -0.02):
        emit("  -> EU-vinduet baerer: byg NKD-strategiens backtest paa EU-morgen-vinduet. Host + backtest.")
    elif us_dense and (us_ac is not None and us_ac < -0.02):
        emit("  -> EU-vinduet baerer ikke, men US-sessionen er taet OG reversion-agtig:")
        emit("     retarget session til US (09:30-17:00 ET) i backtesten. Anden tese,")
        emit("     men datamaessigt farbar. Host US-session-bars + backtest med US-vindue.")
    elif us_dense:
        emit("  -> Kun US-sessionen er taet, men den ser TRENDENDE/stoej ud — mean-reversion a priori")
        emit("     usandsynlig der. Backtest er stadig billig (afgoer det empirisk), men forvent svagt.")
    else:
        emit("  -> Hverken EU eller US har taet 15-min NKD-data paa front-kontrakten. Front-kontrakten")
        emit("     raekker maaske ikke dybt nok; overvej stitchede front-maaneder, eller drop NKD.")
    emit("")
    emit("  NB: front-kontraktens aeldste bar = dybden vi kan backteste paa EEN kontrakt.")
    emit("      Dybere historik kraever stitching af flere front-maaneder (byg det hvis backtesten lover).")


async def main_async(args):
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    emit("=" * 78)
    emit("  NKD (CME Nikkei) — PRE-BACKTEST DENSITETS- OG STRUKTUR-TJEK  (read-only)")
    emit("=" * 78)
    emit(f"  Tid: {datetime.now():%Y-%m-%d %H:%M}   Gateway: {args.host}:{args.port}   "
         f"client-id {args.client_id}   bar={BAR_SIZE}")

    ib = IB()
    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        emit(f"  FEJL: kunne ikke forbinde til TWS: {e}")
        return 1
    emit("  Forbundet.\n")

    try:
        contract, _exp = await qualify_front(ib, emit)
        if contract is None:
            return 1
        emit(f"\n  Henter 15-min bars (useRTH=False) op til {args.days} dage bagud...")
        bars = await pull_15m(ib, contract, args.days, emit)
        analyze(bars, lines)
    finally:
        ib.disconnect()

    out_dir = Path("nkd_density_check_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"\nFil: {out_dir / 'summary.txt'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-backtest densitets-/struktur-tjek for NKD")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID,
                    help="eget id — undgaa kollision med backend/strategi")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="hvor langt bagud der proeves hentet (default 400)")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt.")
        return 130


if __name__ == "__main__":
    sys.exit(main())