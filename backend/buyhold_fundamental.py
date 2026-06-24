#!/usr/bin/env python3
"""
buyhold_fundamental.py — Buy-and-Hold fundamentals-motor (Lag 1-3) + adapter.

Tredje soejle (langsigtet koeb-og-hold). Spejler fundamental_score.py's design 1:1:
motoren er AFKOBLET fra datakilden — compute_quality/growth/valuation tager en dict
med raatal + pris ind og producerer signaler -100..+100 vaegtet til lag-scorer.
Adapteren (fetch_buyhold_fundamentals, nederst) fylder dicten fra FMP og udregner selv
de 402-laaste ratios fra de AABNE raa regnskaber (jf. Fase 0-probe: statements aabne,
faerdig-TTM-ratios laaste — samme moenster som fundamental_score's P/E = pris/EPS).

Lag 4 (trend, [BEREGNET] fra IBKR-bars) + selve gate-SAMLINGEN er Fase 2 — men denne
adapter udregner OGSAA gate-raafelterne (Altman Z, udvanding, FCF-distress), saa Fase 2
kun skal vaegte dem.

Tilt: kvalitets-compounder (aaben besl. #1) — toleranter multipler for hoej kvalitet;
Owner-Earnings-yield + FCF-yield vejer tungt i Lag 3. Scoring-kurver = foerste-udkast-
heuristikker (kalibreres i Fase 4). Alt degraderer paent: mangler et felt -> den
afhaengige faktor ekskluderes, resten staar.
"""
from __future__ import annotations

import statistics as _st
from dataclasses import dataclass
from typing import Optional


# ── Kerne-helpers (kopieret fra fundamental_score — uaendret) ────────────────
def clamp(x, lo=-100.0, hi=100.0):
    return float(max(lo, min(hi, x)))


def _band_score(value, bands, default):
    """bands: liste af (oevre_graense, score), sorteret stigende. None -> None."""
    if value is None:
        return None
    for hi, score in bands:
        if value <= hi:
            return float(score)
    return float(default)


# ── Rene udlednings-helpers (testbare uden API) ─────────────────────────────
def _cagr(series_newest_first):
    """CAGR % fra en serie (nyeste foerst). Kraever positive endepunkter."""
    s = [x for x in series_newest_first if x is not None and x > 0]
    if len(s) < 2:
        return None
    newest, oldest, yrs = s[0], s[-1], len(s) - 1
    try:
        return ((newest / oldest) ** (1.0 / yrs) - 1.0) * 100.0
    except (ValueError, ZeroDivisionError):
        return None


def _growth_cv(revenue_newest_first):
    """Variabilitet (std/|middel|) paa aar-til-aar omsaetningsvaekst. Lav = holdbar."""
    s = [x for x in revenue_newest_first if x is not None]
    if len(s) < 3:
        return None
    growths = []
    for i in range(len(s) - 1):
        newer, older = s[i], s[i + 1]
        if older:
            growths.append((newer - older) / abs(older))
    if len(growths) < 2:
        return None
    m = _st.fmean(growths)
    if m == 0:
        return None
    return _st.pstdev(growths) / abs(m)


def _trend_diff(series_newest_first):
    """Fortegn/stoerrelse paa aendring nyeste−aeldste (margin-trend). >0 stigende."""
    s = [x for x in series_newest_first if x is not None]
    if len(s) < 2:
        return None
    return s[0] - s[-1]


def dilution_pct(shares_newest_first):
    """Total %-aendring i aktie-antal over perioden. >0 = udvanding, <0 = tilbagekoeb."""
    s = [x for x in shares_newest_first if x]
    if len(s) < 2 or not s[-1]:
        return None
    return (s[0] - s[-1]) / s[-1] * 100.0


def altman_z(wc, ta, re, ebit, mktcap, tl, sales):
    """Altman Z (oprindelig, driftsselskaber). None hvis et led mangler."""
    if any(v is None for v in (wc, ta, re, ebit, mktcap, tl, sales)) or not ta or not tl:
        return None
    return (1.2 * (wc / ta) + 1.4 * (re / ta) + 3.3 * (ebit / ta)
            + 0.6 * (mktcap / tl) + 1.0 * (sales / ta))


