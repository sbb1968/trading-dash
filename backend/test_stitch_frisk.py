"""
test_stitch_frisk.py — er de sammensyede serier helt fremme ved rådataene?
════════════════════════════════════════════════════════════════════════════════
Arbejdsordrens T4. Bestå-kriteriet, ordret: *stitched-filernes sidste bar =
rådataenes sidste bar, og sessionstællingen matcher.*

⚠ HVORFOR DET IKKE ER "ØJEMÅL PÅ EN TAIL". Den 15-08 stod de sammensyede filer
fire dage bagud uden at noget fejlede — rådata var friske, syningen var ikke
kørt. Ingen advarsel, ingen tom fil, bare et stille efterslæb. En serie der er
fire dage gammel ser præcis ud som en der er frisk, hvis man kun kigger på
formatet.

⚠ TO KONTROLLER, IKKE ÉN, og den anden er den vigtige:
  1. Sidste bar stemmer. Fanger et manglende kørsel.
  2. Sessionstællingen stemmer. Fanger noget værre: at syningen kørte, men tabte
     dage undervejs. Filen ville da SLUTTE rigtigt og alligevel have huller.
     Kun (1) ville kalde det grønt.

    python test_stitch_frisk.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROD = Path(__file__).resolve().parent / "data_harvest"
STITCH = ROD / "mes_m2k_stitched"

# Hvilken rå-kontrakt er den nyeste pr. symbol? Findes ved at måle, ikke ved at
# skrive "202609" ind — kontrakten ruller, og et hardkodet kvartal ville gøre
# prøven grøn på en forældet fil fra det øjeblik rullet sker.
SYMBOLER = ("MES", "M2K", "MNQ")

FEJL = 0


def kraev(ok: bool, hvad: str) -> None:
    global FEJL
    print(f"  {'OK  ' if ok else 'FEJL'} {hvad}")
    if not ok:
        FEJL += 1


def sidste_og_dage(p: Path) -> tuple[str, set[str]]:
    """Sidste tidsstempel og mængden af handelsdage i filen."""
    sidste, dage = "", set()
    with p.open(encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for raekke in r:
            if not raekke or not raekke[0][:1].isdigit():
                continue
            sidste = raekke[0]
            dage.add(raekke[0][:10])
    return sidste, dage


def nyeste_raa(sym: str) -> Path | None:
    kand = sorted(ROD.glob(f"{sym}_2*_1min.csv"))
    return kand[-1] if kand else None


def alle_kilde_dage(sym: str) -> tuple[set[str], int]:
    """Alle handelsdage i det KURATEREDE datasæt — kilden til syningen.

    ⚠ FØRSTE UDGAVE KIGGEDE KUN I DEN NYESTE RÅ-KONTRAKT, og så kunne den ikke
    se en dag der var tabt midt i serien: den dag hører til en ældre kontrakt.
    Falsifikationen "fjern 2026-03-04" gik lige igennem, mens prøven meldte alt
    grønt. En prøve der kun kigger i den sidste niendedel af data, kan kun
    finde fejl i den sidste niendedel.

    De kuraterede per-kontrakt-filer er det syningen FAKTISK bygges af, så det
    er dem der skal sammenlignes med — ikke rådata, som curate med vilje
    trimmer back-month-junk væk fra.
    """
    dage: set[str] = set()
    filer = sorted((ROD / "mes_m2k_clean").glob(f"{sym}_2*_1min.csv"))
    for p in filer:
        _, d = sidste_og_dage(p)
        dage |= d
    return dage, len(filer)


def main() -> int:
    print("T4 — er de sammensyede serier helt fremme?\n")
    for sym in SYMBOLER:
        raa = nyeste_raa(sym)
        syet = STITCH / f"{sym}_1min.csv"
        if raa is None or not syet.exists():
            kraev(False, f"{sym}: mangler filer (raa={raa}, syet={syet.exists()})")
            continue

        raa_sidste, _ = sidste_og_dage(raa)
        syet_sidste, syet_dage = sidste_og_dage(syet)
        kilde_dage, n_kontrakter = alle_kilde_dage(sym)

        # ── 1. Slutter de samme sted? ───────────────────────────────────────
        kraev(syet_sidste == raa_sidste,
              f"{sym}: syet slutter hvor raa slutter "
              f"({syet_sidste[:16]} vs {raa_sidste[:16]})")

        # ── 2. Er alle rå-dage med? ─────────────────────────────────────────
        # ⚠ Kurateringen TRIMMER med vilje back-month-junk væk i starten af en
        # kontrakt, så syet må gerne mangle rå-dage FØR sit eget første døgn.
        # Den må ikke mangle nogen EFTER. Det er dér et tabt døgn ville gemme sig.
        mangler = sorted(kilde_dage - syet_dage)
        kraev(not mangler,
              f"{sym}: ingen dag tabt i syningen "
              f"({len(mangler)} manglende{': ' + ', '.join(mangler[:5]) if mangler else ''})")
        # ⚠ Og den anden vej: syet må heller ikke indeholde dage kilden ikke har.
        ekstra = sorted(syet_dage - kilde_dage)
        kraev(not ekstra,
              f"{sym}: ingen fremmede dage i syningen "
              f"({len(ekstra)}{': ' + ', '.join(ekstra[:5]) if ekstra else ''})")
        print(f"        {len(syet_dage):>5} dage i syet · "
              f"{len(kilde_dage):>5} i {n_kontrakter} kuraterede kontrakter")
    return FEJL


if __name__ == "__main__":
    n = main()
    print(f"\n⚠ {n} FEJL" if n else "\nAlle bestod.")
    sys.exit(1 if n else 0)
