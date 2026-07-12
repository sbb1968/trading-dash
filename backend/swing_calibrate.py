# -*- coding: utf-8 -*-
"""
swing_calibrate.py - MAAL FOER VI BYGGER.

Spoergsmaal: hvor stor skal en billig pre-rank vaere for at fange de bedste
swing-kandidater? Vi kan ikke koere den fulde swing-rapport paa alle US-aktier,
saa vi laver en BILLIG pre-rank (RS vs SPY + trend, beregnet paa cachede dagsbars
- ingen FMP, ingen IBKR-live), tager top-X, og koerer kun den fulde rapport paa
dem. Dette script maaler hvor godt den billige pre-rank gendanner den AEGTE top-K
(rangeret paa rapportens SAMLET-score), saa vi ved hvor stort X skal vaere.

Designvalg:
  * GROUND TRUTH = rapportens SAMLET (c["final"]). Hentes ved at spejle
    swing_report.run_full's scoring-kerne PRAECIST (samme lag, gate, combine),
    men UDEN format-tekst og UDEN info-linjer (float/spread/gap paavirker ikke
    final). manual=None (intet chart-overlay i bulk - som produktions-pipelinen).
  * BILLIGE SIGNALER beregnes fra de SAMME cachede dagsbars rapporten bruger
    (data_source.load_bars) - saa pre-rank koster ingen ekstra API-kald.
  * RESUMERBAR: skriver per aktie til CSV; genstart springer faerdige over
    (som velocity-harvesten). FMP-venlig delay mellem kald.

Koer fra backend/ paa win11sbb eller algoserveren (FMP_API_KEY i miljoeet):
  set FMP_API_KEY=...        (PowerShell: $env:FMP_API_KEY="...")
  python swing_calibrate.py --sample 400          # sample fra cachen, score, analyser
  python swing_calibrate.py --tickers liste.txt    # score praecis denne liste
  python swing_calibrate.py --analyze-only         # kun analyse paa eksisterende CSV

VIGTIGT for et GYLDIGT maal: samplet skal ligne det endelige univers (likvide
US-aktier), ikke momentum-universet. Foed den gerne en bred, tilfaeldig liste af
likvide tickers (fx fra FMP-screeneren), og soerg for at deres dagsbars + SPY er
cachede (load_bars henter manglende fra IBKR - koer da uden for handelstid).
"""

import os
import sys
import csv
import time
import glob
import random
import argparse

# Goer soeskende-moduler i backend/ importerbare uanset cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT_DEFAULT = "swing_calib_scores.csv"
FIELDS = ["ticker", "final", "combined", "gate", "tech", "fund", "cat", "price",
          "rs_3m", "rs_6m", "above_ma200", "dist_ma200", "dist_ma50",
          "dist_52w_high", "roc_12", "n_bars", "error"]


# ── OHLCV (spejler swing_report._ohlcv) ──────────────────────────────────────
def _ohlcv(ticker, source, period):
    if source == "ibkr":
        import data_source
        return data_source.load_bars(ticker)
    import technical_score as tech
    return tech._yf_ohlcv(ticker, period)


# ── Billige signaler (kun fra dagsbars - ingen API) ──────────────────────────
def cheap_signals(df, bench):
    close = df["Close"]
    price = float(close.iloc[-1])

    def ret(series, n):
        if series is not None and len(series) > n and float(series.iloc[-1 - n]) != 0:
            return float(series.iloc[-1]) / float(series.iloc[-1 - n]) - 1
        return None

    bclose = bench["Close"] if bench is not None else None
    r3, r6 = ret(close, 63), ret(close, 126)        # ~3m, ~6m handelsdage
    s3, s6 = ret(bclose, 63), ret(bclose, 126)
    rs_3m = (r3 - s3) * 100 if (r3 is not None and s3 is not None) else None
    rs_6m = (r6 - s6) * 100 if (r6 is not None and s6 is not None) else None

    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    hi52 = float(df["High"].tail(252).max()) if len(df) >= 60 else None
    roc12 = ret(close, 12)

    return {
        "rs_3m": rs_3m,
        "rs_6m": rs_6m,
        "above_ma200": (1 if price > ma200 else 0) if ma200 else None,
        "dist_ma200": (price / ma200 - 1) * 100 if ma200 else None,
        "dist_ma50": (price / ma50 - 1) * 100 if ma50 else None,
        "dist_52w_high": (price / hi52 - 1) * 100 if hi52 else None,
        "roc_12": roc12 * 100 if roc12 is not None else None,
        "n_bars": int(len(df)),
    }


