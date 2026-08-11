#!/usr/bin/env python3
"""
stop_alle_strategier.py — stop alle strategier på algoserveren, og bevis det
════════════════════════════════════════════════════════════════════════════════
Bruges i flatten-sekvensen (`tidsplan_11_august.md`, spor A, kl. 15:30). Ellers
åbner strategierne nye positioner mens man lukker de gamle.

⚠ NAVNENE LÆSES FRA MASKINEN, IKKE FRA EN LISTE HER I FILEN.

En hardkodet liste er en påstand om hvad der kører. Tilføjes en syvende strategi
i morgen, ville den blive stående — og sekvensen ville se ud som om alt var
stoppet. Navnene hentes derfor fra `/health`.

    python stop_alle_strategier.py            # PREVIEW, stopper intet
    python stop_alle_strategier.py --udfoer

⚠ OG DEN VERIFICERER BAGEFTER. `/algo/stop` svarer `ok` også når strategien ikke
kørte, så svaret alene beviser ingenting. Bagefter læses `algo_running` igen —
det er dét der afgør om der stadig arbejder noget.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import accounts

ALGOSERVER = "http://100.76.201.59:8000"


def _kald(url: str, nyttelast: dict | None = None) -> tuple[int, dict | str]:
    h = {"Accept": "application/json",
         "X-Internal-Key": accounts.identity.internal_key}
    data = None
    if nyttelast is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(nyttelast).encode()
    rq = urllib.request.Request(url, data=data, headers=h,
                                method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(rq, timeout=25) as r:
            raa = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raa)
            except json.JSONDecodeError:
                return r.status, raa
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Stop alle strategier og verificér")
    ap.add_argument("--maal", default=ALGOSERVER)
    ap.add_argument("--udfoer", action="store_true")
    args = ap.parse_args()
    base = args.maal.rstrip("/")

    print("=" * 78)
    print(f"STOP ALLE STRATEGIER  ·  {base}"
          + ("" if args.udfoer else "  ·  PREVIEW"))
    print("=" * 78)

    kode, h = _kald(f"{base}/health")
    if not isinstance(h, dict):
        print(f"\n⚠ /health svarede ikke brugbart (HTTP {kode}): {str(h)[:160]}")
        return 1

    navne = [a["strategy"] for a in (h.get("auto_starts") or []) if a.get("strategy")]
    print(f"\nalgo_running: {h.get('algo_running')}")
    print(f"konto:        {h.get('role')} · paper={h.get('paper_trading')}")
    print(f"\n{len(navne)} strategier kendt af maskinen:")
    for n in navne:
        print(f"   {n}")

    if not args.udfoer:
        print("\n  PREVIEW — intet stoppet. Koer igen med --udfoer.")
        return 0

    print("\nstopper:")
    for n in navne:
        kode, svar = _kald(f"{base}/algo/stop", {"strategy": n})
        if isinstance(svar, dict):
            koerte = svar.get("was_running")
            print(f"   {n:20} HTTP {kode}  "
                  + ("stoppet" if koerte else "koerte ikke"))
        else:
            print(f"   {n:20} HTTP {kode}  ⚠ {str(svar)[:90]}")

    # ⚠ SVARET BEVISER INGENTING. /algo/stop siger ok ogsaa naar strategien ikke
    # koerte. Det er algo_running der afgoer om der stadig arbejder noget.
    print("\nverificerer …")
    time.sleep(3)
    kode, h2 = _kald(f"{base}/health")
    koerer = h2.get("algo_running") if isinstance(h2, dict) else "?"
    print(f"   algo_running: {koerer}")

    if koerer is False:
        print("\n  ALT STOPPET ✓ — flatten kan koere uden at der aabnes nyt.")
        return 0
    print("\n⚠ Der koerer stadig noget. Flatten IKKE foer det er afklaret —")
    print("  en strategi der handler mens man lukker, aabner bare igen.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
