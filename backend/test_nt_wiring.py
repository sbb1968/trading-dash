"""
test_nt_wiring.py — kan NT8-ordrevejen lyve om hvad den ved?
════════════════════════════════════════════════════════════════════════════════
Wiringen fra en watchlist-knap til NT8 har to steder hvor en paastand kan snige
sig ind i noget der ser ud som en maaling:

  1. `afvent_ordre` — ATI-stroemmen er et ØJEBLIKSBILLEDE, ikke en opregning.
     En ordre der beviseligt fandtes, stod IKKE i snapshottet lige foer den blev
     annulleret (maalt 26-09). Saa:
       · loeber tiden ud, er svaret UKENDT — aldrig "ikke fyldt"
       · et senere, tomt oplaeg maa ikke SLETTE en fyldpris et tidligere gav

  2. `OrdersTracker` — den beriger fra `ib.trades()`. En NT8-ordre staar aldrig
     der, saa det optimistiske "Submitted · 0 fyldt" ville staa for evigt, ogsaa
     efter at NT8 havde fyldt. Det er samme fejlklasse som de to MES-ordrer
     11-08, men uden nogen der til sidst retter den.

⚠ HVAD DENNE TEST IKKE DAEKKER. Dispatchen i main.py (broker-feltet, og at en
spaerret NT8-vagt ikke falder tilbage til IBKR) ligger inline i WS-loekken og
testes ikke herfra. Den skal koeres mod Sim101 med et rigtigt klik.

    python test_nt_wiring.py
"""
from __future__ import annotations

import asyncio
import sys

# Windows-konsollen er cp1252; testens rammer og ⚠ skal kunne skrives
# uanset hvor den koeres fra.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nt_forbindelse as NT
import orders_tracker as OT

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


def med_oplaeg(*oplaeg: str):
    """Lad _laes_raat levere en scriptet foelge af snapshots."""
    koe = list(oplaeg)

    def laes(sekunder=NT.LYT_SEK):
        return koe.pop(0) if koe else (oplaeg[-1] if oplaeg else "")
    return laes


def test_fyldning() -> None:
    print("  ── fyldning: felterne ATI altid har pushet ──")
    raa = ("OrderStatus|NTM1 Filled Filled|NTM1 2 "
           "AvgFillPrice|NTM1 4603.25 ATI True ")
    kraev(NT.fyldning("NTM1", raa) == (2, 4603.25),
          f"Filled+AvgFillPrice laeses ({NT.fyldning('NTM1', raa)})")
    kraev(NT.fyldning("NTM9", raa) == (0, 0.0),
          "en ordre der ikke staar i oplaegget giver (0, 0.0)")
    # ⚠ Og (0, 0.0) maa ikke forveksles med "afvist". Begge er 0 — kun
    # OrderStatus skelner dem.
    kraev(NT.ordre_status("NTM9", raa) == "",
          "…og status er tom, saa kalderen KAN skelne ukendt fra nul")
    # Et uforstaaeligt tal maa ikke vaelte noget.
    kraev(NT.fyldning("NTM2", "Filled|NTM2 x AvgFillPrice|NTM2 y ") == (0, 0.0),
          "ulaeselige tal giver (0, 0.0) frem for at kaste")


