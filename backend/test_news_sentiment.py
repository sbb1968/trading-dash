"""
test_news_sentiment.py — keyword-sentiment paa VIRKELIGE overskrift-former
──────────────────────────────────────────────────────────────────────────
_guess_sentiment bruges af Trend Join Long (katalysator-gaten), catalyst_intraday,
News Room og news_source_bench. Listerne blev udvidet 3/8-2026 fordi de var skrevet
til mega-cap-nyheder og missede micro-cap-katalysatorer som "FDA Clearance".

Samtidig gik matcheren fra raa substring til ordgraenser — den gamle version lod
"won" ramme "Wonder Group" og "loss" ramme "Glossier".

Sektioner:
  A — micro-cap katalysatorer (dem TJL faktisk moeder)
  B — udvanding er bearish (den gap-type der fader)
  C — ordgraenser (falske positiver fra substring-matchning)
  D — neutralt forbliver neutralt

Kør i backend-mappen:  python test_news_sentiment.py
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from finnhub_news import _guess_sentiment

FEJL = []


def s(headline, forventet):
    faktisk = _guess_sentiment(headline)
    if faktisk == forventet:
        print(f"  PASS  [{faktisk:<7}] {headline[:66]}")
    else:
        print(f"  FAIL  [{faktisk:<7}] forventet {forventet}: {headline[:56]}")
        FEJL.append(headline)


print("\nSektion A — micro-cap katalysatorer")
s("NuWellis Announces FDA Clearance of Aquadex SmartFlow System", "bullish")
s("Company Receives FDA Breakthrough Device Designation", "bullish")
s("Acme Therapeutics Reports Positive Topline Results from Phase 3 Trial", "bullish")
s("XYZ Corp Wins $12 Million Defense Contract", "bullish")
s("BioTech Granted Orphan Drug Designation for Rare Disease Program", "bullish")
s("SmallCo Signs Exclusive Partnership Agreement with Global Distributor", "bullish")
s("Firm Selected for Multi-Year Government Award", "bullish")
s("Company Announces Uplisting to Nasdaq", "bullish")
s("Q2 Revenue Beats Estimates on Strong Growth", "bullish")
s("Board Authorized $50 Million Share Repurchase", "bullish")

print("\nSektion B — udvanding er bearish (gap'et der fader)")
s("Acme Pharma Announces Pricing of $50 Million Public Offering", "bearish")
s("Company Announces Reverse Stock Split", "bearish")
s("SmallCo Files Shelf Registration for Future Sales", "bearish")
s("Firm Prices Registered Direct Offering with Warrants", "bearish")
s("Nasdaq Notifies Company of Non-Compliance with Listing Rules", "bearish")
s("Company Receives Complete Response Letter from FDA", "bearish")
s("CEO Resigns Amid Securities Fraud Investigation", "bearish")

print("\nSektion C — ordgraenser (substring-fejl fra den gamle matcher)")
s("Wonder Group Provides Business Update", "neutral")          # 'won' i 'Wonder'
s("Glossier Names New Chief Operating Officer", "neutral")      # 'loss' i 'Glossier'
s("Beaten Path Capital Discloses Position", "neutral")          # 'beat' i 'Beaten'
s("Halterman Industries Schedules Investor Day", "neutral")     # 'halt' i 'Halterman'
s("Missouri Valley Bancorp Completes Routine Filing", "neutral")  # 'miss' i 'Missouri'

print("\nSektion D — ægte neutralt forbliver neutralt")
s("Company to Present at Upcoming Investor Conference", "neutral")
s("SmallCo Schedules Second Quarter Earnings Call", "neutral")
s("Firm Announces Participation in Industry Panel", "neutral")

print("\nSektion E — modstridende signaler taeller mod hinanden")
s("Company Wins FDA Clearance but Announces Dilutive Offering", "neutral")

if FEJL:
    print(f"\n{len(FEJL)} FEJL")
    raise SystemExit(1)
print("\nALLE TESTS BESTÅET ✓")
