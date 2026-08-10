"""
port_tjek.py — lytter der præcis ÉN proces på porten?
════════════════════════════════════════════════════════════════════════════════
⚠ DENNE FÆLDE ER IKKE EN KODEFEJL. Den er en driftsfælde, og den kan opstå på
enhver maskine med en glemt proces.

Målt 2026-08-10 på Sørens workstation, midt i konto 2-testen:

    TCP    0.0.0.0:8000      LISTENING    28280   ← startet 13:39, GAMMEL kode
    TCP    127.0.0.1:8000    LISTENING    24276   ← startet 19:16, ny kode

Windows tillader begge bindinger samtidig — én på wildcard, én på loopback — så
**ingenting fejler**. En forespørgsel til 127.0.0.1:8000 kan ramme hvilken som
helst af dem. Den gamle proces havde ikke `ordre_forbindelse`, så en ordre
gennem den ville være gået til den DELTE forbindelse og landet på en anden
konto. Tavst, og tilsyneladende tilfældigt.

Kør før enhver test der sender ordrer:

    python port_tjek.py            # default 8000
    python port_tjek.py --port 4002

Exit 0 = præcis én lytter · 1 = ⚠ FLERE eller INGEN
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys


def lyttere(port: int) -> list[tuple[str, str]]:
    """[(lokal adresse, pid)] for LISTENING på porten."""
    try:
        ud = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                            capture_output=True, text=True, timeout=25).stdout
    except Exception as e:
        print(f"netstat fejlede: {e}")
        return []
    fundet = []
    for linje in ud.splitlines():
        if "LISTENING" not in linje:
            continue
        d = linje.split()
        if len(d) < 5:
            continue
        adr, pid = d[1], d[-1]
        # ⚠ Match paa PORTEN, ikke paa understreng. ":8000" ville ogsaa matche
        # ":80000" og en KLIENT-port som 58000 — og en kontrol der matcher
        # bredere end sin paastand, svarer paa noget andet end den siger.
        m = re.search(r":(\d+)$", adr)
        if m and int(m.group(1)) == port:
            fundet.append((adr, pid))
    return fundet


def navn(pid: str) -> str:
    try:
        ud = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, timeout=15).stdout
        return ud.split(",")[0].strip('" \n') or "?"
    except Exception:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser(description="Lytter der praecis ÉN proces paa porten?")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    L = lyttere(args.port)
    print(f"Port {args.port}: {len(L)} lytter(e)")
    for adr, pid in L:
        print(f"   {adr:24} PID {pid:>7}  {navn(pid)}")

    if len(L) == 1:
        print("\n  Præcis én — fortsæt.")
        return 0
    if not L:
        print("\n  INGEN lytter. Er backenden startet?")
        return 1

    print(f"\n⚠⚠ {len(L)} PROCESSER LYTTER PÅ SAMME PORT — STOP")
    print("   Windows tillader det når interfacene er forskellige (0.0.0.0 mod")
    print("   127.0.0.1), så ingenting fejler. Men en forespørgsel kan ramme")
    print("   hvilken som helst af dem, og de kan køre FORSKELLIG kode.")
    print("\n   En ordre kan dermed lande på den forkerte konto, tavst.")
    print("\n   Dræb alle på nær én:")
    for _adr, pid in L:
        print(f"      taskkill /F /PID {pid}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
