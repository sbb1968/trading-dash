#!/usr/bin/env python3
"""
catalyst_intraday.py — day trading katalysator-lag (trin 7): realtids-news + halts.

Day-tradings pendant til swings catalyst_score. Tre faktorer:
    news_fresh  — minutter siden seneste overskrift (recency; fersk = momentum)
    halt_state  — er aktien haltet lige nu (1=alm., 2=news; news vejer tungest)
    news_impact — net sentiment over de nylige overskrifter (keyword-heuristik)

Faktorer UDEN data ekskluderes (som de oevrige lag). Har symbolet hverken nyhed
eller halt -> alle tre ekskluderet -> lag_score=None -> katalysator renorm'es UD
af konfluensen (teknisk+forsyning 0.80/0.20). Katalysator paavirker KUN scoren
naar der ER en katalysator.

Nyhederne hentes i data_source_intraday.fetch_catalyst_news (Finnhub + IBKR DJ,
flettet); _build_catalyst_data her samler dem til catalyst_data-dicten. Sentiment-
heuristikken (ordlister + _guess_sentiment) er KOPIERET fra finnhub_news (day trading-
laget er selvstaendigt; "Python regner, LLM narrerer kun" -> keyword, ikke LLM).
Returnerer samme form som de oevrige lag: {results, excluded, lag_score}.
ASCII i kode; dansk prosa.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from technical_intraday import ParamResult, _band


def clamp(x, lo=-100.0, hi=100.0):
    return float(max(lo, min(hi, x)))


# === Sentiment-heuristik (kopieret fra finnhub_news; ikke importeret) ========
BULLISH_WORDS = [
    "beats", "beat", "surge", "surges", "soars", "soared", "rally", "rallies",
    "upgrade", "upgraded", "buy rating", "raises guidance", "record high",
    "outperform", "approval", "approved", "wins", "won", "contract",
    "partnership", "acquires", "acquired", "buyback", "dividend increase",
    "breakthrough", "exceeds", "exceeded", "strong", "growth",
]
BEARISH_WORDS = [
    "miss", "misses", "missed", "plunge", "plunges", "falls", "fell",
    "downgrade", "downgraded", "sell rating", "cuts guidance", "lawsuit",
    "underperform", "rejected", "loses", "lost", "warns", "warning",
    "delays", "delayed", "recall", "fraud", "investigation", "probe",
    "bankruptcy", "loss", "losses", "weak", "decline", "declined",
    "halt", "halted", "subpoena",
]


def _guess_sentiment(headline: str) -> str:
    """Simpel keyword-baseret sentiment -> bullish/bearish/neutral."""
    lower = (headline or "").lower()
    bull = sum(1 for w in BULLISH_WORDS if w in lower)
    bear = sum(1 for w in BEARISH_WORDS if w in lower)
    return "bullish" if bull > bear else "bearish" if bear > bull else "neutral"


# === Faktor-scorere: scorer(c) -> None | signal ============================
def s_news_fresh(c):
    """Recency: fersk nyhed = staerkt positivt for momentum; henfalder."""
    m = c.get("news_age_min")          # minutter siden seneste overskrift; None = ingen nyhed
    if m is None:
        return None
    if m <= 15:
        return 100.0
    if m <= 60:
        return 60.0
    if m <= 180:
        return 30.0
    if m <= 360:
        return 10.0
    return 0.0


def s_news_impact(c):
    """Net sentiment over de nylige overskrifter (-1..+1 -> -100..+100)."""
    n = c.get("sentiment")             # {"bull": int, "bear": int, "total": int} | None
    if not n or n.get("total", 0) == 0:
        return None
    net = (n["bull"] - n["bear"]) / n["total"]
    return round(clamp(net * 100.0), 1)


def s_halt_state(c):
    """Er aktien haltet lige nu. Halt = stort event (gaten lukker handel imens)."""
    h = c.get("halted")                # -1 ukendt, 0 nej, 1 alm., 2 news
    if h is None or h < 1:
        return None
    return 90.0 if h == 2 else 60.0     # news-halt vejer tungere end alm. halt


# === Parameter-register (vaegt = andel af laget; summer til 1.00) ===========
PARAMS = [
    ("news_fresh",  "Fersk nyhed (min)", "Katalysator", 0.45, s_news_fresh),
    ("halt_state",  "Halt / resume",     "Katalysator", 0.30, s_halt_state),
    ("news_impact", "Nyheds-impact",     "Katalysator", 0.25, s_news_impact),
]


def _raw_for(key, c) -> str:
    """Raa-aflaesning pr. faktor (som de oevrige lags .raw)."""
    if key == "news_fresh":
        m = c.get("news_age_min")
        return f"{m:.0f} min siden" if m is not None else "-"
    if key == "news_impact":
        n = c.get("sentiment") or {}
        return f"bull {n.get('bull', 0)} / bear {n.get('bear', 0)} af {n.get('total', 0)}"
    if key == "halt_state":
        h = c.get("halted")
        return "news-halt" if h == 2 else "alm. halt" if h == 1 else "-"
    return "-"


def compute_catalyst_intraday(catalyst_data: Optional[dict] = None) -> dict:
    """catalyst_data: {news_age_min, sentiment, halted, headlines} (None hvor ukendt).
    Renormaliserer over faktorer MED data; alle None -> lag_score=None (renorm'es UD
    af konfluensen). Samme form som de oevrige lag."""
    c = catalyst_data or {}
    results, excluded = [], []
    for key, name, group, weight, scorer in PARAMS:
        try:
            out = scorer(c)
        except Exception as e:
            out = None
            excluded.append((name, f"fejl: {type(e).__name__}"))
        if out is None:
            excluded.append((name, "ingen data"))
            continue
        results.append(ParamResult(key, name, group, _raw_for(key, c), float(out), weight, 0.0))

    total_w = sum(r.weight for r in results)
    if total_w > 0:
        for r in results:
            r.weight /= total_w
            r.weighted = r.weight * r.signal
    lag_score = sum(r.weighted for r in results) if results else None   # None = ingen katalysator
    return {"results": results, "excluded": excluded, "lag_score": lag_score,
            "headlines": c.get("headlines", [])}


def _build_catalyst_data(headlines, halted_raw) -> dict:
    """Saml fetch_catalyst_news-output + halt-status til catalyst_data.
    headlines: list[{ts_utc, headline, provider}] nyeste foerst. halted_raw: int."""
    news_age_min = None
    sentiment = None
    if headlines:
        delta = datetime.now(timezone.utc) - headlines[0]["ts_utc"]
        news_age_min = max(0.0, delta.total_seconds() / 60.0)
        bull = sum(1 for h in headlines if _guess_sentiment(h["headline"]) == "bullish")
        bear = sum(1 for h in headlines if _guess_sentiment(h["headline"]) == "bearish")
        sentiment = {"bull": bull, "bear": bear, "total": len(headlines)}
    halted = None if (halted_raw is None or halted_raw < 0) else int(halted_raw)
    return {"news_age_min": news_age_min, "sentiment": sentiment, "halted": halted,
            "headlines": headlines[:6]}


def format_catalyst_report(symbol: str, res: Optional[dict] = None) -> str:
    if not res or res.get("lag_score") is None:
        return f"Katalysator ({symbol}): ingen fersk nyhed eller halt -> ekskluderet fra scoren."
    L = [f"Katalysator ({symbol}): {res['lag_score']:+.1f}  -> {_band(res['lag_score'])}"]
    for r in res["results"]:
        L.append(f"  {r.name:<18}{r.signal:>+6.0f}  {r.raw}")
    heads = res.get("headlines") or []
    if heads:
        L.append("  Nylige overskrifter:")
        for h in heads[:5]:
            ts = h["ts_utc"].strftime("%H:%M")
            L.append(f"    [{ts}] ({h.get('provider', '?')}) {str(h['headline'])[:70]}")
    L.append("  (sentiment = keyword-heuristik; verificér selv ved tvivl.)")
    return "\n".join(L)