# ── Ground truth: rapportens SAMLET (spejler run_full's kerne) ────────────────
def score_ticker(ticker, api_key, source="ibkr", period="1y"):
    import technical_score as tech
    import fundamental_score as fund
    import catalyst_score as cat
    import swing_report as sr

    df = _ohlcv(ticker, source, period)
    if df is None or df.empty:
        return {"error": "ingen prisdata"}
    price = float(df["Close"].iloc[-1])

    bench = _ohlcv("SPY", source, period)
    if bench is not None and bench.empty:
        bench = None

    fdict = fund.fetch_fundamentals(ticker, api_key)
    fund_res = fund.compute_fundamental(fdict, price)

    sector_df, sector_mom = None, None
    etf = tech.SECTOR_ETF.get(fdict.get("sector"))
    if etf:
        sector_df = _ohlcv(etf, source, period)
        if sector_df is not None and not sector_df.empty and len(sector_df) > 63:
            sector_mom = (sector_df["Close"].iloc[-1] / sector_df["Close"].iloc[-64] - 1) * 100
        else:
            sector_df = None

    tech_res = tech.compute_technical(df, benchmark=bench, sector=sector_df)
    cat_dict = cat.fetch_catalyst(ticker, api_key, price, sector_momentum_pct=sector_mom)
    cat_res = cat.compute_catalyst(cat_dict, price)

    adv = float(df["Volume"].tail(50).mean())
    gate = sr.compute_gate(adv_shares=adv, dollar_vol=adv * price, price=price,
                           market_cap=fdict.get("market_cap"))

    c = sr.combine({"technical": tech_res, "fundamental": fund_res, "catalyst": cat_res},
                   gate=gate, manual=None, days_to_earnings=cat_dict.get("days_to_earnings"))

    adj = c.get("adj", {})
    row = {
        "final": round(c["final"], 2),
        "combined": round(c.get("combined", float("nan")), 2),
        "gate": round(gate, 3),
        "tech": round(adj.get("technical"), 2) if adj.get("technical") is not None else None,
        "fund": round(adj.get("fundamental"), 2) if adj.get("fundamental") is not None else None,
        "cat": round(adj.get("catalyst"), 2) if adj.get("catalyst") is not None else None,
        "price": round(price, 2),
        "error": "",
    }
    row.update(cheap_signals(df, bench))
    return row


# ── Resumerbar harvest ───────────────────────────────────────────────────────
def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                done.add(r["ticker"].upper())
    return done


