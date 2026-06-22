#!/usr/bin/env python3
"""
swing_top10.py - Daglig top-10 swing-scanner.

Maal: find de 10 aktier med hoejest SAMLET (final) fra swing_report, UDEN at
koere den dyre fulde rapport paa hele universet.

Pipeline (kalibreret i swing_calibrate.py):
  1. Hent TV-universet (~2000 likvide US-aktier).            -> tv_universe()
  2. Beregn rs_3m (3-mdr RS vs SPY) for alle - kun dagsbars. -> cheap_signals()
  3. Tag top FRACTION (default 25 %) efter rs_3m.            -> kortliste ~500
  4. Koer fuld swing-scoring paa kortlisten.                 -> score_ticker()
  5. Sorter efter final, tag top TOP (default 10).

Hvorfor 25 %: kalibreringen viste at rs_3m top-25 % fanger HELE den aegte top-10
(recall 1.00) - forfilteret koster ingen gode navne. Se swing_calibrate.py.

VIGTIGT:
  - rs_3m og final beregnes med de SAMME funktioner som kalibreringen brugte
    (importeret fra swing_calibrate) -> rangeringen er byte-identisk med det der
    blev valideret. Ingen re-implementering, ingen drift.
  - Trin 1-2 henter dagsbars for hele universet via data_source.load_bars (cache
    foerst, manglende fra IBKR). FOERSTE koersel populerer cachen og er LANGSOM
    -> koer UDEN FOR HANDELSTID (ellers Error 162 mod en koerende strategi).
  - Trin 4 er RESUMERBAR: hver scoret aktie skrives straks til scores-CSV'en;
    en afbrudt koersel fortsaetter hvor den slap (--fresh starter forfra).

Koer fra backend/ (FMP_API_KEY i miljoeet for trin 4):
  $env:FMP_API_KEY="..."                  # PowerShell
  python swing_top10.py                    # fuld koersel
  python swing_top10.py --prefilter-only   # kun rs_3m + kortliste (FMP ikke noedvendig)
  python swing_top10.py --fresh            # ignorer i dags gamle scores-fil
"""

from __future__ import annotations

import os
import sys
import csv
import math
import time
import argparse
from datetime import date

# Goer soeskende-moduler i backend/ importerbare uanset cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Genbrug de kalibrerede byggeklodser DIREKTE -> rs_3m og final er byte-identiske
# med det swing_calibrate.py validerede. Ingen re-implementering, ingen drift.
from swing_calibrate import tv_universe, cheap_signals, _ohlcv, score_ticker

SCORE_FIELDS = ["ticker", "final", "combined", "gate", "tech", "fund", "cat", "rs_3m", "error"]

# Faste filnavne som Swing top-10-vinduet (UI) laeser:
LATEST_JSON = "swing_top10_latest.json"   # seneste resultat + genereret-tidsstempel
RUN_LOCK = "swing_top10_running.lock"     # findes mens en koersel er i gang


def compute_rs(source, period):
    """Trin 1-2: hent TV-universet og beregn rs_3m for alle navne (kun dagsbars).
    Returnerer {ticker: rs_3m} for de navne der har tilstraekkelige bars."""
    uni = tv_universe()
    if not uni:
        print("[top10] tomt univers fra TradingView - afbryder.")
        return None
    bench = _ohlcv("SPY", source, period)
    if bench is None or getattr(bench, "empty", True):
        print("[top10] kunne ikke hente SPY (benchmark) - rs_3m umulig. Afbryder.")
        return None
    print(f"[top10] beregner rs_3m for {len(uni)} navne (kun dagsbars)...")
    rs = {}
    missing = 0
    for i, t in enumerate(uni, 1):
        try:
            df = _ohlcv(t, source, period)
            if df is None or df.empty:
                missing += 1
                continue
            v = cheap_signals(df, bench).get("rs_3m")
            if v is None:
                missing += 1
            else:
                rs[t] = float(v)
        except Exception as e:
            missing += 1
            if i <= 5:
                print(f"  {t}: bar-fejl ({type(e).__name__})")
        if i % 250 == 0:
            print(f"  ... {i}/{len(uni)} ({len(rs)} med rs_3m)")
    print(f"[top10] {len(rs)} navne med gyldig rs_3m ({missing} uden tilstraekkelige bars).")
    return rs


