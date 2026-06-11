#!/usr/bin/env python3
"""
news_data_probe.py — realtids- + historisk nyheds-adgang for en katalysator-strategi
═══════════════════════════════════════════════════════════════════════════════════
En nyheds-drevet strategi har brug for nyheder i TO former, og denne probe tester
begge pr. kilde:

  • REALTID (push, sekunder) — til at HANDLE på. Måles som: hvor gammel er den
    NYESTE nyhed lige nu? VIGTIGT: friskheds-tal er kun meningsfulde UNDER åbningstid
    (09:30-16:00 ET); uden for åbningstid er der bare ikke brudt nyt, og dommen
    forplumres — proben markerer det selv.
  • HISTORISK med præcise tidsstempler — til at sende strategien gennem MØLLEN
    (backtest/OOS). Måles nu på FLERE dybde-checkpoints (7/30/90/180/365 dage tilbage)
    så vi ser den SANDE dybde, ikke et kunstigt loft. Møllen kræver desuden at
    backtest-kilden MATCHER live-kilden — historik fra én kilde validerer ikke en
    strategi der handler på en anden.

To kilder vi allerede har adgang til:

  1. IBKR (via ib_async, samme forbindelse som K2/reversion). reqNewsProviders viser
     hvilke providere kontoen er BERETTIGET til. Dow Jones-familien (DJ-N, DJ-RT*) er
     realtid; Benzinga "BZ" (~$35/md via API) ligeså. reqHistoricalNews → friskhed +
     dybde-checkpoints.

  2. Finnhub (REST company-news, samme nøgle som FinnhubNewsFeed). Måler faktisk
     friskhed + samme dybde-checkpoints, så IBKR- og Finnhub-dybde kan sammenlignes
     direkte ved samme tidspunkter.

KØBER INTET. Kun læsning af berettigelser + offentlige/abonnerede overskrifter.
Egen client-id 39. Python 3.14: event-loop-fix øverst. ib_async + aiohttp.

Brug (fra backend/):
    python news_data_probe.py
    python news_data_probe.py --symbols NVDA,TSLA,AAPL
    python news_data_probe.py --skip-ibkr / --skip-finnhub
    python news_data_probe.py --depth-symbol NVDA   # hvilket navn dybde-testes på

Placering: C:\\projects\\trading_dash\\backend\\news_data_probe.py
"""

from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

OUTPUT_DIRNAME = "news_data_probe_output"
DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL"]

# Dybde-checkpoints: dage tilbage. Den dybeste der stadig giver nyheder = praktisk
# backtest-dybde for den kilde.
DEPTH_CHECKPOINTS = [7, 30, 90, 180, 365]
DEPTH_WINDOW_DAYS = 3        # bredde af hvert checkpoint-vindue
DEPTH_LIMIT       = 100      # loft pr. checkpoint (markeres hvis nået)

# Provider-koder → note. DJ-N + DJ-RT* + BZ regnes som realtids-breaking.
PROVIDER_HINT = {
    "BZ":       "Benzinga Breaking News — realtid (~$35/md via API)",
    "DJ-N":     "Dow Jones Global Equity Trader — realtid aktie-nyheder",
    "DJ-RTG":   "Dow Jones Top Stories Global — realtid",
    "DJ-RTPRO": "Dow Jones Top Stories Pro — realtid",
    "DJ-RTA":   "Dow Jones Top Stories Asia — realtid (regional)",
    "DJ-RTE":   "Dow Jones Top Stories Europe — realtid (regional)",
    "DJ-RTW":   "Dow Jones Top Stories — realtid (regional)",
    "DJNL":     "Dow Jones Newsletters (ikke nødvendigvis realtid breaking)",
    "BRFG":     "Briefing.com general (sampling — ikke breaking)",
    "BRFUPDN":  "Briefing.com analyst actions",
    "FLY":      "Fly on the Wall (abonnement)",
}


def is_realtime_code(code: str) -> bool:
    return code == "BZ" or code == "DJ-N" or code.startswith("DJ-RT")


