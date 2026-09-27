"""
nt_klik_test.py — sender præcis det watchlist-knappen sender
════════════════════════════════════════════════════════════════════════════════
Frontenden har en kalendervagt (`tradeStatus`) der spærrer MES når CME er
lukket. Den er rigtig, men den står FØR bekræftelsen, så knappen kan ikke bruges
til at teste ordrevejen uden for handelstid.

Dette script sender den samme WebSocket-besked som knappen. Det springer KUN
frontendens kalendervagt over — dispatchen, V1-V4, journal-vagten, OIF-skrivningen,
`afvent_ordre` og forensikken køres alle som ved et rigtigt klik.

⚠ DEN LÆGGER EN RIGTIG ORDRE PÅ Sim101 (NT8's lokale simulator — ingen penge,
ingen broker). Og den ANNULLERER DEN IGEN, altid, også når noget fejler
undervejs. Begrundelsen står i ninjatrader_ordre_test.py: testkørslen 16-09
efterlod `TDPROBE1789542345` Working på Sim101 i ti dage. En DAY-ordre lagt før
en session kan fylde når markedet åbner — her om få timer — og så har vi selv
lavet en ejerløs position.

⚠ OG ANNULLERINGEN VERIFICERES I STRØMMEN, ikke i loggen. NT8's log skriver
Name='' på ordren, så vores id står ikke dér; ATI pusher det præcist.

    python nt_klik_test.py              # de sikre tjek + én rigtig ordre
    python nt_klik_test.py --kun-afvist # KUN de tjek der ikke lægger noget
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WS = "ws://127.0.0.1:8000/ws"
SVAR = "ibkr_order_result"


async def _send(besked: dict, vent: float = 60.0) -> dict | None:
    """Send én ordrebesked og returnér backendens ibkr_order_result."""
    import websockets
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps(besked))
        slut = asyncio.get_event_loop().time() + vent
        while asyncio.get_event_loop().time() < slut:
            try:
                raa = await asyncio.wait_for(ws.recv(), timeout=vent)
            except asyncio.TimeoutError:
                return None
            try:
                d = json.loads(raa)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("type") == SVAR:
                return d
    return None


def _vis(d: dict | None) -> None:
    if d is None:
        print("      (intet svar inden for tiden)")
        return
    ok = d.get("success")
    print(f"      success   : {ok}")
    if not ok:
        print(f"      error     : {d.get('error')}")
        return
    for n in ("status", "filled", "avg_fill", "konto", "broker",
              "forbindelse", "order_ref", "nt_spist", "nt_oplaeg"):
        if n in d:
            print(f"      {n:<10}: {d[n]}")
    for l in (d.get("nt_log") or []):
        print(f"      NT8-log   : {str(l)[:150]}")


async def afvist_uden_broker() -> bool:
    """⚠ Den vigtigste enkeltregel: der er INGEN standardbroker."""
    print("\n  1. Ordre UDEN broker-felt (gammel app.exe)")
    d = await _send({"type": "ordre_buy", "ticker": "MES", "shares": 1}, vent=20)
    _vis(d)
    ok = bool(d and d.get("success") is False
              and "broker" in (d.get("error") or "").lower())
    print(f"      -> {'OK, spaerret' if ok else '⚠ IKKE SPAERRET'}")
    return ok


async def afvist_med_vrovl() -> bool:
    print("\n  2. Ordre med ukendt broker")
    d = await _send({"type": "ordre_buy", "ticker": "MES", "shares": 1,
                     "broker": "TRADOVATE"}, vent=20)
    _vis(d)
    ok = bool(d and d.get("success") is False)
    print(f"      -> {'OK, spaerret' if ok else '⚠ IKKE SPAERRET'}")
    return ok


async def rigtig_ordre() -> bool:
    print("\n  3. ⚠ RIGTIG ORDRE: koeb 1 MES via NT8 (Sim101)")
    print("      (op mod 30 sek — OIF-filen skal spises, saa lyttes der)")
    d = await _send({"type": "ordre_buy", "ticker": "MES", "shares": 1,
                     "broker": "NT8"}, vent=90)
    _vis(d)
    if not d or not d.get("success"):
        return False

    ref = d.get("order_ref") or ""
    if not ref:
        print("      ⚠ INTET ORDRE-ID I SVARET — kan ikke annullere maalrettet.")
        print("      ⚠ TJEK NT8's Orders-fane MANUELT.")
        return False

    # ── Oprydningen. Sker uanset udfald. ─────────────────────────────────
    print(f"\n  4. Annullerer {ref} — en DAY-ordre maa ikke overleve til aabningen")
    import nt_forbindelse as NT
    try:
        svar = await asyncio.to_thread(NT.annuller, ref)
        for l in (svar.get("logliner") or [])[:4]:
            print(f"      NT8-log   : {str(l)[:150]}")
        await asyncio.sleep(2)
        # ⚠ I STROEMMEN. Loggen skriver Name='' og kender ikke vores id.
        status = await asyncio.to_thread(NT.ordre_status, ref)
        print(f"      status    : {status or '(ikke set i dette oplaeg)'}")
        if status in NT.TERMINALE:
            print("      -> OK, bekraeftet terminal")
            return True
        # ⚠ "" er UKENDT, ikke "vaek". Stroemmen er et oejebliksbillede.
        print("      ⚠ ikke bekraeftet terminal — rydder HELE Sim101")
        svar = await asyncio.to_thread(NT.annuller_alt, "Sim101")
        for l in (svar.get("logliner") or [])[:4]:
            print(f"      NT8-log   : {str(l)[:150]}")
        await asyncio.sleep(2)
        status = await asyncio.to_thread(NT.ordre_status, ref)
        print(f"      status    : {status or '(ikke set)'}")
        if status not in NT.TERMINALE:
            print(f"      ⚠⚠ TJEK NT8's Orders-fane MANUELT for {ref}")
            return False
        return True
    except Exception as e:
        print(f"      ⚠ ANNULLERING FEJLEDE: {type(e).__name__}: {e}")
        print(f"      ⚠⚠ TJEK NT8's Orders-fane MANUELT for {ref}")
        return False


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kun-afvist", action="store_true",
                    help="koer kun de tjek der IKKE laegger en ordre")
    a = ap.parse_args()

    print("  ── nt_klik_test: samme besked som watchlist-knappen ──")
    r1 = await afvist_uden_broker()
    r2 = await afvist_med_vrovl()
    if a.kun_afvist:
        print("\n  (--kun-afvist: ingen ordre lagt)")
        return 0 if (r1 and r2) else 1
    r3 = await rigtig_ordre()
    print(f"\n  {'ALT GRoeNT' if (r1 and r2 and r3) else '⚠ se ovenfor'}")
    return 0 if (r1 and r2 and r3) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