def test_afvent_ordre() -> None:
    print("\n  ── afvent_ordre: paastaar den noget den ikke ved? ──")
    aegte = NT._laes_raat
    try:
        # 1. Terminal med det foerste.
        NT._laes_raat = med_oplaeg(
            "OrderStatus|A Filled Filled|A 1 AvgFillPrice|A 4600.5 ")
        r = NT.afvent_ordre("A", sekunder=1, oplaeg_sek=0.01)
        kraev(r["terminal"] and r["status"] == "Filled",
              f"en fyldt ordre meldes terminal ({r['status']})")
        kraev(r["filled"] == 1 and r["avg_fill"] == 4600.5,
              f"fyldning og snitpris kommer med ({r['filled']} @ {r['avg_fill']})")

        # 2. ⚠ ALDRIG "IKKE FYLDT". Tom stroem hele vejen.
        NT._laes_raat = med_oplaeg("", "", "")
        r = NT.afvent_ordre("B", sekunder=0.05, oplaeg_sek=0.01)
        kraev(r["terminal"] is False, "tom stroem -> terminal=False")
        kraev(r["status"] == "",
              f"…og status er TOM, ikke et gaet ({r['status']!r})")
        kraev(r["filled"] == 0 and r["avg_fill"] == 0.0,
              "…og der opdigtes ingen fyldning")

        # 3. ⚠ DEN VIGTIGSTE. Snapshottet taber ordren igen (maalt 26-09).
        #    Et frisk, tomt kig maa IKKE overskrive en fyldpris vi HAVDE.
        NT._laes_raat = med_oplaeg(
            "Filled|C 1 AvgFillPrice|C 4611.75 ",     # fyldningen ses …
            "ATI True ",                              # … og er vaek igen
            "ATI True ")
        r = NT.afvent_ordre("C", sekunder=0.05, oplaeg_sek=0.01)
        kraev(r["avg_fill"] == 4611.75,
              f"⚠ en fyldpris fra et tidligere oplaeg BEVARES ({r['avg_fill']})")
        kraev(r["filled"] == 1, "…ogsaa antallet")
        kraev(r["terminal"] is False,
              "…men uden OrderStatus er den stadig IKKE terminal")

        # 4. Status kommer sent — den skal stadig fanges.
        NT._laes_raat = med_oplaeg(
            "ATI True ", "OrderStatus|D Rejected ")
        r = NT.afvent_ordre("D", sekunder=0.05, oplaeg_sek=0.01)
        kraev(r["status"] == "Rejected" and r["terminal"],
              f"en afvisning i andet oplaeg fanges ({r['status']})")
        kraev(r["oplaeg"] >= 2, f"der blev laest mere end én gang ({r['oplaeg']})")
    finally:
        NT._laes_raat = aegte


class FalskIbkr:
    connected = True
    account = "DUQ441063"

    class ib:
        @staticmethod
        def trades():
            # IBKR kender KUN sin egen ordre 4711 — aldrig NT8's.
            class OS:
                status, filled, remaining, avgFillPrice = "Filled", 1, 0, 7600.0

            class O:
                orderId = 4711

            class T:
                order, orderStatus = O(), OS()
            return [T()]


