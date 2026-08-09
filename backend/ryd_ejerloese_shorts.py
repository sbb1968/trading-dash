#!/usr/bin/env python3
"""
ryd_ejerloese_shorts.py — ENGANGS-VAERKTOEJ, slettes efter brug
═══════════════════════════════════════════════════════════════
Lukker de korte aktie-positioner der ligger paa kontoen UDEN journal-spor fra
nogen strategi. De er over-sell-residualer: en lukkeordre blev ikke bekraeftet
inden for 8 sekunder, strategien gen-afgav den, og BEGGE fyldte — den ene
lukkede positionen, den anden aabnede en tilsvarende short.

HVORFOR IKKE manual_reconcile.py? Fordi den med VILJE aldrig roerer en position
uden journal-spor — det er dens sikkerhedsgaranti mod at lukke en anden
strategis eller en manuel handel. Netop derfor kan den ikke rydde op her.

HVAD DEN GOER
  - Laeser IBKR's faktiske positioner (reliable read, ikke cache)
  - Finder KORTE positioner uden aaben journal-raekke fra nogen kilde
  - Sender ÉN daekningsordre (BUY MKT) pr. symbol med deterministisk orderRef
  - GEN-AFGIVER ALDRIG. Netop den adfaerd skabte problemet.

Koeres i weekenden fylder ordrerne foerst ved aabning mandag. Det er forventet:
status vil staa 'Inactive'/'PreSubmitted' indtil da. Verificér mandag.

BRUG (fra backend/ paa ALGOSERVEREN, hvor TWS koerer):
    python ryd_ejerloese_shorts.py              # PREVIEW — roerer intet
    python ryd_ejerloese_shorts.py --execute    # sender daekningsordrer

SIKKERHED
  - Preview er default. Intet sendes uden --execute.
  - Afbryder hvis en strategi koerer (--force overstyrer).
  - Roerer KUN korte positioner uden journal-spor. Lange positioner og alt med
    et journal-spor lades i fred.
  - Egen tilfaeldig client-id -> kicker ikke backendens IBKR-forbindelse.

Placering: C:\\Projects\\trading_dash\\backend\\ryd_ejerloese_shorts.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request

# Python 3.14 event loop-fix (som resten af projektet)
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


ORDER_REF_PREFIX = "cleanup_orphan_short"


def backend_algo_running():
    """True/False fra backendens /status (algo.running); None hvis ikke naaet."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=3) as r:
            d = json.loads(r.read().decode("utf-8"))
        return bool(d.get("algo", {}).get("running"))
    except Exception:
        return None


async def open_journal_symbols(db) -> set[str]:
    """Symboler der har en AABEN journal-raekke fra en hvilken som helst kilde.

    Disse roerer vi ALDRIG: de tilhoerer en strategi der selv styrer dem.
    """
    from trade_queries import list_trades
    rows = await list_trades(db, status="open")
    return {(r.get("symbol") or "").upper() for r in rows if r.get("symbol")}


