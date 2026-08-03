"""
test_force_close_policy.py — fælles regel for tvangsluk i US-sessionen
──────────────────────────────────────────────────────────────────────
Søren 3/8-2026: ALLE strategier der handler i den amerikanske session skal have
lukket senest en halv time før markedet (16:00 ET), så et genforsøg når at ske
mens der stadig er likviditet.

Før denne dato lå de spredt: K2 15:45, BuyTheDip 15:30, Trend Join 15:51,
Relativ Styrke 15:51, US-reversion 15:00. Testen findes for at de ikke skrider
fra hinanden igen — en enkelt strategi der glider til 15:51 er nem at overse.

To ting testen også fanger:
  * at entry-cutoff ligger MEDMINDELIGT før tvangsluk (Trend Join stod på 15:30
    entry-cutoff OG 15:51 tvangsluk; rykkes tvangsluk til 15:30 uden at flytte
    cutoff, kan en handel åbnes og lukkes i samme minut)
  * at Europa-reversion IKKE fanges af reglen — den handler i den europæiske
    session og har sit eget tvangsluk

Kør i backend-mappen:  python test_force_close_policy.py
"""

import sys
from datetime import time as dtime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

SENEST = dtime(15, 30)      # 30 min før 16:00 ET
MIN_LUFT_MIN = 30           # entry-cutoff skal ligge mindst så længe før tvangsluk

FEJL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FEJL.append(name)


def minutter(t: dtime) -> int:
    return t.hour * 60 + t.minute


# ── indlæs de fem US-strategiers tidspunkter ───────────────────
import algo_buythedip as BTD
import algo_relstyrke as RS
import algo_trendjoin as TJ
from strategies.confluence2.config import VARIANTS, LIVE_VARIANT_KEY
from strategies.us_reversion.config import FORCE_CLOSE_ET as USREV_FC
from strategies.us_reversion.config import ENTRY_CUTOFF_ET as USREV_CUT

_k2 = VARIANTS[LIVE_VARIANT_KEY]

# (navn, tvangsluk, entry-cutoff eller None hvis strategien kun har én beslutning)
US_STRATEGIER = [
    ("Konfluens 2",     dtime(*_k2.force_close_hhmm), dtime(*_k2.entry_cutoff_hhmm)),
    ("BuyTheDip",       BTD.FORCE_CLOSE_ET,           BTD.OPEN_UNTIL_ET),
    ("Trend Join Long", TJ.FORCE_CLOSE_ET,            TJ.ENTRY_LATEST),
    ("Relativ Styrke",  RS.FORCE_CLOSE_ET,            None),   # én beslutning 09:45
    ("US-reversion",    USREV_FC,                     USREV_CUT),
]

print("\nSektion A — tvangsluk senest 15:30 ET")
for navn, fc, _cut in US_STRATEGIER:
    check(f"A {navn}: tvangsluk {fc.strftime('%H:%M')} ≤ 15:30",
          minutter(fc) <= minutter(SENEST), fc)

print("\nSektion B — entry-cutoff giver en handel tid til at virke")
for navn, fc, cut in US_STRATEGIER:
    if cut is None:
        print(f"  SKIP  B {navn}: én beslutning pr. dag, intet løbende cutoff")
        continue
    luft = minutter(fc) - minutter(cut)
    check(f"B {navn}: {luft} min mellem sidste entry ({cut.strftime('%H:%M')}) "
          f"og tvangsluk ({fc.strftime('%H:%M')})",
          luft >= MIN_LUFT_MIN, luft)

print("\nSektion C — Europa-reversion er IKKE omfattet")
# Den handler 02:00-08:00 ET. Fanges den af reglen ovenfor, er noget forvekslet.
from algo_europa_reversion import FORCE_CLOSE_ET as EUREV_FC
check("C1 Europa-reversion lukker i den europæiske session (før 15:30 ET)",
      minutter(EUREV_FC) < minutter(SENEST), EUREV_FC)
check("C2 … og altså ikke ved US-lukning", EUREV_FC != SENEST, EUREV_FC)

print("\nSektion D — K2's modul-fallback følger varianten")
import algo_confluence2 as K2
check("D1 MARKET_CLOSE-fallback = 15:30 (bruges hvis varianten intet angiver)",
      K2.MARKET_CLOSE == SENEST, K2.MARKET_CLOSE)

if FEJL:
    print(f"\n{len(FEJL)} FEJL")
    raise SystemExit(1)
print("\nALLE TESTS BESTÅET ✓")
