#!/usr/bin/env python3
"""
ninjatrader_ordre_test.py — kan ATI faktisk lægge en ordre?
════════════════════════════════════════════════════════════════════════════════
Sidste ubesvarede spørgsmål i `ninjatrader_adgang.md`.

⚠ TRANSPORTEN ER SKIFTET 16-09. Den første udgave sendte OIF-kommandoer på
socket 36973 og fik intet svar. Årsagen er nu målt: **den socket er udgående.**
NT8's egen log skriver `Server.AtiServer.ConnectNow` når vi forbinder og
INTET når vi sender — den pusher kontotilstand ud, den tager ikke ordrer ind.

Skrivevejen er **filer i `incoming\\`**, og fælden lå i filNAVNET:

    oif.txt          ✓ behandlet
    oif_probe.txt    ✓ behandlet
    oif1.txt         ✓ behandlet
    TDPROBE.oif.txt  ✗ "Unknown OIF file type"

Navnet skal begynde med `oif`. Fejlbeskeden sagde "file **type**", ikke
"unknown command" — og dét ord var hele nøglen. Med et forkert navn læses
filen aldrig, uanset hvor rigtigt indholdet er.

⚠ OG LOGGEN ER KVITTERINGEN. NT8 skriver hvad den gør med hver eneste
OIF-kommando:

    OIF, 'CANCEL;...' processing
    OIF, 'CANCEL;...' order with ID/Name 'TDNOPE...' does not exist
    OIF, 'PLACE;...'  holds unknown instrument 'XYZ_FINDES_IKKE_99'

Uden den kanal fejlsøger man i blinde. Scriptet læser derfor logvinduet
omkring hver kommando og viser det.

────────────────────────────────────────────────────────────────────────────────
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
    CANCEL;;;;;;;;;;<ordre-id>;;

⚠ INSTRUMENTNAVNET er NT8's eget. `MES 09-26` (symbol + MM-YY) er NT8's
futures-format. Er det forkert, siger loggen `holds unknown instrument '…'`
og der oprettes INGEN ordre — så en forkert gætning er ufarlig, bare
uproduktiv.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 36973

NT8 = pathlib.Path.home() / "Documents" / "NinjaTrader 8"
INCOMING = NT8 / "incoming"
LOGMAPPE = NT8 / "log"

# ⚠ Sim101 ER NT8's EGEN simulator og ruter INGEN STEDER.
# DEMO8580770 er Tradovate-kontoen: den sender ud af huset og afviste vores
# ordre med "Real-time market data required to trade this contract" (16-09
# 07:56). Sim101 fylder mod NT8's egen feed og tog samme ordre igennem hele
# livscyklussen paa ét sekund — Submitted → Accepted → Working → Cancelled.
#
# Skal Tradovate-vejen bruges senere, kraever den et CME-abonnement. Til at
# bevise at kanalen virker, kraever den ingenting.
SIM_KONTI = {"Sim101", "DEMO8580770"}
FORVENTET_KONTO = "Sim101"
MINDSTE_AFSTAND_PCT = 30.0


class Vagt(Exception):
    """Rejses når en vagt spærrer. Aldrig fanget for at fortsætte."""


def hent_marked(ticker: str = "MES") -> float | None:
    """MES-kursen fra vores egen kilde — algoserveren eller den lokale IBKR.

    ⚠ POINTEN ER AT DEN IKKE KOMMER FRA BRUGEREN. En afstandsvagt der regner på
    et tal man selv har tastet, kan ikke fange at tallet var forkert.
    """
    import json
    import urllib.request

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


# ── NT8's log: vores eneste kvittering ─────────────────────────────────────
def _nyeste_log() -> pathlib.Path | None:
    try:
        filer = list(LOGMAPPE.glob("log.*.txt"))
    except OSError:
        return None
    return max(filer, key=lambda p: p.stat().st_mtime) if filer else None


def _loglaengde(log: pathlib.Path | None) -> int:
    if log is None:
        return 0
    try:
        return len(log.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _nye_logliner(log: pathlib.Path | None, fra: int) -> list[str]:
    if log is None:
        return []
    try:
        return [l.strip() for l in
                log.read_text(encoding="utf-8", errors="replace").splitlines()[fra:]
                if l.strip()]
    except OSError:
        return []


def afvent_ordre(log, fra: int, sekunder: int = 60) -> tuple[str, list[str]]:
    """Vent til NT8 har AFGJORT ordren. Returnerer (sidste tilstand, logliner).

    ⚠ DEN HER FUNKTION ER RETTELSEN AF EN VAGT DER IKKE VIRKEDE.
    Første udgave annullerede så snart PLACE-filen var væk. Målt 16-09:

        07:56:14  OIF 'PLACE;...' processing
        07:56:40  OIF 'CANCEL;...' order with ID/Name '...' does not exist
        07:56:41  Order ... New state='Rejected'

    Annulleringen kom et halvt sekund FØR ordren blev oprettet og ramte intet.
    At der alligevel ikke lå noget bagefter, skyldtes at ordren blev AFVIST —
    ikke at vagten gjorde sit arbejde.
    """
    seneste = ""
    for _ in range(sekunder):
        time.sleep(1)
        nye = _nye_logliner(log, fra)
        for l in nye:
            m = re.search(r"New state='(\w+)'", l)
            if m and "Order=" in l:
                seneste = m.group(1)
        if seneste:
            # Læs et øjeblik mere, så Accepted/Working/Rejected når med.
            time.sleep(1.5)
            nye = _nye_logliner(log, fra)
            for l in nye:
                m = re.search(r"New state='(\w+)'", l)
                if m and "Order=" in l:
                    seneste = m.group(1)
            return seneste, nye
    return "", _nye_logliner(log, fra)


def send_oif(kommando: str, mrk: str) -> list[str]:
    """Skriv én OIF-kommando og returnér hvad NT8 skrev om den.

    ⚠ FILNAVNET SKAL BEGYNDE MED `oif` — ellers læses filen aldrig. Det var
    fejlen 11-08, og den så ud som et formatproblem i indholdet.
    """
    log = _nyeste_log()
    foer = _loglaengde(log)
    fil = INCOMING / f"oif_td_{mrk}_{int(time.time() * 1000)}.txt"
    INCOMING.mkdir(parents=True, exist_ok=True)
    fil.write_text(kommando + "\n", encoding="ascii")

    spist = False
    for _ in range(12):
        time.sleep(1)
        if not fil.exists():
            spist = True
            break
    if not spist:
        # NT8 holder filen åben mens den behandles; kan vi ikke rydde op, så sig det.
        try:
            fil.unlink()
            print(f"   ⚠ filen blev ikke behandlet ({fil.name}) — slettet igen")
        except OSError:
            print(f"   ⚠ filen ligger stadig og kan ikke slettes: {fil.name}")
    time.sleep(1.2)   # loggen skrives et øjeblik efter
    return [l for l in _nye_logliner(log, foer) if "OIF" in l or "rder" in l]


# ── ATI-socket: KUN læsning ────────────────────────────────────────────────
def lyt(sekunder: float = 4.0) -> str:
    """Alt hvad ATI pusher i vinduet. ⚠ Der sendes aldrig noget på den her."""
    try:
        s = socket.create_connection((HOST, PORT), timeout=5)
    except OSError as e:
        return f"(ingen ATI-forbindelse: {e})"
    s.settimeout(sekunder)
    buf = b""
    slut = time.time() + sekunder
    try:
        while time.time() < slut:
            try:
                d = s.recv(8192)
                if not d:
                    break
                buf += d
            except socket.timeout:
                break
    finally:
        s.close()
    return buf.replace(b"\x00", b" ").decode(errors="replace")


def ordre_status(tekst: str, ordre_id: str) -> str:
    """Vores ordres tilstand, aflæst i ATI-strømmen.

    ⚠ STRØMMEN KENDER VORES ID — LOGGEN GØR IKKE. NT8's log skriver `Name=''`
    på hver ordre, og det fik os 16-09 til at konkludere at ordre-id'et ikke
    bandt. Det gjorde det hele tiden; ATI pusher det som:

        OrderStatus|TDPROBE1789538169 Rejected
        Filled|TDPROBE1789538169 0
        AvgFillPrice|TDPROBE1789538169 0

    Den forkerte konklusion kostede os en V4 der annullerede hele kontoen i
    stedet for én ordre — bredere end nødvendigt, og derfor dårligere.

    ⚠ VERIFICERET 26-09 PÅ EN RIGTIG EFTERLADT ORDRE. Testkørslen 16-09 kl.
    09:05 fejlede og efterlod `TDPROBE1789542345` **Working** på Sim101.
    Den lå der i ti dage. En `CANCEL` på præcis det id ramte den:

        OIF, 'CANCEL;;;;;;;;;;TDPROBE1789542345;;' processing
        Order='b0c947dc…/Sim101'  New state='Cancel submitted'
        Order='b0c947dc…/Sim101'  New state='Cancelled'

    ⚠ OG EN TING MERE, SOM ER LET AT TAGE FEJL AF: strømmen er et
    ØJEBLIKSBILLEDE, ikke en fuld opregning. Ordren stod **ikke** i det
    snapshot jeg læste umiddelbart før annulleringen — men den fandtes.
    At et id ikke ses i ét oplæg, beviser derfor ingenting; kun en
    `OrderStatus|<id> <tilstand>` med en terminal tilstand gør.
    Derfor behandler kalderen "" som IKKE-terminal og rydder bredere.
    """
    m = re.search(rf"OrderStatus\|{re.escape(ordre_id)}\s+(\S+)", tekst)
    return m.group(1) if m else ""


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
    # ⚠ NT8's eget futures-format: symbol + MM-YY.
    ap.add_argument("--instrument", default="MES 09-26")
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
    print("NT8 ATI ORDRE-TEST  ·  OIF-fil i incoming\\"
          + ("" if args.udfoer else "  ·  PREVIEW"))
    print("=" * 78)

    # ── V1 + V2 FØR der skrives noget ───────────────────────────────────────
    if konto not in SIM_KONTI:
        print(f"\n⚠ V1 SPAERRER: kontoen er '{konto}', forventet "
              f"'{FORVENTET_KONTO}'.\n  Ret --konto bevidst hvis det er meningen.")
        return 1
    if afstand < MINDSTE_AFSTAND_PCT:
        print(f"\n⚠ V2 SPAERRER: limit {limit} ligger kun {afstand:.1f} % under "
              f"markedet {marked} (hentet).\n  Kravet er {MINDSTE_AFSTAND_PCT} %.")
        return 1
    if not INCOMING.is_dir():
        print(f"\n⚠ SPAERRER: {INCOMING} findes ikke — koerer NT8?")
        return 1

    kommando = (f"PLACE;{konto};{args.instrument};BUY;{args.antal};LIMIT;"
                f"{limit};;DAY;;{ordre_id};;")
    print(f"\n  konto       {konto}   (V1 ✓)")
    print(f"  instrument  {args.instrument}")
    print(f"  ordre       BUY {args.antal} LIMIT {limit}")
    print(f"  marked      {marked}  (hentet)  →  {afstand:.0f} % under  (V2 ✓)")
    print(f"  TIF         DAY  (V3 ✓ — aldrig GTC)")
    print(f"  ordre-id    {ordre_id}")
    print(f"  fil         oif_td_place_*.txt   (⚠ navnet SKAL begynde med 'oif')")
    print(f"\n  {kommando}")

    if not args.udfoer:
        print("\n  PREVIEW — intet sendt. Koer igen med --udfoer.")
        return 0

    ukendt_instrument = False
    try:
        print("\n1. Udgangspunkt — hvad staar der FOER?")
        print(f"   Orders|{konto}: '{ordrer_for(lyt(4.0), konto)}'")

        print("\n2. Sender PLACE som OIF-fil")
        _log = _nyeste_log()
        _fra = _loglaengde(_log)
        for l in send_oif(kommando, "place") or ["   (ingen logsvar)"]:
            print(f"   {l[:145]}")

        # ⚠ VENT PAA NT8's AFGOERELSE — behandlingen er ASYNKRON.
        # Maalt 16-09: under 1 sekund paa Sim101, men 26 sekunder paa
        # Tradovate-kontoen, fordi en bekraeftelses-popup ventede paa et
        # menneske. En kontrol der antager at det gaar oejeblikkeligt, ser en
        # ordre der endnu ikke findes — og konkluderer at alt er fint.
        tilstand, linjer = afvent_ordre(_log, _fra)
        print(f"   tilstand: {tilstand or '(NT8 sagde intet inden for 60 s)'}")
        if any("unknown instrument" in l for l in linjer):
            ukendt_instrument = True

        if ukendt_instrument:
            # ⚠ Der blev IKKE oprettet nogen ordre. Intet at annullere.
            print(f"\n⚠ NT8 kender ikke instrumentet '{args.instrument}'.")
            print("  Der er ikke oprettet nogen ordre — proev et andet navn.")
            print("  NT8's futures-format er symbol + MM-YY, fx 'MES 12-26'.")
            return 1

        print("\n3. Hvilken tilstand naaede ordren?")
        for l in linjer:
            if "New state=" in l or "Native error" in l:
                print(f"   {l[:200]}")

    finally:
        # ── V4: annullér ALTID ──────────────────────────────────────────────
        # ⚠ RETTET 16-09. Foerste udgave annullerede PAA ORDRE-ID og gjorde det
        # straks. Maalt samme dag:
        #     07:56:14  OIF 'PLACE;...' processing
        #     07:56:40  OIF 'CANCEL;...' order with ID/Name '...' does not exist
        #     07:56:41  Order ... New state='Rejected'
        # Annulleringen kom et halvt sekund FOER ordren blev oprettet og ramte
        # intet. At der alligevel ikke laa noget bagefter, skyldtes at ordren
        # blev AFVIST — ikke at vagten virkede. NT8 ventede 26 sekunder paa en
        # bekraeftelses-popup, og vagten antog at behandlingen var oejeblikkelig.
        #
        # ⚠ OG ORDRE-ID'ET BED IKKE. Loggen viser Name='' selv om vi satte
        # felt 11. Derfor annulleres der nu paa KONTO, ikke paa id.
        # CANCELALLORDERS er scoped til den ene navngivne konto og roerer kun
        # ordrer — aldrig positioner (det ville FLATTENEVERYTHING goere).
        if not ukendt_instrument:
            print(f"\n4. Annullerer {ordre_id} (V4 — sker uanset udfald)")
            try:
                for l in send_oif(f"CANCEL;;;;;;;;;;{ordre_id};;", "cancel") \
                        or ["   (ingen logsvar)"]:
                    print(f"   {l[:170]}")
                time.sleep(2)

                # ⚠ VERIFICÉR I STROEMMEN, IKKE I LOGGEN. NT8's log skriver
                # Name='' paa ordren, saa vores id staar ikke dér — men
                # ATI-strommen sporer den praecist:
                #     OrderStatus|TDPROBE1789538169 Rejected
                # Det var dét der afkraeftede "id'et bider ikke". Det gjorde
                # det hele tiden; CANCEL ramte bare en ordre der endnu ikke
                # fandtes, fordi behandlingen er asynkron.
                status = ordre_status(lyt(6.0), ordre_id)
                print(f"   status i stroemmen: {status or '(ikke set)'}")

                # ⚠ SIDSTE UDVEJ, ikke foerste. CANCELALLORDERS rammer hele
                # kontoen; den praecise annullering rammer kun vores egen
                # ordre. Bredere end noedvendigt er ikke sikrere.
                if status not in ("Cancelled", "Rejected", "Filled"):
                    print("   ⚠ ikke bekraeftet annulleret — rydder HELE kontoen")
                    for l in send_oif(f"CANCELALLORDERS;{konto};;;;;;;;;;;", "ryd") \
                            or ["   (ingen logsvar)"]:
                        print(f"   {l[:170]}")
                    time.sleep(2)
                    status = ordre_status(lyt(6.0), ordre_id)
                    print(f"   status efter oprydning: {status or '(ikke set)'}")
                    if status not in ("Cancelled", "Rejected", "Filled"):
                        print(f"   ⚠⚠ TJEK NT8's Orders-fane MANUELT for {ordre_id}")
            except Exception as e:
                print(f"   ⚠ ANNULLERING FEJLEDE: {type(e).__name__}: {e}")
                print(f"   ⚠ TJEK NT8's Orders-fane MANUELT for {ordre_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
