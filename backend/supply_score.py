#!/usr/bin/env python3
"""
supply_score.py — Intradag FORSYNINGS-lag (day-tradings pendant til swings
fundamentale lag).

Hvor swings fundamental_score scorer en virksomheds kvalitet/vaekst/vaerdi,
scorer dette lag en akties FORSYNINGSPROFIL — frem for alt FLOAT (frit
omsaettelige aktier), fordi lav float er kernen i Warrior/Ross Cameron-momentum:
faa aktier = eksplosive bevaegelser. Returnerer samme form som compute_fundamental
({results, excluded, lag_score}), saa intradag_report.py (trin 4) kan plugge det
ind IDENTISK med swing.

VIRKELIGHED (afsoegt + verificeret, juni 2026):
  * float_shares_outstanding VIRKER paa TradingViews screener (40/40 udfyldt).
  * TV's screener baerer IKKE short interest (alle short-felter tomme). FMP-probe
    inkonklusiv (forkerte feltnavne); Finnhub /stock/short-interest gav 403.
  -> Laget er FLOAT-centreret. Short-interest-faktorerne er DEFINERET (baand +
     vaegt) men pt. altid ekskluderet (ingen kilde) og renormaliseres ud — praecis
     som det tekniske lag udelader faktorer uden data. Naar en SI-kilde findes,
     plugges de ind uden arkitektur-aendring. Re-probe IKKE TV/Finnhub for SI.

Genbruger ParamResult fra technical_intraday (homogene intradag-resultater).
ASCII i kode + konsol; dansk prosa.
"""
from __future__ import annotations

from technical_intraday import ParamResult   # key,name,group,raw,signal,weight,weighted


# === Faktor-scorere: scorer(d) -> None | signal | (raw_text, signal) ========
def s_float_size(d):
    """Dominerende faktor: lav float = eksplosiv. Float angives i MILLIONER.
    Returnerer (raw, signal) saa ekstremer faar et informativt flag (spejler
    swings tiered-flag-filosofi, fx s_peg's ROEDT FLAG)."""
    fl = d.get("float_shares")
    if fl is None:
        return None
    m = fl / 1e6
    if m < 2:
        return f"{m:.1f}M - MIKRO FLOAT (eksplosiv)", 100.0
    if m < 5:
        return f"{m:.1f}M aktier (meget lav float)", 85.0
    if m < 10:
        return f"{m:.1f}M aktier (lav float)", 60.0
    if m < 20:
        return f"{m:.1f}M aktier", 30.0
    if m < 50:
        return f"{m:.1f}M aktier", 0.0
    if m < 100:
        return f"{m:.0f}M aktier (hoej float)", -30.0
    if m < 300:
        return f"{m:.0f}M aktier (stor float)", -55.0
    return f"{m:.0f}M - STOR FLOAT (ej small-cap)", -75.0


def s_float_pct(d):
    """Sekundaer: lav float% af udestaaende = mange aktier laast hos insidere/
    institutioner = strammere tilgaengelig forsyning. Lille vaegt."""
    pct = d.get("float_pct")
    if pct is None:
        return None
    if pct < 20:
        return 40.0
    if pct < 50:
        return 20.0
    if pct < 80:
        return 0.0
    return -10.0


def s_short_float(d):
    """Squeeze-braendstof: short i % af float. PENDING (None nu — ingen SI-kilde)."""
    v = d.get("short_float_pct")
    if v is None:
        return None
    if v >= 30:
        return 90.0
    if v >= 20:
        return 70.0
    if v >= 10:
        return 40.0
    if v >= 5:
        return 15.0
    return 0.0


def s_days_cover(d):
    """Squeezbarhed: days to cover (SI / gns. dagsvolumen). PENDING (None nu)."""
    v = d.get("days_to_cover")
    if v is None:
        return None
    if v >= 5:
        return 70.0
    if v >= 3:
        return 50.0
    if v >= 1:
        return 25.0
    return 0.0


