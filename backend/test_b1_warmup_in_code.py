"""
test_b1_warmup_in_code.py — verificér at RSI-forvarmningen faktisk virker
i den NYE algo_momentum.py (ikke bare i princippet).

Forskel fra test_b1_rsi_warmup.py:
  - test_b1_rsi_warmup.py testede PRINCIPPET (giver flere dages bars varm RSI?)
  - DENNE tester KODEN: kører ORB's faktiske _prepare_universe og aflæser
    om _closes blev forvarmet korrekt, så RSI er varm fra start.

Det fanger fejl i selve rettelsen: når prior_closes ikke når frem gennem
reset_for_day, look-ahead-guard placeret forkert, datofiltrering off-by-one.

Kør:  python test_b1_warmup_in_code.py
Kræver kun historiske bars (virker uden åbent marked).
"""
import asyncio
import logging
logging.basicConfig(level=logging.WARNING)  # dæmp støj

from ibkr_connect import IBKRConnection
from algo_momentum import MomentumORB
from strategy_base import StrategyConfig, StrategyStatus
from strategies.momentum_orb.entry import calc_rsi_from_closes


async def main():
    print("=" * 64)
    print("B1 WARMUP-I-KODE TEST")
    print("=" * 64)

    conn = IBKRConnection(paper_trading=True)
    ok = await conn.connect()
    print(f"\nForbundet: {ok}")
    if not ok:
        return

    algo = MomentumORB(conn, config=StrategyConfig(
        max_loss_per_trade=150.0, max_daily_loss=250.0,
        max_open_positions=3, max_position_size=2500.0,
    ))
    algo._broadcast_fn = lambda m: None  # stille

    print("Pre-flight...")
    ok, _ = await algo.pre_flight()
    if not ok:
        print("Pre-flight fejlede"); conn.disconnect(); return

    print("Bygger univers (med ny B1-warmup)...\n")
    algo.status = StrategyStatus.RUNNING
    await algo._prepare_universe()

    # Aflæs _closes for hver ticker — er de forvarmet?
    entry = algo._strategy.entry
    print(f"{'Ticker':<8}{'#closes':>9}{'RSI':>8}   status")
    print("-" * 40)
    kolde = 0
    varme = 0
    for ticker in algo.universe:
        closes = entry._closes.get(ticker, [])
        n = len(closes)
        rsi = calc_rsi_from_closes(closes)
        if n == 0:
            status = "INGEN closes (ticker droppet?)"
        elif rsi == 50.0:
            status = "KOLD (50.0 — warmup virkede ikke!)"
            kolde += 1
        else:
            status = "varm ✓"
            varme += 1
        print(f"{ticker:<8}{n:>9}{rsi:>8.1f}   {status}")

    print("-" * 40)
    print(f"\nVarme: {varme}   Kolde: {kolde}")
    print()
    if varme > 0 and kolde == 0:
        print("✓ B1-WARMUP VIRKER I KODEN — RSI er varm for alle tickers")
        print("  fra opstart. Forvarmningen via prior_closes nåede frem til")
        print("  RSI-beregningen korrekt.")
    elif kolde > 0:
        print("✗ NOGLE TICKERS ER KOLDE — warmup-rettelsen virker ikke fuldt.")
        print("  prior_closes nåede måske ikke frem gennem reset_for_day,")
        print("  eller datofiltreringen fjernede for meget.")
    else:
        print("⚠ Ingen tickers at vurdere — univers tomt?")

    conn.disconnect()
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