def owner_earnings(ocf, total_capex, ppe, revenue, prev_revenue, da):
    """Buffett Owner Earnings = OCF − vedligeholdelses-capex (ESTIMAT).
    Faldende estimat-kaede -> (owner_earnings, maint_capex, trin-tekst) el. (None,None,..)."""
    if ocf is None or total_capex is None:
        return None, None, "ingen data"
    total_capex = abs(total_capex)
    # 1) Greenwald: vaekst-capex = (PP&E/oms) × max(0, ΔOms); vedligehold = total − vaekst
    if ppe and revenue and prev_revenue is not None and revenue > 0:
        d_rev = max(0.0, revenue - prev_revenue)
        growth_capex = (ppe / revenue) * d_rev
        maint = min(max(total_capex - growth_capex, 0.0), total_capex)
        return ocf - maint, maint, "Greenwald (PP&E)"
    # 2) D&A-proxy
    if da is not None:
        maint = min(abs(da), total_capex)
        return ocf - maint, maint, "D&A-proxy"
    # 3) gulv: vedligehold = total capex -> OE = FCF
    return ocf - total_capex, total_capex, "gulv (=FCF)"


OE_NORM_YEARS = 3   # Owner Earnings normaliseres over N aar (Buffett: GENNEMSNITLIG capex)


def owner_earnings_normalized(cf_rows, inc_rows, bal_rows, n=OE_NORM_YEARS):
    """Normaliseret Owner Earnings = avg(OCF, N aar) − avg(vedligeholds-capex, N aar).
    Et enkelt stoejende aar (engangs-capex/working-capital) forvraenger ellers
    hovedfaktoren. Gennemsnit hver KOMPONENT (ikke slut-OE'en). Rows nyeste foerst.
    Returnerer (oe_norm, avg_maint, step_nyeste, n_brugt, avg_revenue)."""
    years = min(n, len(cf_rows)) if cf_rows else 0
    if years == 0:
        return None, None, "ingen data", 0, None
    ocfs, maints, revs = [], [], []
    step_latest = None
    for i in range(years):
        c = cf_rows[i]
        ocf = c.get("operatingCashFlow")
        capex = c.get("capitalExpenditure")
        da = c.get("depreciationAndAmortization")
        revenue = inc_rows[i].get("revenue") if i < len(inc_rows) else None
        prev_rev = inc_rows[i + 1].get("revenue") if (i + 1) < len(inc_rows) else None
        ppe = bal_rows[i].get("propertyPlantEquipmentNet") if i < len(bal_rows) else None
        _oe, maint_i, step_i = owner_earnings(ocf, capex, ppe, revenue, prev_rev, da)
        if maint_i is None or ocf is None:
            continue
        ocfs.append(ocf)
        maints.append(maint_i)
        if revenue is not None:
            revs.append(revenue)
        if step_latest is None:
            step_latest = step_i           # nyeste aars estimat-trin (repraesentativt)
    if not ocfs:
        return None, None, "ingen data", 0, None
    avg_ocf = sum(ocfs) / len(ocfs)
    avg_maint = sum(maints) / len(maints)
    avg_rev = (sum(revs) / len(revs)) if revs else None
    return avg_ocf - avg_maint, avg_maint, step_latest, len(ocfs), avg_rev


def _safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


# ── Scorere (input dict f + pris -> signal el. (raw, signal)) ────────────────
# LAG 1 — KVALITET
def s_roic(f, price):
    return _band_score(f.get("roic"), [(0, -70), (5, -20), (10, 10), (15, 40), (25, 70)], 85)


def s_roe(f, price):
    return _band_score(f.get("roe"), [(0, -60), (10, 0), (20, 30)], 60)


def s_net_margin(f, price):
    return _band_score(f.get("net_margin"), [(0, -80), (5, -20), (10, 10), (20, 40)], 70)


def s_gross_margin(f, price):
    return _band_score(f.get("gross_margin"), [(20, -30), (35, 0), (50, 30), (65, 60)], 80)


def s_netdebt_ebitda(f, price):
    v = f.get("netdebt_ebitda")
    if v is None:
        return None
    sig = _band_score(v, [(-0.5, 60), (0, 50), (1, 30), (2, 10), (3, -10), (4, -40)], -70)
    return (f"Nettogaeld/EBITDA {v:.1f}" + (" (nettokontant)" if v < 0 else ""), sig)