async def run(execute: bool, force: bool, db_path: str) -> int:
    from accounts import load_identity, aktiv_konto
    from journal import Journal
    from ibkr_connect import IBKRConnection

    identity = load_identity()
    print(f"\n  Konto: {aktiv_konto()} "
          f"({'paper' if identity.paper_trading else 'LIVE'})")

    if not identity.paper_trading:
        print("  ❌ AFBRUDT: dette vaerktoej er kun tiltaenkt paper-kontoen.")
        return 1

    running = backend_algo_running()
    if running and not force:
        print("  ❌ AFBRUDT: en strategi koerer lige nu (algo.running=true).")
        print("     Vent til efter sessionen, eller brug --force hvis bevidst.")
        return 1
    if running is None:
        print("  ⚠  Kunne ikke naa backendens /status — fortsaetter "
              "(koer helst naar ingen strategi handler).")

    conn = IBKRConnection(paper_trading=identity.paper_trading)
    if not await conn.connect():
        print("  ❌ Kunne ikke forbinde til IBKR/TWS.")
        return 1

    try:
        positions, feed_ok = await conn.get_positions_reliable()
        if not feed_ok:
            print("  ❌ AFBRUDT: positions-feedet er upaalideligt (tomt/timeout).")
            print("     En tom liste her maa ALDRIG tolkes som 'ingen positioner'.")
            return 1

        journal = Journal(db_path)
        await journal.init()      # AABNER aiosqlite-forbindelsen (_db)
        try:
            owned = await open_journal_symbols(journal._db)
        finally:
            await journal.close()

        shorts = [p for p in positions if (p.get("position") or 0) < 0]
        orphans = [p for p in shorts
                   if (p.get("ticker") or "").upper() not in owned]
        adopted = [p for p in shorts
                   if (p.get("ticker") or "").upper() in owned]

        print(f"\n  IBKR-positioner i alt : {len(positions)}")
        print(f"  heraf korte           : {len(shorts)}")
        print(f"  med journal-spor      : {len(adopted)}  (roeres IKKE)")
        print(f"  EJERLOESE korte       : {len(orphans)}  (daekkes)")

        if adopted:
            print("\n  Lades i fred (har journal-spor):")
            for p in adopted:
                print(f"    {p['ticker']:<6} {p['position']:>+7.0f}")

        if not orphans:
            print("\n  ✅ Ingen ejerloese korte positioner. Intet at goere.")
            return 0

        print("\n  Daekkes med BUY MKT:")
        for p in orphans:
            print(f"    {p['ticker']:<6} {p['position']:>+7.0f}  ->  BUY "
                  f"{abs(int(p['position']))}")

        if not execute:
            print("\n  PREVIEW — intet er sendt. Koer med --execute for at daekke.")
            return 0

        print("\n  Sender daekningsordrer ...")
        sent = 0
        doede = 0
        for p in orphans:
            sym = p["ticker"]
            qty = abs(int(p["position"]))
            ref = f"{ORDER_REF_PREFIX}_{sym}"
            try:
                # await_fill_sec=0: vi VENTER ikke og gen-afgiver ALDRIG. Det var
                # netop gen-afgivelsen paa en ubekraeftet ordre der skabte disse
                # positioner. Én ordre pr. symbol, punktum.
                #
                # tif="DAY" er IKKE valgfrit. Uden den tvang TWS' order preset
                # TIF til GTC, og en MARKEDSORDRE med GTC er ugyldig hos IBKR —
                # alle otte ordrer blev annulleret med fejl 10349 (3/8-2026).
                res = await conn.place_paper_order(
                    sym, "BUY", qty, source="cleanup", order_ref=ref, tif="DAY")
            except Exception as e:
                print(f"    ❌ {sym}: undtagelse — {e}")
                continue

            if not res:
                print(f"    ❌ {sym}: ordren kunne IKKE sendes")
                continue

            status = str(res.get("status") or "")
            why = res.get("reject_reason")
            # En ordre der er annulleret eller afvist er IKKE afgivet. Foer
            # rapporterede scriptet "sendt" alene fordi der kom et svar tilbage —
            # og pyntede dermed over otte annullerede ordrer.
            if status in ("Cancelled", "ApiCancelled", "Inactive"):
                doede += 1
                print(f"    ❌ {sym}: ordren blev {status} — INGEN daekning"
                      + (f"  ({why})" if why else ""))
                continue

            sent += 1
            print(f"    ✅ {sym}: BUY {qty} afgivet  (id={res.get('order_id')}, "
                  f"status={status or '?'}, filled={res.get('filled')})"
                  + (f"  IBKR: {why}" if why else ""))

        print(f"\n  {sent}/{len(orphans)} ordrer lever · {doede} annulleret/afvist.")
        if doede:
            print("  ⚠ De annullerede daekker INTET. Se fejlkoden ovenfor.")
        if sent:
            print("  Er markedet lukket, staar de levende som PreSubmitted til "
                  "aabningen — det er forventet.")
        print("  VERIFICÉR at kontoen er flad paa disse symboler, og slet "
              "derefter dette script.")
        return 0 if not doede else 1
    finally:
        conn.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Engangs-oprydning: daek ejerloese korte aktie-positioner")
    ap.add_argument("--execute", action="store_true",
                    help="send faktisk daekningsordrerne (default: preview)")
    ap.add_argument("--force", action="store_true",
                    help="koer selv om en strategi er markeret koerende")
    ap.add_argument("--db", default="trading_dash.db")
    args = ap.parse_args()
    return asyncio.run(run(args.execute, args.force, args.db))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
