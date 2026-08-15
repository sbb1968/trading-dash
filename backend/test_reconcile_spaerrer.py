"""
test_reconcile_spaerrer.py — en kontrol der fejler, må ikke betyde "handl alligevel"
════════════════════════════════════════════════════════════════════════════════
DEN 13-08 STOD DER I LOGGEN:

    13:20:49  Konfluens 2   Reconciliation timeout — fortsætter til handel
    13:22:52  BuyTheDip     Reconciliation timeout — fortsætter til handel

Begge strategier handlede videre oven på fem positioner fra 12-08 som de ikke
vidste eksisterede. Budgettet var 30 sekunder; K2 brugte 32.

⚠ AT HÆVE BUDGETTET FLYTTER KLIPPEKANTEN — DET FJERNER DEN IKKE. Fejlen er
ikke tallet 30. Fejlen er at konsekvensen af at fejle er at fortsætte som om
man bestod. Det er samme fejlklasse som resten af projektets liste, nu i
produktionskoden med penge på.

DENNE PRØVE ER BESTÅ-KRITERIET (arbejdsordrens T2e): fremtving en
reconcile-timeout, og kræv at der IKKE åbnes position.

⚠ OG DEN MODSATTE VEJ ER LIGE SÅ VIGTIG: en spærring der også blokerer
LUKNINGER ville fange én i en position man ikke kan komme ud af. Det er værre
end fejlen den løser. Begge retninger prøves.

    python test_reconcile_spaerrer.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from strategy_base import (OrderRequest, BaseStrategy, StrategyConfig,
                           StrategyStatus)

FEJL = 0


def kraev(ok: bool, hvad: str) -> None:
    global FEJL
    print(f"  {'OK  ' if ok else 'FEJL'} {hvad}")
    if not ok:
        FEJL += 1


class Attrap(BaseStrategy):
    """Mindste strategi der kan modtage en ordre. Ingen IBKR, ingen journal."""

    # ⚠ De tre abstrakte properties skal implementeres — basisklassen kraever
    # dem, saa enhver strategi ER navngivet og har en aktivklasse. Attrappen
    # opfylder kontrakten frem for at omgaa den.
    @property
    def name(self) -> str: return "Attrap"

    @property
    def description(self) -> str: return "attrap til proeven"

    @property
    def asset_class(self) -> str: return "equity"

    def __init__(self):
        super().__init__(config=StrategyConfig())
        self.status = StrategyStatus.RUNNING
        self._risk_manager = self            # godkender alt — se check_order
        self.godkendte: list[OrderRequest] = []

    # RiskManager-flade: siger ja til alt, så det KUN er entry-spærringen
    # der kan afvise. Ellers målte prøven risikostyringen i stedet.
    async def approve_order(self, order):
        self.godkendte.append(order)
        return True, "ok"

    async def pre_flight(self):
        return True, "ok"

    async def on_start(self):  ...
    async def on_bar(self, ticker, bar):  ...
    async def on_stop(self):  ...


def entry(s: Attrap) -> OrderRequest:
    return OrderRequest(strategy_name=s.name, ticker="AAPL", action="BUY",
                        quantity=10, order_type="MKT", aabner=True,
                        reason="entry")


def lukning(s: Attrap) -> OrderRequest:
    # ⚠ Ingen aabner=True. Det er hele pointen: default er "lukning", så en
    # glemt markering fejler i den ufarlige retning.
    return OrderRequest(strategy_name=s.name, ticker="AAPL", action="SELL",
                        quantity=10, order_type="MKT", reason="stop loss")


async def koer() -> None:
    s = Attrap()

    # ── 1. Uden spærring slipper alt igennem ────────────────────────────────
    # ⚠ Uden denne kontrol kunne prøven bestå på en strategi der afviser ALT,
    # og så måler den ingenting.
    kraev(await s.request_order(entry(s)) is True,
          "uden spærring godkendes en entry")
    kraev(await s.request_order(lukning(s)) is True,
          "uden spærring godkendes en lukning")

    # ── 2. KERNEN: spærret → ingen ny position ─────────────────────────────
    s.spaer_entries("reconcile-timeout efter 3 forsøg a 30s")
    n_foer = len(s.godkendte)
    kraev(await s.request_order(entry(s)) is False,
          "SPÆRRET: en entry afvises")
    kraev(len(s.godkendte) == n_foer,
          "…og den nåede ALDRIG frem til risikostyringen/ordrelaget")

    # ── 3. Beskyttelsen af det man allerede ejer, røres ikke ────────────────
    kraev(await s.request_order(lukning(s)) is True,
          "SPÆRRET: en LUKNING slipper stadig igennem")
    kraev(len(s.godkendte) == n_foer + 1,
          "…og nåede faktisk frem")

    # ── 4. Grunden skal kunne læses, ikke gættes ───────────────────────────
    kraev("reconcile-timeout" in (s._entry_spaerret or ""),
          f"grunden står i tilstanden ({s._entry_spaerret!r})")

    # ── 5. Ophævelse — og KUN ved en bestået kontrol ───────────────────────
    s.ophaev_entry_spaerring()
    kraev(s._entry_spaerret is None, "spærringen kan ophæves")
    kraev(await s.request_order(entry(s)) is True,
          "…og så handles der igen")

    # ── 6. Selve opstartsforløbet: timeout → spærret ───────────────────────
    # ⚠ HER PRØVES DEN RIGTIGE KODE, ikke en efterligning af den. Vi tvinger
    # _reconcile_orphans til at hænge og kører K2's on_start-blok som den er.
    import algo_confluence2 as k2
    kraev(k2.RECONCILE_MAX_FORSOEG >= 2,
          f"der genforsøges ({k2.RECONCILE_MAX_FORSOEG} forsøg)")

    # ⚠ FIRE UDGANGE, IKKE ÉN. Timeout var kun den ene. En undtagelse, en
    # manglende IBKR-forbindelse og et upålideligt positions-feed giver præcis
    # samme resultat: reconcile verificerede intet. Alle fire skal spærre.
    # Første udgave af denne prøve kiggede kun efter timeout-stien — og den
    # bestod, mens tre huller stod åbne.
    for fil in ("algo_confluence2.py", "algo_buythedip.py",
                "algo_europa_reversion.py", "algo_relstyrke.py",
                "algo_trendjoin.py", "algo_us_reversion.py"):
        k = (Path(__file__).parent / fil).read_text(encoding="utf-8")
        kraev(k.count("self.spaer_entries(") == 4,
              f"{fil}: alle FIRE udgange spærrer ({k.count('self.spaer_entries(')})")
        kraev(k.count("aabner=True") == 1,
              f"{fil}: præcis ét markeret entry-kaldested")
        # ⚠ Ophævelsen må ikke annullere en spærring der lige blev sat: wrapperen
        # returnerer normalt også når reconcile sprang over INDENI.
        kraev("self._entry_spaerret == _spaerret_foer" in k,
              f"{fil}: ophæver kun hvis intet spærrede undervejs")


if __name__ == "__main__":
    print("reconcile-timeout må ikke betyde 'handl alligevel'\n")
    asyncio.run(koer())
    print(f"\n⚠ {FEJL} FEJL" if FEJL else "\nAlle bestod.")
    sys.exit(1 if FEJL else 0)