def s_interest_coverage(f, price):
    v = f.get("interest_coverage")
    if v is None:
        return None
    if v == float("inf"):
        return "Ingen rentebyrde", 80.0
    return (f"Rentedaekning {v:.1f}x",
            _band_score(v, [(1, -70), (2, -30), (4, 0), (8, 30), (15, 60)], 80))


def s_current_ratio(f, price):
    return _band_score(f.get("current_ratio"), [(0.8, -50), (1.0, 0), (1.5, 30), (3.0, 50)], 30)


def s_debt_equity(f, price):
    return _band_score(f.get("debt_to_equity"), [(0.5, 40), (1.0, 20), (2.0, 0), (3.0, -30)], -60)


def s_fcf_margin(f, price):
    return _band_score(f.get("fcf_margin"), [(0, -60), (5, 10), (10, 40), (20, 60)], 75)


def s_fcf_conversion(f, price):
    v = f.get("fcf_conversion")
    if v is None:
        return None
    return (f"FCF/nettoresultat {v:.2f}",
            _band_score(v, [(0, -70), (0.6, -20), (0.8, 10), (1.0, 40)], 60))


def s_oe_margin(f, price):
    v = f.get("oe_margin")
    if v is None:
        return None
    return (f"Owner-Earnings margin {v:.1f}% (est.)",
            _band_score(v, [(0, -60), (5, 10), (12, 40)], 65))


# LAG 2 — VAEKST & HOLDBARHED
def s_rev_cagr(f, price):
    return _band_score(f.get("rev_cagr5"), [(0, -50), (5, 0), (10, 25), (20, 50)], 70)


def s_eps_cagr(f, price):
    return _band_score(f.get("eps_cagr5"), [(0, -60), (8, 10), (15, 40)], 70)


def s_fcf_growth(f, price):
    return _band_score(f.get("fcf_growth"), [(0, -40), (10, 10), (25, 40)], 60)


def s_growth_consistency(f, price):
    v = f.get("growth_consistency")
    if v is None:
        return None
    return (f"Vaekst-variabilitet {v:.2f}",
            _band_score(v, [(0.3, 60), (0.6, 30), (1.0, 0), (1.5, -30)], -50))


def s_margin_trend(f, price):
    v = f.get("margin_trend")        # pp-aendring nyeste−aeldste
    if v is None:
        return None
    if v > 1.0:
        return "Margin stigende 5y", 40.0
    if v < -1.0:
        return "Margin faldende 5y", -40.0
    return "Margin stabil 5y", 5.0


def s_reinvestment(f, price):
    roic, g = f.get("roic"), f.get("revenue_growth")
    if roic is None or g is None:
        return None
    if roic >= 15 and g >= 8:
        return f"Compounder (ROIC {roic:.0f}% + vaekst {g:.0f}%)", 60.0
    if g >= 8 and roic < 8:
        return f"Vaerdiødelaeggende vaekst (vaekst {g:.0f}% / ROIC {roic:.0f}%)", -40.0
    if roic >= 10:
        return f"Sund geninvestering (ROIC {roic:.0f}%)", 25.0
    return f"Svag geninvestering (ROIC {roic:.0f}%)", -10.0


# LAG 3 — VAERDIANSAETTELSE
def s_pe(f, price):
    eps, pe = f.get("eps_ttm"), None
    if eps is not None and price is not None and eps > 0:
        pe = price / eps
    elif f.get("pe_ttm"):
        pe = f["pe_ttm"]
    if pe is None:
        if eps is not None and eps <= 0:
            return "P/E n/a (underskud)", -40.0
        return None
    if pe < 0:
        return "P/E n/a (underskud)", -40.0
    return f"P/E {pe:.1f}", _band_score(pe, [(15, 20), (30, 15), (45, 0), (70, -25)], -50)


def s_peg(f, price):
    eps, g = f.get("eps_ttm"), f.get("eps_growth")
    pe = (price / eps) if (eps and price and eps > 0) else f.get("pe_ttm")
    if pe is None or g is None:
        return None
    if g <= 0:
        if g <= -10:
            return f"EPS FALDER {g:+.1f}% - ROEDT FLAG (PEG n/a)", -70.0
        return f"EPS FALDER {g:+.1f}% - advarsel (PEG n/a)", -50.0
    if pe < 0:
        return None
    peg = pe / g
    return f"PEG {peg:.2f}", _band_score(peg, [(1, 40), (2, 10), (3, -10)], -30)


