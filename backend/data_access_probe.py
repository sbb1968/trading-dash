#!/usr/bin/env python3
"""
data_access_probe.py
════════════════════
Aflæser om du HAR real-time markedsdata på en række futures — uden at abonnere
på noget. Den beder Gateway om LIVE data pr. kontrakt og læser status:
real-time, kun delayed, eller slet ikke abonneret (fejl 354/10167/10168).

Tester:
  • CME-micros:  MES (S&P), MNQ (Nasdaq), MGC (guld)  — bekræfter real-time futures
  • Eurex:       DAX, EuroStoxx 50                      — til London-morgensporet

Køber INTET. reqMktData på et uabonneret instrument giver bare en fejl/delayed —
ingen månedsgebyrer udløses. Kør mod Gateway på algoserveren (ingen Client
Portal-login → feeden bumpes ikke). Egen client-id 40 (distinkt fra 27/35/36).

Python 3.14: event-loop-fix øverst. ib_async.

Brug:
    python data_access_probe.py
    python data_access_probe.py --port 7497 --client-id 40

Placering: C:\\projects\\trading_dash\\backend\\data_access_probe.py
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
from datetime import date, datetime
from pathlib import Path

OUTPUT_DIRNAME = "data_access_probe_output"

# (label, kwargs til kontrakt). localSymbol = kendt front-måned (M6 = juni 2026);
# symbol = lad reqContractDetails finde front-måneden (Eurex — ukendte localsymbols).
INSTRUMENTS = [
    ("CME micro S&P (MES)",      dict(localSymbol="MESM6", exchange="CME",   currency="USD")),
    ("CME micro Nasdaq (MNQ)",   dict(localSymbol="MNQM6", exchange="CME",   currency="USD")),
    ("COMEX micro guld (MGC)",   dict(localSymbol="MGCM6", exchange="COMEX", currency="USD")),
    ("Eurex DAX",                dict(symbol="DAX",        exchange="EUREX", currency="EUR")),
    ("Eurex EuroStoxx 50",       dict(symbol="ESTX50",     exchange="EUREX", currency="EUR")),
]

# fejlkoder
NOT_SUB = {354, 10168}       # ikke abonneret, ingen/ingen-delayed
DELAYED_SHOWN = {10167}      # ikke abonneret — viser delayed
COMPETING = {10197}          # abonneret, men konkurrerende live-session
BENIGN = {2104, 2106, 2107, 2108, 2158, 2150, 2119}  # data-farm-status, ufarlige


def classify(mdt, got_price, error_codes):
    """Ren klassificering — unit-testbar."""
    codes = set(error_codes)
    if codes & COMPETING:
        return "ABONNERET (men konkurrerende live-session er aktiv)"
    if codes & NOT_SUB:
        return "IKKE ABONNERET (ingen live, ingen delayed)"
    if codes & DELAYED_SHOWN:
        return "IKKE ABONNERET (kun delayed tilgængelig)"
    if mdt == 1:
        return "REAL-TIME LIVE" if got_price else "REAL-TIME LIVE (frosset — marked lukket)"
    if mdt in (3, 4):
        return "KUN DELAYED (ikke abonneret på live)"
    if mdt == 2:
        return "REAL-TIME (frozen — marked lukket)"
    return "UAFKLARET (ingen ticks/fejl modtaget)"


def _front_month(cds):
    """Vælg nærmeste kontrakt-måned >= i dag fra reqContractDetails-resultater."""
    today = date.today().strftime("%Y%m%d")
    best = None
    for cd in cds:
        c = cd.contract
        exp = (c.lastTradeDateOrContractMonth or "")
        key = (exp + "01")[:8] if len(exp) == 6 else exp[:8]
        if key and key >= today and (best is None or key < best[0]):
            best = (key, c)
    if best is None and cds:  # alt udløbet — tag seneste
        best = ("", sorted(cds, key=lambda x: x.contract.lastTradeDateOrContractMonth or "")[-1].contract)
    return best[1] if best else None


async def resolve_contract(ib, kwargs):
    from ib_async import Future
    if "localSymbol" in kwargs:
        c = Future(**kwargs)
        try:
            q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=10)
            # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
            return q[0] if (q and getattr(q[0], "conId", 0)) else None
        except Exception:
            return None
    # symbol → find front-måned via contract details
    try:
        cds = await asyncio.wait_for(ib.reqContractDetailsAsync(Future(**kwargs)), timeout=15)
    except Exception:
        return None
    return _front_month(cds) if cds else None


async def probe(host, port, client_id, emit):
    from ib_async import IB
    ib = IB()
    errors = []  # (reqId, code, msg)

    def on_error(reqId, code, msg, *_):
        errors.append((reqId, code, msg))

    ib.errorEvent += on_error
    await ib.connectAsync(host, port, clientId=client_id, timeout=15)
    ib.reqMarketDataType(1)  # bed om LIVE; ikke-abonnerede giver fejl (ingen auto-delayed)
    results = []
    try:
        for label, kwargs in INSTRUMENTS:
            emit(f"  prøver {label} ...")
            c = await resolve_contract(ib, kwargs)
            if c is None:
                results.append((label, "—", "KUNNE IKKE FINDE KONTRAKT (symbol/expiry)"))
                continue
            errors.clear()
            ticker = ib.reqMktData(c, "", False, False)
            await asyncio.sleep(3.5)
            prices = [ticker.last, ticker.close, ticker.bid, ticker.ask]
            got_price = any(p is not None and not (isinstance(p, float) and math.isnan(p)) for p in prices)
            codes = [code for _, code, _ in errors if code not in BENIGN]
            mdt = getattr(ticker, "marketDataType", None)
            verdict = classify(mdt, got_price, codes)
            detail = c.localSymbol or f"{c.symbol} {c.lastTradeDateOrContractMonth}"
            note = ""
            if codes:
                msgs = "; ".join(sorted({m.split(":")[-1].strip()[:48] for _, cc, m in errors if cc in codes}))
                note = f"  [{msgs}]"
            results.append((label, detail, verdict + note))
            try:
                ib.cancelMktData(c)
            except Exception:
                pass
    finally:
        ib.disconnect()
    return results


def main():
    ap = argparse.ArgumentParser(description="IBKR real-time data-adgangs-probe (køber intet)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=40)
    args = ap.parse_args()

    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 78)
    emit("  IBKR REAL-TIME DATA-ADGANGS-PROBE  (køber intet)")
    emit("=" * 78)
    emit(f"Tid: {datetime.now():%Y-%m-%d %H:%M}   Gateway: {args.host}:{args.port}   "
         f"client-id {args.client_id}")
    emit("Bemærk: marked lukket = 'frosset' men stadig real-time HVIS abonneret.")
    emit("")

    try:
        results = asyncio.run(probe(args.host, args.port, args.client_id, emit))
    except Exception as e:
        emit(f"FORBINDELSESFEJL: {e}")
        emit("Tjek at Gateway kører på algoserveren og at API-port er åben.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    emit("")
    emit("─" * 78)
    emit(f"  {'instrument':<26}{'kontrakt':<14}status")
    emit("─" * 78)
    for label, detail, verdict in results:
        emit(f"  {label:<26}{detail:<14}{verdict}")
    emit("")

    live = [l for l, _, v in results if v.startswith("REAL-TIME") or v.startswith("ABONNERET")]
    nope = [l for l, _, v in results if "IKKE ABONNERET" in v or "KUN DELAYED" in v]
    emit("─" * 78)
    emit("  OPSUMMERING")
    emit("─" * 78)
    emit(f"  Real-time/abonneret: {', '.join(live) if live else '(ingen)'}")
    emit(f"  Mangler abonnement:  {', '.join(nope) if nope else '(ingen)'}")
    emit("  → Eurex-status afgør om London-morgensporet kræver nyt dataabonnement.")
    emit("  → Real-time på CME-micros = forudsætning før evt. live futures-handel.")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())