def shortlist_from_rs(rs, fraction):
    """Trin 3: top FRACTION efter rs_3m (afrundet op). Returnerer (liste, cutoff)."""
    ranked = sorted(rs.items(), key=lambda kv: kv[1], reverse=True)
    cutoff = max(1, math.ceil(fraction * len(ranked)))
    return [t for t, _ in ranked[:cutoff]], cutoff


def load_done(path):
    """Resumer: hvilke tickers er allerede scoret i en eksisterende scores-CSV."""
    done = set()
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip().upper()
                if t:
                    done.add(t)
    return done


def score_shortlist(shortlist, rs, api_key, source, period, delay, scores_path):
    """Trin 4: fuld scoring af kortlisten (RESUMERBAR). Append pr. ticker, flush hver gang."""
    done = load_done(scores_path)
    todo = [t for t in shortlist if t.upper() not in done]
    print(f"[top10] scorer {len(todo)} navne ({len(done)} allerede gjort) -> {scores_path}")
    new_file = not os.path.exists(scores_path)
    with open(scores_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
        if new_file:
            w.writeheader()
        for i, t in enumerate(todo, 1):
            row = {k: "" for k in SCORE_FIELDS}
            row["ticker"] = t
            row["rs_3m"] = round(rs[t], 2) if rs.get(t) is not None else ""
            try:
                res = score_ticker(t, api_key, source=source, period=period)
                for k in ("final", "combined", "gate", "tech", "fund", "cat"):
                    if res.get(k) is not None:
                        row[k] = res.get(k)
                row["error"] = res.get("error", "") or ""
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"
            w.writerow(row)
            f.flush()
            fin = row["final"]
            tag = f"final={fin:+.2f}" if isinstance(fin, (int, float)) else f"FEJL: {row['error']}"
            print(f"  [{i}/{len(todo)}] {t:<6} {tag}")
            if delay:
                time.sleep(delay)


def emit_top(scores_path, top_n, top_path, latest_json_path, source):
    """Trin 5: laes scores, sorter efter final, skriv + print top-N."""
    from swing_report import _band   # genbrug de kanoniske baand-graenser (45/25/0/-25)

    rows = []
    with open(scores_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("error"):
                continue
            try:
                r["final_num"] = float(r["final"])
            except (TypeError, ValueError):
                continue
            rows.append(r)
    rows.sort(key=lambda r: r["final_num"], reverse=True)
    top = rows[:top_n]

    with open(top_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "ticker", "final", "band", "combined", "gate", "tech", "fund", "cat", "rs_3m"])
        for rank, r in enumerate(top, 1):
            w.writerow([rank, r["ticker"], f'{r["final_num"]:.2f}', _band(r["final_num"]),
                        r.get("combined", ""), r.get("gate", ""), r.get("tech", ""),
                        r.get("fund", ""), r.get("cat", ""), r.get("rs_3m", "")])

    def num(v, spec):
        try:
            return format(float(v), spec)
        except (TypeError, ValueError):
            return str(v) if v not in (None, "") else "-"

    print()
    print(f"=== TOP {top_n} (sorteret efter SAMLET / final) ===")
    print(f"  {'#':>2}  {'ticker':<6} {'final':>7}  {'band':<22} {'gate':>5}  "
          f"{'tek':>6} {'fund':>6} {'kat':>6}  {'rs_3m':>7}")
    for rank, r in enumerate(top, 1):
        print(f"  {rank:>2}  {r['ticker']:<6} {r['final_num']:>+7.2f}  "
              f"{_band(r['final_num']):<22} {num(r.get('gate'), '.2f'):>5}  "
              f"{num(r.get('tech'), '+.1f'):>6} {num(r.get('fund'), '+.1f'):>6} "
              f"{num(r.get('cat'), '+.1f'):>6}  {num(r.get('rs_3m'), '+.1f'):>7}")
    print()
    print(f"[top10] skrevet: {top_path}  ({len(top)} navne)")

    # Skriv ogsaa et fast-navngivet JSON som Swing top-10-vinduet laeser (med tidsstempel).
    import json
    import fundamental_score as _fund   # firmanavn (yfinance, cachet) til UI'et
    gen_local, gen_utc = _now_pair()
    payload = {
        "generated_local": gen_local,
        "generated_utc": gen_utc,
        "source": source,
        "count": len(top),
        "rows": [
            {
                "rank": rank,
                "ticker": r["ticker"],
                "company": _fund._company_name_yf(r["ticker"]),
                "final": round(r["final_num"], 2),
                "band": _band(r["final_num"]),
                "combined": r.get("combined", ""),
                "gate": r.get("gate", ""),
                "tech": r.get("tech", ""),
                "fund": r.get("fund", ""),
                "cat": r.get("cat", ""),
                "rs_3m": r.get("rs_3m", ""),
            }
            for rank, r in enumerate(top, 1)
        ],
    }
    with open(latest_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[top10] JSON til UI: {latest_json_path}")


def _now_pair():
    """(lokal_streng, utc_iso) for nu."""
    import datetime
    now = datetime.datetime.now()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M:%S"), now_utc.isoformat()


def _write_lock(out_dir):
    """Skriv laasefil ved koerselsstart -> UI viser 'koerer', backend hindrer dobbelt-koersel."""
    import json
    _, started_utc = _now_pair()
    try:
        with open(os.path.join(out_dir, RUN_LOCK), "w", encoding="utf-8") as f:
            json.dump({"started_utc": started_utc, "pid": os.getpid()}, f)
    except OSError:
        pass


def _remove_lock(out_dir):
    try:
        os.remove(os.path.join(out_dir, RUN_LOCK))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(
        description="Daglig top-10 swing-scanner (rs_3m forfilter -> fuld swing-scoring)")
    ap.add_argument("--fraction", type=float, default=0.25,
                    help="andel af universet til kortlisten efter rs_3m (default 0.25)")
    ap.add_argument("--top", type=int, default=10, help="antal i slutlisten (default 10)")
    ap.add_argument("--source", default="ibkr", choices=["ibkr", "yfinance"])
    ap.add_argument("--period", default="1y")
    ap.add_argument("--delay", type=float, default=0.4,
                    help="sek. mellem fulde rapporter (FMP rate limit, default 0.4)")
    ap.add_argument("--prefilter-only", action="store_true",
                    help="kun rs_3m + kortliste, INGEN fulde rapporter (FMP ikke noedvendig)")
    ap.add_argument("--fresh", action="store_true",
                    help="ignorer i dags eksisterende scores-fil og start forfra")
    ap.add_argument("--out-dir", default=".", help="mappe til CSV-output (default cwd)")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    args = ap.parse_args()

    today = date.today().isoformat()
    scores_path = os.path.join(args.out_dir, f"swing_scores_{today}.csv")
    top_path = os.path.join(args.out_dir, f"swing_top{args.top}_{today}.csv")
    latest_json_path = os.path.join(args.out_dir, LATEST_JSON)

    # --prefilter-only: hurtig, FMP-fri, INGEN laas (det er ikke en lang koersel).
    if args.prefilter_only:
        rs = compute_rs(args.source, args.period)
        if not rs:
            sys.exit(1)
        shortlist, cutoff = shortlist_from_rs(rs, args.fraction)
        print(f"[top10] kortliste = top {args.fraction:.0%} efter rs_3m = {cutoff} navne "
              f"(af {len(rs)} med gyldig rs_3m).")
        print("[top10] --prefilter-only: stopper foer fuld scoring. Top 20 efter rs_3m:")
        for i, (t, v) in enumerate(sorted(rs.items(), key=lambda kv: kv[1], reverse=True)[:20], 1):
            print(f"   {i:>2}  {t:<6} rs_3m={v:+.1f}")
        return

    if not args.api_key:
        print("FEJL: fuld scoring kraever FMP_API_KEY (miljoe eller --api-key).")
        print("      Brug --prefilter-only hvis du kun vil se rs_3m-kortlisten.")
        sys.exit(1)

    if args.fresh and os.path.exists(scores_path):
        os.remove(scores_path)
        print(f"[top10] --fresh: slettede {scores_path}")

    # Laas hele den lange koersel (forfilter + scoring), saa UI viser 'koerer'
    # og backend ikke starter to samtidige koersler. Fjernes uanset udfald.
    _write_lock(args.out_dir)
    try:
        rs = compute_rs(args.source, args.period)
        if not rs:
            sys.exit(1)
        shortlist, cutoff = shortlist_from_rs(rs, args.fraction)
        print(f"[top10] kortliste = top {args.fraction:.0%} efter rs_3m = {cutoff} navne "
              f"(af {len(rs)} med gyldig rs_3m).")
        score_shortlist(shortlist, rs, args.api_key, args.source, args.period, args.delay, scores_path)
        emit_top(scores_path, args.top, top_path, latest_json_path, args.source)
    finally:
        _remove_lock(args.out_dir)


if __name__ == "__main__":
    main()
