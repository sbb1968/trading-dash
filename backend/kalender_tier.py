#!/usr/bin/env python3
"""
kalender_tier.py — hvilke events vises, og hvad hedder de hos de andre kilder
════════════════════════════════════════════════════════════════════════════════
Tabellerne stammer fra `MES_oekonomiske_events.md` (desktop Claude, 18-08-2026)
§0.1: *"Tabellerne kan bruges direkte som udgangspunkt for TIER1_TITLER,
TIER2_TITLER og TITEL_SYNONYMER."*

⚠ NAVNENE ER FOREX FACTORYS, fordi det er vores kilde. Synonymtabellen gaar den
anden vej: fra en ANDEN kildes navn til vores kanoniske. Den bruges foerst naar
en anden kilde flettes ind — den er ikke i brug endnu, og det staar her frem for
at lade nogen tro at fletningen er proevet.

⚠ INGEN FUZZY MATCHING. Dokumentets egen advarsel, og den er rigtig: enhver
tolerant strengsammenligning slaar "Core CPI m/m" sammen med "CPI m/m" foer
eller siden — og de to er forskellige tal der bevaeger markedet hver sin vej.
Opslaget er eksakt, og en ukendt titel bliver tier 0 frem for at blive gaettet
ind i tier 1.

⚠ OG DERFOR MATCHES DER PAA NAVN, IKKE PAA TIDSPUNKT. Maalt i cachen: 10-y og
30-y Bond Auction ligger 13:01, ikke 13:00 som dokumentet angiver. Et opslag der
brugte klokkeslaet ville tabe dem begge.

⚠ TIER 0 ER IKKE "KASSERET". Alt hoestes og gemmes; tier styrer kun hvad der
VISES. En eventtype kan forfremmes naar en maaling viser at den betyder noget —
og maalingen kraever at data ligger der i forvejen.
"""
from __future__ import annotations

# ── Tier 1: flytter MES direkte og forudsigeligt ────────────────────────────
TIER1_TITLER = {
    # Inflation
    "CPI m/m", "CPI y/y", "Core CPI m/m", "Core CPI y/y",
    "PPI m/m", "Core PPI m/m",
    "Core PCE Price Index m/m",
    # Arbejdsmarked — NFP-udgivelsen er tre tal der handles samtidigt
    "Non-Farm Employment Change", "Unemployment Rate",
    "Average Hourly Earnings m/m",
    # Forbrug
    "Retail Sales m/m", "Core Retail Sales m/m",
    # Aktivitet
    "ISM Manufacturing PMI", "ISM Services PMI",
    "Advance GDP q/q", "Advance GDP Price Index q/q",
    # Pengepolitik
    "Federal Funds Rate", "FOMC Statement", "FOMC Press Conference",
    "FOMC Meeting Minutes", "FOMC Economic Projections",
    "Fed Chair Powell Speaks", "Fed Chair Powell Testifies",
}

# ── Tier 2: kan flytte MES, mindre paalideligt ──────────────────────────────
TIER2_TITLER = {
    "Unemployment Claims",
    "ADP Non-Farm Employment Change", "ADP Weekly Employment Change",
    "Prelim UoM Consumer Sentiment", "Revised UoM Consumer Sentiment",
    "Prelim UoM Inflation Expectations", "Revised UoM Inflation Expectations",
    "CB Consumer Confidence", "JOLTS Job Openings",
    "Flash Manufacturing PMI", "Flash Services PMI",
    "Philly Fed Manufacturing Index", "Empire State Manufacturing Index",
    "Chicago PMI",
    "Core Durable Goods Orders m/m", "Durable Goods Orders m/m",
    "Industrial Production m/m",
    "Challenger Job Cuts y/y", "Trade Balance",
    "10-y Bond Auction", "30-y Bond Auction",
    "Beige Book",
    "Housing Starts", "Building Permits",
    "New Home Sales", "Existing Home Sales",
    "Crude Oil Inventories",
    "President Trump Speaks",
    # ⚠ Feds regionale talere staar som "FOMC Member <navn> Speaks". Dokumentets
    # §3 rangordner dem UNDER formanden, og formanden har sin egen tier 1-raekke.
    # Navnene kan ikke listes udtoemmende (medlemmerne skifter), saa de fanges
    # af et praefiks — se `tier()`.
}

