"""
test_ordrevindue_markering.py — den aabne position skal kunne SES
════════════════════════════════════════════════════════════════════════════════
Iben bad 08-10 om at kunne se i Ordrer-fanen hvilken entry der stadig er aaben,
og at markeringen forsvinder naar exit'en fylder.

⚠ DET NAERLIGGENDE VAR AT GENBRUGE `exit_mulig`. Den peger allerede paa den
raekke der skal have exit-knapper. Men den er IKKE det samme spoergsmaal:

    position_aaben  ER der en position paa denne raekke?   (et faktum om kontoen)
    exit_mulig      KAN vi tilbyde knapper paa den?        (kraever derudover at
                                                            kontraktmaaneden er
                                                            slaaet op)

Farvede man raekken efter `exit_mulig`, ville en AABEN position se LUKKET ud i
det oejeblik kontraktopslaget var nede — en maaling hvis fravaer blev vist som
et faktum. Det er samme fejlklasse som resten af exit-modulet er bygget imod,
og den ville ramme praecis naar man mest har brug for at vide at man staar med
noget aabent.

Testen findes for at holde de to adskilt. Falder de sammen igen, fejler den.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import main

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


def r(oid, type_, action, filled=1, tid="08:00", status="Filled"):
    return {"order_id": oid, "placed_at": f"2026-10-08T{tid}",
            "ordre_type": type_, "action": action, "filled": filled,
            "status": status, "ticker": "MES", "source": "manual_watchlist"}


def _med_instrument(navn):
    """Saet kontraktmaaneden. Returnerer en gendan-funktion."""
    gl = main._EXIT_INSTRUMENT.get("navn")
    main._EXIT_INSTRUMENT["navn"] = navn
    return lambda: main._EXIT_INSTRUMENT.__setitem__("navn", gl)


def koer() -> None:
    print("  -- markering af den aabne position --")

    # ── 1. Aaben long -> markeret ────────────────────────────────────────
    aaben = [r("NTM1", "LONG", "BUY")]
    gendan = _med_instrument("MES 12-26")
    try:
        ud = main._berig_med_exit([dict(x) for x in aaben])
        kraev(ud[0]["position_aaben"] is True,
              "aaben long -> position_aaben")
        kraev(ud[0]["exit_mulig"] is True, "…og exit_mulig, for vi har instrumentet")
    finally:
        gendan()

    # ── 2. ⚠ KERNEN: uden kontraktmaaned er positionen STADIG aaben ──────
    print()
    gendan = _med_instrument(None)
    try:
        ud = main._berig_med_exit([dict(x) for x in aaben])
        kraev(ud[0]["position_aaben"] is True,
              "⚠ uden kontraktmaaned er positionen STADIG aaben — og markeret")
        kraev(ud[0]["exit_mulig"] is False,
              "⚠ …men der tilbydes ingen knapper. De to er IKKE samme felt.")
    finally:
        gendan()

    # ── 3. Lukket position -> markeringen forsvinder af sig selv ─────────
    print()
    gendan = _med_instrument("MES 12-26")
    try:
        lukket = aaben + [r("NTM2", "EXIT", "SELL", tid="09:00")]
        ud = main._berig_med_exit([dict(x) for x in lukket])
        for o in ud:
            if o["order_id"] == "NTM1":
                kraev(o["position_aaben"] is False,
                      "⚠ exit fyldt -> markeringen slukker, uden oprydning")

        # Ny position bagefter -> markeringen flytter til den NYE raekke.
        igen = lukket + [r("NTM3", "LONG", "BUY", tid="10:00")]
        ud = main._berig_med_exit([dict(x) for x in igen])
        markerede = [o["order_id"] for o in ud if o.get("position_aaben")]
        kraev(markerede == ["NTM3"],
              f"ny position -> kun den NYE raekke markeres ({markerede})")

        # ── 4. Exit-raekker markeres ikke. De er ikke en position. ───────
        print()
        med_exit = [r("NTM4", "LONG", "BUY", tid="11:00"),
                    r("NTX1", "STOP", "SELL", filled=0, tid="11:01",
                      status="Working")]
        med_exit[1]["source"] = "manual_exit"
        med_exit[1]["parent_order_id"] = "NTM4"
        ud = main._berig_med_exit([dict(x) for x in med_exit])
        markerede = [o["order_id"] for o in ud if o.get("position_aaben")]
        kraev(markerede == ["NTM4"],
              f"⚠ kun entry-raekken markeres, ikke dens STOP ({markerede})")

        # ── 5. Ingen position -> ingen markering overhovedet ─────────────
        print()
        ud = main._berig_med_exit([])
        kraev(ud == [], "tom liste -> tom liste")
        kun_exit = [dict(med_exit[1])]
        ud = main._berig_med_exit(kun_exit)
        kraev(not any(o.get("position_aaben") for o in ud),
              "en foraeldreloes exit-raekke markeres ikke")
    finally:
        gendan()


def main_() -> int:
    koer()
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main_())
