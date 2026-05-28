"""
test_orb_opstart.py — kør ORB's faktiske opstartssekvens isoleret.

Formål: på WORKSTATIONEN (hvor TWS på Sørens konto kører og HAR datafeed)
verificere at ORB's KODE er sund — scanner, bar-hentning, context-opbygning
og entry-mekanik. Det afdækker skjulte kodebugs uafhængigt af algoserverens
datafeed-problem.

Kør:  python test_orb_opstart.py

Dette starter IKKE live-loopet og placerer INGEN ordrer. Det kører kun
pre_flight + _prepare_universe (scanner + context) og tester derefter
ÉT entry-check pr. ticker mod den seneste bar, så vi kan se om
entry-mekanikken overhovedet producerer signaler eller afviser alt.
"""
import asyncio
import logging
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
ET = pytz.timezone("America/New_York")

from ibkr_connect import IBKRConnection
from algo_momentum import MomentumORB
from strategy_base import StrategyConfig, StrategyStatus
from strategies.base import Bar


async def main():
    print("=" * 72)
    print("ORB OPSTARTSTEST — workstation")
    print("=" * 72)

    conn = IBKRConnection(paper_trading=True)
    ok = await conn.connect()
    print(f"\n[1] IBKR forbundet: {ok}")
    if not ok:
        print("    → TWS ikke tilgaengelig. Stop.")
        return

    algo = MomentumORB(conn, config=StrategyConfig(
        max_loss_per_trade=150.0, max_daily_loss=250.0,
        max_open_positions=3, max_position_size=2500.0,
    ))
    msgs = []
    algo._broadcast_fn = lambda m: msgs.append(m)

    # ── Pre-flight ────────────────────────────────────────────
    print("\n[2] Pre-flight...")
    ok, summary = await algo.pre_flight()
    print(f"    resultat: {ok}")
    print(f"    summary:  {summary}")
    if not ok:
        print("    → Pre-flight fejlede. Det er her ORB doede paa algoserveren.")
        print("    → Paa workstationen burde datafeed virke. Hvis det fejler her,")
        print("      er det et kodeproblem, ikke konto/abonnement.")
        conn.disconnect()
        return

    # ── Univers-opbygning (scanner + context) ─────────────────
    print("\n[3] Bygger univers (scanner + context)...")
    algo.status = StrategyStatus.RUNNING   # _prepare bruger ikke status, men for en sikkerheds skyld
    await algo._prepare_universe()

    print(f"\n    Univers efter opbygning: {len(algo.universe)} tickers")
    print(f"    {', '.join(algo.universe)}")
    print(f"    Day-contexts bygget: {len(algo._day_contexts)}")

    if not algo._day_contexts:
        print("\n    ✗ INGEN day-contexts. ORB har intet at handle paa.")
        print("      Mulige aarsager:")
        print("      - Scanner returnerede tomt (TV-screener fejl)")
        print("      - build_day_context returnerede None for alle (for faa bars)")
        print("      - Marked lukket: 1D 5min-bars findes maaske ikke for i dag")
        conn.disconnect()
        return

    # Vis ORB-range for hver ticker
    print("\n    ORB-range pr. ticker:")
    for tk, ctx in list(algo._day_contexts.items())[:25]:
        print(f"      {tk:<6} H={ctx.get('orb_high'):.2f} L={ctx.get('orb_low'):.2f} "
              f"avgVol={ctx.get('avg_vol'):.0f}")

    # ── Entry-mekanik-test ────────────────────────────────────
    # Kald check_entry mod den seneste bar for hver ticker og se hvad
    # der sker. Vi forventer typisk None (intet breakout lige nu), men
    # det vigtige er at det IKKE kaster exception — at mekanikken kører.
    print("\n[4] Tester entry-mekanik (kalder check_entry pr. ticker)...")
    signals = 0
    errors = 0
    for ticker in algo.universe:
        ctx = algo._day_contexts.get(ticker)
        if ctx is None:
            continue
        bars = algo._bar_history.get(ticker, [])
        if not bars:
            continue
        last_bar = bars[-1]
        try:
            sig = algo._strategy.entry.check_entry(ticker, last_bar, ctx)
            if sig is not None:
                signals += 1
                print(f"      {ticker}: SIGNAL {sig.side} @ {sig.entry_price:.2f}")
        except Exception as e:
            errors += 1
            print(f"      {ticker}: EXCEPTION i check_entry: {type(e).__name__}: {e}")

    print(f"\n    check_entry koert for {len(algo._day_contexts)} tickers")
    print(f"    signaler: {signals}   exceptions: {errors}")
    if errors > 0:
        print("    ✗ check_entry kaster exception — KODEBUG i ORB entry-mekanik")
    else:
        print("    ✓ entry-mekanik koerer uden exception")
        print("      (0 signaler er normalt — kraever live breakout over ORB-high)")

    conn.disconnect()
    print("\n" + "=" * 72)
    print("KONKLUSION:")
    print("  Hvis pre-flight OK + univers bygget + contexts + 0 exceptions")
    print("  → ORB's KODE er sund. Algoserverens problem er datafeed/abonnement,")
    print("    ikke kode. Bekraeftes naar Ibens TWS er tilgaengelig.")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
