"""
test_nt_position.py — kan positionsvagten narres?
════════════════════════════════════════════════════════════════════════════════
ATI kan rapportere positioner. Det troede vi ikke, og hele NT8-stien blev bygget
på at den ikke kunne. Målt 28-09-2026 pusher strømmen:

    MarketPosition|MES DEC26|Sim101 -4
    AvgEntryPrice|MES DEC26|Sim101 7773.125
    RealizedPnL|Sim101 -2.5

⚠ MEN NAVNET ER IKKE DET SAMME SOM DET VI SENDER.
Vi skriver `MES 12-26` i OIF-kommandoen; strømmens nøgle er `MES DEC26`. Og ved
siden af den ligger FORÆLDEDE ekkoer under andre stavemåder — `@MES`, `MESZ26`,
`MES Z6` — som alle stod til **1** mens den rigtige stod til **-4**. Slår man
den forkerte op, får man et forkert svar der ser fuldstændig rigtigt ud.

Det er hele grunden til at denne fil findes. De tre ting den kræver:

  1. oversættelsen rammer NT8's eget navn
  2. et FORÆLDET ekko under en anden stavemåde bliver ALDRIG læst
  3. en manglende nøgle er UKENDT, ikke "flad" — og fører til advarsel, ikke spærring

    python test_nt_position.py
"""
from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import manuel_forensik as MF
import nt_forbindelse as NT

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


# Et ægte oplæg, kopieret fra den kørende NT8 28-09 kl. 09:05.
# ⚠ Bemærk de tre forældede ekkoer med værdien 1.
STROEM = (
    "2 MarketPosition|MES DEC26|Sim101 -4 "
    "2 AvgEntryPrice|MES DEC26|Sim101 7773.125 "
    "2 MarketPosition|@MES|Sim101 1 "
    "2 AvgEntryPrice|@MES|Sim101 7772.5 "
    "2 MarketPosition|MESZ26|Sim101 1 "
    "2 AvgEntryPrice|MESZ26|Sim101 7772.5 "
    "2 MarketPosition|MES Z6|Sim101 1 "
    "2 RealizedPnL|Sim101 -2.5 "
    "2 CashValue|Sim101 100000 2 ATI True "
)


def med(stroem: str):
    NT._laes_raat = lambda sekunder=NT.LYT_SEK: stroem       # type: ignore


def test_navnet() -> None:
    print("  ── oversaettelsen: OIF-navn -> ATI-navn ──")
    kraev(NT.ati_noegle("MES 12-26") == "MES DEC26", "MES 12-26 -> MES DEC26")
    kraev(NT.ati_noegle("M2K 03-27") == "M2K MAR27", "M2K 03-27 -> M2K MAR27")
    kraev(NT.ati_noegle("MES 09-26") == "MES SEP26", "MES 09-26 -> MES SEP26")
    for daarlig in ("MES", "MES DEC26", "MES 13-26", ""):
        try:
            NT.ati_noegle(daarlig)
            kraev(False, f"{daarlig!r} burde kaste")
        except NT.NtForbindelseFejl:
            kraev(True, f"{daarlig!r} kaster i stedet for at gaette")


def test_position() -> None:
    print("\n  ── ⚠ laeses den RIGTIGE noegle? ──")
    aegte = NT._laes_raat
    try:
        med(STROEM)
        p = NT.position("MES 12-26", "Sim101")
        kraev(p["noegle"] == "MES DEC26", f"noeglen er NT8's eget navn ({p['noegle']})")
        # ⚠ Kernen. De foraeldede ekkoer staar til 1; den rigtige til -4.
        kraev(p["netto"] == -4,
              f"⚠ den RIGTIGE position laeses, ikke et foraeldet ekko ({p['netto']})")
        kraev(p["netto"] != 1,
              "⚠ @MES / MESZ26 / 'MES Z6' stod alle til 1 — ingen af dem blev laest")
        kraev(p["snit"] == 7773.125, f"snitkursen foelger med ({p['snit']})")
        kraev(p["realiseret"] == -2.5, f"realiseret P&L laeses ({p['realiseret']})")
        kraev(p["set"] is True, "noeglen blev fundet")

        # ── Flad er 0, og det er et SVAR ──────────────────────────────────
        med(STROEM.replace("MES DEC26|Sim101 -4", "MES DEC26|Sim101 0"))
        p = NT.position("MES 12-26", "Sim101")
        kraev(p["netto"] == 0 and p["set"], f"flad laeses som 0, ikke None ({p['netto']})")

        # ── ⚠ Manglende noegle er UKENDT, ikke flad ───────────────────────
        print("\n  ── ⚠ fravaer af noeglen ──")
        med("2 CashValue|Sim101 100000 2 ATI True ")
        p = NT.position("MES 12-26", "Sim101")
        kraev(p["netto"] is None,
              f"noeglen mangler -> netto=None (UKENDT), ikke 0 ({p['netto']})")
        kraev(p["set"] is False, "…og `set` siger at vi ikke saa den")

        # ── Forkert konto maa ikke matche ─────────────────────────────────
        med(STROEM)
        p = NT.position("MES 12-26", "DEMO8580770")
        kraev(p["netto"] is None,
              f"Sim101's position lækker IKKE til DEMO8580770 ({p['netto']})")

        # ── Tom stroem kaster ─────────────────────────────────────────────
        med("")
        try:
            NT.position("MES 12-26", "Sim101")
            kraev(False, "tom stroem burde kaste")
        except NT.NtTilstandUkendt:
            kraev(True, "tom stroem kaster NtTilstandUkendt")
    finally:
        NT._laes_raat = aegte                                 # type: ignore