def et_market_open_now() -> tuple[bool, str]:
    """Er US-aktiemarkedet åbent lige nu? (grov: hverdag 09:30-16:00 ET, ingen helligdage)."""
    if ET is None:
        return True, "(kan ikke afgøre marked-åben — zoneinfo mangler)"
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False, f"weekend ({now_et:%H:%M} ET)"
    t = now_et.time()
    from datetime import time as dtime
    if dtime(9, 30) <= t <= dtime(16, 0):
        return True, f"åbent ({now_et:%H:%M} ET)"
    return False, f"uden for åbningstid ({now_et:%H:%M} ET)"


def age_str(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (now - dt).total_seconds()
    if secs < 0:
        return "fremtid? (tz-skævhed)"
    if secs < 90:
        return f"{int(secs)} sek siden"
    if secs < 5400:
        return f"{int(secs/60)} min siden"
    if secs < 172800:
        return f"{secs/3600:.1f} timer siden"
    return f"{secs/86400:.1f} dage siden"


def freshness_verdict(age_secs: float, market_open: bool) -> str:
    if not market_open:
        return "kan ikke dømmes (marked lukket — mål under åbningstid)"
    if age_secs < 120:
        return "REALTID (brugbar til katalysator-handel)"
    if age_secs < 1800:
        return "NÆR-REALTID (minutter — marginal til hurtige katalysatorer)"
    return "FORSINKET (for langsom til katalysator-handel)"


def depth_summary(results: list[tuple[int, int, str, str]]) -> str:
    """results = [(days_ago, count, oldest, newest), ...]. Returnér praktisk-dybde-dom."""
    hit = [d for (d, c, _o, _n) in results if c > 0]
    if not hit:
        return "INGEN historik fundet — ude til møllen"
    deepest = max(hit)
    empty_above = [d for (d, c, _o, _n) in results if c == 0 and d > deepest]
    if empty_above:
        nxt = min(empty_above)
        return f"praktisk backtest-dybde: mellem {deepest} og {nxt} dage tilbage"
    return f"praktisk backtest-dybde: mindst {deepest} dage tilbage (nåede checkpoint-grænsen)"


# ─────────────────────────────────────────────────────────────────────────────
# IBKR
# ─────────────────────────────────────────────────────────────────────────────
async def probe_ibkr(host, port, client_id, symbols, depth_symbol, market_open, emit) -> None:
    from ib_async import IB, Stock
    emit("=" * 78)
    emit("  KILDE 1 — IBKR (samme forbindelse som K2/reversion)")
    emit("=" * 78)
    ib = IB()
    try:
        await ib.connectAsync(host, port, clientId=client_id, timeout=15)
    except Exception as e:
        emit(f"  ❌ FORBINDELSESFEJL: {e}")
        emit(f"     Tjek Gateway + port {port} + ledigt client-id {client_id}.")
        return
    try:
        accts = ib.managedAccounts()
        emit(f"  ✅ Forbundet. Konto: {', '.join(accts) or '(ingen)'}")

        emit("")
        emit("  BERETTIGEDE NYHEDS-PROVIDERE (reqNewsProviders):")
        try:
            providers = await ib.reqNewsProvidersAsync()
        except Exception as e:
            emit(f"     ❌ kunne ikke hente providere: {e}")
            providers = []
        codes = []
        for p in providers:
            code = getattr(p, "code", "?")
            name = getattr(p, "name", "?")
            codes.append(code)
            hint = PROVIDER_HINT.get(code, "")
            emit(f"     {code:<10} {name}" + (f"   ← {hint}" if hint else ""))
        rt = [c for c in codes if is_realtime_code(c)]
        emit("")
        if rt:
            emit(f"  → Realtids-breaking-providere berettiget: {', '.join(rt)}")
        else:
            emit("  → INGEN realtids-breaking-provider berettiget. Billig vej: 'Benzinga")
            emit("    Breaking News via API' (~$35/md) i Client Portal — samme forbindelse.")

        provider_str = "+".join(codes) if codes else ""

        # ── Realtids-friskhed pr. navn ──
        for sym in symbols:
            emit("")
            emit("  " + "─" * 60)
            emit(f"  {sym}  (realtids-friskhed)")
            contract = Stock(sym, "SMART", "USD")
            try:
                await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=10)
            except Exception as e:
                emit(f"     ❌ kvalificering: {e}")
                continue
            conid = getattr(contract, "conId", None)
            if not conid or not provider_str:
                emit("     (ingen conId eller ingen providere)")
                continue
            try:
                recent = await asyncio.wait_for(
                    ib.reqHistoricalNewsAsync(conid, provider_str, "", "", 15), timeout=20)
            except Exception as e:
                emit(f"     ❌ reqHistoricalNews: {e}")
                continue
            if recent:
                try:
                    ndt = datetime.strptime(str(recent[0].time)[:19], "%Y-%m-%d %H:%M:%S")
                    a = (datetime.now(timezone.utc) - ndt.replace(tzinfo=timezone.utc)).total_seconds()
                    emit(f"     Nyeste af {len(recent)}: {age_str(ndt)} "
                         f"→ {freshness_verdict(a, market_open)}")
                except Exception:
                    emit(f"     Nyeste af {len(recent)}: tid={recent[0].time}")
                for h in recent[:4]:
                    emit(f"       [{h.time}] ({h.providerCode}) {str(h.headline)[:64]}")
            else:
                emit("     ingen nyere overskrifter")

        # ── Historisk dybde (ét repræsentativt navn, flere checkpoints) ──
        emit("")
        emit("  " + "═" * 60)
        emit(f"  HISTORISK DYBDE — {depth_symbol} (Dow Jones/IBKR via reqHistoricalNews)")
        c2 = Stock(depth_symbol, "SMART", "USD")
        depth_results = []
        try:
            await asyncio.wait_for(ib.qualifyContractsAsync(c2), timeout=10)
            dconid = getattr(c2, "conId", None)
        except Exception as e:
            emit(f"     ❌ kvalificering af {depth_symbol}: {e}")
            dconid = None
        if dconid and provider_str:
            for days in DEPTH_CHECKPOINTS:
                start = (datetime.now() - timedelta(days=days + DEPTH_WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S.0")
                end   = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.0")
                try:
                    items = await asyncio.wait_for(
                        ib.reqHistoricalNewsAsync(dconid, provider_str, start, end, DEPTH_LIMIT),
                        timeout=25)
                except Exception as e:
                    emit(f"     {days:>4} dage tilbage:  FEJL {e}")
                    depth_results.append((days, 0, "", ""))
                    continue
                if items:
                    times = sorted(str(i.time)[:10] for i in items)
                    cap = "  (loft nået — kan være flere)" if len(items) >= DEPTH_LIMIT else ""
                    emit(f"     {days:>4} dage tilbage:  {len(items):>3} nyheder  [{times[0]} .. {times[-1]}]{cap}")
                    depth_results.append((days, len(items), times[0], times[-1]))
                else:
                    emit(f"     {days:>4} dage tilbage:    0 nyheder  (tom)")
                    depth_results.append((days, 0, "", ""))
            emit(f"  → {depth_summary(depth_results)}")
    finally:
        ib.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Finnhub
# ─────────────────────────────────────────────────────────────────────────────
async def probe_finnhub(symbols, depth_symbol, market_open, emit) -> None:
    import aiohttp
    try:
        from finnhub_news import FINNHUB_API_KEY, FINNHUB_BASE
    except Exception as e:
        emit(f"  ❌ kunne ikke importere Finnhub-nøgle: {e}")
        return
    emit("")
    emit("=" * 78)
    emit("  KILDE 2 — Finnhub REST company-news")
    emit("=" * 78)
    url = f"{FINNHUB_BASE}/company-news"

    async with aiohttp.ClientSession() as session:
        async def fetch(sym, frm, to):
            params = {"symbol": sym, "from": frm.isoformat(), "to": to.isoformat(),
                      "token": FINNHUB_API_KEY}
            async with session.get(url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=20)) as r:
                return await r.json() if r.status == 200 else []

        # Realtids-friskhed pr. navn
        for sym in symbols:
            emit("")
            emit(f"  {sym}  (realtids-friskhed)")
            to_d = datetime.now().date()
            try:
                items = await fetch(sym, to_d - timedelta(days=2), to_d)
            except Exception as e:
                emit(f"     ❌ request-fejl: {e}")
                continue
            if not isinstance(items, list) or not items:
                emit("     ingen nyheder i 2-dages vindue")
                continue
            newest_ts = max(int(it.get("datetime", 0)) for it in items)
            ndt = datetime.fromtimestamp(newest_ts, tz=timezone.utc)
            age = (datetime.now(timezone.utc) - ndt).total_seconds()
            emit(f"     {len(items)} nyheder. Nyeste: {age_str(ndt)} "
                 f"→ {freshness_verdict(age, market_open)}")
            for it in sorted(items, key=lambda x: -int(x.get("datetime", 0)))[:3]:
                t = datetime.fromtimestamp(int(it.get("datetime", 0)), tz=timezone.utc)
                emit(f"       [{t:%Y-%m-%d %H:%M} UTC] {str(it.get('headline',''))[:64]}")
            await asyncio.sleep(1.1)

        # Historisk dybde (samme checkpoints)
        emit("")
        emit(f"  HISTORISK DYBDE — {depth_symbol} (Finnhub)")
        depth_results = []
        for days in DEPTH_CHECKPOINTS:
            to_d = (datetime.now() - timedelta(days=days)).date()
            frm_d = (datetime.now() - timedelta(days=days + DEPTH_WINDOW_DAYS)).date()
            try:
                items = await fetch(depth_symbol, frm_d, to_d)
            except Exception as e:
                emit(f"     {days:>4} dage tilbage:  FEJL {e}")
                depth_results.append((days, 0, "", ""))
                continue
            n = len(items) if isinstance(items, list) else 0
            if n:
                ds = sorted(datetime.fromtimestamp(int(i.get("datetime", 0)), tz=timezone.utc).strftime("%Y-%m-%d")
                            for i in items)
                emit(f"     {days:>4} dage tilbage:  {n:>3} nyheder  [{ds[0]} .. {ds[-1]}]")
                depth_results.append((days, n, ds[0], ds[-1]))
            else:
                emit(f"     {days:>4} dage tilbage:    0 nyheder  (tom)")
                depth_results.append((days, 0, "", ""))
            await asyncio.sleep(1.1)
        emit(f"  → {depth_summary(depth_results)}")


# ─────────────────────────────────────────────────────────────────────────────
async def main_async(args, market_open, emit) -> int:
    if not args.skip_ibkr:
        await probe_ibkr(args.host, args.port, args.client_id, args.symbols,
                         args.depth_symbol, market_open, emit)
    if not args.skip_finnhub:
        await probe_finnhub(args.symbols, args.depth_symbol, market_open, emit)
    emit("")
    emit("─" * 78)
    emit("  SÅDAN LÆSES DET")
    emit("─" * 78)
    emit("  • Realtids-friskhed kan KUN dømmes under åbningstid. 'kan ikke dømmes' =")
    emit("    kør igen mellem 09:30-16:00 ET (15:30-22:00 dansk).")
    emit("  • Historisk dybde: den dybeste checkpoint med nyheder = backtest-rækkevidde.")
    emit("  • MØLLEN kræver at backtest-kilden MATCHER live-kilden. Dyb Finnhub-historik")
    emit("    hjælper KUN en strategi der også handler på Finnhub — ikke en DJ-drevet.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Realtids- + historisk nyheds-adgangs-probe (køber intet)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=39)
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--depth-symbol", default="NVDA",
                    help="hvilket (godt-dækket) navn dybden testes på")
    ap.add_argument("--skip-ibkr", action="store_true")
    ap.add_argument("--skip-finnhub", action="store_true")
    args = ap.parse_args()
    args.symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    args.depth_symbol = args.depth_symbol.strip().upper()

    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines: list[str] = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    market_open, mkt_note = et_market_open_now()
    emit("=" * 78)
    emit("  NYHEDS-DATA-PROBE — realtid + historik  (køber intet, læs-only)")
    emit("=" * 78)
    emit(f"Tid: {datetime.now():%Y-%m-%d %H:%M}   marked: {mkt_note}   "
         f"dybde-navn: {args.depth_symbol}")
    emit(f"Testnavne: {', '.join(args.symbols)}")
    emit("")

    try:
        rc = asyncio.run(main_async(args, market_open, emit))
    except KeyboardInterrupt:
        emit("\nAfbrudt.")
        rc = 130
    finally:
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
