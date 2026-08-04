#!/usr/bin/env python3
"""
futures_session_volume_test.py
══════════════════════════════
Modbevis-test for DAX/EuroStoxx' 60% continuation i EU-morgenen. Den forrige
test målte seriel afhængighed på RÅ 5-min bars — men tynde/illikvide bars skaber
falsk continuation (små ensrettede skridt, arvet fortegn) der IKKE kan handles.
Mistanken: 60%-tallet er et likviditets-artefakt, ikke en trend-edge (det stod
jo ~60% DØGNET rundt, også midt om natten — en ægte morgen-edge ville være
koncentreret i morgenvinduet).

To rensninger, begge gratis/historiske (intet abonnement):
  1. VOLUMEN-FILTER — gentag continuation kun på bars med reel volumen
     (>= median af positiv volumen). Falder 60% mod 50% = artefakt bekræftet.
  2. BAR-STØRRELSE — samme test på 15- og 30-min (5-min aggregeret op), hvor
     enkelt-bar mikrostruktur midles ud. Forsvinder edgen = artefakt.

Henter 5-min fuld session MED volumen, cacher som close+volume, og udleder
15/30-min lokalt (ingen ekstra hentning). Egen client-id 42.

Python 3.14: event-loop-fix. ib_async.

Brug:
    python futures_session_volume_test.py
    python futures_session_volume_test.py --min-vol-pct 50 --days 90

Placering: C:\\projects\\trading_dash\\backend\\futures_session_volume_test.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date as date_cls
from pathlib import Path
from statistics import median

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

CACHE_DIRNAME = "futures_cache"
OUTPUT_DIRNAME = "futures_volume_test_output"
EU_HOURS = [2, 3, 4, 5, 6, 7]
US_HOURS = [10, 11, 12, 13, 14, 15]
BAR_SIZES = [5, 15, 30]  # minutter

INSTRUMENTS = [
    ("MES (S&P micro)",       dict(localSymbol="MESM6", exchange="CME",   currency="USD")),
    ("MNQ (Nasdaq micro)",    dict(localSymbol="MNQM6", exchange="CME",   currency="USD")),
    ("MGC (guld micro)",      dict(localSymbol="MGCM6", exchange="COMEX", currency="USD")),
    ("DAX (Eurex)",           dict(symbol="DAX",        exchange="EUREX", currency="EUR")),
    ("EuroStoxx 50 (Eurex)",  dict(symbol="ESTX50",     exchange="EUREX", currency="EUR")),
]


@dataclass
class Bar:
    ts: datetime
    close: float
    volume: float


# ─────────────────────────────────────────────────────────────────────────────
# Resolve + fetch (med volumen)
# ─────────────────────────────────────────────────────────────────────────────
def _front_month(cds):
    today = date_cls.today().strftime("%Y%m%d")
    best = None
    for cd in cds:
        exp = (cd.contract.lastTradeDateOrContractMonth or "")
        key = (exp + "01")[:8] if len(exp) == 6 else exp[:8]
        if key and key >= today and (best is None or key < best[0]):
            best = (key, cd.contract)
    if best is None and cds:
        best = ("", sorted(cds, key=lambda x: x.contract.lastTradeDateOrContractMonth or "")[-1].contract)
    return best[1] if best else None


async def resolve_contract(ib, kwargs):
    from ib_async import Future
    if "localSymbol" in kwargs:
        try:
            q = await asyncio.wait_for(ib.qualifyContractsAsync(Future(**kwargs)), timeout=10)
            # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
            return q[0] if (q and getattr(q[0], "conId", 0)) else None
        except Exception:
            return None
    try:
        cds = await asyncio.wait_for(ib.reqContractDetailsAsync(Future(**kwargs)), timeout=15)
    except Exception:
        return None
    return _front_month(cds) if cds else None


def to_et(ts):
    if not isinstance(ts, datetime):
        return ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET) if ET is not None else ts


async def fetch_session_bars(ib, contract, days, chunk_days):
    seen = {}
    end_dt = datetime.now(timezone.utc)
    done = 0
    while done < days:
        step = min(chunk_days, days - done)
        try:
            raw = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                contract, endDateTime=end_dt, durationStr=f"{step} D",
                barSizeSetting="5 mins", whatToShow="TRADES", useRTH=False, formatDate=2),
                timeout=45)
        except Exception as e:
            if "different IP" in str(e) or "session is connected" in str(e):
                print("AFBRUDT: TWS-session fra anden IP.")
            break
        if not raw:
            break
        for b in raw:
            ts = b.date
            if isinstance(ts, datetime):
                k = ts.isoformat()
                if k not in seen:
                    seen[k] = Bar(to_et(ts), float(b.close), float(b.volume or 0))
        oldest = min(raw, key=lambda b: b.date).date
        if isinstance(oldest, datetime):
            end_dt = (oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)) - timedelta(minutes=5)
        done += step
        await asyncio.sleep(1.5)
    return sorted(seen.values(), key=lambda b: b.ts)


# ─────────────────────────────────────────────────────────────────────────────
# Aggregering + struktur-mål
# ─────────────────────────────────────────────────────────────────────────────
def aggregate(bars, factor_min):
    """5-min → factor_min ved at gruppere på ET-ur-bucket. close=sidste, volume=sum."""
    if factor_min == 5:
        return bars
    buckets = {}
    for b in bars:
        m = (b.ts.minute // factor_min) * factor_min
        key = b.ts.replace(minute=m, second=0, microsecond=0)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = [b.ts, b.close, b.volume]
        else:
            if b.ts >= cur[0]:
                cur[0], cur[1] = b.ts, b.close   # seneste close i bucket
            cur[2] += b.volume
    return [Bar(k, v[1], v[2]) for k, v in sorted(buckets.items())]


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def build_returns(bars, bar_seconds, min_vol=None):
    """[(ts, hour, ret)] for sammenhængende par; min_vol filtrerer tynde bars fra."""
    out, prev = [], None
    for b in bars:
        if min_vol is not None and b.volume < min_vol:
            continue  # tynd bar droppes; gap-tjek bryder pairing
        if prev is not None and (b.ts - prev.ts).total_seconds() == bar_seconds and prev.close > 0:
            out.append((b.ts, b.ts.hour, (b.close - prev.close) / prev.close))
        prev = b
    return out


def hourly(bars, bar_seconds, min_vol=None):
    rets = build_returns(bars, bar_seconds, min_vol)
    cont = defaultdict(lambda: [0, 0])
    pairs = defaultdict(lambda: ([], []))
    for i in range(len(rets) - 1):
        ts0, h0, r0 = rets[i]
        ts1, h1, r1 = rets[i + 1]
        if (ts1 - ts0).total_seconds() != bar_seconds:
            continue
        cont[h0][1] += 1
        if (r0 > 0) == (r1 > 0):
            cont[h0][0] += 1
        pairs[h0][0].append(r0)
        pairs[h0][1].append(r1)
    out = {}
    for h in cont:
        c, t = cont[h]
        out[h] = (t, 100 * c / t if t else 0, pearson(*pairs[h]))
    return out


def pool(struct, hours):
    tot = sum(struct[h][0] for h in hours if h in struct)
    if tot == 0:
        return (0, None, None)
    c = sum(struct[h][0] * struct[h][1] / 100 for h in hours if h in struct)
    acs = [(struct[h][0], struct[h][2]) for h in hours if h in struct and struct[h][2] is not None]
    ac = sum(n * a for n, a in acs) / sum(n for n, _ in acs) if acs else None
    return (tot, 100 * c / tot, ac)


def median_pos_vol(bars):
    vols = [b.volume for b in bars if b.volume > 0]
    return median(vols) if vols else 0


# ─────────────────────────────────────────────────────────────────────────────
# Cache (med volumen — eget filnavn, så gammel volumen-løs cache ikke bruges)
# ─────────────────────────────────────────────────────────────────────────────
def cache_file(backend, contract):
    sym = (contract.localSymbol or contract.symbol or "UNK").replace(" ", "_")
    return backend / CACHE_DIRNAME / f"{sym}_fullsession_5min_vol.csv"


def save_bars(path, bars):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_et", "close", "volume"])
        for b in bars:
            w.writerow([b.ts.isoformat(), b.close, b.volume])


def load_bars(path):
    out = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            out.append(Bar(datetime.fromisoformat(row["timestamp_et"]), float(row["close"]), float(row["volume"])))
    return sorted(out, key=lambda b: b.ts)


# ─────────────────────────────────────────────────────────────────────────────
async def gather(args, backend, emit):
    from ib_async import IB
    ib = IB()
    await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    data = {}
    try:
        for label, kwargs in INSTRUMENTS:
            emit(f"  {label}: resolver ...")
            c = await resolve_contract(ib, kwargs)
            if c is None:
                emit("    kunne ikke finde kontrakt."); data[label] = None; continue
            cf = cache_file(backend, c)
            if cf.exists() and not args.refetch:
                bars = load_bars(cf)
                emit(f"    cache: {len(bars)} bars")
            else:
                emit(f"    henter ({c.localSymbol or c.symbol}) ...")
                bars = await fetch_session_bars(ib, c, args.days, args.chunk_days)
                if bars:
                    save_bars(cf, bars)
                emit(f"    {len(bars)} bars (m. volumen)")
            data[label] = bars if len(bars) > 50 else None
    finally:
        ib.disconnect()
    return data


def main():
    ap = argparse.ArgumentParser(description="Futures EU-morgen: volumen- + bar-størrelse-rensning")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=42)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--chunk-days", type=int, default=30)
    ap.add_argument("--min-vol-pct", type=float, default=50.0, help="behold bars med volumen >= denne percentil (50=median)")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--backend-dir", default=None)
    args = ap.parse_args()

    backend = Path(args.backend_dir).resolve() if args.backend_dir else Path.cwd()
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  FUTURES EU-MORGEN — VOLUMEN- OG BAR-STØRRELSE-RENSNING")
    emit("=" * 78)
    emit(f"Gateway: {args.host}:{args.port}   dage: {args.days}   "
         f"vol-filter: >= {args.min_vol_pct:.0f}-percentil")
    emit("Spørgsmål: kollapser EU-morgen-continuation mod 50% når tynde bars fjernes")
    emit("og bars gøres større? Ja = likviditets-artefakt. Nej = ægte struktur.")
    emit("")

    try:
        data = asyncio.run(gather(args, backend, emit))
    except Exception as e:
        emit(f"FORBINDELSESFEJL: {e}")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    emit("")
    verdicts = []
    for label, _ in INSTRUMENTS:
        bars5 = data.get(label)
        emit("─" * 78)
        emit(f"  {label}")
        emit("─" * 78)
        if not bars5:
            emit("  (ingen data)"); emit(""); continue

        # volumen-virkelighed: hvor tynde er EU-morgen-bars vs nat?
        eu_v = [b.volume for b in bars5 if b.ts.hour in EU_HOURS]
        night_v = [b.volume for b in bars5 if b.ts.hour in (22, 23, 0, 1)]
        emit(f"     median 5-min volumen — EU-morgen: {int(median(eu_v)) if eu_v else 0}   "
             f"nat(22-01): {int(median(night_v)) if night_v else 0}")
        emit(f"     {'bar':>6}{'filter':>12}{'EU n':>8}{'EU cont%':>10}{'EU autokorr':>13}"
             f"{'(US cont%)':>12}")
        eu_clean = None
        for bs in BAR_SIZES:
            agg = aggregate(bars5, bs)
            sec = bs * 60
            thr = None
            # alle
            sa = hourly(agg, sec, None)
            eu_a, us_a = pool(sa, EU_HOURS), pool(sa, US_HOURS)
            aca = f"{eu_a[2]:+.3f}" if eu_a[2] is not None else "—"
            emit(f"     {bs:>4}m{'alle':>12}{eu_a[0]:>8}{eu_a[1]:>10.1f}{aca:>13}{us_a[1]:>12.1f}")
            # volumen-filtreret
            vols = [b.volume for b in agg if b.volume > 0]
            if vols:
                vols.sort()
                thr = vols[min(len(vols) - 1, int(len(vols) * args.min_vol_pct / 100))]
            sv = hourly(agg, sec, thr)
            eu_v2, us_v2 = pool(sv, EU_HOURS), pool(sv, US_HOURS)
            kept = 100 * eu_v2[0] / eu_a[0] if eu_a[0] else 0
            acv = f"{eu_v2[2]:+.3f}" if eu_v2[2] is not None else "—"
            emit(f"     {bs:>4}m{'vol-filt':>12}{eu_v2[0]:>8}{eu_v2[1]:>10.1f}{acv:>13}"
                 f"{us_v2[1]:>12.1f}   (beholdt {kept:.0f}%)")
            if bs == 30:
                eu_clean = eu_v2
        # dom pr. instrument: holder EU-morgen >53% ved 30-min vol-filtreret?
        held = eu_clean is not None and eu_clean[1] is not None and eu_clean[1] > 53 and eu_clean[0] > 200
        verdicts.append((label, held, eu_clean))
        emit("")

    emit("─" * 78)
    emit("  DOM")
    emit("─" * 78)
    real = [l for l, held, _ in verdicts if held]
    if real:
        emit(f"  EU-morgen-continuation OVERLEVER volumen+30-min-rensning: {', '.join(real)}")
        emit("  → ikke et rent artefakt. Næste skridt: en faktisk lille backtest med")
        emit("    realistisk Eurex-spænd FØR du betaler for data.")
    else:
        emit("  EU-morgen-continuation KOLLAPSER under rensning på alle instrumenter.")
        emit("  → 60%-tallet var et likviditets-artefakt (tynde bars), ikke en edge.")
        emit("    Læg futures endeligt væk; spar Eurex-abonnementet.")
    emit("  (Continuation er stadig ikke P&L — kun en backtest med spænd afgør penge.)")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    emit("→ Send mig summary.txt.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())