def harvest(tickers, api_key, out_path, source, period, delay):
    done = load_done(out_path)
    todo = [t for t in tickers if t.upper() not in done]
    print(f"[harvest] {len(tickers)} tickers, {len(done)} faerdige, {len(todo)} tilbage")
    new_file = not os.path.exists(out_path)
    with open(out_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for i, t in enumerate(todo, 1):
            t = t.upper()
            row = {k: None for k in FIELDS}
            row["ticker"] = t
            try:
                res = score_ticker(t, api_key, source=source, period=period)
                row.update({k: res.get(k) for k in FIELDS if k in res})
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"
            w.writerow(row)
            f.flush()
            fin = row.get("final")
            print(f"  [{i}/{len(todo)}] {t:<6} final={fin if fin is not None else '-':>6}  "
                  f"{row.get('error','')}")
            if delay:
                time.sleep(delay)
    print(f"[harvest] faerdig -> {out_path}")


# ── Analyse: Spearman + recall@X ─────────────────────────────────────────────
def analyze(out_path, topks=(10, 20), xs=(25, 50, 75, 100, 150, 200, 300)):
    import pandas as pd

    df = pd.read_csv(out_path)
    df = df[df["final"].notna()].copy()
    n = len(df)
    print(f"\n=== ANALYSE ({n} scorede aktier i {out_path}) ===")
    if n < 100:
        print("ADVARSEL: < 100 scorede aktier - recall-tallene er stoejende. "
              "Sample bredere foer du stoler paa dem.")

    sig_cols = ["rs_3m", "rs_6m", "dist_ma200", "dist_ma50", "dist_52w_high", "roc_12", "above_ma200"]
    print("\nSpearman-korrelation med SAMLET (final) - hvilke billige signaler forudsiger scoren:")
    for col in sig_cols:
        sub = df[[col, "final"]].dropna()
        if len(sub) > 10:
            rho = sub[col].rank().corr(sub["final"].rank())  # Spearman = Pearson paa ranks (ingen scipy)
            print(f"  {col:<14}: {rho:+.3f}   (n={len(sub)})")

    # Kandidat-rankere (alle BILLIGE - kun dagsbar-signaler)
    df["rs_6m_uptrend"] = df["rs_6m"].where(df["above_ma200"] == 1)
    df["rs_3m_uptrend"] = df["rs_3m"].where(df["above_ma200"] == 1)

    def zser(s):
        sd = s.std()
        return (s - s.mean()) / sd if sd and sd > 0 else s * 0.0
    df["blend_rs"] = zser(df["rs_6m"]) + zser(df["rs_3m"])
    df["blend_rs_uptrend"] = df["blend_rs"].where(df["above_ma200"] == 1)

    rankers = ["rs_6m", "rs_3m", "rs_6m_uptrend", "rs_3m_uptrend",
               "blend_rs", "blend_rs_uptrend", "dist_ma200"]

    for K in topks:
        if n < K:
            continue
        true_top = set(df.nlargest(K, "final")["ticker"])
        print(f"\nRECALL @ X  (andel af AEGTE top-{K} fanget af billig top-X)")
        header = "  ranker             " + "".join(f"X={x:<4}" for x in xs)
        print(header)
        for rk in rankers:
            sub = df[["ticker", rk]].dropna()
            cells = []
            for x in xs:
                if x > n:
                    cells.append("  -  ")
                    continue
                cheap_top = set(sub.nlargest(x, rk)["ticker"])
                rec = len(true_top & cheap_top) / K
                cells.append(f"{rec:>4.2f} ")
            print(f"  {rk:<18} " + "".join(cells))

    print("\nLAESNING: find den ranker + mindste X hvor recall naar fx 0.90 for din "
          "oenskede top-K. Det X er din recall/omkostnings-knap i produktions-"
          "pipelinen (stoerre X = flere fulde rapporter, men faerre missede gode navne).")


# ── Bredt likvidt univers fra FMP-screeneren (best-effort) ───────────────────
def fmp_universe(api_key, min_price=5.0, min_vol=500_000, min_cap=300_000_000, limit=2000):
    """Hent et bredt likvidt US-univers fra FMP's stable screener. Retry paa 429
    (rate limit). 402/403 = premium-gated -> brug --tickers i stedet."""
    import json
    import time as _t
    import urllib.request
    import urllib.parse
    import urllib.error
    params = {
        "priceMoreThan": min_price, "volumeMoreThan": int(min_vol),
        "marketCapMoreThan": int(min_cap), "country": "US",
        "isActivelyTrading": "true", "isEtf": "false", "isFund": "false",
        "limit": int(limit),
    }
    url = (f"https://financialmodelingprep.com/stable/company-screener?"
           f"{urllib.parse.urlencode(params)}&apikey={api_key}")
    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                wait = 15 * (attempt + 1)
                print(f"[fmp] 429 rate-limit - venter {wait}s og proever igen ({attempt + 1}/3)...")
                _t.sleep(wait)
                continue
            if e.code == 429:
                print("[fmp] stadig 429 - din FMP-kvote er sandsynligvis opbrugt (per-minut eller "
                      "dagligt). Vent, eller brug --tickers med en egen liste.")
                return []
            if e.code in (401, 402, 403):
                print(f"[fmp] HTTP {e.code} - screeneren er premium-gated paa din plan. Brug --tickers.")
                return []
            print(f"[fmp] HTTP {e.code}: {e}. Brug --tickers.")
            return []
        except Exception as e:
            print(f"[fmp] screener fejlede ({type(e).__name__}: {e}). Brug --tickers.")
            return []
    if not isinstance(data, list) or not data:
        print(f"[fmp] screener gav intet brugbart: {str(data)[:200]}")
        return []
    syms = [d.get("symbol") for d in data if d.get("symbol")]
    # luk aabenlyse ikke-aktier ude (warrants/units med punktum eller bindestreg)
    syms = [s.upper() for s in syms if s and "." not in s and "-" not in s]
    print(f"[fmp] screener gav {len(syms)} likvide tickers")
    return syms


# ── Bredt likvidt univers fra TradingViews screener (ingen FMP-loft) ─────────
def tv_universe(min_price=5.0, min_vol=500_000, min_cap=300_000_000, limit=5000):
    """Hent et bredt likvidt US-aktie-univers fra TradingViews screener via
    tradingview-screener (allerede en afhaengighed; intet FMP rate-limit).
    Feltnavnene er de samme som jeres tv_scanner.py bruger (verificeret mod TV).
    get_scanner_data() returnerer (total, df): total = antal der matchede filteret."""
    try:
        from tradingview_screener import Query, col
    except ImportError:
        print("[tv] tradingview-screener mangler. Koer: pip install tradingview-screener")
        return []
    try:
        total, df = (
            Query()
            .select("name", "close", "volume", "average_volume_30d_calc",
                    "market_cap_basic", "exchange", "type")
            .where(
                col("close") > min_price,
                col("market_cap_basic") > min_cap,
                col("average_volume_30d_calc") > min_vol,
                col("exchange").isin(["NYSE", "NASDAQ", "AMEX"]),
                col("type").isin(["stock"]),
            )
            .order_by("market_cap_basic", ascending=False)
            .limit(limit)
            .get_scanner_data()
        )
    except Exception as e:
        print(f"[tv] query fejlede: {type(e).__name__}: {e}")
        return []
    if df is None or df.empty:
        print("[tv] tom respons")
        return []
    syms = []
    for _, row in df.iterrows():
        t = row.get("ticker", "") or ""
        s = t.split(":")[-1] if ":" in t else t
        if s and len(s) <= 5 and not any(c in s for c in [".", "/", "-"]):
            syms.append(s.upper())
    syms = sorted(set(syms))
    print(f"[tv] {total} aktier matchede filteret; {len(syms)} rene symboler hentet "
          f"(limit={limit})")
    return syms


# ── Sample fra cachen ────────────────────────────────────────────────────────
def sample_from_cache(n):
    import data_source
    paths = glob.glob(str(data_source.CACHE_DIR / "*_daily.csv"))
    tickers = [os.path.basename(p).replace("_daily.csv", "").upper() for p in paths]
    # Udeluk benchmarks/sektor-ETF'er - de er ikke swing-kandidater
    excl = {"SPY", "QQQ", "IWM", "DIA"}
    try:
        import technical_score as tech
        excl |= {v.upper() for v in tech.SECTOR_ETF.values()}
    except Exception:
        pass
    tickers = [t for t in tickers if t not in excl]
    if not tickers:
        print(f"[sample] tom cache i {data_source.CACHE_DIR} - "
              f"download dagsbars foerst, eller brug --tickers.")
        return []
    random.shuffle(tickers)
    return tickers[:n]


def main():
    ap = argparse.ArgumentParser(description="Kalibrer billig pre-rank mod swing-rapportens SAMLET")
    ap.add_argument("--tickers", help="fil med en ticker pr. linje")
    ap.add_argument("--sample", type=int, help="sample N tickers (fra cachen, eller fra FMP med --from-fmp)")
    ap.add_argument("--from-fmp", action="store_true", help="hent bredt likvidt univers fra FMP-screeneren i stedet for cachen")
    ap.add_argument("--from-tv", action="store_true", help="hent bredt likvidt univers fra TradingViews screener (ingen FMP-loft)")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    ap.add_argument("--source", default="ibkr", choices=["ibkr", "yfinance"])
    ap.add_argument("--period", default="1y")
    ap.add_argument("--delay", type=float, default=0.4, help="sek. mellem aktier (FMP rate limit)")
    ap.add_argument("--analyze-only", action="store_true", help="spring scoring over, kun analyse")
    args = ap.parse_args()

    if args.analyze_only:
        analyze(args.out)
        return

    # FMP er udfaset (død nøgle + 402 på gratis-tier). Kun det gamle --from-fmp kræver en
    # nøgle; alt andet (--from-tv / --tickers / --sample) kører uden. Brug --from-tv.
    if args.from_fmp and not args.api_key:
        print("FEJL: --from-fmp kræver FMP_API_KEY — men FMP er udfaset; brug --from-tv i stedet.")
        sys.exit(1)

    if args.tickers:
        with open(args.tickers, encoding="utf-8") as f:
            tickers = [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")]
    elif args.from_tv:
        uni = tv_universe()
        if not uni:
            sys.exit(1)
        random.shuffle(uni)
        tickers = uni[:(args.sample or 400)]
    elif args.from_fmp:
        uni = fmp_universe(args.api_key)
        if not uni:
            sys.exit(1)
        random.shuffle(uni)
        tickers = uni[:(args.sample or 400)]
    elif args.sample:
        tickers = sample_from_cache(args.sample)
    else:
        print("Angiv --tickers FIL, --from-tv / --from-fmp [--sample N], eller --sample N.")
        sys.exit(1)

    if not tickers:
        sys.exit(1)

    harvest(tickers, args.api_key, args.out, args.source, args.period, args.delay)
    analyze(args.out)


if __name__ == "__main__":
    main()
