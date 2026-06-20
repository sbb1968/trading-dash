#!/usr/bin/env python3
"""
catalyst_score.py — Katalysator- & risiko-lag (Lag 3, 25 % af helheden).

Samme design som de andre lag: motoren er AFKOBLET fra kilden.
compute_catalyst() tager en dict med raatal + aktuel pris ind og
producerer signaler -100..+100, vaegtet til en lag-score.

Felter i dicten (fyldes af FMP-adapteren / kombinations-motoren):
    rating_up, rating_down   — antal op-/nedgraderinger seneste ~90 dage
    price_target             — gns. analytiker-kursmaal
    days_to_earnings         — dage til naeste regnskab
    avg_surprise_pct         — gns. EPS-surprise seneste kvartaler (%)
    sector_momentum_pct      — sektorens afkast 1-3 mdr (fra sektor-ETF)
    news_sentiment           — sentiment -1..+1 (valgfrit)

Scoring-kurverne er foerste-udkast-heuristikker til kalibrering.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def clamp(x, lo=-100.0, hi=100.0):
    return float(max(lo, min(hi, x)))


# === Scorere (input -> signal -100..+100) ===================================
def s_revisions(c, price):
    up, down = c.get("rating_up"), c.get("rating_down")
    if up is None and down is None:
        return None
    up, down = up or 0, down or 0
    if up + down == 0:
        return "ingen nylige", 0.0
    return f"{up}↑ / {down}↓ (90d)", clamp((up - down) * 30)


def s_price_target(c, price):
    tgt = c.get("price_target")
    if tgt is None or not price or price <= 0:
        return None
    upside = (tgt / price - 1) * 100
    return f"mål {tgt:.0f} ({upside:+.0f}%)", clamp(upside * 4)


def s_days_earnings(c, price):
    d = c.get("days_to_earnings")
    if d is None:
        return None
    # Nært regnskab = gap-risiko for swing; god runway = mild medvind.
    sig = (-70 if d < 5 else -20 if d < 10 else 20 if d <= 45 else 0)
    return f"{d} dage til regnskab", float(sig)


def s_surprise(c, price):
    s = c.get("avg_surprise_pct")
    if s is None:
        return None
    return f"snit {s:+.1f}%", clamp(s * 8)


def s_sector_momentum(c, price):
    m = c.get("sector_momentum_pct")
    if m is None:
        return None
    return f"sektor {m:+.1f}%", clamp(m * 6)


def s_news(c, price):
    s = c.get("news_sentiment")
    if s is None:
        return None
    return f"sentiment {s:+.2f}", clamp(s * 100)


# === Parameter-register (vaegt = andel af katalysator-laget) ================
PARAMS = [
    ("analyst_revisions", "Analyst-revisioner", "Analytiker", 0.26, s_revisions),
    ("price_target", "Kursmål vs pris", "Analytiker", 0.14, s_price_target),
    ("days_to_earnings", "Dage til regnskab", "Earnings/risiko", 0.21, s_days_earnings),
    ("earnings_surprise", "Earnings-surprise", "Earnings/risiko", 0.14, s_surprise),
    ("sector_momentum", "Sektor-momentum", "Eksternt", 0.1375, s_sector_momentum),
    ("news_sentiment", "Nyheds-sentiment", "Eksternt", 0.1125, s_news),
]


@dataclass
class ParamResult:
    key: str
    name: str
    group: str
    raw: str
    signal: float
    weight: float
    weighted: float


def compute_catalyst(catalyst: dict, price: Optional[float] = None):
    results, excluded = [], []
    for key, name, group, weight, scorer in PARAMS:
        try:
            out = scorer(catalyst, price)
        except Exception as e:
            out = None
            excluded.append((name, f"fejl: {type(e).__name__}"))
        if out is None:
            excluded.append((name, "mangler data"))
            continue
        raw, signal = out
        results.append(ParamResult(key, name, group, raw, float(signal), weight, 0.0))

    total_w = sum(r.weight for r in results)
    if total_w > 0:
        for r in results:
            r.weight /= total_w
            r.weighted = r.weight * r.signal
    lag_score = sum(r.weighted for r in results)
    return {"results": results, "excluded": excluded, "lag_score": lag_score}


def _band(score):
    if score >= 50:
        return "Staerk katalysator-medvind"
    if score >= 20:
        return "Medvind"
    if score > -20:
        return "Neutral / blandet"
    if score > -50:
        return "Modvind / risiko"
    return "Staerk modvind / event-risiko"


def format_catalyst_report(ticker: str, res: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append(f" KATALYSATOR-LAG — {ticker.upper()}")
    L.append("=" * 78)
    L.append(f"{'Parameter':<26}{'Vaerdi':<22}{'Bidrag':>8}{'Vaegt':>8}{'Vaegtet':>9}")
    L.append("-" * 78)
    last = None
    for r in res["results"]:
        if r.group != last:
            L.append(f"[{r.group}]")
            last = r.group
        L.append(f"  {r.name:<24}{r.raw:<22}{r.signal:>+7.0f} {r.weight*100:>6.1f}% {r.weighted:>+8.1f}")
    L.append("-" * 78)
    score = res["lag_score"]
    L.append(f"{'KATALYSATOR LAG-SCORE':<60}{score:>+8.1f}")
    L.append(f"  -> {_band(score)}")
    if res["excluded"]:
        L.append("")
        L.append("Ekskluderet (mangler data):")
        for name, why in res["excluded"]:
            L.append(f"  - {name} ({why})")
    L.append("=" * 78)
    return "\n".join(L)


# === FMP-adapter (PROVISORISK — feltnavne bekraeftes via fmp_probe_catalyst) =
def fetch_catalyst_fmp(symbol: str, api_key: str, price: Optional[float] = None,
                       sector_momentum_pct: Optional[float] = None) -> dict:
    import urllib.request
    import json
    from datetime import datetime, date

    base = "https://financialmodelingprep.com/stable"

    def get(path):
        sep = "&" if "?" in path else "?"
        url = f"{base}/{path}{sep}apikey={api_key}"
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode())

    out = {}
    # Analyst-revisioner — tæl op/ned seneste 90 dage (endpoint bekræftes)
    try:
        grades = get(f"grades?symbol={symbol}")
        up = down = 0
        today = date.today()
        for g in (grades or []):
            d = g.get("date", "")[:10]
            try:
                age = (today - datetime.strptime(d, "%Y-%m-%d").date()).days
            except Exception:
                age = 0
            if age > 90:
                continue
            act = (g.get("action") or "").lower()
            if "up" in act:
                up += 1
            elif "down" in act:
                down += 1
        out["rating_up"], out["rating_down"] = up, down
    except Exception:
        pass
    # Kursmål (endpoint/feltnavn bekræftes)
    try:
        pt = get(f"price-target-summary?symbol={symbol}")
        p0 = pt[0] if isinstance(pt, list) and pt else pt
        out["price_target"] = (p0.get("lastMonthAvgPriceTarget")
                               or p0.get("avgPriceTarget")
                               or p0.get("priceTarget"))
    except Exception:
        pass
    # Regnskabsdato + surprise-historik (endpoint/feltnavn bekræftes)
    try:
        earn = get(f"earnings?symbol={symbol}")
        from datetime import datetime as _dt, date as _date
        today = _date.today()
        future = []
        surprises = []
        for e in (earn or []):
            ds = e.get("date", "")[:10]
            try:
                dd = _dt.strptime(ds, "%Y-%m-%d").date()
            except Exception:
                continue
            if dd >= today:
                future.append((dd - today).days)
            est = e.get("epsEstimated")
            act = e.get("epsActual") or e.get("eps")
            if est and act and est != 0:
                surprises.append((act - est) / abs(est) * 100)
        if future:
            out["days_to_earnings"] = min(future)
        if surprises:
            out["avg_surprise_pct"] = sum(surprises[:4]) / len(surprises[:4])
    except Exception:
        pass
    # Sektor-momentum kommer fra sektor-ETF (IBKR) via kombinations-motoren
    if sector_momentum_pct is not None:
        out["sector_momentum_pct"] = sector_momentum_pct
    return out


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser(description="Katalysator lag-scoring (swing)")
    ap.add_argument("ticker")
    ap.add_argument("--price", type=float, required=True)
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    args = ap.parse_args()
    cat = fetch_catalyst_fmp(args.ticker, args.api_key, args.price)
    print(format_catalyst_report(args.ticker, compute_catalyst(cat, args.price)))