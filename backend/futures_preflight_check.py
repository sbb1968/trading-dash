#!/usr/bin/env python3
"""
futures_preflight_check.py — futures-connectivity + data-fidelitet for Europa-reversion
═══════════════════════════════════════════════════════════════════════════════════════
KØRES MOD TWS. Pre-paper-verifikation af det STØRSTE ukendte i Europa-reversion:
futures-stien (dit første futures-system). Den offline-valideringskæde er færdig og
bestået; det her svarer på det live IBKR afgør.

Bruger ib_async DIREKTE med et eksplicit, unikt client-id (default 27), så den IKKE
kolliderer med en kørende backend/K2 på samme TWS — samme mønster som dine
probe-scripts (data_access_probe.py). Den genbruger strategiens EGEN z-regel
(rule.compute_z) + config + FUTURES_EXCHANGE, så den kvalificerer præcis de samme
front-måned-kontrakter og beregner z præcis som live gør.

Fire tjek pr. instrument (MES, M2K), hver med ✅/⚠️/❌:

  1. KVALIFICERING — front-måned-Future via reqContractDetails + front-måned-valg
     (10339-sikkert, ikke ContFuture). Viser kontrakt + udløb.
  2. MULTIPLIKATOR — er $/point virkelig 5.0? config.py ANTAGER det og flagger det
     til verifikation; sizing (§2) afhænger af det.
  3. REALTID vs FORSINKET  ← det vigtigste. En 15-min-bar-strategi på 15-min FORSINKET
     data ser bars 15 min for sent → entries lander forkert → paper afspejler IKKE
     virkeligheden. Beder om LIVE (type 1); virker det ikke, prøver DELAYED (type 3).
  4. WARMUP + LIVE-Z — henter 15-min warmup-bars (useRTH=False som strategien) og
     beregner z med rule.compute_z; bekræfter ≥ LOOKBACK bars og endeligt z.

Læs-only: handler ikke, sender ingen ordrer, ændrer intet.

Køres fra backend/ (så strategi-pakken kan importeres):
    python futures_preflight_check.py
    python futures_preflight_check.py --port 7497 --client-id 27

NB: dette er Europa-reversion-versionen — den tjekker ud over data_access_probe.py
også multiplikatoren og kører strategiens FAKTISKE z-beregning på rigtige bars.

Placering: C:\\Projects\\trading_dash\\backend\\futures_preflight_check.py
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from datetime import date, datetime

# Strategiens EGEN kode — så diagnostikken tester præcis det live kører.
try:
    from strategies.europa_reversion import rule
    from strategies.europa_reversion.config import (
        INSTRUMENTS, MULTIPLIER, LOOKBACK, BAR_SIZE,
    )
    from ibkr_connect import FUTURES_EXCHANGE
except ImportError as e:
    print(f"FEJL: kør fra backend/ så strategi-pakken kan importeres. ({e})")
    sys.exit(1)

GREEN, YELLOW, RED = "✅", "⚠️ ", "❌"

# Fejlkoder vi ignorerer i realtid-probet (ikke ægte data-problemer)
BENIGN = {2104, 2106, 2107, 2108, 2119, 2158, 2100, 2150}


def _isnan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _valid_price(t) -> bool:
    for v in (getattr(t, "last", None), getattr(t, "close", None),
              getattr(t, "bid", None), getattr(t, "ask", None)):
        if v is not None and not _isnan(v) and v > 0:
            return True
    return False


async def qualify_front_future(ib, sym: str):
    """Resolve front-måned-Future for sym via reqContractDetails (10339-sikkert).
    Spejler ibkr_connect._front_month: nærmeste ikke-udløbne udløb."""
    from ib_async import Future
    exch = FUTURES_EXCHANGE.get(sym)
    if not exch:
        return None
    try:
        details = await asyncio.wait_for(
            ib.reqContractDetailsAsync(Future(symbol=sym, exchange=exch, currency="USD")),
            timeout=15,
        )
    except Exception:
        return None
    if not details:
        return None
    today = date.today().strftime("%Y%m%d")
    best = None
    for cd in details:
        c = cd.contract
        exp = c.lastTradeDateOrContractMonth or ""
        key = (exp + "01")[:8] if len(exp) == 6 else exp[:8]
        if key and key >= today and (best is None or key < best[0]):
            best = (key, c)
    if best is None and details:   # alt udløbet — tag seneste
        best = ("", sorted(details,
                key=lambda x: x.contract.lastTradeDateOrContractMonth or "")[-1].contract)
    return best[1] if best else None


async def probe_market_data_type(ib, contract, errors: list) -> tuple[str, str]:
    """Realtid vs forsinket. Beder om LIVE (type 1); ellers DELAYED (type 3)."""
    # ── Forsøg 1: LIVE ──
    try:
        ib.reqMarketDataType(1)
        errors.clear()
        tkr = ib.reqMktData(contract, "", False, False)
        for _ in range(8):
            await asyncio.sleep(0.5)
            if _valid_price(tkr):
                break
        mdt = getattr(tkr, "marketDataType", None)
        live_ok = _valid_price(tkr)
        codes = [c for (_, c, _) in errors if c not in BENIGN]
        ib.cancelMktData(contract)
    except Exception as e:
        return RED, f"live-probe fejl: {e}"

    if live_ok and mdt == 1:
        return GREEN, "REALTID (live subscription aktiv) — paper afspejler virkeligheden"
    if live_ok and mdt in (2, None):
        return YELLOW, f"data ankom (marketDataType={mdt}) men ikke bekræftet live — undersøg"

    sub_err = 354 in codes or 10167 in codes or 10089 in codes  # market data not subscribed

    # ── Forsøg 2: DELAYED ──
    await asyncio.sleep(0.4)
    try:
        ib.reqMarketDataType(3)
        tkr = ib.reqMktData(contract, "", False, False)
        for _ in range(8):
            await asyncio.sleep(0.5)
            if _valid_price(tkr):
                break
        delayed_ok = _valid_price(tkr)
        ib.cancelMktData(contract)
    except Exception as e:
        return RED, f"delayed-probe fejl: {e}"

    if delayed_ok:
        tail = " (IBKR svarede 'ikke abonneret' på live)" if sub_err else ""
        return RED, ("KUN FORSINKET (~15 min)" + tail + " — en 15-min-bar-strategi ser bars 15 min "
                     "for sent. Paper afspejler IKKE virkeligheden. Fix: futures-data-bundt "
                     "($10/md, sandsynligvis waived ved kommission) FØR paper-validering tæller.")
    return RED, "INGEN markedsdata (hverken live eller delayed) — tjek subscription/symbol"


async def check_instrument(ib, sym: str, errors: list) -> bool:
    print("─" * 78)
    print(f"  {sym}")
    print("─" * 78)
    ok = True

    # ── 1) Kvalificering ──
    fut = await qualify_front_future(ib, sym)
    if fut is None:
        print(f"   {RED} KVALIFICERING: kunne ikke resolve front-måned for {sym}")
        return False
    expiry = getattr(fut, "lastTradeDateOrContractMonth", "?")
    local  = getattr(fut, "localSymbol", "?")
    exch   = getattr(fut, "exchange", "?")
    print(f"   {GREEN} KVALIFICERING: {local}  udløb {expiry}  ({exch})")

    # ── 2) Multiplikator ──
    raw_mult = getattr(fut, "multiplier", None)
    try:
        mult = float(raw_mult) if raw_mult not in (None, "") else None
    except (TypeError, ValueError):
        mult = None
    expected = MULTIPLIER.get(sym)
    if mult is None:
        print(f"   {YELLOW}MULTIPLIKATOR: IBKR rapporterede ingen — kan ikke verificere (config antager {expected})")
        ok = False
    elif expected is not None and abs(mult - expected) < 1e-9:
        print(f"   {GREEN} MULTIPLIKATOR: ${mult:.0f}/point — matcher config")
    else:
        print(f"   {RED} MULTIPLIKATOR: IBKR siger ${mult:.0f}/point, config antager ${expected}/point "
              f"— RET config.MULTIPLIER['{sym}'] = {mult} (sizing er forkert ellers)")
        ok = False

    # ── 3) Realtid vs forsinket ──
    emoji, desc = await probe_market_data_type(ib, fut, errors)
    print(f"   {emoji}DATA: {desc}")
    if emoji == RED:
        ok = False

    # ── 4) Warmup-bars + live-z ──
    try:
        raw = await asyncio.wait_for(ib.reqHistoricalDataAsync(
            fut, endDateTime="", durationStr="3 D",
            barSizeSetting=BAR_SIZE, whatToShow="TRADES", useRTH=False, formatDate=2),
            timeout=30)
    except Exception as e:
        print(f"   {RED} WARMUP: reqHistoricalData fejlede: {e}")
        return False
    n = len(raw) if raw else 0
    if n == 0:
        print(f"   {RED} WARMUP: 0 bars — kan ikke beregne z")
        return False
    if n < LOOKBACK:
        print(f"   {YELLOW}WARMUP: kun {n} bars (<{LOOKBACK}) — z bliver klar når flere ankommer")
        ok = False
    else:
        closes = [float(b.close) for b in raw[-LOOKBACK:]]
        res = rule.compute_z(closes)
        last_ts = getattr(raw[-1], "date", "?")
        if res is None:
            print(f"   {YELLOW}WARMUP: {n} bars OK, men z udefineret (std≤0?) — usædvanligt for futures")
            ok = False
        else:
            z, sd = res
            print(f"   {GREEN} WARMUP: {n} bars · live-z = {z:+.2f} (std {sd:.4f}) · seneste bar {last_ts}")

    return ok


async def main_async(args) -> int:
    from ib_async import IB

    print("=" * 78)
    print("  EUROPA-REVERSION — FUTURES PRE-PAPER DIAGNOSTIK")
    print("=" * 78)
    print(f"  Instrumenter: {', '.join(INSTRUMENTS)} · bar {BAR_SIZE} · lookback {LOOKBACK}")
    print(f"  Port {args.port} · client-id {args.client_id} · LÆS-ONLY (ingen ordrer)\n")

    ib = IB()
    errors: list = []
    def on_error(reqId, code, msg, *_):
        errors.append((reqId, code, msg))
    ib.errorEvent += on_error

    try:
        await ib.connectAsync(args.host, args.port, clientId=args.client_id, timeout=15)
    except Exception as e:
        print(f"{RED} Kunne ikke forbinde til IBKR: {e}")
        print("   Tjek: TWS/Gateway åben, logget ind, API aktiveret, port 7497, og at")
        print(f"   client-id {args.client_id} ikke allerede er i brug (skift med --client-id).")
        return 1
    print(f"{GREEN} Forbundet til IBKR\n")

    results = {}
    try:
        for sym in INSTRUMENTS:
            results[sym] = await check_instrument(ib, sym, errors)
            print()
    finally:
        ib.disconnect()

    # ── Dom ──
    print("=" * 78)
    print("  DOM")
    print("=" * 78)
    all_ok = all(results.values())
    for sym, ok in results.items():
        print(f"   {GREEN if ok else RED} {sym}: {'klar til paper' if ok else 'problem — se ovenfor'}")
    print()
    if all_ok:
        print("  ✅ Futures-stien er verificeret på DENNE konto. Næste skridt: paper-handel én")
        print("     EU-session (08–14 dansk) — følg at den handler kun i sessionen, evaluerer på")
        print("     færdige bars, tvangslukker 13:55, og at live-handler matcher backtest.")
        print("  HUSK: realtid-svaret gælder KUN denne konto. Produktion = algoserver (DUO509856)")
        print("        — kør samme tjek dér før det autonome job sættes op.")
    else:
        print("  Ret punkterne ovenfor FØR paper-validering — ellers måler vi på et forkert grundlag.")
        print("  Vigtigst: er data REALTID? Forsinket futures-data gør paper-resultater misvisende.")
    return 0 if all_ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Futures pre-paper diagnostik for Europa-reversion")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7497)
    ap.add_argument("--client-id", type=int, default=27, help="eksplicit unikt id — undgå kollision med kørende backend/K2")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nAfbrudt.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
