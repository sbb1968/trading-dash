#!/usr/bin/env python3
"""
tradovate_probe.py — har vi overhovedet API-adgang til Tradovate/NinjaTrader?
════════════════════════════════════════════════════════════════════════════════
Undersøgelse, ikke integration. Scriptet sender **ingen ordrer** og skriver
intet. Det svarer på tre spørgsmål i rækkefølge, og stopper ved det første nej:

    1  Kan vi nå API'et?              (ingen credentials — ren netværkstest)
    2  Kan vi få et access token?     (login)
    3  Kan vi se en konto?            (adgangen rækker til noget)

⚠ CREDENTIALS LÆSES FRA MILJØET, ALDRIG FRA EN FIL I REPOET.

    $env:TRADOVATE_USERNAME = "..."
    $env:TRADOVATE_PASSWORD = "..."
    python tradovate_probe.py

De sættes for det vindue du står i og forsvinder når det lukkes. Skal det
automatiseres senere, læses de fra en fil UDEN FOR repoet med begrænsede
rettigheder — og den fil skal nævnes her når den oprettes.

────────────────────────────────────────────────────────────────────────────────
⚠ FÆLDEN: ET MISLYKKET LOGIN SVARER HTTP 200

Målt 11-08-2026 mod demo-endepunktet:

    {"name": "...", "password": "forkert"}
      -> HTTP 200
         {"errorText":"Incorrect username or password. …"}

Kode der bruger `response.raise_for_status()` som sin kontrol, vil altså mene at
login lykkedes, og først falde over det når `data["accessToken"]` giver KeyError
et helt andet sted. Statuskoden er ikke svaret her — `errorText` er.

⚠ OG APPID/CID/SEC ER IKKE PÅKRÆVEDE FELTER. Skemaet forlanger kun `name` og
`password`; udelader man API-nøglen, fejler kaldet ikke — det svarer bare noget
andet. Fraværet af en fejl er derfor ikke bevis for at nøglen virkede.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEMO = "https://demo.tradovateapi.com/v1"
LIVE = "https://live.tradovateapi.com/v1"


def _post(url: str, nyttelast: dict, token: str = "") -> tuple[int, dict | str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    rq = urllib.request.Request(url, data=json.dumps(nyttelast).encode(),
                                method="POST", headers=h)
    try:
        with urllib.request.urlopen(rq, timeout=25) as r:
            raa = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raa)
            except json.JSONDecodeError:
                return r.status, raa
    except urllib.error.HTTPError as e:
        raa = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raa)
        except json.JSONDecodeError:
            return e.code, raa


def _get(url: str, token: str) -> tuple[int, dict | list | str]:
    rq = urllib.request.Request(url, headers={
        "Accept": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(rq, timeout=25) as r:
            raa = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raa)
            except json.JSONDecodeError:
                return r.status, raa
    except urllib.error.HTTPError as e:
        raa = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raa)
        except json.JSONDecodeError:
            return e.code, raa


def _forklar(fejl: str) -> None:
    """Oversæt Tradovates fejltekst til hvad der faktisk mangler.

    ⚠ De to hyppigste ligner hinanden i alvor og er helt forskellige i årsag.
    Målt 11-08-2026, samme endepunkt, kun brugernavnet ændret:

        opdigtet bruger  ->  "Incorrect username or password"
        rigtig bruger    ->  "The app is not registered"

    Kontrasten er selve beviset: får man den ANDEN, er brugernavn og password
    accepteret, og spærringen ligger i appId. Uden den sammenligning ville
    "app is not registered" let læses som endnu en loginfejl, og man ville
    bruge eftermiddagen på at nulstille et password der virker.
    """
    f = (fejl or "").lower()
    if "not registered" in f:
        print("\n   → OVERSAT: brugernavn og password blev ACCEPTERET.")
        print("     Det der mangler, er en registreret applikation — altsaa en")
        print("     API-noegle (appId + cid + sec).")
        print("\n     Beviset er kontrasten: et opdigtet brugernavn giver")
        print("     'Incorrect username or password' paa samme endepunkt.")
        print("     Naar svaret skifter, er login-delen passeret.")
        print("\n     Naeste skridt er IKKE et nyt password, men en API-noegle.")
    elif "incorrect username or password" in f:
        print("\n   → OVERSAT: tvetydigt. Enten er koden forkert, ELLER brugeren")
        print("     findes ikke i Tradovate. API'et skelner ikke.")
    elif "captcha" in f or "locked" in f:
        print("\n   → OVERSAT: kontoen er midlertidigt spaerret af for mange")
        print("     forsoeg. Vent, og log ind i webplatformen foerst.")


def sloer(s: str, vis: int = 4) -> str:
    """Nok til at genkende, for lidt til at bruge."""
    s = str(s or "")
    return f"{s[:vis]}…{s[-vis:]} ({len(s)} tegn)" if len(s) > vis * 2 else "(kort/tom)"


def main() -> int:
    base = LIVE if "--live" in sys.argv else DEMO
    er_live = base is LIVE

    print("=" * 78)
    print(f"TRADOVATE-PROBE  ·  {base}")
    print("=" * 78)
    if er_live:
        # ⚠ Live-endepunktet taler til rigtige penge. Det skal vaelges bevidst,
        # og proben skal sige det hoejt — ikke antage at brugeren husker det.
        print("\n⚠ LIVE-endepunktet er valgt. Der sendes stadig ingen ordrer,")
        print("  men tokenet ville give adgang til en rigtig konto.")

    # ── 1. Naaes API'et? ────────────────────────────────────────────────────
    print("\n1. Kan vi naa API'et?  (ingen credentials sendes)")
    kode, svar = _post(f"{base}/auth/accesstokenrequest", {})
    if kode == 400 and "name" in str(svar):
        print(f"   OK   HTTP {kode} — endepunktet lever og validerer sit skema")
    else:
        print(f"   ⚠    HTTP {kode}: {str(svar)[:160]}")
        print("        Uventet. Er der en proxy eller firewall imellem?")
        return 1

    # ── 2. Token ────────────────────────────────────────────────────────────
    bruger = os.getenv("TRADOVATE_USERNAME", "")
    kode_ord = os.getenv("TRADOVATE_PASSWORD", "")
    app_id = os.getenv("TRADOVATE_APP_ID", "")
    cid = os.getenv("TRADOVATE_CID", "")
    sec = os.getenv("TRADOVATE_SECRET", "")

    print("\n2. Access token")
    if not bruger or not kode_ord:
        print("   Miljøvariabler mangler. Sæt dem i DETTE vindue:")
        print('      $env:TRADOVATE_USERNAME = "..."')
        print('      $env:TRADOVATE_PASSWORD = "..."')
        print("   Valgfrit (API-nøgle, hvis du har fået en):")
        print('      $env:TRADOVATE_APP_ID = "..."  ·  _CID  ·  _SECRET')
        return 1

    print(f"   bruger  {bruger}")
    print(f"   appId   {app_id or '(ingen — logger ind uden API-nøgle)'}")
    print(f"   cid     {cid or '(ingen)'}   sec {sloer(sec) if sec else '(ingen)'}")

    nyttelast = {"name": bruger, "password": kode_ord,
                 "appId": app_id or "trading_dash_probe", "appVersion": "1.0",
                 "deviceId": "trading-dash-probe"}
    if cid:
        try:
            nyttelast["cid"] = int(cid)
        except ValueError:
            print(f"   ⚠ TRADOVATE_CID='{cid}' er ikke et tal — udelades")
    if sec:
        nyttelast["sec"] = sec

    kode, svar = _post(f"{base}/auth/accesstokenrequest", nyttelast)

    # ⚠ HER LIGGER FAELDEN. Et mislykket login svarer HTTP 200 med errorText.
    # Statuskoden er ikke svaret; errorText er.
    if not isinstance(svar, dict):
        print(f"   FEJL HTTP {kode}: {str(svar)[:200]}")
        return 1
    if svar.get("errorText"):
        fejl = svar["errorText"]
        print(f"   FEJL HTTP {kode} — men login mislykkedes alligevel:")
        print(f"        {fejl}")
        print("\n   ⚠ Bemaerk at statuskoden var 200. Kode der stoler paa")
        print("     raise_for_status() ville have troet at det lykkedes.")
        _forklar(fejl)
        return 1
    token = svar.get("accessToken")
    if not token:
        print(f"   FEJL HTTP {kode}: intet accessToken i svaret")
        print(f"        noegler: {sorted(svar)}")
        return 1

    print(f"   OK   token {sloer(token, 6)}")
    print(f"        udloeber {svar.get('expirationTime', '?')}")
    for felt in ("userId", "name", "hasLive"):
        if felt in svar:
            print(f"        {felt}: {svar[felt]}")

    # ── 3. Rækker adgangen til noget? ───────────────────────────────────────
    print("\n3. Konti — rækker adgangen til data?")
    kode, svar = _get(f"{base}/account/list", token)
    if isinstance(svar, list):
        print(f"   OK   {len(svar)} konto(er)")
        for k in svar:
            print(f"        id={k.get('id')}  {k.get('name')}  "
                  f"type={k.get('accountType')}  "
                  f"aktiv={k.get('active')}  {k.get('legalStatus', '')}")
        if not svar:
            print("   ⚠ Tom liste. Tokenet virker, men der er ingen konto bag —")
            print("     typisk hvis simulation ikke er aktiveret paa profilen.")
    else:
        print(f"   FEJL HTTP {kode}: {str(svar)[:220]}")
        return 1

    print("\n" + "=" * 78)
    print("Adgang bekraeftet. Naeste skridt er markedsdata og en TESTORDRE —")
    print("og det skal vaere et bevidst valg, ikke naeste linje i dette script.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