def s_pfcf(f, price):
    fy = f.get("fcf_yield")          # fraktion (fx 0.05)
    if fy is None:
        return None
    if fy <= 0:
        return "P/FCF n/a (negativ FCF)", -60.0
    pfcf = 1.0 / fy
    # Re-centreret (interim): kvalitets-normal (~28-33x) neutralt, billigt positivt.
    return f"P/FCF {pfcf:.1f}", _band_score(pfcf, [(15, 30), (25, 10), (35, 0), (50, -20)], -40)


def s_ev_ebitda(f, price):
    v = f.get("ev_ebitda")
    if v is None:
        return None
    if v <= 0:
        return "EV/EBITDA n/a (negativ EBITDA)", -50.0
    # Re-centreret (interim): kvalitets-large-cap (18-22x) neutralt; aegte dyrt (>30) negativt.
    return f"EV/EBITDA {v:.1f}", _band_score(v, [(10, 30), (15, 10), (22, 0), (30, -20), (45, -40)], -60)


def s_oe_yield(f, price):
    v = f.get("oe_yield")            # % (Owner Earnings / markedsvaerdi)
    if v is None:
        return None
    if v < 0:
        return f"Owner-Earnings yield {v:.1f}% - ROEDT (negativ OE)", -70.0
    # Re-centreret (interim): kvalitets-normal yield ~3.5% lander neutralt, billigt positivt.
    return (f"Owner-Earnings yield {v:.1f}% (norm., est.)",
            _band_score(v, [(1, -40), (2, -10), (3.5, 0), (5, 20), (7, 40)], 60))


def s_fcf_yield(f, price):
    v = f.get("fcf_yield")
    if v is None:
        return None
    vp = v * 100.0
    return f"FCF-yield {vp:.1f}%", _band_score(vp, [(0, -50), (2, 0), (4, 20), (7, 50)], 60)


def s_earnings_yield(f, price):
    v = f.get("earnings_yield")
    if v is None:
        return None
    vp = v * 100.0
    return f"Earnings yield {vp:.1f}%", _band_score(vp, [(2, -20), (4, 0), (6, 20), (9, 40)], 50)


def s_dividend(f, price):
    """KONTEKST (vaegt 0, jf. aaben besl. #2) — ikke-udbyttebetalere straffes ikke."""
    y = f.get("dividend_yield")
    if y is None:
        return None
    yp = y * 100.0
    po = f.get("payout")
    po_txt = f", payout {po*100:.0f}%" if po is not None else ""
    return f"Udbytte {yp:.1f}%{po_txt}", 0.0


# ── Parameter-registre pr. lag (vaegt = andel af LAGET; renormaliseres) ──────
PARAMS_QUALITY = [
    # Profitabilitet ~45 %
    ("roic", "Return on invested capital", "Profitabilitet", 0.15, s_roic, "{:.1f}%"),
    ("roe", "Return on equity", "Profitabilitet", 0.10, s_roe, "{:.1f}%"),
    ("net_margin", "Nettomargin", "Profitabilitet", 0.10, s_net_margin, "{:.1f}%"),
    ("gross_margin", "Bruttomargin", "Profitabilitet", 0.10, s_gross_margin, "{:.1f}%"),
    # Balance & soliditet ~30 %
    ("netdebt_ebitda", "Nettogaeld/EBITDA", "Balance", 0.09, s_netdebt_ebitda, None),
    ("interest_coverage", "Rentedaekning", "Balance", 0.08, s_interest_coverage, None),
    ("current_ratio", "Current ratio", "Balance", 0.06, s_current_ratio, "{:.2f}"),
    ("debt_to_equity", "Gaeld/egenkapital", "Balance", 0.07, s_debt_equity, "{:.2f}"),
    # Cash flow-kvalitet ~25 %
    ("fcf_margin", "FCF-margin", "Cash flow", 0.09, s_fcf_margin, "{:.1f}%"),
    ("fcf_conversion", "FCF/nettoresultat", "Cash flow", 0.08, s_fcf_conversion, None),
    ("oe_margin", "Owner-Earnings margin", "Cash flow", 0.08, s_oe_margin, None),
]

