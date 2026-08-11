#!/usr/bin/env python3
"""
preflight_genstart.py — kan den nye kode overhovedet starte her?
════════════════════════════════════════════════════════════════════════════════
Køres **efter `git pull`, før genstart**, mens den gamle kode stadig kører og
handler. Alt her er skrivebeskyttet og rører hverken den kørende proces, IBKR
eller databasen.

⚠ HVORFOR DET IKKE ER OVERFLØDIGT. Backenden kan nægte at starte af grunde der
først findes ved import — og opdager man det midt i en genstart, står maskinen
uden backend mens markedet er åbent:

  · `ibkr_client_ids.kontroller()` kaster ved dublet-id'er. Den kører **ved
    import**, altså før noget andet når at ske.
  · `accounts.load_identity()` fejler hårdt ved en fejlindrykket nøgle i
    `account.yaml` (tilføjet 11-08). Samme sted: ved import.
  · Journalens migration kører ved `journal.init()` under opstart. Den fejlede
    én gang i praksis — `no such column: paper`, 11-08 — og backenden startede
    ikke.

Import her er præcis det samme arbejde som opstarten laver, bare uden at binde
en port eller åbne en forbindelse.

    python preflight_genstart.py

Exit 0 = genstart trygt · 1 = noget ville have fejlet

────────────────────────────────────────────────────────────────────────────────
⚠ `test_feed.py` køres ALDRIG herfra. Den forbinder rigtigt til TWS på 7497 med
clientId=4, og en ekstra forbindelse midt i en handelsdag er præcis den slags
der ligner alt muligt andet når det går galt. Se ibkr_client_ids.py.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HER = Path(__file__).parent

# ⚠ Underprocesser skriver ae/oe/aa og ⚠. Uden dette afkoder Windows deres
# output som cp1252, kaster UnicodeDecodeError inde i subprocess' egen
# laesetraad, og efterlader r.stdout som None — hvorefter kontrollen falder
# over sin egen tekstsoegning i stedet for at rapportere testen.
MILJOE = {**os.environ, "PYTHONIOENCODING": "utf-8"}
KOER = dict(capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=MILJOE)

# ⚠ Tests der oprettede en RIGTIG forbindelse eller kræver netværk. De siger
# intet om hvorvidt koden kan starte, og de kan forstyrre en kørende maskine.
SPRING_OVER = {
    "test_feed.py",              # connectAsync mod TWS 7497, clientId=4
    "test_ollama.py",            # lokal LLM
    "test_dagsnoter.py",         # kalder algoserveren over HTTP
    "test_notes_routing.py",     # ditto
    "test_krypto_kurs.py",       # TradingView (harmløs, men uden for pointen)
}


def kap(t: str) -> None:
    print(f"\n{t}\n" + "─" * 78)


def main() -> int:
    fejl: list[str] = []

    # ── 1. Kan modulerne overhovedet importeres? ────────────────────────────
    kap("1. Import — det opstarten gør, uden at binde en port")
    for modul, hvad in [
        ("ibkr_client_ids", "client-id-registret (kontroller() kaster ved dubletter)"),
        ("accounts",        "account.yaml (fejler haardt ved fejlindrykning)"),
        ("journal",         "journalen"),
        ("krypto_kurs",     "crypto-kurser"),
        ("ordre_forbindelse", "den skrivende forbindelse"),
        ("orders_tracker",  "ordre-log"),
        ("strategy_base",   "strategigrundlaget"),
        ("main",            "⚠ HELE backenden — alle ruter, alle strategier"),
    ]:
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-c", f"import {modul}"],
                           cwd=HER, timeout=300, **KOER)
        dt = time.monotonic() - t0
        if r.returncode == 0:
            print(f"  OK   {modul:20} {dt:5.1f}s  {hwd if (hwd := hvad) else ''}")
        else:
            print(f"  FEJL {modul:20} {dt:5.1f}s  {hvad}")
            for linje in (r.stderr or "").strip().splitlines()[-4:]:
                print(f"         {linje}")
            fejl.append(f"import {modul}")

    # ── 2. Testsuiten ───────────────────────────────────────────────────────
    kap("2. Tests (uden dem der forbinder til noget)")
    filer = sorted(p.name for p in HER.glob("test_*.py")
                   if p.name not in SPRING_OVER)
    bestod, faldt, tomme = [], [], []
    for f in filer:
        r = subprocess.run([sys.executable, f], cwd=HER, timeout=300, **KOER)
        if r.returncode == 0:
            # ⚠ En fil uden tests er ikke det samme som en fil der bestod. Den
            # skal taelles for sig, ellers laeses "58 bestod" som daekning der
            # ikke findes.
            ud = r.stdout or ""
            (bestod if "OK  " in ud or "bestod" in ud.lower()
             else tomme).append(f)
        else:
            faldt.append((f, ((r.stdout or "") + (r.stderr or ""))
                          .strip().splitlines()[-3:]))

    print(f"  {len(bestod)} bestod · {len(tomme)} uden testudtryk · "
          f"{len(faldt)} FALDT · {len(SPRING_OVER)} sprunget over")
    for f, hale in faldt:
        print(f"\n  FEJL {f}")
        for linje in hale:
            print(f"         {linje}")
        fejl.append(f)

    if tomme:
        print(f"\n  (uden testudtryk — koerte rent, men rapporterede intet: "
              f"{', '.join(tomme[:6])}{' …' if len(tomme) > 6 else ''})")
    print(f"\n  (sprunget over med vilje: {', '.join(sorted(SPRING_OVER))})")

    # ── Dom ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    if fejl:
        print(f"⚠ {len(fejl)} TING VILLE HAVE FEJLET VED GENSTART:")
        for f in fejl:
            print(f"   · {f}")
        print("\nGENSTART IKKE. Ret dem foerst — en maskine uden backend midt i")
        print("en handelsdag er vaerre end en maskine med gammel kode.")
    else:
        print("Koden kan startes her. Genstart trygt.")
    print("\n⚠ Dette siger intet om IBKR-forbindelsen eller om markedet opfoerer")
    print("  sig — kun at koden kan indlaeses og at logikken holder.")
    print("=" * 78)
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
