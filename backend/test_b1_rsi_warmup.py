"""
test_b1_rsi_warmup.py — verificér RSI-forvarmning UDEN åbent marked.

Spørgsmålet B1 hænger på: er RSI(14) "varm" (et rigtigt tal, ikke neutral
50.0) ved starten af entry-vinduet (09:45 ET), når ORB kører på rigtige
5min-bars?

Problemet: RSI(14) kræver 15 closes. Ved 09:45 ET findes kun ORB-vinduets
~3 bars (09:30-09:44). Det er ikke nok → RSI = 50.0 = filter slukket.

Denne test henter rigtige bars og simulerer hvad self._closes ville
indeholde ved entry-vinduets start under TO scenarier:
  A) forvarmning med KUN dagens bars (som min nuværende patch gør)
  B) forvarmning med flere dages bars (foregående dage + dagens ORB-vindue)

Kør:  python test_b1_rsi_warmup.py
Kræver kun historiske bars (virker uden åbent marked).
"""
import asyncio
from datetime import datetime, time as dtime
import pytz

from ibkr_connect import IBKRConnection
from strategies.momentum_orb.entry import calc_rsi_from_closes

ET = pytz.timezone("America/New_York")
ENTRY_START = dtime(9, 45)


async def main():
    c = IBKRConnection(paper_trading=True)
    ok = await c.connect()
    print("connected:", ok)
    if not ok:
        return

    tk = "ASTC"

    # ── Scenario A: kun 1 dags bars (min nuværende patch) ──
    one_day = await c.get_historical_bars(tk, duration="1 D", bar_size="5 mins", what_to_show="TRADES")
    print(f"\n1 D bars: {len(one_day) if one_day else 0}")

    def to_et(b):
        ts = b["datetime"]
        if ts.tzinfo is None:
            ts = ET.localize(ts)
        else:
            ts = ts.astimezone(ET)
        return ts

    if one_day:
        # closes FØR entry-vinduets start (det forvarmningen ville give ved 09:45)
        pre_entry_closes_A = [b["close"] for b in one_day if to_et(b).time() < ENTRY_START]
        print(f"  closes før 09:45 ET (scenario A): {len(pre_entry_closes_A)}")
        rsi_A = calc_rsi_from_closes(pre_entry_closes_A)
        print(f"  RSI ved entry-start (scenario A): {rsi_A:.1f}  "
              f"{'← VARM' if rsi_A != 50.0 else '← KOLD (50.0 = filter slukket!)'}")

    # ── Scenario B: 5 dages bars (foregående dage + dagens ORB) ──
    five_day = await c.get_historical_bars(tk, duration="5 D", bar_size="5 mins", what_to_show="TRADES")
    print(f"\n5 D bars: {len(five_day) if five_day else 0}")
    if five_day:
        # Alle closes op til og med dagens ORB-vindue-slut (09:44)
        # Brug sidste dags dato i datasættet som "i dag"
        last_date = to_et(five_day[-1]).date()
        closes_B = [b["close"] for b in five_day
                    if to_et(b).date() < last_date
                    or (to_et(b).date() == last_date and to_et(b).time() < ENTRY_START)]
        print(f"  closes før 09:45 ET sidste dag (scenario B): {len(closes_B)}")
        rsi_B = calc_rsi_from_closes(closes_B)
        print(f"  RSI ved entry-start (scenario B): {rsi_B:.1f}  "
              f"{'← VARM' if rsi_B != 50.0 else '← KOLD'}")

    print("\n" + "=" * 60)
    print("KONKLUSION:")
    print("  Hvis scenario A er KOLD (50.0) og B er VARM → forvarmningen")
    print("  skal bruge FLERE dages bars, ikke kun dagens. Min patch (der")
    print("  kun bruger dagens bars) er da utilstrækkelig og skal rettes")
    print("  til at hente warmup-historik FØR forvarmning.")
    print("  Hvis BEGGE er varme → dagens bars rækker (usandsynligt så")
    print("  tidligt på dagen).")
    print("=" * 60)

    c.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
