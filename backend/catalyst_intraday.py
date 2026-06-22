#!/usr/bin/env python3
"""
catalyst_intraday.py — intradag katalysator-lag (realtids-news/halts).

STUB: returnerer pending indtil trin 7. Naar lag_score gaar fra None til et tal,
joiner laget AUTOMATISK i intradag_report.combine (renormalisering) — ingen
arkitektur-aendring. Spejler katalysator-lagets flade men leverer pending.

Returnerer samme form som de oevrige lag: {results, excluded, lag_score}.
VIGTIGT: lag_score=None (ikke 0.0) — 0.0 ville traekke combined mod neutral;
None faar laget renormaliseret UD.

ASCII i kode; dansk prosa.
"""
from __future__ import annotations

from typing import Optional

from technical_intraday import ParamResult   # noqa: F401  (homogen intradag-type; trin 7 bruger den)

# Faktorer er DEFINERET (saa trin 7 + UI kender noegler/navne/grupper) men pt.
# altid ekskluderet. (key, navn, gruppe)
PENDING_FACTORS = [
    ("news_fresh",  "Fersk nyhed (min)", "Katalysator"),
    ("news_impact", "Nyheds-impact",     "Katalysator"),
    ("halt_state",  "Halt / resume",     "Katalysator"),
]


def fetch_catalyst(symbol: str) -> dict:
    """STUB. Trin 7: realtids-news (IBKR reqHistoricalNews/reqNewsBulletins,
    Dow Jones-entitlement) + halt-status."""
    return {"pending": True}


def compute_catalyst_intraday(catalyst_data: Optional[dict] = None) -> dict:
    """Pending: ingen results, alle faktorer ekskluderet, lag_score=None.
    excluded holdes som 2-tupler (name, why) — homogent med de oevrige lag, saa
    trin 5 kan iterere excluded ensartet (PENDING_FACTORS baerer key/gruppe)."""
    excluded = [(name, "PENDING (trin 7)") for _, name, _ in PENDING_FACTORS]
    return {"results": [], "excluded": excluded, "lag_score": None}


def format_catalyst_report(symbol: str, res: Optional[dict] = None) -> str:
    return f"Katalysator ({symbol}): PENDING - realtids-news/halts ikke implementeret (trin 7)."
