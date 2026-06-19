#!/usr/bin/env python3
"""
catalyst_history_probe.py — Katalysator-møllen, Trin 1: datagrundlags-tjek
═══════════════════════════════════════════════════════════════════════════
Afgør OM vi overhovedet kan backteste en katalysator-strategi ÆRLIGT — FØR vi
designer signalet eller bygger backtest-motoren. Måler historisk nyheds-DYBDE og
TIDSSTEMPEL-PRÆCISION pr. kilde. Måler IKKE realtid (separat, senere spørgsmål).

Hvorfor: en katalysator-backtest med latency-knap (entry ved nyhed+1/5/15/30/60
min) er kun ærlig hvis den historiske nyheds-data er (a) dyb nok (måneder, ikke
dage) og (b) tidsstemplet præcist nok til at time en entry på minut-niveau. Den
forrige probe (news_data_probe.py) viste et kunstigt totalResults=10-loft og kørte
før åbningstid — den besvarede IKKE dybde/præcision. Denne gør netop det.

To kilder (samme adgang som vi allerede har):
  • KILDE A — Finnhub REST company-news (samme nøgle som FinnhubNewsFeed).
  • KILDE B — IBKR Dow Jones (reqHistoricalNews, samme forbindelse som K2/reversion).

KØBER INTET. Læs-only: sender INGEN ordrer, skriver INTET til trading_dash.db,
ændrer ingen state. Egen client-id (default 27) der ikke kolliderer med backend.

Brug (fra backend/):
    python catalyst_history_probe.py
    python catalyst_history_probe.py --symbols NVDA,TSLA,AAPL --depth-symbol NVDA
    python catalyst_history_probe.py --skip-ibkr        # kun Finnhub
    python catalyst_history_probe.py --skip-finnhub      # kun IBKR

Output i ./catalyst_history_probe_output/: summary.txt

Placering: C:\\Projects\\trading_dash\\backend\\catalyst_history_probe.py
"""

from __future__ import annotations

import asyncio
# Python 3.14 event-loop-fix (samme som resten af kodebasen) — ib_async kræver
# en current event loop ved import/konstruktion.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT_DIRNAME = "catalyst_history_probe_output"
DEFAULT_SYMBOLS = ["NVDA", "TSLA", "AAPL"]

# Dybde-checkpoints: vinduer der slutter NU og strækker N dage tilbage. Den dybeste
# der stadig giver reel dækning = praktisk backtest-rækkevidde for kilden.
DEPTH_CHECKPOINTS = [7, 30, 90, 180, 365]

# Tærskler for DOMMEN (dokumenteret, ikke magiske):
DEPTH_OK_DAYS      = 90      # "dyb nok": reel dækning mindst 90 dage tilbage
PRECISION_OK_PCT   = 80.0    # "minut-præcis nok": ≥80% af stikprøven har intradag-tid
PRECISION_SAMPLE   = 50      # antal nyeste nyheder vi måler præcision på
PRECISION_WINDOW_D = 7       # stikprøve-vindue (dage) til præcisions-målingen

IBKR_TOTAL_RESULTS = 300     # fjern det kunstige 10-loft fra forrige probe

# Dow Jones-providere der KAN have enkelt-artikel-historik. Briefing (BRFG/BRFUPDN),
# Newsletters (DJNL) og Fly (FLY) springes over — de er sampling/nyhedsbreve, ikke
# tidsstemplet enkelt-artikel-historik egnet til en latency-backtest.
DJ_HISTORICAL_CANDIDATES = ["DJ-N", "DJ-RTG", "DJ-RTPRO", "DJ-RTA", "DJ-RTE", "DJ-RTW"]
SKIP_PROVIDERS           = {"BRFG", "BRFUPDN", "DJNL", "FLY"}


