"""
test_algo_journal_integration.py — Verificér at algo_momentum.py
korrekt kalder log_trade_open/close/state.

Vi kører IKKE rigtig algoritme — vi bare:
  1. Opretter en Journal med testbar DB
  2. Verificerer at vores nye journal-metoder kan kaldes med samme
     args som algo_momentum.py bruger
"""
import asyncio
from datetime import datetime
from pathlib import Path

import aiosqlite
import pytz

from journal import Journal

ET = pytz.timezone("America/New_York")


async def main():
    test_db = "test_algo_integration.db"
    if Path(test_db).exists():
        Path(test_db).unlink()

    j = Journal(test_db)
    await j.init()

    # Simulér _open() — præcis de samme parametre som vi tilføjede
    print("Simulerer algo_momentum._open() journal-kald...")
    trade_id = await j.log_trade_open(
        source="Momentum ORB",
        symbol="CLOV",
        side="long",
        shares=250,
        entry_price=2.50,
        entry_time=datetime.now(ET),
        variant="all_winner",
        entry_reason="Break & Retest LONG @ $2.50",
        current_stop=2.45,
        current_target=2.60,
        current_stage="initial",
    )
    assert trade_id is not None
    print(f"  ✓ trade_id: {trade_id}")

    # Simulér update_trade_state() — fra _handle_ticker-loopet
    print("\nSimulerer state-opdatering under live-loop...")
    for stop, target, stage in [
        (2.45, 2.60, "initial"),
        (2.50, 2.60, "breakeven"),  # ratchet til BE
        (2.55, None, "trailing"),    # ind i trail
    ]:
        await j.update_trade_state(
            trade_id=trade_id,
            current_stop=stop,
            current_target=target,
            current_stage=stage,
            trail_stop=2.57 if stage == "trailing" else None,
        )
        print(f"  ✓ stop={stop}, target={target}, stage={stage!r}")

    # Simulér _close() — exit
    print("\nSimulerer algo_momentum._close() journal-kald...")
    ok = await j.log_trade_close(
        trade_id=trade_id,
        exit_price=2.57,
        exit_time=datetime.now(ET),
        exit_reason="trail",
        pnl=17.50,    # (2.57 - 2.50) * 250
    )
    assert ok
    print("  ✓ Trade lukket")

    # Verificér slutresultat
    async with aiosqlite.connect(test_db) as db:
        async with db.execute(
            "SELECT source, symbol, side, shares, entry_price, exit_price, "
            "exit_reason, pnl, pnl_pct, duration_sec, current_stage, current_stop "
            "FROM trades WHERE trade_id = ?",
            (trade_id,)
        ) as cur:
            row = await cur.fetchone()

    print("\nFinal trade-row:")
    keys = ["source", "symbol", "side", "shares", "entry_price", "exit_price",
            "exit_reason", "pnl", "pnl_pct", "duration_sec", "current_stage", "current_stop"]
    for k, v in zip(keys, row):
        print(f"  {k:18s} = {v!r}")

    await j.close()
    Path(test_db).unlink()
    print("\n✓ Integration-test bestået")


if __name__ == "__main__":
    asyncio.run(main())