"""
test_ordrer_periode.py — "aktuel dag" må ikke vise i går
════════════════════════════════════════════════════════════════════════════════
Iben meldte at Ordrer-vinduet viste handler fra dagen før. Mistanken var at hun
stod med "Sidste 3 dage" uden at vide det. Det gjorde hun ikke — valget hed
**"I dag (24 timer)"** og var et RULLENDE vindue:

    cutoff = datetime.now() - timedelta(hours=24)

Kl. 09:30 betød det "tilbage til 09:30 i går", altså hele gårsdagens session.
Målt 28-09 på rigtige data: 8 ordrer vist, 2 af dem fra i går. Etiketten sagde
"I dag"; koden sagde noget andet. Det er samme fejlklasse som resten af ugen —
en påstand der ikke blev efterprøvet.

Denne test er skrevet mod præcis dét scenarie: **en ordre 23 timer gammel, som
er fra i går.** Den må ikke være med i "aktuel dag", og den SKAL være med i
"rullende 24 timer" — ellers har vi bare flyttet fejlen.

    python test_ordrer_periode.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import orders_tracker as OT

fejl: list[str] = []


def kraev(betingelse: bool, hvad: str) -> None:
    print(f"  {'OK  ' if betingelse else 'FEJL'} {hvad}")
    if not betingelse:
        fejl.append(hvad)


class IngenIbkr:
    connected = False


async def koer() -> None:
    nu = datetime.now()
    midnat = nu.replace(hour=0, minute=0, second=0, microsecond=0)

    # ⚠ Testen skal koere paa et tidspunkt hvor "23 timer siden" ER i gaar.
    # Er klokken over 23:00, falder 23 timer siden inden for samme doegn, og
    # saa tester vi ikke det vi tror. Sig det frem for at bestaa ved et tilfaelde.
    if nu - timedelta(hours=23) >= midnat:
        print(f"  ⚠ Klokken er {nu:%H:%M} — '23 timer siden' er samme kalenderdag.")
        print("     Testen ville bestaa uden at vise noget. Springes over.")
        return

    with tempfile.TemporaryDirectory() as d:
        aegte = OT.ORDERS_LOG
        try:
            OT.ORDERS_LOG = pathlib.Path(d) / "log.json"
            t = OT.OrdersTracker()
            t._entries = []

            sager = [
                ("i_gaar_23t",  nu - timedelta(hours=23), False),
                ("i_gaar_sent", midnat - timedelta(minutes=5), False),
                ("i_dag_tidlig", midnat + timedelta(minutes=5), True),
                ("i_dag_nu",    nu - timedelta(minutes=2), True),
            ]
            for navn, tid, _ in sager:
                t.record_placed(order_id=navn, source="manual_watchlist",
                                ticker="MES", action="BUY", shares=1)
                t._entries[-1]["placed_at"] = tid.isoformat()

            # ── "Aktuel dag" — skaeringspunktet er midnat ────────────────
            print("  ── aktuel dag (since=midnat) ──")
            dag = {o["order_id"] for o in await t.get_all_orders(
                IngenIbkr(), period_hours=24, since=midnat)}
            for navn, tid, i_dag in sager:
                med = navn in dag
                kraev(med is i_dag,
                      f"{navn} ({tid:%d-%m %H:%M}) "
                      f"{'ER med' if med else 'er IKKE med'} — forventet "
                      f"{'med' if i_dag else 'ikke med'}")
            # ⚠ Kernen i Ibens melding.
            kraev("i_gaar_23t" not in dag,
                  "⚠ en ordre fra 23 TIMER SIDEN (i gaar) vises IKKE")
            kraev("i_gaar_sent" not in dag,
                  "⚠ heller ikke én fra fem minutter foer midnat")

            # ── "Rullende 24 timer" skal stadig betyde det den siger ─────
            print("\n  ── rullende 24 timer (uaendret adfaerd) ──")
            rul = {o["order_id"] for o in await t.get_all_orders(
                IngenIbkr(), period_hours=24)}
            kraev("i_gaar_23t" in rul,
                  "23 timer siden ER med — ellers har vi flyttet fejlen")
            kraev("i_gaar_sent" in rul, "…og den fra i gaar aften ogsaa")
            kraev(len(rul) == 4, f"alle fire er inden for 24 timer ({len(rul)})")

            # ⚠ Og de to maa ikke give samme svar — saa var skellet meningsloest.
            kraev(dag != rul,
                  "⚠ 'aktuel dag' og 'rullende 24 timer' giver FORSKELLIGE svar")
            print(f"       aktuel dag: {len(dag)} · rullende: {len(rul)}")

            # ── Noget meget gammelt er ude af begge ─────────────────────
            t.record_placed(order_id="uge_gammel", source="manual_watchlist",
                            ticker="MES", action="BUY", shares=1)
            t._entries[-1]["placed_at"] = (nu - timedelta(days=7)).isoformat()
            dag2 = {o["order_id"] for o in await t.get_all_orders(
                IngenIbkr(), period_hours=24, since=midnat)}
            kraev("uge_gammel" not in dag2, "en uge gammel ordre er ude")
        finally:
            OT.ORDERS_LOG = aegte


def main() -> int:
    print("  ── Ordrer-vinduets periode ──")
    asyncio.run(koer())
    print(f"\n  {'ALLE BESTAAET' if not fejl else f'⚠ {len(fejl)} FEJLEDE'}")
    return 1 if fejl else 0


if __name__ == "__main__":
    sys.exit(main())