# === Parameter-register (vaegt = andel af forsynings-laget; summer til 1.00) =
# (key, navn, gruppe, vaegt, scorer, raw_fmt)
PARAMS = [
    ("float_size",      "Float (frie aktier)", "Float",          0.60, s_float_size,  None),
    ("float_pct",       "Float % af udest.",   "Float",          0.15, s_float_pct,   "{:.0f}%"),
    ("short_float_pct", "Short % af float",    "Short interest", 0.18, s_short_float, "{:.0f}%"),
    ("days_to_cover",   "Days to cover",       "Short interest", 0.07, s_days_cover,  "{:.1f}"),
]
# Med short pt. ekskluderet renormaliserer det til float_size 0.80 / float_pct 0.20.


def compute_supply(supply_data: dict):
    """supply_data: {'float_shares','float_pct','short_float_pct','days_to_cover'}
    (None hvor ukendt). Renormaliserer over faktorer MED data — som de oevrige lag."""
    results, excluded = [], []
    for key, name, group, weight, scorer, fmt in PARAMS:
        try:
            out = scorer(supply_data)
        except Exception as e:
            out = None
            excluded.append((name, f"fejl: {type(e).__name__}"))
        if out is None:
            excluded.append((name, "mangler data"))
            continue
        if isinstance(out, tuple):           # scorer leverede egen raw-tekst
            raw, signal = out
        else:                                 # simpelt signal -> formatér raatal
            signal = out
            val = supply_data.get(key)
            raw = fmt.format(val) if (fmt and val is not None) else str(val)
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
        return "Eksplosiv forsyning (lav float)"
    if score >= 20:
        return "Gunstig"
    if score > -20:
        return "Neutral"
    if score > -50:
        return "Tung forsyning"
    return "Fraraades (forsyning)"


def format_supply_report(ticker: str, res: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append(f" FORSYNINGS LAG — {ticker.upper()}")
    L.append("=" * 78)
    L.append(f"{'Parameter':<24}{'Vaerdi':<24}{'Bidrag':>7}{'Vaegt':>8}{'Vaegtet':>9}")
    L.append("-" * 78)
    last = None
    for r in res["results"]:
        if r.group != last:
            L.append(f"[{r.group}]")
            last = r.group
        L.append(f"  {r.name:<22}{r.raw:<24}{r.signal:>+6.0f} {r.weight*100:>6.1f}% {r.weighted:>+8.1f}")
    L.append("-" * 78)
    score = res["lag_score"]
    L.append(f"{'FORSYNINGS LAG-SCORE':<60}{score:>+8.1f}")
    L.append(f"  -> {_band(score)}")
    if res["excluded"]:
        L.append("")
        L.append("Ekskluderet (mangler data):")
        for name, why in res["excluded"]:
            L.append(f"  - {name} ({why})")
    L.append("=" * 78)
    return "\n".join(L)


# === Float-rotation: dynamisk hjaelper (IKKE en vaegtet faktor) ==============
def float_rotation(cum_vol, float_shares):
    """Hvor mange gange floaten er omsat i dag (dagens volumen / float) — et af
    de staerkeste day-trade-forsyningssignaler, men DYNAMISK (kraever intradag-
    volumen), saa det hoerer ikke i den statiske score. Rapporten/scanneren viser
    den; cum_vol tages fra det tekniske lags context. Returnerer (multiple, label)."""
    if not float_shares or float_shares <= 0 or cum_vol is None:
        return (None, "-")
    r = cum_vol / float_shares
    label = ("ekstrem (floaten omsat 3x+)" if r >= 3 else
             "hoej (hele floaten omsat)" if r >= 1 else
             "betydelig" if r >= 0.5 else "lav")
    return (round(r, 2), label)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Forsynings-lag (intradag) — scorer + live float")
    ap.add_argument("--symbol", default="TEST")
    ap.add_argument("--float", type=float, default=None,
                    help="float i MILLIONER (syntetisk scorer-test uden net)")
    ap.add_argument("--floatpct", type=float, default=None, help="float %% af udestaaende (valgfri)")
    args = ap.parse_args()

    if args.float is not None:
        data = {"float_shares": args.float * 1e6, "float_pct": args.floatpct,
                "short_float_pct": None, "days_to_cover": None}
    else:
        from data_source_intraday import fetch_supply
        data = fetch_supply(args.symbol)

    print(format_supply_report(args.symbol, compute_supply(data)))