TIER2_PRAEFIKS = ("FOMC Member",)

# ── Synonymer: anden kildes navn -> vores kanoniske ─────────────────────────
# ⚠ IKKE I BRUG ENDNU. Ligger klar til den dag en anden kilde flettes ind.
# ✓ = observeret i Soerens sammenligning 12. og 14. august; resten er
# kvalificerede gaet fra dokumentet og skal verificeres FOER de bruges.
TITEL_SYNONYMER = {
    # Trading Economics                    # ForexFactory (kanonisk)
    "Inflation Rate MoM":                  "CPI m/m",              # ✓
    "Inflation Rate YoY":                  "CPI y/y",              # ✓
    "Core Inflation Rate MoM":             "Core CPI m/m",         # ✓
    "Core Inflation Rate YoY":             "Core CPI y/y",         # ✓
    "Retail Sales MoM":                    "Retail Sales m/m",     # ✓
    "Retail Sales Ex Autos MoM":           "Core Retail Sales m/m",# ✓
    "Michigan Consumer Sentiment Prel":    "Prelim UoM Consumer Sentiment",   # ✓
    "Michigan Inflation Expectations":     "Prelim UoM Inflation Expectations",  # ✓
    "Philadelphia Fed Manufacturing Index": "Philly Fed Manufacturing Index",  # ✓
    "Initial Jobless Claims":              "Unemployment Claims",
    "Non Farm Payrolls":                   "Non-Farm Employment Change",
    "Producer Prices MoM":                 "PPI m/m",
    "Core Producer Prices MoM":            "Core PPI m/m",
    # Investing.com
    "CPI (MoM)":                           "CPI m/m",              # ✓
    "CPI (YoY)":                           "CPI y/y",              # ✓
    "Core CPI (MoM)":                      "Core CPI m/m",         # ✓
    "Retail Sales (MoM)":                  "Retail Sales m/m",     # ✓
    "Core Retail Sales (MoM)":             "Core Retail Sales m/m",# ✓
    "Michigan Consumer Sentiment (P)":     "Prelim UoM Consumer Sentiment",   # ✓
    "Nonfarm Payrolls":                    "Non-Farm Employment Change",
    "ISM Non-Manufacturing PMI":           "ISM Services PMI",
}

# ⚠ TO FALDGRUBER DOKUMENTET NAEVNER, OG DE ER BEGGE REELLE:
#
#   1. "Retail Sales Control Group" findes IKKE hos ForexFactory. Efterproevet
#      mod cachen 18-08: nul raekker med "ontrol" i navnet i hele ugen. Vil vi
#      have den reneste aflaesning, kraever det en anden kilde.
#
#   2. Investing.com deler Michigan-inflationsforventninger i TO raekker (1-aarig
#      og 5-aarig) hvor ForexFactory har én. En fletning paa titel alene giver
#      enten en dublet eller et tabt event. Derfor staar der kun ÉN afbildning
#      ovenfor — den anden maa haandteres eksplicit naar fletningen bygges.
MANGLER_HOS_FF = {"Retail Sales Control Group"}
DELT_HOS_INVESTING = {"Prelim UoM Inflation Expectations":
                      ["Michigan 1-Year Inflation Expectations",
                       "Michigan 5-Year Inflation Expectations"]}


def tier(navn: str) -> int:
    """1, 2 eller 0. Eksakt opslag — en ukendt titel bliver 0, ikke gaettet."""
    if navn in TIER1_TITLER:
        return 1
    if navn in TIER2_TITLER:
        return 2
    if any(navn.startswith(p) for p in TIER2_PRAEFIKS):
        return 2
    return 0