PARAMS_GROWTH = [
    # Vaekst ~55 %
    ("rev_cagr5", "Omsaetnings-CAGR 5y", "Vaekst", 0.20, s_rev_cagr, "{:.1f}%"),
    ("eps_cagr5", "EPS-CAGR 5y", "Vaekst", 0.20, s_eps_cagr, "{:.1f}%"),
    ("fcf_growth", "FCF-vaekst", "Vaekst", 0.15, s_fcf_growth, "{:.1f}%"),
    # Holdbarhed ~45 %
    ("growth_consistency", "Vaekst-konsistens", "Holdbarhed", 0.15, s_growth_consistency, None),
    ("margin_trend", "Margin-trend 5y", "Holdbarhed", 0.15, s_margin_trend, None),
    ("reinvestment", "Reinvesteringsafkast", "Holdbarhed", 0.15, s_reinvestment, None),
]

PARAMS_VALUATION = [
    # Multipler ~60 %
    ("pe", "P/E", "Multipler", 0.15, s_pe, None),
    ("peg", "PEG", "Multipler", 0.15, s_peg, None),
    ("pfcf", "P/FCF", "Multipler", 0.15, s_pfcf, None),
    ("ev_ebitda", "EV/EBITDA", "Multipler", 0.15, s_ev_ebitda, None),
    # Yield & relativ ~40 % (Owner-Earnings yield = HOVEDFAKTOR)
    ("oe_yield", "Owner-Earnings yield", "Yield", 0.20, s_oe_yield, None),
    ("fcf_yield", "FCF-yield", "Yield", 0.10, s_fcf_yield, None),
    ("earnings_yield", "Earnings yield", "Yield", 0.10, s_earnings_yield, None),
    ("dividend", "Udbytte (kontekst)", "Yield", 0.00, s_dividend, None),   # vaegt 0
]

LAYERS = {
    "quality":   ("Kvalitet", PARAMS_QUALITY),
    "growth":    ("Vaekst & holdbarhed", PARAMS_GROWTH),
    "valuation": ("Vaerdiansaettelse", PARAMS_VALUATION),
}

# Sektor-undtagelse: capex/FCF/Owner-Earnings/gaeld-faktorer er meningsloese for
# finans/bank -> ekskludér dem (Altman Z haandteres i gaten, Fase 2).
FINANCIAL_EXCLUDE = {"oe_margin", "fcf_margin", "fcf_conversion", "netdebt_ebitda",
                     "oe_yield", "pfcf", "fcf_yield"}


def is_financial(sector: Optional[str]) -> bool:
    s = (sector or "").lower()
    return any(k in s for k in ("financ", "bank", "insurance", "capital markets"))


@dataclass
class ParamResult:
    key: str
    name: str
    group: str
    raw: str
    signal: float
    weight: float
    weighted: float


def compute_layer(params, f: dict, price: Optional[float] = None,
                  exclude_keys: Optional[set] = None, exclude_reason: str = "") -> dict:
    """Generisk lag-beregning (spejler compute_fundamental). exclude_keys -> de keys
    markeres ekskluderet med exclude_reason (sektor-undtagelse)."""
    exclude_keys = exclude_keys or set()
    results, excluded = [], []
    for key, name, group, weight, scorer, fmt in params:
        if key in exclude_keys:
            excluded.append((name, exclude_reason or "ekskluderet"))
            continue
        try:
            out = scorer(f, price)
        except Exception as e:
            excluded.append((name, f"fejl: {type(e).__name__}"))
            continue
        if out is None:
            excluded.append((name, "mangler data"))
            continue
        if isinstance(out, tuple):
            raw, signal = out
        else:
            signal = out
            val = f.get(key)
            raw = fmt.format(val) if (fmt and val is not None) else str(val)
        results.append(ParamResult(key, name, group, raw, float(signal), weight, 0.0))

    total_w = sum(r.weight for r in results)
    if total_w > 0:
        for r in results:
            r.weight /= total_w
            r.weighted = r.weight * r.signal
    lag_score = sum(r.weighted for r in results)
    return {"results": results, "excluded": excluded, "lag_score": lag_score}


def compute_quality(f, price=None, exclude_keys=None, reason=""):
    return compute_layer(PARAMS_QUALITY, f, price, exclude_keys, reason)


def compute_growth(f, price=None, exclude_keys=None, reason=""):
    return compute_layer(PARAMS_GROWTH, f, price, exclude_keys, reason)


