"""
test_entry_fill.py — vagt mod fantom-positioner paa ENTRY-stien.

Modstykket til test_k2_close_robusthed.py, som daekker LUK-stien. Entry havde
ingen fyldnings-verifikation: en afvist ordre (status=Inactive, filled=0) blev
bogfoert som en aegte position, og resten af kaeden — journal, oversigt,
force-close, reconcile — arbejdede videre paa en fiktion. EUREVERSION stod
saadan 20/7-30/7 2026: tre uger, ~410 afviste ordrer om dagen, nul alarmer.

Koeres med:  python test_entry_fill.py

Placering: C:\\Projects\\trading_dash\\backend\\test_entry_fill.py
"""

import asyncio
import sys

from strategy_base import BaseStrategy, StrategyConfig

_ok = True


def check(name: str, passed: bool, got=None) -> None:
    global _ok
    _ok = _ok and bool(passed)
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + ("" if passed else f"  {got!r}"))


class _Dummy(BaseStrategy):
    """Minimal konkret strategi — vi tester kun BaseStrategy._entry_fill_qty."""
    name = "Test"
    description = "test"
    asset_class = "equity"

    async def pre_flight(self):
        return True, ""

    async def on_start(self):
        pass

    async def on_bar(self, ticker, bar):
        pass

    async def on_stop(self):
        pass


async def section_A():
    print("\nSektion A - _entry_fill_qty bogfoerer kun bekraeftet fyldning")
    s = _Dummy(StrategyConfig())

    # Ordren kunne slet ikke sendes (conn returnerede None)
    check("A1 result=None -> 0 (ingen position)",
          await s._entry_fill_qty(None, "AAA", 10) == 0.0)

    # DEN kritiske: IBKR afviste ordren. Praecis EUREVERSION-tilstanden.
    rejected = {"status": "Inactive", "filled": 0,
                "reject_reason": "201: insufficient margin"}
    check("A2 Inactive/filled=0 -> 0 (ingen fantom-position)",
          await s._entry_fill_qty(rejected, "MES", 1) == 0.0)

    # Normal fuld fyldning
    check("A3 fuldt fyldt -> hele antallet",
          await s._entry_fill_qty({"status": "Filled", "filled": 100}, "BBB", 100) == 100.0)

    # Delvis fyldning bogfoeres som DET IBKR FYLDTE. At afvise den ville
    # efterlade den fyldte del foraeldreloes hos IBKR — stik mod formaalet.
    check("A4 delvis 50/100 -> 50 (ikke afvist, ikke 100)",
          await s._entry_fill_qty({"status": "Filled", "filled": 50}, "CCC", 100) == 50.0)


async def section_B():
    print("\nSektion B - fyldnings-regnskab baerer nul-fyldnings-alarmen")
    s = _Dummy(StrategyConfig())
    await s._entry_fill_qty(None, "AAA", 10)
    await s._entry_fill_qty({"status": "Inactive", "filled": 0}, "MES", 1)
    await s._entry_fill_qty({"status": "Filled", "filled": 100}, "BBB", 100)

    check("B1 entries_attempted taeller ALLE forsoeg",
          s.stats.entries_attempted == 3, s.stats.entries_attempted)
    check("B2 entries_filled taeller kun bekraeftede",
          s.stats.entries_filled == 1, s.stats.entries_filled)
    check("B3 orders_rejected taeller afviste",
          s.stats.orders_rejected == 2, s.stats.orders_rejected)

    # Alarm-betingelsen i _log_self_stop: forsoegt > 0 OG fyldt == 0.
    dead = _Dummy(StrategyConfig())
    await dead._entry_fill_qty({"status": "Inactive", "filled": 0}, "MES", 1)
    check("B4 alt afvist -> alarm-betingelsen er sand",
          dead.stats.entries_attempted > 0 and dead.stats.entries_filled == 0)

    # ... og den maa IKKE fyre naar strategien bare ikke saa nogen signaler.
    quiet = _Dummy(StrategyConfig())
    check("B5 ingen forsoeg -> INGEN alarm (stille dag er ikke en fejl)",
          not (quiet.stats.entries_attempted > 0 and quiet.stats.entries_filled == 0))


async def main():
    print("Test: entry-fyldning (vagt mod fantom-positioner)")
    await section_A()
    await section_B()
    print("\nRESULTAT:", "ALLE OK" if _ok else "FEJL")


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0 if _ok else 1)