async def test_tracker() -> None:
    print("\n  ── trackeren: staar en NT8-ordre som en paastand? ──")
    import tempfile
    import pathlib
    with tempfile.TemporaryDirectory() as d:
        aegte_log = OT.ORDERS_LOG
        try:
            OT.ORDERS_LOG = pathlib.Path(d) / "orders_log.json"
            t = OT.OrdersTracker()
            t._entries = []

            # IBKR-ordre — som foer.
            t.record_placed(order_id=4711, source="manual_watchlist",
                            ticker="MES", action="BUY", shares=1)
            # NT8-ordre, FYLDT ifoelge ATI.
            t.record_placed(order_id="NTM_FYLDT", source="manual_watchlist",
                            ticker="MES", action="BUY", shares=1,
                            ibkr_account="Sim101", broker="NT8",
                            maalt={"status": "Filled", "filled": 1,
                                   "avg_fill": 4603.25})
            # NT8-ordre, ATI naaede ingen terminal status.
            t.record_placed(order_id="NTM_UKLAR", source="manual_watchlist",
                            ticker="MES", action="SELL", shares=1,
                            ibkr_account="Sim101", broker="NT8",
                            maalt={"status": "", "filled": 0, "avg_fill": 0})

            # ⚠ Et streng-id maa ikke vaeltes af int().
            ider = [e["order_id"] for e in t._entries]
            kraev("NTM_FYLDT" in ider,
                  f"NT8's streng-id overlever record_placed ({ider})")

            ordrer = {o["order_id"]: o for o in await t.get_all_orders(
                FalskIbkr(), period_hours=72)}

            f = ordrer["NTM_FYLDT"]
            kraev(f["status"] == "Filled" and f["bekraeftet"] is True,
                  f"⚠ ATI's terminale svar staar som BEKRAEFTET ({f['status']})")
            kraev(f["avg_fill"] == 4603.25,
                  f"…med fyldprisen intakt ({f['avg_fill']})")
            # ⚠ Kernen: IBKR's 4711-fyldning maa IKKE smitte over paa NT8-raekken.
            kraev(f["filled"] == 1 and f["avg_fill"] != 7600.0,
                  "⚠ IBKR's egen fyldning smitter ikke over paa NT8-ordren")

            u = ordrer["NTM_UKLAR"]
            kraev(u["status"] == "UNKNOWN",
                  f"⚠ en uafklaret NT8-ordre staar som UKENDT, ikke 'Submitted' "
                  f"({u['status']})")
            kraev(u["note"] and "NinjaTrader" in u["note"],
                  f"…og noten siger hvorfor: {(u['note'] or '')[:58]}")

            # IBKR-ordren skal stadig beriges som foer — intet er gaaet tabt.
            i = ordrer[4711]
            kraev(i["status"] == "Filled" and i["bekraeftet"] is True,
                  f"IBKR-ordren beriges stadig af ib.trades() ({i['status']})")

            # ⚠ MUTATION: fjern broker-maerket fra NT8-raekken. Saa BEHANDLES den
            # som IBKR — og bliver staaende paa sit optimistiske gaet. Det er
            # praecis den fejl `broker` findes for at forhindre.
            print("\n  ── mutation: NT8-ordre uden broker-maerke ──")
            t._entries = []
            t.record_placed(order_id="NTM_NUM", source="manual_watchlist",
                            ticker="MES", action="BUY", shares=1)
            o = {x["order_id"]: x for x in await t.get_all_orders(
                FalskIbkr(), period_hours=72)}["NTM_NUM"]
            kraev(o["status"] == "Submitted" and not o["bekraeftet"],
                  "uden broker-maerket staar gaettet 'Submitted' — dét er faren")
        finally:
            OT.ORDERS_LOG = aegte_log


def test_ruten_er_laast() -> None:
    """Reglen staar ÉT sted, og den staar rigtigt."""
    print("\n  ── ruten: Futures -> NT8, Stocks -> IBKR ──")
    import pathlib
    import re
    sti = pathlib.Path(__file__).parent.parent / "src" / "brokerruter.ts"
    kraev(sti.is_file(), f"brokerruter.ts findes ({sti.name})")
    if not sti.is_file():
        return
    t = sti.read_text(encoding="utf-8")
    m = re.search(r"BROKER_FOR_LISTE[^{]*\{([^}]*)\}", t, re.S)
    kraev(m is not None, "BROKER_FOR_LISTE kan laeses")
    if m:
        krop = m.group(1)
        kraev(re.search(r'futures:\s*"NT8"', krop) is not None,
              "Watchlist Futures -> NT8")
        kraev(re.search(r'stocks:\s*"IBKR"', krop) is not None,
              "Watchlist Stocks -> IBKR")
    # ⚠ Ingen default. Findes der en, er "ALTID" ikke laengere sandt.
    kraev("||" not in t and "?? " not in t,
          "⚠ ingen fallback-operator i rute-modulet")

    # Og backenden maa ikke have en standardbroker.
    hoved = (pathlib.Path(__file__).parent / "main.py").read_text(encoding="utf-8")
    kraev('message.get("broker", "")' in hoved,
          "backenden laeser broker UDEN en standardvaerdi")
    kraev('if broker not in ("IBKR", "NT8"):' in hoved,
          "…og afviser alt andet end de to kendte")


def main() -> int:
    print("  ── NT8-wiring ──")
    test_fyldning()
    test_afvent_ordre()
    asyncio.run(test_tracker())
    test_ruten_er_laast()
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