def compute_valuation(f, price=None, exclude_keys=None, reason=""):
    return compute_layer(PARAMS_VALUATION, f, price, exclude_keys, reason)


def _band(score):
    if score >= 50:
        return "Staerk koeb-og-hold-kandidat"
    if score >= 20:
        return "Solid kvalitet / medvind"
    if score > -20:
        return "Neutral / blandet"
    if score > -50:
        return "Svag"
    return "Fraraades (langsigtet)"


# ── FMP-adapter: fylder f fra de AABNE raa regnskaber + udregner det laaste ──
def fetch_buyhold_fundamentals(symbol: str, api_key: str):
    """Returnér (f, meta). f = scorer-input-dict; meta = sektor/Owner-Earnings-spaend/
    gate-raafelter/coverage. Spejler fetch_fundamentals_fmp's klient (base=/stable)."""
    import json
    import urllib.error
    import urllib.request

    base = "https://financialmodelingprep.com/stable"

    def get(path):
        url = f"{base}/{path}{'&' if '?' in path else '?'}apikey={api_key}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return {"__error__": f"402-LAAST" if e.code == 402 else f"HTTP {e.code}"}
        except Exception as e:
            return {"__error__": f"FEJL {type(e).__name__}"}

    def first(d):
        if isinstance(d, list):
            return d[0] if d else {}
        return d if isinstance(d, dict) else {}

    def col(rows, field):
        """Kolonne (nyeste foerst) af et felt fra en liste af aarsrecords."""
        return [r.get(field) for r in rows] if isinstance(rows, list) else []

    f: dict = {}
    meta: dict = {"errors": []}

    rt = first(get(f"ratios-ttm?symbol={symbol}"))
    km = first(get(f"key-metrics-ttm?symbol={symbol}"))
    fg = first(get(f"financial-growth?symbol={symbol}&period=annual&limit=5"))
    inc = get(f"income-statement?symbol={symbol}&period=annual&limit=5")
    bal = get(f"balance-sheet-statement?symbol={symbol}&period=annual&limit=5")
    cf = get(f"cash-flow-statement?symbol={symbol}&period=annual&limit=5")
    rat = get(f"ratios?symbol={symbol}&period=annual&limit=5")
    prof = first(get(f"profile?symbol={symbol}"))
    for label, blob in (("ratios-ttm", rt), ("key-metrics-ttm", km), ("financial-growth", fg),
                        ("income", inc), ("balance", bal), ("cash-flow", cf),
                        ("ratios", rat), ("profile", prof)):
        if isinstance(blob, dict) and blob.get("__error__"):
            meta["errors"].append(f"{label}: {blob['__error__']}")

    inc_l = inc if isinstance(inc, list) else []
    bal_l = bal if isinstance(bal, list) else []
    cf_l = cf if isinstance(cf, list) else []
    rat_l = rat if isinstance(rat, list) else []
    inc0, bal0, cf0 = first(inc_l), first(bal_l), first(cf_l)

    price = prof.get("price")
    mktcap = prof.get("marketCap")
    f["price"] = price
    meta["sector"] = prof.get("sector")
    meta["company_name"] = prof.get("companyName")
    meta["market_cap"] = mktcap

    # --- direkte (TTM) felter (aabne ratios) ---
    if rt.get("netProfitMarginTTM") is not None:
        f["net_margin"] = rt["netProfitMarginTTM"] * 100
    if rt.get("grossProfitMarginTTM") is not None:
        f["gross_margin"] = rt["grossProfitMarginTTM"] * 100
    f["current_ratio"] = rt.get("currentRatioTTM")
    f["debt_to_equity"] = rt.get("debtToEquityRatioTTM")
    f["pe_ttm"] = rt.get("priceToEarningsRatioTTM")
    f["dividend_yield"] = rt.get("dividendYieldTTM") if rt.get("dividendYieldTTM") is not None else rt.get("dividendYielTTM")
    f["payout"] = rt.get("dividendPayoutRatioTTM") if rt.get("dividendPayoutRatioTTM") is not None else rt.get("payoutRatioTTM")
    if km.get("returnOnEquityTTM") is not None:
        f["roe"] = km["returnOnEquityTTM"] * 100
    if km.get("returnOnInvestedCapitalTTM") is not None:
        f["roic"] = km["returnOnInvestedCapitalTTM"] * 100
    elif km.get("roicTTM") is not None:
        f["roic"] = km["roicTTM"] * 100
    f["netdebt_ebitda"] = km.get("netDebtToEBITDATTM")
    f["ev_ebitda"] = km.get("evToEBITDATTM") if km.get("evToEBITDATTM") is not None else km.get("enterpriseValueOverEBITDATTM")
    f["fcf_yield"] = km.get("freeCashFlowYieldTTM")
    f["earnings_yield"] = km.get("earningsYieldTTM")

    # --- EPS(ttm) til P/E = pris/EPS ---
    f["eps_ttm"] = inc0.get("eps") if inc0.get("eps") is not None else rt.get("netIncomePerShareTTM")
    if fg.get("epsgrowth") is not None:
        f["eps_growth"] = fg["epsgrowth"] * 100
    if fg.get("revenueGrowth") is not None:
        f["revenue_growth"] = fg["revenueGrowth"] * 100

    # --- vaekst (CAGR 5y: foretraek financial-growth's per-share, ellers fra serie) ---
    rev_series = col(inc_l, "revenue")
    eps_series = col(inc_l, "eps")
    if fg.get("fiveYRevenueGrowthPerShare") is not None:
        f["rev_cagr5"] = fg["fiveYRevenueGrowthPerShare"] * 100 / 5.0
    else:
        f["rev_cagr5"] = _cagr(rev_series)
    if fg.get("fiveYNetIncomeGrowthPerShare") is not None:
        f["eps_cagr5"] = fg["fiveYNetIncomeGrowthPerShare"] * 100 / 5.0
    else:
        f["eps_cagr5"] = _cagr(eps_series)
    if fg.get("freeCashFlowGrowth") is not None:
        f["fcf_growth"] = fg["freeCashFlowGrowth"] * 100
    else:
        f["fcf_growth"] = _cagr(col(cf_l, "freeCashFlow"))

    # --- holdbarhed ---
    f["growth_consistency"] = _growth_cv(rev_series)
    npm_series = col(rat_l, "netProfitMargin")
    f["margin_trend"] = (_trend_diff([(x * 100 if x is not None else None) for x in npm_series])
                         if npm_series else None)

    # --- balance-afledt ---
    revenue = inc0.get("revenue")
    op_income = inc0.get("operatingIncome")
    int_exp = inc0.get("interestExpense")
    if op_income is not None and int_exp is not None:
        f["interest_coverage"] = float("inf") if int_exp == 0 else op_income / abs(int_exp)

    # --- cash flow-kvalitet ---
    ocf = cf0.get("operatingCashFlow")
    fcf = cf0.get("freeCashFlow")
    total_capex = cf0.get("capitalExpenditure")
    da = cf0.get("depreciationAndAmortization")
    ni = cf0.get("netIncome") if cf0.get("netIncome") is not None else inc0.get("netIncome")
    f["fcf_margin"] = (_safe_div(fcf, revenue) or 0) * 100 if (fcf is not None and revenue) else None
    f["fcf_conversion"] = _safe_div(fcf, ni)

    # --- Owner Earnings (3-aars normaliseret, est.) ---
    # Et enkelt stoejende aar (engangs-capex/skat/working-capital) forvraenger ellers
    # hovedfaktoren (KO 2024: FCF kunstigt lav). Normalisér over N aar.
    oe_norm, avg_maint, oe_step, oe_n, avg_rev = owner_earnings_normalized(cf_l, inc_l, bal_l)
    meta["owner_earnings"] = oe_norm
    meta["ocf"] = ocf                 # seneste aars OCF (til spaend-kontekst)
    meta["fcf"] = fcf                 # seneste aars FCF (til spaend-kontekst)
    meta["maint_capex"] = avg_maint   # gns. vedligeholds-capex over N aar
    meta["oe_step"] = oe_step
    meta["oe_n_years"] = oe_n
    if oe_norm is not None and avg_rev:
        f["oe_margin"] = oe_norm / avg_rev * 100      # mod gns. revenue (konsistent basis)
    if oe_norm is not None and mktcap:
        f["oe_yield"] = oe_norm / mktcap * 100        # mod NUVAERENDE marketCap = korrekt yield

    # --- gate-raafelter (Fase 2 samler gaten) ---
    wc = (_safe_div(bal0.get("totalCurrentAssets"), 1) or 0) - (bal0.get("totalCurrentLiabilities") or 0) \
        if (bal0.get("totalCurrentAssets") is not None and bal0.get("totalCurrentLiabilities") is not None) else None
    meta["gate_raw"] = {
        "altman_z": altman_z(wc, bal0.get("totalAssets"), bal0.get("retainedEarnings"),
                             op_income, mktcap, bal0.get("totalLiabilities"), revenue),
        "dilution_pct": dilution_pct(col(inc_l, "weightedAverageShsOut")),
        "fcf_sign": (1 if (fcf or 0) > 0 else (-1 if fcf is not None else None)),
        "netdebt_ebitda": f.get("netdebt_ebitda"),
        "current_ratio": f.get("current_ratio"),
    }

    # --- coverage / sektor ---
    meta["is_financial"] = is_financial(meta["sector"])
    statement_locked = all(("LAAST" in e) for e in meta["errors"] if e.split(":")[0] in
                           ("income", "balance", "cash-flow", "ratios")) and any(
        e.split(":")[0] in ("income", "balance", "cash-flow") for e in meta["errors"])
    meta["fundamental_available"] = bool(rev_series or rt or km)
    meta["statement_locked"] = statement_locked
    return f, meta


