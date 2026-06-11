#!/usr/bin/env python3
"""
futures_session_structure.py
════════════════════════════
Måler om futures har exploiterbar intradag-RETNINGSSTRUKTUR i den EUROPÆISKE
morgen — det vindue US-RTH-karakteriseringen aldrig kiggede på (den kørte
useRTH=True og smed natten + Europa væk).

Henter FULD session (useRTH=False) på gratis HISTORISKE bars — kræver INTET
real-time abonnement (bekræftet: delayed/historisk kommer igennem uden sub).

To skala-FRIE mål pr. ET-time (ER bevidst udeladt — det var det skala-afhængige
mål der fejlagtigt fik US-futures til at ligne et trend-vindue):
  • continuation%  — andel af bar-par hvor retningen fortsætter (50% = møntkast)
  • autokorr(lag1) — Pearson på (r_t, r_{t+1}); >0 trend, <0 mean-revert, ~0 støj

Europæisk morgen = 02:00–08:00 ET ≈ 08:00–14:00 dansk tid (Eurex/London-sessionen).
US-session = 10:00–16:00 ET til kontrast.

Instrumenter: MES, MNQ (CME), MGC (COMEX), DAX, EuroStoxx 50 (Eurex).
Cacher bars i ./futures_cache/ → genkørsel af analysen er gratis (--refetch tvinger ny hentning).

Python 3.14: event-loop-fix. ib_async. Egen client-id 41.

Brug:
    python futures_session_structure.py
    python futures_session_structure.py --days 90 --refetch

Placering: C:\\projects\\trading_dash\\backend\\futures_session_structure.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date as date_cls
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

CACHE_DIRNAME = "futures_cache"
OUTPUT_DIRNAME = "futures_session_output"
BAR_SECONDS = 300  # 5 min
EU_HOURS = [2, 3, 4, 5, 6, 7]        # europæisk morgen (ET)
US_HOURS = [10, 11, 12, 13, 14, 15]  # us-session (ET)

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


# ─────────────────────────────────────────────────────────────────────────────
# Kontrakt-resolve + fetch
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
            return q[0] if q else None
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
    """Fuld-session 5-min bars (useRTH=False), hentet i bidder bagud. Dedup+sort."""
    seen = {}
    end_dt = datetime.now(timezone.utc)
    fetched_days = 0
    while fetched_days < days:
        step = min(chunk_days, days - fetched_days)
        try:
            raw = await asyncio.wait_for(ib.reqHistoricalDataAsync(
                contract, endDateTime=end_dt, durationStr=f"{step} D",
                barSizeSetting="5 mins", whatToShow="TRADES", useRTH=False, formatDate=2),
                timeout=45)
        except Exception as e:
            if "different IP" in str(e) or "session is connected" in str(e):
                print("AFBRUDT: TWS-session fra anden IP. Kør i sikkert vindue.")
            break
        if not raw:
            break
        for b in raw:
            ts = b.date
            if isinstance(ts, datetime):
                key = ts.isoformat()
                if key not in seen:
                    seen[key] = Bar(to_et(ts), float(b.close))
        oldest = min(raw, key=lambda b: b.date).date
        if isinstance(oldest, datetime):
            end_dt = (oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)) - timedelta(minutes=5)
        fetched_days += step
        await asyncio.sleep(1.5)
    return sorted(seen.values(), key=lambda b: b.ts)


# ─────────────────────────────────────────────────────────────────────────────
# Struktur-mål (skala-frie)
# ─────────────────────────────────────────────────────────────────────────────
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


def build_returns(bars):
    """[(ts, et_hour, ret)] kun for sammenhængende 5-min-par (ingen overnight-gap)."""
    out = []
    for i in range(1, len(bars)):
        if (bars[i].ts - bars[i - 1].ts).total_seconds() == BAR_SECONDS and bars[i - 1].close > 0:
            out.append((bars[i].ts, bars[i].ts.hour, (bars[i].close - bars[i - 1].close) / bars[i - 1].close))
    return out


def hourly_structure(bars):
    """Pr. ET-time: {hour: (n_par, continuation%, autokorr)}."""
    rets = build_returns(bars)
    cont_by_hour = defaultdict(lambda: [0, 0])     # hour -> [cont, total]
    pair_by_hour = defaultdict(lambda: ([], []))   # hour -> (x, y) for autokorr
    for i in range(len(rets) - 1):
        ts0, h0, r0 = rets[i]
        ts1, h1, r1 = rets[i + 1]
        if (ts1 - ts0).total_seconds() != BAR_SECONDS:   # ikke tids-konsekutive → spring gap-bro over
            continue
        cont_by_hour[h0][1] += 1
        if (r0 > 0) == (r1 > 0):
            cont_by_hour[h0][0] += 1
        pair_by_hour[h0][0].append(r0)
        pair_by_hour[h0][1].append(r1)
    out = {}
    for h in sorted(cont_by_hour):
        cont, tot = cont_by_hour[h]
        ac = pearson(*pair_by_hour[h])
        out[h] = (tot, 100 * cont / tot if tot else 0, ac)
    return out


def pool(struct, hours):
    """Pool continuation over et sæt timer + autokorr."""
    # genskab ikke par; brug vægtet continuation + samlet autokorr-approx via vægtning
    tot = sum(struct[h][0] for h in hours if h in struct)
    if tot == 0:
        return (0, None, None)
    cont = sum(struct[h][0] * struct[h][1] / 100 for h in hours if h in struct)
    acs = [(struct[h][0], struct[h][2]) for h in hours if h in struct and struct[h][2] is not None]
    ac = sum(n * a for n, a in acs) / sum(n for n, _ in acs) if acs else None
    return (tot, 100 * cont / tot, ac)


# ─────────────────────────────────────────────────────────────────────────────
# Cache I/O
# ─────────────────────────────────────────────────────────────────────────────
def cache_file(backend, contract):
    sym = (contract.localSymbol or contract.symbol or "UNK").replace(" ", "_")
    return backend / CACHE_DIRNAME / f"{sym}_fullsession_5min.csv"


def save_bars(path, bars):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_et", "close"])
        for b in bars:
            w.writerow([b.ts.isoformat(), b.close])


def load_bars(path):
    bars = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp_et"])
            bars.append(Bar(ts, float(row["close"])))
    return sorted(bars, key=lambda b: b.ts)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
async def run(args, backend, emit):
    from ib_async import IB
    need_fetch = []
    cached = {}
    for label, kwargs in INSTRUMENTS:
        cached[label] = None
    # afgør hvad der skal hentes (kontrakt skal resolves for cache-navn → connect altid)
    ib = IB()
    await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    results = {}
    try:
        for label, kwargs in INSTRUMENTS:
            emit(f"  {label}: resolver kontrakt ...")
            contract = await resolve_contract(ib, kwargs)
            if contract is None:
                emit(f"    kunne ikke finde kontrakt — springer over.")
                results[label] = None
                continue
            cf = cache_file(backend, contract)
            if cf.exists() and not args.refetch:
                bars = load_bars(cf)
                emit(f"    cache: {len(bars)} bars ({contract.localSymbol or contract.symbol})")
            else:
                emit(f"    henter fuld session ({contract.localSymbol or contract.symbol}) ...")
                bars = await fetch_session_bars(ib, contract, args.days, args.chunk_days)
                if bars:
                    save_bars(cf, bars)
                emit(f"    {len(bars)} fuld-session 5-min bars")
            results[label] = hourly_structure(bars) if len(bars) > 30 else None
    finally:
        ib.disconnect()
    return results


def main():
    ap = argparse.ArgumentParser(description="Futures fuld-sessions struktur (europæisk morgen)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=41)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--chunk-days", type=int, default=30)
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
    emit("  FUTURES FULD-SESSIONS STRUKTUR — europæisk morgen vs US")
    emit("=" * 78)
    emit(f"Gateway: {args.host}:{args.port}   dage: {args.days}   "
         f"(gratis historiske bars — intet abonnement)")
    emit("Europæisk morgen = 02:00–08:00 ET ≈ 08:00–14:00 dansk tid")
    emit("Skala-frie mål: continuation% (50%=møntkast) og autokorr(lag1) (~0=støj)")
    emit("")

    try:
        results = asyncio.run(run(args, backend, emit))
    except Exception as e:
        emit(f"FORBINDELSESFEJL: {e}")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    emit("")
    eu_verdicts = []
    for label, _ in INSTRUMENTS:
        struct = results.get(label)
        emit("─" * 78)
        emit(f"  {label}")
        emit("─" * 78)
        if not struct:
            emit("  (ingen data)")
            emit("")
            continue
        emit(f"     {'ET-time':>8}{'n-par':>8}{'cont.%':>9}{'autokorr':>10}   vindue")
        for h in sorted(struct):
            n, c, ac = struct[h]
            acs = f"{ac:+.3f}" if ac is not None else "  —  "
            tag = "EU-morgen" if h in EU_HOURS else ("US" if h in US_HOURS else "")
            emit(f"     {h:>6}:00{n:>8}{c:>9.1f}{acs:>10}   {tag}")
        eu = pool(struct, EU_HOURS)
        us = pool(struct, US_HOURS)
        emit("")
        emit(f"     POOL EU-morgen (02–08 ET): n={eu[0]}  cont={eu[1]:.1f}%  "
             f"autokorr={eu[2]:+.3f}" if eu[2] is not None else
             f"     POOL EU-morgen: n={eu[0]}  cont={eu[1]:.1f}%")
        emit(f"     POOL US     (10–15 ET): n={us[0]}  cont={us[1]:.1f}%  "
             f"autokorr={us[2]:+.3f}" if us[2] is not None else
             f"     POOL US: n={us[0]}  cont={us[1]:.1f}%")
        eu_verdicts.append((label, eu))
        emit("")

    # samlet dom
    emit("─" * 78)
    emit("  DOM — er der et europæisk morgen-trendvindue?")
    emit("─" * 78)
    emit("  Støjbånd ved tusinder af par: cont% ~50±1.5. Konsistent >53% ELLER")
    emit("  autokorr klart >0 på tværs af instrumenter = ægte trend-vindue.")
    emit("")
    flagged = [l for l, eu in eu_verdicts if eu[1] is not None and (eu[1] > 53
               or (eu[2] is not None and eu[2] > 0.05))]
    if flagged:
        emit(f"  MULIGT trend-vindue i EU-morgen: {', '.join(flagged)}")
        emit("  → værd at forfølge; NU er Eurex-dataprisen i Client Portal relevant.")
    else:
        emit("  INGEN konsistent struktur i EU-morgen — møntkast som US/FX.")
        emit("  → læg futures helt væk; spar Eurex-abonnementet.")
    emit("  (Continuation/autokorr på 5-min bars; signal, ikke endeligt bevis.)")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    emit("→ Send mig summary.txt.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())