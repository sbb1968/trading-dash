#!/usr/bin/env python3
"""
ninjatrader_ordre_test.py — kan ATI faktisk lægge en ordre?
════════════════════════════════════════════════════════════════════════════════
Sidste ubesvarede spørgsmål i `ninjatrader_adgang.md`. Læsevejen er bevist
(ATI pusher kontotilstand af sig selv); skrivevejen er ikke.

⚠ DER LÆGGES EN LIMITORDRE LANGT FRA MARKEDET, IKKE EN MARKEDSORDRE.

Samme mønster som T4/T5 i `konto2_opsaetning.md`: en ordre der beviseligt ikke
kan fylde, og som annulleres igen med det samme. En markedsordre ville bevise
præcis det samme og efterlade en position — og en position vi ikke havde tænkt
os at have, er den dyreste måde at få et ja på.

FIRE VAGTER, og de er ubetingede:

  V1  KONTOEN skal være den forventede simulationskonto. Ingen default, ingen
      gæt. ⚠ ATI's `Default account` findes netop for at gætte, og det er derfor
      kontoen skrives eksplicit i hver eneste kommando.
  V2  AFSTAND, målt mod en kurs scriptet henter SELV. Første udgave sammenlignede
      limit med det tal brugeren tastede — men limit blev jo beregnet AF det tal,
      så afstanden var altid 40 % og vagten kunne aldrig fyre. En kontrol hvis
      udfald er afgjort af dens egen aritmetik, måler ingenting. Nu hentes MES
      fra vores eget /quote, og limit sammenlignes med DEN.
  V3  TIF = DAY. Aldrig GTC. En ordre vi ikke får annulleret, skal dø af sig
      selv ved sessionens slutning.
  V4  ANNULLÉR ALTID, også hvis noget så forkert ud undervejs. En ordre vi ikke
      kan forklare, skal ikke ligge og vente på at blive forstået.

    python ninjatrader_ordre_test.py                 # PREVIEW, sender intet
    python ninjatrader_ordre_test.py --udfoer

────────────────────────────────────────────────────────────────────────────────
OIF-FORMATET (NinjaTrader Automated Trading Interface)

    PLACE;<konto>;<instrument>;<BUY|SELL>;<antal>;<type>;<limit>;<stop>;
          <TIF>;<oco>;<ordre-id>;<strategi>;<strategi-id>

Instrumentnavnet er NT8's eget, aflæst i platformens log: `MES SEP26`, ikke
IBKR's `MESU6` og ikke det rene `MES`.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 36973

FORVENTET_KONTO = "DEMO8580770"
MINDSTE_AFSTAND_PCT = 30.0


class Vagt(Exception):
    """Rejses når en vagt spærrer. Aldrig fanget for at fortsætte."""


def hent_marked(ticker: str = "MES") -> float | None:
    """MES-kursen fra vores egen kilde — algoserveren eller den lokale IBKR.

    ⚠ POINTEN ER AT DEN IKKE KOMMER FRA BRUGEREN. En afstandsvagt der regner på
    et tal man selv har tastet, kan ikke fange at tallet var forkert.
    """
    import json, urllib.request
    import accounts
    maal = (accounts.identity.replication_target_url or "").rstrip("/")
    if not maal:
        return None
    try:
        rq = urllib.request.Request(
            f"{maal}/quote/{ticker}",
            headers={"X-Internal-Key": accounts.identity.internal_key})
        with urllib.request.urlopen(rq, timeout=12) as r:
            return json.loads(r.read()).get("price")
    except Exception:
        return None


def _forbind() -> socket.socket:
    s = socket.socket()
    s.settimeout(6)
    s.connect((HOST, PORT))
    return s


def lyt(s: socket.socket, sekunder: float = 3.0) -> str:
    """Alt hvad ATI pusher i vinduet, som læsbar tekst."""
    buf = b""
    slut = time.time() + sekunder
    while time.time() < slut:
        try:
            d = s.recv(8192)
            if not d:
                break
            buf += d
        except socket.timeout:
            pass
    return buf.replace(b"\x00", b" ").decode(errors="replace")


def send(s: socket.socket, kommando: str) -> str:
    s.sendall(kommando.encode() + b"\r\n")
    return lyt(s, 3.0)


def ordrer_for(tekst: str, konto: str) -> str:
    """Den seneste Orders|<konto>-værdi i strømmen."""
    seneste = ""
    for stump in tekst.split("2 "):
        if stump.startswith(f"Orders|{konto}"):
            seneste = stump[len(f"Orders|{konto}"):].strip()
    return seneste


def main() -> int:
    ap = argparse.ArgumentParser(description="Kan ATI laegge en ordre?")
    ap.add_argument("--konto", default=FORVENTET_KONTO)
    ap.add_argument("--instrument", default="MES SEP26")
    ap.add_argument("--limit", type=float,
                    help="limitpris. Udelades den, saettes den til 60 %% af "
                         "markedet — men afstanden kontrolleres uanset")
    ap.add_argument("--antal", type=int, default=1)
    ap.add_argument("--udfoer", action="store_true",
                    help="send ordren. Uden denne: preview")
    args = ap.parse_args()

    konto = args.konto.strip()
    ordre_id = f"TDPROBE{int(time.time())}"

    # ⚠ MARKEDET HENTES, IKKE TASTET.
    marked = hent_marked()
    if marked is None:
        print("\n⚠ V2 SPAERRER: kunne ikke hente en MES-kurs at maale imod.")
        print("  Uden en uafhaengig kurs er afstandsvagten blind, og saa")
        print("  sendes der ingen ordre.")
        return 1

    limit = round(args.limit if args.limit else marked * 0.60, 2)
    afstand = (marked - limit) / marked * 100

    print("=" * 78)
    print(f"NT8 ATI ORDRE-TEST  ·  {HOST}:{PORT}"
          + ("" if args.udfoer else "  ·  PREVIEW"))
    print("=" * 78)

    # ── V1 + V2 FØR der forbindes ───────────────────────────────────────────
    if konto != FORVENTET_KONTO:
        print(f"\n⚠ V1 SPAERRER: kontoen er '{konto}', forventet "
              f"'{FORVENTET_KONTO}'.\n  Ret --konto bevidst hvis det er meningen.")
        return 1
    if afstand < MINDSTE_AFSTAND_PCT:
        print(f"\n⚠ V2 SPAERRER: limit {limit} ligger kun {afstand:.1f} % under "
              f"markedet {marked} (hentet).\n  Kravet er {MINDSTE_AFSTAND_PCT} %.")
        return 1

    kommando = (f"PLACE;{konto};{args.instrument};BUY;{args.antal};LIMIT;"
                f"{limit};;DAY;;{ordre_id};;")
    print(f"\n  konto       {konto}   (V1 ✓)")
    print(f"  instrument  {args.instrument}")
    print(f"  ordre       BUY {args.antal} LIMIT {limit}")
    print(f"  marked      {marked}  (hentet)  →  {afstand:.0f} % under  (V2 ✓)")
    print(f"  TIF         DAY  (V3 ✓ — aldrig GTC)")
    print(f"  ordre-id    {ordre_id}")
    print(f"\n  {kommando}")

    if not args.udfoer:
        print("\n  PREVIEW — intet sendt. Koer igen med --udfoer.")
        return 0

    s = _forbind()
    try:
        print("\n1. Udgangspunkt — hvad staar der FOER?")
        foer = lyt(s, 4.0)
        print(f"   Orders|{konto}: '{ordrer_for(foer, konto)}'")

        print("\n2. Sender PLACE")
        svar = send(s, kommando)
        print(f"   svar: {svar[:200]!r}")

        print("\n3. Dukkede den op?")
        efter = svar + lyt(s, 4.0)
        nu = ordrer_for(efter, konto)
        print(f"   Orders|{konto}: '{nu}'")
        fandtes = ordre_id in efter or bool(nu)
        print(f"   {'OK   ordren er synlig i stroemmen' if fandtes else '⚠ ikke set — se NT8s Orders-fane'}")

    finally:
        # ── V4: annullér ALTID ──────────────────────────────────────────────
        print("\n4. Annullerer (V4 — sker uanset hvad ovenfor viste)")
        try:
            svar = send(s, f"CANCEL;;;;;;;;;;{ordre_id};;")
            print(f"   svar: {svar[:200]!r}")
            rest = ordrer_for(svar + lyt(s, 4.0), konto)
            print(f"   Orders|{konto} efter: '{rest}'")
        except Exception as e:
            print(f"   ⚠ ANNULLERING FEJLEDE: {type(e).__name__}: {e}")
            print(f"   ⚠ TJEK NT8's Orders-fane MANUELT for {ordre_id}")
        s.close()

    print("\n" + "=" * 78)
    print("⚠ Bekraeft i NinjaTraders Orders-fane at der ikke ligger noget")
    print("  tilbage. Stroemmen er vores maaling, men platformen er sandheden.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
