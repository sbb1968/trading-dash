"""
test_market_conditions.py
─────────────────────────
Tester MarketConditionChecker direkte.

Kør: python test_market_conditions.py
"""

import asyncio
import logging

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ibkr_connect import IBKRConnection
from market_conditions import MarketConditionChecker


async def main():
    print("\n🔌 Forbinder til IBKR TWS...")
    conn = IBKRConnection(paper_trading=True)
    ok   = await conn.connect()

    if not ok:
        print("❌ Kunne ikke forbinde til IBKR")
        return

    print("✅ Forbundet\n")
    print("📊 Analyserer markedsforhold...")

    checker    = MarketConditionChecker(conn)
    conditions = await checker.check()

    # ── Vis overblik ─────────────────────────────────────────
    print("\n" + "═" * 55)
    print(f"  DAGENS MARKEDSOVERBLIK — {conditions.checked_at}")
    print("═" * 55)

    # VIX
    vix_emoji = "🟢" if conditions.vix_status == "lav" else \
                "🟡" if conditions.vix_status == "normal" else \
                "🟠" if conditions.vix_status == "høj" else \
                "🔴" if conditions.vix_status == "ekstrem" else "⚪"
    vix_str = f"{conditions.vix:.1f}" if conditions.vix > 0 else "ukendt"
    print(f"  {vix_emoji} VIX:          {vix_str:>8}  ({conditions.vix_status})")

    # SPY
    spy_emoji = "🟢" if conditions.spy_gap_pct > 0.3 else \
                "🔴" if conditions.spy_gap_pct < -0.3 else "🟡"
    spy_str = f"${conditions.spy_price:.2f}" if conditions.spy_price > 0 else "ukendt"
    gap_str = f"{conditions.spy_gap_pct:+.2f}%" if conditions.spy_price > 0 else "ukendt"
    print(f"  {spy_emoji} SPY:          {spy_str:>8}  gap {gap_str} ({conditions.spy_gap_status})")

    # Scanner
    print(f"  📡 Gap > 10%:  {conditions.stocks_gap_over_10:>8}  aktier")
    print(f"  📡 Høj vol:    {conditions.stocks_relvol_over_5:>8}  aktier")

    if conditions.top_gainers:
        print(f"  📋 Top gainers: {', '.join(conditions.top_gainers[:8])}")

    print("─" * 55)

    # Score
    score_emoji = "🟢" if conditions.score_label == "aktiv" else \
                  "🟡" if conditions.score_label == "moderat" else "🔴"
    print(f"  {score_emoji} AKTIVITETSSCORE: {conditions.score}/100 — {conditions.score_label.upper()}")

    if conditions.skal_handle:
        print(f"  ✅ Handel tilladt — position size: {conditions.position_size_pct*100:.0f}%")
    else:
        print(f"  🛑 Ingen handel i dag")

    print("═" * 55)

    conn.disconnect()
    print("\n✅ Test afsluttet")


if __name__ == "__main__":
    asyncio.run(main())
