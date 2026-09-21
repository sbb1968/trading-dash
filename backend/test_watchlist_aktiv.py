"""
test_watchlist_aktiv.py — to watchlister, ét tastetryk, én ordre
════════════════════════════════════════════════════════════════════════════════
Trading Dash har nu to watchlister: **Watchlist Futures** og **Watchlist
Stocks**. Samme opbygning, hver sin liste af tickers, hver sin farve.

⚠ DEN FARLIGE DEL ER IKKE LAYOUTET — DET ER TASTATURET.
Genvejslytteren er et `window.addEventListener("keydown")` INDE i
`WatchlistPanel`. Med to paneler åbne er den registreret **to gange**. Uden en
spærre ville ét tryk på **K** lægge **to ordrer** — én i hver liste, på hver
sin ticker, i samme øjeblik.

Det er ikke en teoretisk risiko. Det ville ske ved det første tastetryk.

Derfor:
  · præcis ét panel er aktivt ad gangen (`erAktiv`)
  · de øvrige panelers handler returnerer med det samme
  · det aktive panel har en NEONGUL ramme — den eneste synlige garanti for
    hvor et tastetryk lander
  · et klik gør det andet panel aktivt (onMouseDownCapture, så markeringen
    skifter FØR en knap i panelet reagerer)

⚠ OG SPÆRREN SKAL STÅ FØRST. Står den efter fx ALT+H-grenen, ville den gren
stadig fyre i begge paneler. Testen kontrollerer rækkefølgen, ikke kun at
linjen findes.

    python test_watchlist_aktiv.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROD = Path(__file__).parent.parent
APP = ROD / "src" / "App.tsx"
CSS = ROD / "src" / "App.css"
LAYOUTS = ROD / "src" / "layouts.ts"

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


def genvejsblok(kilde: str) -> str | None:
    """Kroppen af shortcutRef.current-handleren."""
    i = kilde.find("shortcutRef.current = (e: KeyboardEvent) => {")
    if i < 0:
        return None
    return kilde[i:i + 2200]


def kontroller(kilde: str, css: str, layouts: str, stille: bool = False) -> list[str]:
    lokale: list[str] = []

    def k(b: bool, hvad: str) -> None:
        if not stille:
            kraev(b, hvad)
        if not b:
            lokale.append(hvad)

    # ── Navnene ────────────────────────────────────────────────────────────
    k('"Watchlist Futures"' in layouts, "den eksisterende liste hedder 'Watchlist Futures'")
    k('"Watchlist Stocks"' in layouts, "den nye liste hedder 'Watchlist Stocks'")
    k('"watchliststocks"' in layouts, "watchliststocks er en rigtig WindowId")

    # ── ⚠ GENVEJSSPÆRREN, OG AT DEN STÅR FØRST ────────────────────────────
    blok = genvejsblok(kilde)
    k(blok is not None, "genvejshandleren findes")
    if blok:
        k("if (!erAktiv) return;" in blok,
          "handleren returnerer med det samme når panelet IKKE er aktivt")
        # Spærren skal ligge før enhver e.preventDefault() / handleOrder.
        pos_spaerre = blok.find("if (!erAktiv) return;")
        pos_foerste_handling = min(
            [p for p in (blok.find("e.preventDefault()"), blok.find("handleOrder("))
             if p >= 0] or [10**6])
        k(0 <= pos_spaerre < pos_foerste_handling,
          f"spærren står FØR første handling (spærre@{pos_spaerre}, handling@{pos_foerste_handling})")

    # ── Adskilt tilstand pr. liste ────────────────────────────────────────
    k('"watchlist_meta_stocks"' in kilde,
      "aktie-listen har sin EGEN meta-nøgle (ikke delt med futures)")
    k('"watchlist_stocks"' in kilde,
      "aktie-listen har sin egen ticker-nøgle i localStorage")
    k('localStorage.getItem("watchlist")' in kilde,
      "futures-listen beholder den gamle nøgle — Ibens liste overlever")

    # ── Den aktive markering ──────────────────────────────────────────────
    k("watchlist-aktiv" in kilde, "det aktive panel får klassen watchlist-aktiv")
    k(".watchlist-container.watchlist-aktiv" in css,
      "CSS markerer det aktive panel")
    m = re.search(r"\.watchlist-container\.watchlist-aktiv\s*\{[^}]*\}", css, re.S)
    k(m is not None and "#d4ff00" in m.group(0),
      "markeringen er NEONGUL (#d4ff00)")
    k(m is not None and "outline" in m.group(0),
      "markeringen er en RAMME (outline), ikke kun en baggrund")

    # ── Klik aktiverer ────────────────────────────────────────────────────
    k("onMouseDownCapture" in kilde,
      "klik aktiverer i capture-fasen — FØR en knap i panelet reagerer")
    k("onAktiver?.()" in kilde, "klik kalder onAktiver")

    # ── Begge vinduer renderes med hver sin variant ───────────────────────
    k('variant="futures"' in kilde and 'variant="stocks"' in kilde,
      "begge varianter renderes")
    k('case "watchliststocks":' in kilde, "der findes en render-gren for aktie-listen")
    return lokale


def main() -> int:
    kilde = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    layouts = LAYOUTS.read_text(encoding="utf-8")

    print("  ── to watchlister ──")
    kontroller(kilde, css, layouts)

    # ── ⚠ MUTATION 1: fjern spærren ───────────────────────────────────────
    print("\n  ── mutation: fjern genvejsspærren ──")
    m1 = kilde.replace("    if (!erAktiv) return;\n", "", 1)
    kraev(m1 != kilde, "mutationen ændrede kildeteksten")
    f1 = kontroller(m1, css, layouts, stille=True)
    kraev(any("aktivt" in x for x in f1),
          f"uden spærren FEJLER testen ({len(f1)} fejl)")

    # ── ⚠ MUTATION 2: flyt spærren ned under ALT+H ────────────────────────
    # En spærre der staar for sent, ser rigtig ud og virker ikke.
    print("\n  ── mutation: flyt spærren NED under første handling ──")
    blok = genvejsblok(kilde)
    if blok:
        uden = blok.replace("    if (!erAktiv) return;\n", "", 1)
        flyttet = uden.replace("      e.preventDefault();",
                               "      e.preventDefault();\n      if (!erAktiv) return;", 1)
        m2 = kilde.replace(blok, flyttet, 1)
        kraev(m2 != kilde, "mutation 2 ændrede kildeteksten")
        f2 = kontroller(m2, css, layouts, stille=True)
        kraev(any("FØR" in x for x in f2),
              f"en spærre der står for sent FEJLER testen ({len(f2)} fejl)")

    # ── ⚠ MUTATION 3: lad de to lister dele localStorage ──────────────────
    print("\n  ── mutation: lad listerne dele localStorage ──")
    m3 = kilde.replace('"watchlist_meta_stocks"', '"watchlist_meta"')
    kraev(m3 != kilde, "mutation 3 ændrede kildeteksten")
    f3 = kontroller(m3, css, layouts, stille=True)
    kraev(any("EGEN meta" in x for x in f3),
          f"delt localStorage FEJLER testen ({len(f3)} fejl)")

    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
