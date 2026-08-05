"""
test_usrev_afvist.py — kan man se HVORFOR en armeret US-reversion ikke gik ind?
═══════════════════════════════════════════════════════════════════════════════════
`bar_evaluation` sagde kun "z15=-2,45 armeret". Naar strategien saa ikke handlede,
kunne man ikke se hvilket af de tre bekraeftelses-kriterier der holdt igen — og
dermed heller ikke skelne "venter fornuftigt paa vendingen" fra "et kriterium er
saa stramt at det aldrig opfyldes". Opdaget 5/8-2026 mens MES brød begge baand.

    python test_usrev_afvist.py
"""
from __future__ import annotations
import asyncio, sys
from datetime import datetime
from types import SimpleNamespace

import pytz
import algo_us_reversion as m
from strategies.us_reversion import rule
from strategies.us_reversion.config import VARIANTS

ET = pytz.timezone("America/New_York")
FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


class FalskJournal:
    def __init__(self):
        self.events = []

    async def log_event(self, **kw):
        self.events.append(kw)


def afvis(bars5, macd_now, macd_prev, cmf_now, cmf_prev):
    """Koer check_entry + logningen, returner den skrevne payload."""
    cfg = VARIANTS["base"]
    ok, detaljer = rule.check_entry(bars5=bars5, macd_now=macd_now,
                                    macd_prev=macd_prev, cmf_now=cmf_now,
                                    cmf_prev=cmf_prev, cfg=cfg)
    s = m.UsReversionLive.__new__(m.UsReversionLive)
    s._journal = FalskJournal()
    s._strategy = SimpleNamespace(name="US-reversion")   # name er en property
    bar = SimpleNamespace(timestamp=ET.localize(datetime(2026, 8, 5, 11, 40)))
    asyncio.run(s._log_entry_afvist("MES", bar, -2.45, detaljer))
    return ok, (s._journal.events[0]["payload"] if s._journal.events else None)


GROEN = [{"open": 100.0, "close": 100.2}, {"open": 100.2, "close": 100.5}]   # +0,5%
FLAD  = [{"open": 100.0, "close": 100.0}, {"open": 100.0, "close": 100.01}]  # ~0,01%

print("\n1. Kun stigningen mangler")
ok, p = afvis(FLAD, macd_now=2.0, macd_prev=1.0, cmf_now=0.3, cmf_prev=0.1)
kraev(ok is False, "entry afvist")
kraev(p is not None and p["mangler"] == ["stigning"],
      f"'mangler' peger praecis paa stigning ({p and p['mangler']})")
# FLAD har en foerste bar med close == open, altsaa ikke groen. Der ER derfor
# ingen stigning at maale — og beskeden skal SIGE det, ikke skrive "n/a".
kraev(p and "ikke to groenne" in p["kort"], f"kort: {p and p['kort']}")

print("")
print("1b. To groenne, men for lille stigning -> tallet SKAL vises")
SMAA = [{"open": 100.0, "close": 100.01}, {"open": 100.01, "close": 100.02}]
ok, p2 = afvis(SMAA, macd_now=2.0, macd_prev=1.0, cmf_now=0.3, cmf_prev=0.1)
kraev(p2["mangler"] == ["stigning"], f"mangler = {p2['mangler']}")
kraev("stigning 0.020%/0.08%" in p2["kort"], f"kort: {p2['kort']}")

print("\n2. Kun MACD mangler")
ok, p = afvis(GROEN, macd_now=1.0, macd_prev=2.0, cmf_now=0.3, cmf_prev=0.1)
kraev(p["mangler"] == ["macd"], f"'mangler' = {p['mangler']}")
kraev("macd✗" in p["kort"] and "cmf✓" in p["kort"], f"kort: {p['kort']}")

print("\n3. Kun CMF mangler")
ok, p = afvis(GROEN, macd_now=2.0, macd_prev=1.0, cmf_now=0.1, cmf_prev=0.3)
kraev(p["mangler"] == ["cmf"], f"'mangler' = {p['mangler']}")

print("\n4. Alle tre mangler")
ok, p = afvis(FLAD, macd_now=1.0, macd_prev=2.0, cmf_now=0.1, cmf_prev=0.3)
kraev(p["mangler"] == ["stigning", "macd", "cmf"], f"'mangler' = {p['mangler']}")

print("\n5. Manglende data taeller som 'ikke opfyldt' — ikke som crash")
ok, p = afvis(GROEN, macd_now=None, macd_prev=None, cmf_now=None, cmf_prev=None)
kraev(p["mangler"] == ["macd", "cmf"], f"None-indikatorer -> {p['mangler']}")

print("\n6. De raa tal er med, saa tærskler kan vurderes bagefter")
ok, p = afvis(FLAD, macd_now=2.0, macd_prev=1.0, cmf_now=0.3, cmf_prev=0.1)
for felt in ("rise_pct", "rise_krav", "macd", "macd_prev", "cmf", "cmf_prev", "z15"):
    kraev(felt in p, f"payload baerer '{felt}'")

print("\n7. Alt opfyldt -> INGEN afvisnings-event (ellers stoej)")
cfg = VARIANTS["base"]
ok, _ = rule.check_entry(bars5=GROEN, macd_now=2.0, macd_prev=1.0,
                         cmf_now=0.3, cmf_prev=0.1, cfg=cfg)
kraev(ok is True, "entry accepteret naar alle tre er opfyldt")

print("\n8. Uden journal maa den ikke kaste")
s = m.UsReversionLive.__new__(m.UsReversionLive)
s._journal = None
s._strategy = None   # name-opslag vil KASTE — fejlstien skal taale det
bar = SimpleNamespace(timestamp=ET.localize(datetime(2026, 8, 5, 11, 40)))
try:
    asyncio.run(s._log_entry_afvist("MES", bar, -2.45, {}))
    kraev(True, "ingen journal -> stille retur, ingen exception")
except Exception as e:
    kraev(False, f"kastede: {e}")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")
