"""
afstem_konto.py — holder IBKR og journalen op mod hinanden
═══════════════════════════════════════════════════════════════════════════════════
Otte positioner laa paa Ibens algoserver i en uge uden at nogen opdagede det.
Journalen sagde de var lukket 30.-31. juli; IBKR holdt dem stadig 6. august. Intet
i systemet sammenlignede de to, saa intet raabte op.

Strategiernes egen reconcile kan ikke fange det: den er scoped til strategiens EGNE
journal-raekker og lader eksplicit alt andet vaere (ellers ville én strategi kunne
lukke en andens position). Det er rigtigt — men det betyder at ingen ser helheden.

Denne afstemning ser helheden. Den handler ALDRIG; den laeser og rapporterer.

Tre slags uenighed, og de betyder noget forskelligt:

  EJERLOES  IBKR holder en position ingen aaben journal-raekke daekker.
            Ingen strategi passer paa den — intet stop, ingen exit-plan.
            Det var denne slags der laa i en uge.

  FANTOM    Journalen har en aaben raekke IBKR ikke kender.
            Strategien tror den er i markedet og kan finde paa at "lukke" noget
            der ikke findes — eller vente paa et stop der aldrig rammer.

  UENIG     Begge kender symbolet, men ikke om antallet. Delvis fyldning, eller
            to strategier i samme papir hvor kun den ene er bogfoert.

Bruges:
    python afstem_konto.py                          # denne maskine
    python afstem_konto.py --url http://iben-algo:8000
    python afstem_konto.py --json                   # til overvaagning

Exit-kode 0 = enige, 1 = uenighed fundet, 2 = kunne ikke afgoere (maskine nede).
Den skelnen er vigtig: "kunne ikke afgoere" maa ALDRIG se ud som "alt er fint".
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _internal_key() -> str:
    """Nøglen fra account.yaml. Uden den afvises de beskyttede endpoints."""
    try:
        import yaml
        sti = Path(__file__).parent / "account.yaml"
        return str(yaml.safe_load(sti.read_text(encoding="utf-8"))["auth"]["internal_key"])
    except Exception:
        return ""


def _hent(url: str, sti: str, key: str, timeout: float = 20.0):
    req = urllib.request.Request(url.rstrip("/") + sti)
    if key:
        req.add_header("X-Internal-Key", key)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def afstem(ibkr_positioner: list[dict], journal_raekker: list[dict]) -> dict:
    """Sammenlign de to sider. Ren funktion — ingen I/O, saa den kan testes.

    Nettoer PR. SYMBOL paa begge sider foer sammenligning. To strategier kan holde
    samme papir, og IBKR kender kun nettoet — sammenligner man raekke-for-raekke,
    melder man falsk uenighed hver gang to strategier deler en ticker.
    """
    ibkr: dict[str, float] = {}
    for p in ibkr_positioner:
        sym = (p.get("ticker") or "").upper().strip()
        if not sym:
            continue
        ibkr[sym] = ibkr.get(sym, 0.0) + float(p.get("position") or 0)

    jour: dict[str, float] = {}
    kilder: dict[str, set] = {}
    for r in journal_raekker:
        sym = (r.get("symbol") or "").upper().strip()
        if not sym:
            continue
        antal = float(r.get("shares") or 0)
        # side: journalen gemmer "long"/"short"; IBKR bruger fortegn.
        if str(r.get("side") or "long").strip().lower().startswith("s"):
            antal = -antal
        jour[sym] = jour.get(sym, 0.0) + antal
        kilder.setdefault(sym, set()).add(r.get("source") or "?")

    ejerloese, fantomer, uenige, enige = [], [], [], []
    for sym in sorted(set(ibkr) | set(jour)):
        i = ibkr.get(sym, 0.0)
        j = jour.get(sym, 0.0)
        k = sorted(kilder.get(sym, []))
        if abs(i - j) < 1e-9:
            if i != 0:
                enige.append({"symbol": sym, "antal": i, "kilder": k})
            continue
        if j == 0:
            ejerloese.append({"symbol": sym, "ibkr": i, "journal": 0.0, "kilder": []})
        elif i == 0:
            fantomer.append({"symbol": sym, "ibkr": 0.0, "journal": j, "kilder": k})
        else:
            uenige.append({"symbol": sym, "ibkr": i, "journal": j, "kilder": k})

    return {
        "enige":     enige,
        "ejerloese": ejerloese,
        "fantomer":  fantomer,
        "uenige":    uenige,
        "i_orden":   not (ejerloese or fantomer or uenige),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Afstem IBKR mod journalen")
    ap.add_argument("--url", default="http://127.0.0.1:8000",
                    help="backend at afstemme (standard: denne maskine)")
    ap.add_argument("--json", action="store_true", help="maskinlaesbart output")
    args = ap.parse_args()

    key = _internal_key()
    try:
        konto = _hent(args.url, "/account/dash-snapshot", key)
        aabne = _hent(args.url, "/journal/open-positions", key)
    except (urllib.error.URLError, OSError, ValueError) as e:
        # KAN IKKE AFGOERES — ikke det samme som "enige". Egen exit-kode, saa et
        # overvaagningsjob ikke fejlagtigt melder alt vel naar maskinen er nede.
        besked = f"Kunne ikke naa {args.url}: {e}"
        print(json.dumps({"ok": False, "fejl": besked}) if args.json else f"FEJL: {besked}")
        return 2

    positioner = konto.get("positions") or []
    raekker = aabne.get("positions") if isinstance(aabne, dict) else aabne
    raekker = raekker or []
    r = afstem(positioner, raekker)
    r["ibkr_konto"] = konto.get("ibkr_account")
    r["url"] = args.url

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["i_orden"] else 1

    print(f"\nAfstemning — {args.url}")
    print(f"IBKR-konto: {r['ibkr_konto']}")
    print(f"  IBKR-positioner : {len(positioner)}")
    print(f"  aabne i journal : {len(raekker)}")

    if r["i_orden"]:
        print("\n  ✅ Enige — hver position har en aaben journal-raekke og omvendt.")
        for e in r["enige"]:
            print(f"     {e['symbol']:8s} {e['antal']:>+9g}  {', '.join(e['kilder'])}")
        return 0

    if r["ejerloese"]:
        print(f"\n  ⚠ EJERLOESE ({len(r['ejerloese'])}) — IBKR holder dem, ingen strategi passer paa dem:")
        for e in r["ejerloese"]:
            print(f"     {e['symbol']:8s} IBKR {e['ibkr']:>+9g}   journal 0")
    if r["fantomer"]:
        print(f"\n  ⚠ FANTOMER ({len(r['fantomer'])}) — journalen tror de er aabne, IBKR kender dem ikke:")
        for e in r["fantomer"]:
            print(f"     {e['symbol']:8s} IBKR 0          journal {e['journal']:>+9g}  "
                  f"({', '.join(e['kilder'])})")
    if r["uenige"]:
        print(f"\n  ⚠ UENIGE ({len(r['uenige'])}) — begge kender symbolet, ikke antallet:")
        for e in r["uenige"]:
            print(f"     {e['symbol']:8s} IBKR {e['ibkr']:>+9g}   journal {e['journal']:>+9g}  "
                  f"({', '.join(e['kilder'])})")
    if r["enige"]:
        print(f"\n  I orden ({len(r['enige'])}): "
              + ", ".join(f"{e['symbol']} {e['antal']:+g}" for e in r["enige"]))
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