# ── CLI: sanity for de fire probe-tickers ────────────────────────────────────
def _fmt_layer(title, res):
    L = [f"[{title}]  lag-score {res['lag_score']:+.1f}  ({_band(res['lag_score'])})"]
    last = None
    for r in res["results"]:
        if r.group != last:
            L.append(f"  ({r.group})")
            last = r.group
        L.append(f"    {r.name:<26}{r.raw:<34}{r.signal:>+7.0f} {r.weight*100:>5.1f}% {r.weighted:>+7.1f}")
    if res["excluded"]:
        L.append("    Ekskluderet: " + "; ".join(f"{n} ({w})" for n, w in res["excluded"]))
    return "\n".join(L)


def main() -> int:
    import argparse
    import os
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Buy-and-Hold fundamentals-motor — sanity-CLI")
    ap.add_argument("ticker")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    a = ap.parse_args()
    if not a.api_key:
        print("FEJL: ingen FMP-noegle (FMP_API_KEY el. --api-key).")
        return 1

    f, meta = fetch_buyhold_fundamentals(a.ticker.upper(), a.api_key)
    print("=" * 84)
    print(f"  BUY-AND-HOLD FUNDAMENTAL — {a.ticker.upper()}  ({meta.get('company_name') or ''})")
    print(f"  LANGSIGTET vurdering (min. 1 aar).  Sektor: {meta.get('sector') or '—'}")
    print("=" * 84)
    if meta["errors"]:
        print("  Datakilde-noter: " + "; ".join(meta["errors"]))
    if not meta.get("fundamental_available"):
        print("  ❌ Fundamental data utilgaengelig for denne ticker — vis kun teknik/trend (Fase 2).")
        return 0

    excl, reason = (FINANCIAL_EXCLUDE, "finansiel sektor") if meta["is_financial"] else (set(), "")
    if meta["is_financial"]:
        print("  ⚠️  BEGRAENSET MODEL — finansiel sektor: capex/FCF/Owner-Earnings/gaeld-faktorer udeladt.")

    oe, ocf, fcf = meta.get("owner_earnings"), meta.get("ocf"), meta.get("fcf")
    if oe is not None:
        print(f"  Owner Earnings ({meta.get('oe_n_years')}-aars norm., est., {meta['oe_step']}): {oe:,.0f}  "
              f"[seneste aar: OCF {ocf:,.0f} · FCF {fcf:,.0f} · gns. maint-capex {meta.get('maint_capex'):,.0f}]")
    print()
    print(_fmt_layer("KVALITET (Lag 1)", compute_quality(f, f.get("price"), excl, reason)))
    print(_fmt_layer("VAEKST & HOLDBARHED (Lag 2)", compute_growth(f, f.get("price"), excl, reason)))
    print(_fmt_layer("VAERDIANSAETTELSE (Lag 3)", compute_valuation(f, f.get("price"), excl, reason)))
    gr = meta.get("gate_raw", {})
    print(f"\n  Gate-raafelter (Fase 2): Altman Z {gr.get('altman_z')}  ·  "
          f"udvanding {gr.get('dilution_pct')}%  ·  FCF-fortegn {gr.get('fcf_sign')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