class TomJournal:
    db = None

    async def log_event(self, **kw):
        pass


async def test_vagten() -> None:
    print("\n  ── vagten: samme regel som IBKR's ──")
    aegte = NT._laes_raat
    j = TomJournal()
    try:
        # short 4 — et koeb paa 6 ville daekke OG aabne long 2.
        med(STROEM)
        ok, besked, det = await MF.kontroller_ordre_nt8(
            j, symbol="MES", instrument="MES 12-26", konto="Sim101",
            action="BUY", shares=6)
        kraev(ok is False, "koeb 6 mod short 4 SPAERRES (vender fortegnet)")
        kraev(det["kilde"] == "nt8-ati" and det["kontrolleret"] is True,
              f"…og den siger at BROKEREN blev spurgt ({det['kilde']})")
        kraev("short 4" in besked, f"    beskeden naevner positionen: {besked[:52]}")

        # Et koeb paa 4 daekker praecis — tilladt.
        ok, _b, _d = await MF.kontroller_ordre_nt8(
            j, symbol="MES", instrument="MES 12-26", konto="Sim101",
            action="BUY", shares=4)
        kraev(ok is True, "koeb 4 mod short 4 tillades (daekker praecis)")

        # ⚠ Og et salg oven i en short er HELT NORMALT. Reglen er ikke
        # "aabn aldrig mere" — det var netop den for brede udgave der 25-08
        # gjorde at Iben ikke kunne handle.
        ok, _b, _d = await MF.kontroller_ordre_nt8(
            j, symbol="MES", instrument="MES 12-26", konto="Sim101",
            action="SELL", shares=1)
        kraev(ok is True, "salg oven i en short tillades — det oeger bare")

        # ── ⚠ UKENDT POSITION SPAERRER IKKE ───────────────────────────────
        print("\n  ── ⚠ ukendt position -> advarsel, ikke spaerring ──")
        med("2 CashValue|Sim101 100000 2 ATI True ")
        ok, besked, det = await MF.kontroller_ordre_nt8(
            j, symbol="MES", instrument="MES 12-26", konto="Sim101",
            action="SELL", shares=99)
        kraev(ok is True, "⚠ et salg SPAERRES IKKE naar positionen er ukendt")
        kraev(det["kontrolleret"] is False,
              "…og markeres som UKONTROLLERET, ikke som godkendt")
        kraev("journal" in det.get("kilde", ""),
              f"    kilden siger hvad der faktisk blev spurgt: {det.get('kilde')}")
        kraev(bool(det.get("grund")), f"    og hvorfor: {str(det.get('grund'))[:56]}")

        # Et uoversaetteligt navn maa heller ikke spaerre.
        med(STROEM)
        ok, _b, det = await MF.kontroller_ordre_nt8(
            j, symbol="MES", instrument="noget vrovl", konto="Sim101",
            action="SELL", shares=1)
        kraev(ok is True, "et uoversaetteligt instrumentnavn spaerrer ikke")
        kraev(det["kontrolleret"] is False, "…men er heller ikke kontrolleret")
    finally:
        NT._laes_raat = aegte                                 # type: ignore


def test_delt_regel() -> None:
    """⚠ ÉN regel, to brokere — ikke to kopier."""
    print("\n  ── reglen er delt med IBKR-vagten ──")
    kraev(hasattr(MF, "_vender_positionen"), "_vender_positionen findes")
    ok, b = MF._vender_positionen(2, "SELL", 5, "MES")
    kraev(ok is False and "2 MES" in b, "long 2, salg 5 -> spaerret")
    ok, _ = MF._vender_positionen(0, "SELL", 1, "MES")
    kraev(ok is True,
          "⚠ netto 0, salg 1 -> TILLADT (det var dét dobbeltklikket gjorde)")


def main() -> int:
    print("  ── NT8-positionsvagt ──")
    test_navnet()
    test_position()
    asyncio.run(test_vagten())
    test_delt_regel()
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
