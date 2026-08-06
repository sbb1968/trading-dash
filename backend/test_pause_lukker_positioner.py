"""
test_pause_lukker_positioner.py — pause maa ikke forlade aabne positioner
═══════════════════════════════════════════════════════════════════════════════════
5. august 2026 ramte Konfluens 2 sin daglige tabsgraense ($25) kl. 12:13 ET.
request_order satte status = PAUSED, og hovedloekken stod

    while self.status == StrategyStatus.RUNNING:

Loekken afsluttede dermed oejeblikkeligt — og INDE i den ligger stop-tjek, trailing
og tvangslukning. SLS og VELO laa aabne natten over uden stop. Tvangslukningen
15:30 ET fyrede aldrig, og fordi nedlukningen skrev shutdown_reason='unknown',
lignede det en genstart.

Selvmodsigelsen: en graense der findes for at standse blødningen, holdt op med at
passe paa de positioner der allerede blødte.

Reglen er nu: pause = "ingen NYE entries", ikke "hold op med at passe paa det du
har". Entry-stien er stadig lukket (request_order afviser alt der ikke er RUNNING).

    python test_pause_lukker_positioner.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategy_base import BaseStrategy, StrategyStatus

FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


class _Bar(BaseStrategy):
    """Minimal konkret strategi — BaseStrategy er abstrakt, saa vi kan ikke
    instantiere den direkte. Kun loop_skal_koere afproeves her."""
    name = "test"; description = "test"; asset_class = "stock"
    async def pre_flight(self): return True, ""
    async def on_start(self): pass
    async def on_stop(self): pass
    async def on_bar(self, *a, **k): pass


def strategi(status: str, aabne: int):
    """Et bart objekt med kun de felter loop_skal_koere laeser."""
    s = _Bar.__new__(_Bar)
    s.status = status
    s.stats = type("S", (), {"open_positions": aabne})()
    return s


print("\n1. Kernereglen")
kraev(BaseStrategy.loop_skal_koere(strategi(StrategyStatus.RUNNING, 0)) is True,
      "RUNNING uden positioner -> koerer (leder efter entries)")
kraev(BaseStrategy.loop_skal_koere(strategi(StrategyStatus.RUNNING, 2)) is True,
      "RUNNING med positioner -> koerer")

print("\n2. Det der gik galt 5/8 — PAUSED med aabne positioner")
kraev(BaseStrategy.loop_skal_koere(strategi(StrategyStatus.PAUSED, 2)) is True,
      "PAUSED med 2 aabne -> koerer VIDERE (stop, trailing, tvangsluk virker)")
kraev(BaseStrategy.loop_skal_koere(strategi(StrategyStatus.PAUSED, 1)) is True,
      "PAUSED med 1 aaben -> koerer videre")

print("\n3. Naar sidste position er lukket, stopper den selv")
kraev(BaseStrategy.loop_skal_koere(strategi(StrategyStatus.PAUSED, 0)) is False,
      "PAUSED uden positioner -> stopper (intet tilbage at passe paa)")

print("\n4. Manuelt stop og fejl overtrumfer ALT")
# Et menneske der trykker stop, eller en strategi i fejltilstand, skal ikke
# kunne holdes i live af en aaben position — dér er indgrebet bevidst.
for st in (StrategyStatus.STOPPED, StrategyStatus.ERROR, StrategyStatus.IDLE):
    kraev(BaseStrategy.loop_skal_koere(strategi(st, 3)) is False,
          f"{st} med 3 aabne -> stopper alligevel")

print("\n5. Entry-stien er STADIG lukket under pause")
# Det er hele pointen: vi aabner loekken for at kunne LUKKE, ikke for at kunne
# aabne. request_order afviser alt der ikke er RUNNING — foerste tjek i funktionen.
kilde = Path("strategy_base.py").read_text(encoding="utf-8")
m = re.search(r"async def request_order.*?\n(.*?)\n    async def", kilde, re.S)
krop = m.group(1) if m else ""
kraev("if self.status != StrategyStatus.RUNNING" in krop,
      "request_order afviser stadig alt der ikke er RUNNING")

print("\n6. Ingen strategi har den gamle loekke tilbage")
algoer = ["algo_confluence2", "algo_buythedip", "algo_relstyrke",
          "algo_trendjoin", "algo_europa_reversion", "algo_us_reversion"]
for a in algoer:
    t = Path(a + ".py").read_text(encoding="utf-8")
    gammel = "while self.status == StrategyStatus.RUNNING:" in t
    ny = "loop_skal_koere()" in t
    kraev(not gammel and ny, f"{a}: bruger loop_skal_koere, ingen rest af den gamle")

print("\n7. Ogsaa genforsoegs-loekkerne i _close_all")
# Disse havde `and self.status == RUNNING`. Uden rettelsen ville selv et
# tvangsluk-forsoeg vaere lammet i samme oejeblik graensen blev ramt.
for a in ("algo_confluence2", "algo_europa_reversion"):
    t = Path(a + ".py").read_text(encoding="utf-8")
    kraev("and self.status == StrategyStatus.RUNNING)" not in t,
          f"{a}: genforsoegs-loekken er ikke laenger RUNNING-laast")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")
