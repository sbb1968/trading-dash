"""
test_k2_trailing.py — adfaerds-laas paa K2's trailing take-profit.

Tilfoejet 1/8-2026. Uden trailing har exit_mode="impulse_low" INGEN take-profit:
en vinder kan kun komme ud kl. 15:45. Juli 2026 viste konsekvensen — 30
session_close-exits med 5-6 timers holdetid, mens alle 159 stop-exits tabte.

Laaser fire ting fast: (1) eksisterende varianter er UAENDREDE (trail_pct=0),
(2) HH foelger hoejeste CLOSE og starter ved entry, (3) stoppet har forrang naar
begge rammer samme bar, (4) session-luk slaar alt.

Koeres med:  python test_k2_trailing.py

Placering: C:\Projects\trading_dash\backend\test_k2_trailing.py
"""
import sys
from datetime import datetime, time as dtime
sys.path.insert(0, r"C:\Projects\trading_dash\backend")
import pytz
from strategies.base import Bar, EntrySignal
from strategies.confluence2.strategy import Confluence2Exit
from strategies.confluence2.config import VARIANTS

ET = pytz.timezone("America/New_York")
ok = True


def check(name, cond, got=None):
    global ok
    ok = ok and cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {got!r}"))


def bar(c, lo=None, hi=None, hhmm=(11, 0)):
    ts = ET.localize(datetime(2026, 7, 30, *hhmm))
    return Bar(timestamp=ts, open=c, high=hi if hi is not None else c,
               low=lo if lo is not None else c, close=c, volume=1000)


def pos(vk, entry=100.0, impulse_low=98.0):
    ex = Confluence2Exit()
    sig = EntrySignal(ticker="AAA", entry_price=entry,
                      entry_time=ET.localize(datetime(2026, 7, 30, 10, 0)),
                      metadata={"impulse_low": impulse_low, "atr": 0.5})
    return ex, ex.open_position(sig, 10, vk)


print("Sektion A - basis-varianten er UAENDRET (trail_pct = 0)")
ex, p = pos("A_atrfloor_20")
check("A1 hh_close initialiseret til entry", p.state.hh_close == 100.0, p.state.hh_close)
ex.update(p, high_seen=105.0, variant_key="A_atrfloor_20", low_seen=104.0)
check("A2 hh_close roeres IKKE naar trail_pct=0", p.state.hh_close == 100.0, p.state.hh_close)
check("A3 stort fald giver INGEN exit (kun stoppet gaelder)",
      ex.check_exit_bar(p, bar(101.0, lo=100.5), "A_atrfloor_20") is None)

print("\nSektion B - T_trail_2_0: HH foelger closes")
VK = "T_trail_2_0"
ex, p = pos(VK)
check("B1 hh starter ved entry", p.state.hh_close == 100.0)
for c in (101.0, 103.0, 102.0, 105.0, 104.0):
    ex.update(p, high_seen=c, variant_key=VK, low_seen=c - 0.2)
check("B2 hh = hoejeste close (105), ikke seneste", p.state.hh_close == 105.0, p.state.hh_close)

print("\nSektion C - udloesning ved 2,0 % under HH")
# HH = 105 -> niveau 102.90
check("C1 close 103.00 (over niveau) -> ingen exit",
      ex.check_exit_bar(p, bar(103.00, lo=102.9), VK) is None)
d = ex.check_exit_bar(p, bar(102.80, lo=102.7), VK)
check("C2 close 102.80 (under niveau) -> trail_pct", d is not None and d.reason == "trail_pct", d)
check("C3 exit-pris = barens close", d is not None and d.exit_price == 102.80, d)

print("\nSektion D - stoppet har forrang")
ex, p = pos(VK)
for c in (101.0, 105.0):
    ex.update(p, high_seen=c, variant_key=VK, low_seen=c - 0.2)
# Stop = min(impuls-low 98, entry-2*ATR=99) = 98. Bar bryder BEGGE.
d = ex.check_exit_bar(p, bar(97.0, lo=97.0), VK)
check("D1 baade stop og trailing ramt -> 'stop' vinder",
      d is not None and d.reason == "stop", d)

print("\nSektion E - session-luk slaar alt")
ex, p = pos(VK)
d = ex.check_exit_bar(p, bar(104.0, lo=103.9, hhmm=(15, 45)), VK)
check("E1 kl 15:45 -> session_close uanset trailing",
      d is not None and d.reason == "session_close", d)

print("\nSektion F - dyk lige efter entry saenker ikke referencen")
ex, p = pos(VK)
for c in (99.5, 99.0, 99.4):
    ex.update(p, high_seen=c, variant_key=VK, low_seen=c - 0.1)
check("F1 hh forbliver entry (100), ikke 99.5", p.state.hh_close == 100.0, p.state.hh_close)

print("\nSektion G - alle T_trail-varianter er velformede")
for k in [x for x in VARIANTS if x.startswith("T_trail")]:
    c = VARIANTS[k]
    check(f"G {k}: trail_pct={c.trail_pct} · stop-gulv {c.stop_atr_floor_mult}x",
          c.trail_pct > 0 and c.exit_mode == "impulse_low" and c.stop_atr_floor_mult == 2.0)

print("\nRESULTAT:", "ALLE OK" if ok else "FEJL")
sys.exit(0 if ok else 1)
