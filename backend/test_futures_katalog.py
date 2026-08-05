"""
test_futures_katalog.py — kataloget er ÉN sandhedskilde, og det skal kunne bevises
═══════════════════════════════════════════════════════════════════════════════════
Backend-siden er nu udledt af kataloget, saa dér KAN de ikke divergere. Frontenden
kan: `src/App.tsx` har sin egen hardkodede liste, fordi et fetch der fejler paa en
handelsdag ville vaere vaerre end en liste der skal opdateres.

Prisen for det valg er at nogen kan glemme frontenden. Denne test er betalingen:
den laeser App.tsx som tekst og fejler hvis listerne ikke er enige.

Koeres: python test_futures_katalog.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import futures_katalog
from futures_katalog import KATALOG

FEJL: list[str] = []


def kraev(betingelse, besked):
    if betingelse:
        print(f"  OK   {besked}")
    else:
        print(f"  FEJL {besked}")
        FEJL.append(besked)


def frontend_symboler() -> set[str] | None:
    """Pil FUTURES_SYMBOLS ud af App.tsx. None hvis filen/linjen ikke findes."""
    app = Path(__file__).parent.parent / "src" / "App.tsx"
    if not app.exists():
        return None
    m = re.search(r"const\s+FUTURES_SYMBOLS\s*=\s*new\s+Set\(\[([^\]]*)\]\)",
                  app.read_text(encoding="utf-8"))
    if not m:
        return None
    return set(re.findall(r'"([^"]+)"', m.group(1)))


print("\n1. Kataloget er internt konsistent")
for sym, inst in KATALOG.items():
    kraev(sym == inst.symbol, f"{sym}: noeglen matcher instrumentets symbol")
    kraev(inst.multiplier > 0, f"{sym}: multiplikator er sat ({inst.multiplier})")
    kraev(bool(inst.exchange), f"{sym}: boers er sat ({inst.exchange})")

print("\n2. Backend udleder af kataloget (kan ikke divergere — men bevis det)")
from ibkr_connect import FUTURES_EXCHANGE, is_future_symbol
kraev(set(FUTURES_EXCHANGE) == set(KATALOG),
      f"ibkr_connect.FUTURES_EXCHANGE == kataloget ({sorted(FUTURES_EXCHANGE)})")

from strategies.europa_reversion.config import MULTIPLIER
kraev(set(MULTIPLIER) == set(KATALOG),
      f"europa_reversion.MULTIPLIER == kataloget ({sorted(MULTIPLIER)})")
kraev(all(MULTIPLIER[s] == KATALOG[s].multiplier for s in KATALOG),
      "multiplikator-VAERDIERNE er ens (ikke bare de samme noegler)")

print("\n3. Frontendens liste er enig — DET er den der kan glemmes")
fe = frontend_symboler()
if fe is None:
    kraev(False, "FUTURES_SYMBOLS kunne ikke laeses af src/App.tsx "
                 "(er linjen omskrevet? saa skal denne test foelge med)")
else:
    manglende = set(KATALOG) - fe
    ekstra = fe - set(KATALOG)
    kraev(not manglende,
          f"App.tsx mangler ikke symboler fra kataloget "
          f"{'— mangler: ' + str(sorted(manglende)) if manglende else ''}")
    kraev(not ekstra,
          f"App.tsx har ingen symboler kataloget ikke kender "
          f"{'— ekstra: ' + str(sorted(ekstra)) if ekstra else ''}")

print("\n4. Opslag taaler skidt input (watchlist-indtastning er ikke ren)")
kraev(is_future_symbol(" mes ") is True, "' mes ' genkendes som future")
kraev(futures_katalog.multiplikator("mes") == 5.0, "multiplikator('mes') = 5,0")
kraev(futures_katalog.multiplikator("AAPL") == 1.0, "en aktie giver 1,0")
kraev(futures_katalog.multiplikator("") == 1.0, "tom streng giver 1,0 (ingen crash)")
kraev(futures_katalog.multiplikator(None) == 1.0, "None giver 1,0 (ingen crash)")
kraev(futures_katalog.er_future("MNQ") is False, "MNQ er IKKE i kataloget endnu")

print("\n5. Halvt tilfoejet symbol fanges")
kraev(futures_katalog.manglende_i_katalog(["MES", "MNQ", "M2K"]) == ["MNQ"],
      "manglende_i_katalog peger paa MNQ")
kraev(futures_katalog.manglende_i_katalog(["MES", "M2K"]) == [],
      "intet mangler naar alle er kendte")

print("\n6. Rullevinduet — hvornaar spoerger vi markedet?")
import datetime as _dt
from ibkr_connect import IBKRConnection
c = IBKRConnection.__new__(IBKRConnection)      # ingen forbindelse noedvendig
i_dag = _dt.date.today()
kraev(c._i_rullevindue((i_dag + _dt.timedelta(days=3)).strftime("%Y%m%d")) is True,
      "udloeb om 3 dage -> i rullevindue")
kraev(c._i_rullevindue((i_dag + _dt.timedelta(days=43)).strftime("%Y%m%d")) is False,
      "udloeb om 43 dage (MESU6 i dag) -> IKKE i rullevindue, ingen ekstra kald")
kraev(c._i_rullevindue((i_dag + _dt.timedelta(days=14)).strftime("%Y%m%d")) is True,
      "udloeb praecis paa graensen (14 dage) -> i rullevindue")
kraev(c._i_rullevindue("ikke-en-dato") is False,
      "ulaeselig dato -> ingen rul paa et gaet")

print("\n7. Rullebeslutningen — SKIFTER den naar likviditeten skifter?")
# Live mod IBKR er kun den ene halvdel beviselig i dag: 5/8-2026 har MESU6
# 1.125.421 mod MESZ6's 4.998, saa den bliver korrekt paa front. At den ogsaa
# SKIFTER kan foerst ses i rulleugen — med mindre vi fodrer den tallene.
import asyncio
from types import SimpleNamespace


class _StubIB:
    """Returnerer en fast volumen pr. localSymbol. `None` = opslaget fejler."""

    def __init__(self, volumener):
        self.volumener = volumener

    async def reqHistoricalDataAsync(self, kontrakt, **_):
        v = self.volumener[kontrakt.localSymbol]
        if v is None:
            raise TimeoutError("stub: opslag fejlede")
        return [SimpleNamespace(volume=v)]


def _afgoer(v_front, v_naeste):
    c = IBKRConnection.__new__(IBKRConnection)
    c.ib = _StubIB({"FRONT": v_front, "NAESTE": v_naeste})
    front = SimpleNamespace(localSymbol="FRONT")
    naeste = SimpleNamespace(localSymbol="NAESTE")
    return asyncio.run(c._mest_handlede("TEST", front, naeste)).localSymbol


kraev(_afgoer(1_125_421, 4_998) == "FRONT",
      "front har al volumen -> bliver paa front (dagens MESU6/MESZ6-tal)")
kraev(_afgoer(4_998, 1_125_421) == "NAESTE",
      "likviditeten er skiftet -> RULLER til naeste maaned")
kraev(_afgoer(500_000, 500_001) == "NAESTE",
      "knebent flertal taeller ogsaa — reglen er 'mest handlet', ikke 'meget mere'")
kraev(_afgoer(500_000, 500_000) == "FRONT",
      "uafgjort -> bliver paa front (status quo vinder tvivlen)")
kraev(_afgoer(None, 1_125_421) == "FRONT",
      "front-opslag FEJLER -> bliver paa front, ruller ikke paa mangelfulde data")
kraev(_afgoer(1_125_421, None) == "FRONT",
      "naeste-opslag fejler -> bliver paa front")
kraev(_afgoer(0, 0) == "FRONT",
      "ingen volumen nogen af stederne -> bliver paa front")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print(f"  - {f}")
    sys.exit(1)
print("Alt groent.")