@dataclass
class SourceResult:
    """Målte tal pr. kilde — bruges til DOM-sektionen."""
    name:         str
    reach_days:   int   = 0       # dage tilbage til ÆLDSTE observerede nyhed
    total_count:  int   = 0       # samlet antal nyheder set (på tværs af vinduer)
    precise_pct:  float = 0.0     # % af stikprøven med intradag-tidsstempel
    sample_n:     int   = 0       # størrelse af præcisions-stikprøven
    notes:        list  = field(default_factory=list)

    @property
    def depth_ok(self) -> bool:
        return self.reach_days >= DEPTH_OK_DAYS

    @property
    def precision_ok(self) -> bool:
        return self.sample_n > 0 and self.precise_pct >= PRECISION_OK_PCT


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _reach_days(oldest: datetime | None) -> int:
    if oldest is None:
        return 0
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    return max(0, (int((_utc_now() - oldest).total_seconds()) // 86400))


def _is_intraday(dt: datetime) -> bool:
    """True hvis tidsstemplet har en ægte intradag-tid (ikke ren midnat 00:00:00).
    Et tidsstempel på rene midnat-grænse = kun dato-præcision (ubrugelig til
    minut-timing); alt andet bærer en intradag-tid."""
    return (dt.hour, dt.minute, dt.second) != (0, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# KILDE A — Finnhub
# ─────────────────────────────────────────────────────────────────────────────
async def probe_finnhub(symbols, depth_symbol, emit) -> SourceResult:
    import aiohttp
    res = SourceResult("Finnhub")
    emit("")
    emit("=" * 78)
    emit("  KILDE A — Finnhub REST company-news")
    emit("=" * 78)
    try:
        from finnhub_news import FINNHUB_API_KEY, FINNHUB_BASE
    except Exception as e:
        emit(f"  ❌ kunne ikke importere Finnhub-nøgle: {e}")
        res.notes.append("import-fejl")
        return res

    url = f"{FINNHUB_BASE}/company-news"
    oldest_overall: datetime | None = None

    async with aiohttp.ClientSession() as session:
        async def fetch(sym, frm_date, to_date):
            params = {"symbol": sym, "from": frm_date.isoformat(),
                      "to": to_date.isoformat(), "token": FINNHUB_API_KEY}
            async with session.get(url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=25)) as r:
                return await r.json() if r.status == 200 else []

        # ── 1. Dybde pr. ticker (vinduer der slutter NU, strækker N dage tilbage) ──
        for sym in symbols:
            emit("")
            emit(f"  {sym} — dybde (vindue slutter nu, strækker N dage tilbage):")
            emit(f"     {'N dage':>7}{'antal':>8}   {'ældste':<12}{'nyeste':<12}")
            for days in DEPTH_CHECKPOINTS:
                to_d  = datetime.now().date()
                frm_d = (datetime.now() - timedelta(days=days)).date()
                try:
                    items = await fetch(sym, frm_d, to_d)
                except Exception as e:
                    emit(f"     {days:>7}   FEJL {e}")
                    continue
                if not isinstance(items, list):
                    items = []
                if items:
                    dts = sorted(datetime.fromtimestamp(int(i.get("datetime", 0)), tz=timezone.utc)
                                 for i in items if int(i.get("datetime", 0)) > 0)
                    if dts:
                        old, new = dts[0], dts[-1]
                        emit(f"     {days:>7}{len(items):>8}   {old:%Y-%m-%d}  {new:%Y-%m-%d}")
                        res.total_count = max(res.total_count, len(items))
                        if oldest_overall is None or old < oldest_overall:
                            oldest_overall = old
                    else:
                        emit(f"     {days:>7}{len(items):>8}   (ingen gyldige tidsstempler)")
                else:
                    emit(f"     {days:>7}{0:>8}   (tom)")
                await asyncio.sleep(1.1)   # Finnhub gratis = 60/min

        res.reach_days = _reach_days(oldest_overall)

        # ── 2. Tidsstempel-præcision (stikprøve: seneste vindue, depth_symbol) ──
        emit("")
        emit(f"  Tidsstempel-præcision — {depth_symbol}, seneste {PRECISION_WINDOW_D} dage:")
        try:
            to_d  = datetime.now().date()
            frm_d = (datetime.now() - timedelta(days=PRECISION_WINDOW_D)).date()
            items = await fetch(depth_symbol, frm_d, to_d)
        except Exception as e:
            emit(f"     ❌ request-fejl: {e}")
            items = []
        sample = sorted([i for i in (items or []) if int(i.get("datetime", 0)) > 0],
                        key=lambda x: -int(x.get("datetime", 0)))[:PRECISION_SAMPLE]
        if sample:
            intraday = 0
            for it in sample:
                dt = datetime.fromtimestamp(int(it.get("datetime", 0)), tz=timezone.utc)
                if _is_intraday(dt):
                    intraday += 1
            res.sample_n = len(sample)
            res.precise_pct = 100.0 * intraday / len(sample)
            emit(f"     stikprøve: {len(sample)} nyheder · {intraday} med intradag-tid "
                 f"({res.precise_pct:.0f}%) · {len(sample) - intraday} kun dato/midnat")
            for it in sample[:4]:
                dt = datetime.fromtimestamp(int(it.get("datetime", 0)), tz=timezone.utc)
                emit(f"       [{dt:%Y-%m-%d %H:%M:%S} UTC] {str(it.get('headline',''))[:60]}")
        else:
            emit("     ingen nyheder i stikprøve-vinduet (kan ikke måle præcision)")

    return res


# ─────────────────────────────────────────────────────────────────────────────
# KILDE B — IBKR Dow Jones
# ─────────────────────────────────────────────────────────────────────────────
def _parse_ibkr_time(t) -> datetime | None:
    """IBKR-artikeltid 'YYYY-MM-DD HH:MM:SS[.f]' → naiv UTC-datetime (eller None)."""
    s = str(t)[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def probe_ibkr(host, port, client_id, symbols, depth_symbol, emit) -> SourceResult:
    from ib_async import IB, Stock
    res = SourceResult("IBKR Dow Jones")
    emit("")
    emit("=" * 78)
    emit("  KILDE B — IBKR Dow Jones (reqHistoricalNews)")
    emit("=" * 78)

    ib = IB()
    try:
        await ib.connectAsync(host, port, clientId=client_id, timeout=15)
    except Exception as e:
        emit(f"  ❌ FORBINDELSESFEJL: {e}")
        emit(f"     Tjek Gateway/TWS + port {port} + ledigt client-id {client_id}.")
        res.notes.append("forbindelsesfejl")
        return res

    try:
        accts = ib.managedAccounts()
        emit(f"  ✅ Forbundet. Konto: {', '.join(accts) or '(ingen)'}")

        # Berettigede providere → udvælg DJ-historik-kandidater.
        try:
            providers = await ib.reqNewsProvidersAsync()
        except Exception as e:
            emit(f"  ❌ kunne ikke hente providere: {e}")
            res.notes.append("provider-fejl")
            return res
        eligible = {getattr(p, "code", "") for p in providers}
        emit(f"  Berettigede providere: {', '.join(sorted(c for c in eligible if c)) or '(ingen)'}")
        use_codes = [c for c in DJ_HISTORICAL_CANDIDATES
                     if c in eligible and c not in SKIP_PROVIDERS]
        if not use_codes:
            emit("  ❌ INGEN Dow Jones-historik-provider berettiget — kan ikke dybde-teste IBKR.")
            res.notes.append("ingen DJ-provider")
            return res
        provider_str = "+".join(use_codes)
        emit(f"  Bruger DJ-providere: {provider_str}  (Briefing/Newsletters sprunget over)")

        # Hjælper: kvalificér → conId.
        async def conid_for(sym):
            c = Stock(sym, "SMART", "USD")
            await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=10)
            return getattr(c, "conId", None)

        oldest_overall: datetime | None = None

        # ── 1. Dybde pr. ticker (per-provider optælling fra ét kald pr. vindue) ──
        for sym in symbols:
            emit("")
            emit(f"  {sym} — dybde (totalResults={IBKR_TOTAL_RESULTS}, vindue strækker N dage tilbage):")
            try:
                conid = await conid_for(sym)
            except Exception as e:
                emit(f"     ❌ kvalificering: {e}")
                continue
            if not conid:
                emit("     ❌ ingen conId")
                continue
            emit(f"     conId={conid}   (artikler knyttes til denne ticker via conId ✓)")
            emit(f"     {'N dage':>7}{'antal':>8}   {'ældste':<12}{'nyeste':<12}  pr. provider")
            for days in DEPTH_CHECKPOINTS:
                start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S.0")
                end   = ""   # tom = nu
                try:
                    items = await asyncio.wait_for(
                        ib.reqHistoricalNewsAsync(conid, provider_str, start, end, IBKR_TOTAL_RESULTS),
                        timeout=30)
                except Exception as e:
                    emit(f"     {days:>7}   FEJL {e}")
                    continue
                if items:
                    dts = sorted(d for d in (_parse_ibkr_time(i.time) for i in items) if d)
                    per_prov = {}
                    for i in items:
                        per_prov[i.providerCode] = per_prov.get(i.providerCode, 0) + 1
                    prov_str = ", ".join(f"{k}={v}" for k, v in sorted(per_prov.items()))
                    cap = "  (loft nået)" if len(items) >= IBKR_TOTAL_RESULTS else ""
                    if dts:
                        emit(f"     {days:>7}{len(items):>8}   {dts[0]:%Y-%m-%d}  {dts[-1]:%Y-%m-%d}  "
                             f"{prov_str}{cap}")
                        res.total_count = max(res.total_count, len(items))
                        if oldest_overall is None or dts[0] < oldest_overall:
                            oldest_overall = dts[0]
                    else:
                        emit(f"     {days:>7}{len(items):>8}   (uparselige tidsstempler)  {prov_str}")
                else:
                    emit(f"     {days:>7}{0:>8}   (tom)")

        res.reach_days = _reach_days(oldest_overall)

        # ── 2. Tidsstempel-præcision (seneste ~50 på depth_symbol) ──
        emit("")
        emit(f"  Tidsstempel-præcision — {depth_symbol}, seneste {PRECISION_SAMPLE} artikler:")
        try:
            dconid = await conid_for(depth_symbol)
        except Exception as e:
            emit(f"     ❌ kvalificering af {depth_symbol}: {e}")
            dconid = None
        if dconid:
            try:
                recent = await asyncio.wait_for(
                    ib.reqHistoricalNewsAsync(dconid, provider_str, "", "", PRECISION_SAMPLE),
                    timeout=25)
            except Exception as e:
                emit(f"     ❌ reqHistoricalNews: {e}")
                recent = []
            parsed = [d for d in (_parse_ibkr_time(i.time) for i in (recent or [])) if d]
            if parsed:
                intraday = sum(1 for d in parsed if _is_intraday(d))
                res.sample_n = len(parsed)
                res.precise_pct = 100.0 * intraday / len(parsed)
                emit(f"     stikprøve: {len(parsed)} artikler · {intraday} med intradag-tid "
                     f"({res.precise_pct:.0f}%) · {len(parsed) - intraday} kun dato/midnat")
                for i, h in enumerate(recent[:4]):
                    emit(f"       [{h.time}] ({h.providerCode}) {str(h.headline)[:56]}")
            else:
                emit("     ingen parselige artikler (kan ikke måle præcision)")
    finally:
        ib.disconnect()

    return res


# ─────────────────────────────────────────────────────────────────────────────
# DOM
# ─────────────────────────────────────────────────────────────────────────────
def emit_verdict(sources: list[SourceResult], emit) -> str:
    emit("")
    emit("─" * 78)
    emit("  DOM — kan datagrundlaget bære en ærlig katalysator-backtest?")
    emit("─" * 78)
    emit(f"  Krav: DYBDE ≥{DEPTH_OK_DAYS} dage reel dækning · PRÆCISION ≥{PRECISION_OK_PCT:.0f}% "
         f"intradag-tidsstempler.")
    emit("")
    for s in sources:
        depth_ans = "JA " if s.depth_ok else "NEJ"
        prec_ans  = "JA " if s.precision_ok else "NEJ"
        prec_detail = (f"{s.precise_pct:.0f}% af {s.sample_n}" if s.sample_n
                       else "ingen stikprøve")
        note = f"   [{', '.join(s.notes)}]" if s.notes else ""
        emit(f"  {s.name:<16}  dybde: {depth_ans} (rækker {s.reach_days} dage)   "
             f"præcision: {prec_ans} ({prec_detail}){note}")
    emit("")

    green = any(s.depth_ok and s.precision_ok for s in sources)
    has_any = any(s.total_count > 0 for s in sources)
    has_strong_dim = any(s.depth_ok or s.precision_ok for s in sources)

    if green:
        cat = "GRØN"
        emit("  → GRØN: mindst én kilde har BÅDE dyb OG minut-præcis historik.")
        emit("    Backtest kan bygges på gratis data. Næste skridt: definér katalysator-")
        emit("    signal + byg latency-backtest (entry ved nyhed+1/5/15/30/60 min,")
        emit("    PF per latency, OOS-split, omkostnings-sweep — møllen-mønsteret).")
    elif has_any and has_strong_dim:
        cat = "GUL"
        emit("  → GUL: historik findes, men ingen ÉN kilde rammer både dybde OG præcision.")
        emit("    Backtest er mulig men svag — noter forbeholdene (hvilken dimension der")
        emit("    halter), og overvej om det er nok til at retfærdiggøre et signal-design.")
    else:
        cat = "RØD"
        emit("  → RØD: ingen kilde kan bære en ærlig backtest (dybde og/eller præcision")
        emit("    utilstrækkelig overalt). Katalysator kræver betalt historik (fx Benzinga)")
        emit("    FØR den kan testes — eller sporet parkeres som 'data utilstrækkelig'.")
    emit("")
    emit("  (Realtids-friskhed er IKKE målt her — separat spørgsmål, kun relevant hvis grøn/gul.)")
    return cat


# ─────────────────────────────────────────────────────────────────────────────
async def main_async(args, emit) -> list[SourceResult]:
    sources: list[SourceResult] = []
    if not args.skip_finnhub:
        sources.append(await probe_finnhub(args.symbols, args.depth_symbol, emit))
    if not args.skip_ibkr:
        sources.append(await probe_ibkr(args.host, args.port, args.client_id,
                                        args.symbols, args.depth_symbol, emit))
    return sources


def main() -> int:
    # UTF-8 stdout — headerne emitterer ≥/≤/— der ellers crasher en cp1252-konsol.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(
        description="Katalysator-møllen Trin 1: historisk nyheds-dybde + præcision (køber intet)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=27,
                    help="ledigt client-id der ikke kolliderer med backend")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--depth-symbol", default="NVDA",
                    help="hvilket (godt-dækket) navn præcisions-stikprøven tages på")
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

    emit("=" * 78)
    emit("  KATALYSATOR-MØLLEN — TRIN 1: historisk nyheds-datagrundlag (køber intet)")
    emit("=" * 78)
    emit(f"Tid: {datetime.now():%Y-%m-%d %H:%M}   testnavne: {', '.join(args.symbols)}   "
         f"dybde-/præcisions-navn: {args.depth_symbol}")
    emit("Måler: (1) historisk DYBDE pr. checkpoint, (2) tidsstempel-PRÆCISION. IKKE realtid.")

    try:
        sources = asyncio.run(main_async(args, emit))
    except KeyboardInterrupt:
        emit("\nAfbrudt.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 130

    cat = emit_verdict(sources, emit) if sources else "RØD"
    emit("")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    emit(f"→ Kategori: {cat}. Send summary.txt + kategori tilbage.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
