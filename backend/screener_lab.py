#!/usr/bin/env python3
"""
screener_lab.py — Fase A: karakterisér puljedefinitioner fra daglige bars
═════════════════════════════════════════════════════════════════════════════
Det fælles, look-ahead-frie grundlag fra download_daily_universe.py (daily_cache/)
bruges her til at SAMMENLIGNE forskellige screener/pulje-definitioner UDEN at
backteste — så vi kan snævre ind til nogle få lovende, før vi bruger tung
1-min-download + backtest (Fase B) på dem.

GENERALISERER reconstruct_midcap_universe.py's udvælgelse til noget vi kan sweepe:
i stedet for én fast vol-proxy + top-N, definerer vi flere puljer langs de akser
vi blev enige om — dagligt ATR%, prisbånd, snitvolumen, relativ volumen, plus en
multi-timeframe momentum-alignment (Sørens "alle tre grønne") — og måler hvad hver
definition giver.

═══ POINT-IN-TIME-DISCIPLIN (afgørende) ═══
Hver udvælgelses-metrik for handelsdag d beregnes KUN fra bars til og med d-1
(gårsdagens luk). Intet kigger på dag d's egen bevægelse. Det er det der gør
resultatet implementerbart live — og det er den fælde reconstruct_midcap_universe
allerede undgår. Vi gør det samme.

═══ HVAD FASE A ER / IKKE ER ═══
DESKRIPTIV: hvad INDEHOLDER en pulje (navne/dag, distinkte, SEKTORFORDELING,
overlap med det validerede 99-navns-univers). Den siger IKKE om washout TJENER
på puljen — det er Fase B (1-min backtest, sektor-tagget). Sektor-sweepen her er
SAMMENSÆTNING, ikke edge.

Metrikker pr. ticker-dag (alle frem til d-1):
  • pris            = gårsdagens luk (prisbånds-filter)
  • dagligt ATR%    = ATR(N) / gårsdagens luk · 100   (volatilitet)
  • snitvolumen     = middel volumen over N dage      (likviditetsgulv)
  • relativ volumen = gårsdagens volumen / snitvolumen (retnings-agnostisk aktivitet)
  • chg% 1d/1u/1m   = afkast frem til i går over 1/5/21 handelsdage
  • grønne (0-3)    = hvor mange af de tre chg% er positive (momentum-alignment)

Input: daily_cache/ (fra download_daily_universe.py) + TV-CSV'en (for sektor).
Valgfrit: de validerede univers-JSON'er (--validated-universe) til overlap —
BEMÆRK de er gitignored, så de ligger måske ikke på algoserveren; kopiér dem
derover hvis du vil have overlap-tallet. Uden dem springes overlap bare over.

Rent offline. Kun stdlib. Ingen IBKR.

Brug (fra backend/, EFTER download_daily_universe er færdig):
    python screener_lab.py --meta "Claude_screener_general_2026-06-09_29336.csv"
    python screener_lab.py --meta tv.csv --validated-universe "historical_universe_midcap_*.json"
    python screener_lab.py --meta tv.csv --emit baseline    # skriv pulje-JSON til Fase B

Placering: C:\\projects\\trading_dash\\backend\\screener_lab.py
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import defaultdict
from datetime import date as date_cls
from pathlib import Path

CACHE_DIRNAME = "daily_cache"
OUTPUT_DIRNAME = "screener_lab_output"

# Metrik-vinduer (handelsdage)
ATR_WINDOW = 14
VOL_WINDOW = 20
WEEK_DAYS = 5
MONTH_DAYS = 21
MIN_HISTORY = MONTH_DAYS + 2     # nok bagud til chg% 1m + ATR

# ── Kandidat-puljedefinitioner — REDIGÉR/UDVID frit ──────────────────────────
# rank_by ∈ {"rvol","chg1d","chg1w","chg1m","atr","green"}
# momentum_min_green: krav om antal grønne chg% (0 = intet krav)
POOL_DEFS = [
    dict(name="baseline",        price=(5, 50),  atr_pct_min=3.0, avg_vol_min=500_000,
         momentum_min_green=0, rank_by="rvol",  top_n=25),
    dict(name="hoej_vol",        price=(5, 50),  atr_pct_min=5.0, avg_vol_min=500_000,
         momentum_min_green=0, rank_by="rvol",  top_n=25),
    dict(name="momentum_alignet", price=(5, 50), atr_pct_min=3.0, avg_vol_min=500_000,
         momentum_min_green=3, rank_by="green", top_n=25),
    dict(name="momentum_blod",   price=(5, 50),  atr_pct_min=3.0, avg_vol_min=500_000,
         momentum_min_green=2, rank_by="chg1w", top_n=25),
    dict(name="bredt_prisbaand", price=(5, 100), atr_pct_min=3.0, avg_vol_min=500_000,
         momentum_min_green=0, rank_by="rvol",  top_n=25),
    dict(name="rang_chg1d",      price=(5, 50),  atr_pct_min=3.0, avg_vol_min=500_000,
         momentum_min_green=0, rank_by="chg1d", top_n=25),
]


# ── Daglige bars + metadata ──────────────────────────────────────────────────
def load_daily(cache_dir: Path, ticker: str):
    """Læs daglige bars for ticker fra daily_cache. Sorteret stigende. [] hvis ingen."""
    for p in sorted(cache_dir.glob(f"{ticker}_*_daily.csv")):
        try:
            if p.stat().st_size < 100:
                continue
        except OSError:
            continue
        rows = []
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append((date_cls.fromisoformat(r["date"]), float(r["open"]),
                                 float(r["high"]), float(r["low"]), float(r["close"]),
                                 float(r["volume"])))
                except (ValueError, KeyError):
                    continue
        rows.sort(key=lambda x: x[0])
        if rows:
            return rows
    return []


def _detect_delim(line: str) -> str:
    return ";" if line.count(";") > line.count(",") else ","


def load_meta(path: Path):
    """{symbol: {'sector':..., 'mktcap':float|None, 'exchange':...}} fra TV-CSV."""
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}
    delim = _detect_delim(lines[0])
    rdr = csv.reader(lines, delimiter=delim)
    header = [h.strip().lower() for h in next(rdr)]

    def idx(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_sym = idx("symbol", "ticker")
    i_sec = idx("sector")
    i_cap = idx("market capitalization", "market cap")
    i_exch = idx("exchange")
    out = {}
    for row in rdr:
        if i_sym is None or i_sym >= len(row):
            continue
        sym = row[i_sym].strip().upper().strip('"')
        if not sym:
            continue
        cap = None
        if i_cap is not None and i_cap < len(row):
            try:
                cap = float(row[i_cap])
            except ValueError:
                cap = None
        out[sym] = {
            "sector": (row[i_sec].strip() if i_sec is not None and i_sec < len(row) else "") or "Ukendt",
            "mktcap": cap,
            "exchange": row[i_exch].strip() if i_exch is not None and i_exch < len(row) else "",
        }
    return out


# ── Point-in-time metrikker (alt frem til d-1) ───────────────────────────────
def _true_range(h, l, prev_close):
    return max(h - l, abs(h - prev_close), abs(l - prev_close))


def per_day_metrics(bars):
    """For hver handelsdag d (= bars[i]) beregn udvælgelses-metrikker fra bars[:i]
    (til og med d-1). Returnér {date_d: dict}. Springer dage uden nok historik."""
    out = {}
    n = len(bars)
    for i in range(MIN_HISTORY, n):
        # alt herunder bruger KUN indeks < i (gårsdag og tidligere) → ingen look-ahead
        prior_close = bars[i - 1][4]
        if prior_close <= 0:
            continue
        # dagligt ATR% over de seneste ATR_WINDOW dage frem til i-1
        trs = []
        for j in range(i - ATR_WINDOW, i):
            if j <= 0:
                continue
            trs.append(_true_range(bars[j][2], bars[j][3], bars[j - 1][4]))
        atr_pct = (sum(trs) / len(trs) / prior_close * 100.0) if trs else 0.0
        # snitvolumen over de seneste VOL_WINDOW dage frem til i-1
        vols = [bars[j][5] for j in range(i - VOL_WINDOW, i)]
        avg_vol = sum(vols) / len(vols) if vols else 0.0
        rvol = (bars[i - 1][5] / avg_vol) if avg_vol > 0 else 0.0
        # change% frem til i-1
        def chg(back):
            k = i - 1 - back
            if k < 0 or bars[k][4] <= 0:
                return None
            return (bars[i - 1][4] - bars[k][4]) / bars[k][4] * 100.0
        c1d, c1w, c1m = chg(1), chg(WEEK_DAYS), chg(MONTH_DAYS)
        green = sum(1 for c in (c1d, c1w, c1m) if c is not None and c > 0)
        out[bars[i][0]] = {
            "price": prior_close, "atr_pct": atr_pct, "avg_vol": avg_vol, "rvol": rvol,
            "chg1d": c1d or 0.0, "chg1w": c1w or 0.0, "chg1m": c1m or 0.0, "green": green,
        }
    return out


# ── Anvend en puljedefinition → {dag: [tickere]} ─────────────────────────────
RANK_KEY = {"rvol": "rvol", "chg1d": "chg1d", "chg1w": "chg1w",
            "chg1m": "chg1m", "atr": "atr_pct", "green": "green"}


def build_pool(metrics_by_ticker, defn):
    pmin, pmax = defn["price"]
    rk = RANK_KEY[defn["rank_by"]]
    by_day = defaultdict(list)
    for ticker, mbd in metrics_by_ticker.items():
        for d, m in mbd.items():
            if not (pmin <= m["price"] <= pmax):
                continue
            if m["atr_pct"] < defn["atr_pct_min"]:
                continue
            if m["avg_vol"] < defn["avg_vol_min"]:
                continue
            if m["green"] < defn["momentum_min_green"]:
                continue
            by_day[d].append((m[rk], ticker))
    pool = {}
    for d, cands in by_day.items():
        cands.sort(key=lambda x: (-x[0], x[1]))
        pool[d] = [t for _, t in cands[:defn["top_n"]]]
    return pool


# ── Karakterisering ──────────────────────────────────────────────────────────
def characterize(pool, sector_map, validated=None):
    days = sorted(pool)
    name_days = sum(len(pool[d]) for d in days)
    distinct = set()
    sector_count = defaultdict(int)
    for d in days:
        for t in pool[d]:
            distinct.add(t)
            sector_count[sector_map.get(t, {}).get("sector", "Ukendt")] += 1
    res = {
        "days": len(days),
        "avg_per_day": name_days / len(days) if days else 0.0,
        "distinct": len(distinct),
        "name_days": name_days,
        "sectors": dict(sorted(sector_count.items(), key=lambda x: -x[1])),
    }
    if validated is not None:
        # overlap: andel af pulje-navn-dage der OGSÅ er i det validerede univers, og omvendt
        v_pairs = set()
        for d, ticks in validated.items():
            try:
                dd = date_cls.fromisoformat(d) if isinstance(d, str) else d
            except ValueError:
                continue
            for t in ticks:
                v_pairs.add((dd, t.upper()))
        p_pairs = set((d, t) for d in days for t in pool[d])
        inter = p_pairs & v_pairs
        res["overlap_pool_in_validated"] = (len(inter) / len(p_pairs)) if p_pairs else 0.0
        res["overlap_validated_in_pool"] = (len(inter) / len(v_pairs)) if v_pairs else 0.0
        res["validated_name_days"] = len(v_pairs)
    return res


def load_validated(pattern):
    merged = {}
    for fp in sorted(glob.glob(pattern)):
        try:
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            continue
        for d, ticks in data.items():
            merged.setdefault(d, [])
            merged[d].extend(ticks)
    return merged or None


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Fase A: karakterisér puljedefinitioner (deskriptivt)")
    ap.add_argument("--cache-dir", default=CACHE_DIRNAME)
    ap.add_argument("--meta", required=True, help="TV-CSV med sektor-metadata")
    ap.add_argument("--validated-universe", default=None,
                    help="glob til validerede univers-JSON'er (valgfrit, til overlap)")
    ap.add_argument("--emit", default=None, help="skriv pulje-JSON for denne definition (til Fase B)")
    args = ap.parse_args()

    cache_dir = Path.cwd() / args.cache_dir if not Path(args.cache_dir).is_absolute() else Path(args.cache_dir)
    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    if not cache_dir.exists():
        emit(f"FEJL: {cache_dir} findes ikke — kør download_daily_universe.py først.")
        return 1
    meta_path = Path(args.meta) if Path(args.meta).is_absolute() else cache_dir.parent / args.meta
    if not meta_path.exists():
        meta_path = Path.cwd() / args.meta
    sector_map = load_meta(meta_path) if meta_path.exists() else {}

    # Indlæs alle cachede tickers og beregn metrikker
    tickers = sorted({Path(fp).name.split("_")[0].upper()
                      for fp in glob.glob(str(cache_dir / "*_daily.csv"))})
    emit("=" * 78)
    emit("  SCREENER-LAB — FASE A (deskriptiv puljekarakterisering)")
    emit("=" * 78)
    emit(f"Cache: {cache_dir}   tickers i cache: {len(tickers)}   "
         f"metadata: {'ja' if sector_map else 'NEJ (sektor=Ukendt)'}")
    emit(f"Metrikker frem til d-1: ATR%({ATR_WINDOW}), snitvol({VOL_WINDOW}), rvol, "
         f"chg% 1d/1u/1m, grønne(0-3).  Point-in-time — intet look-ahead.")
    emit("")

    metrics_by_ticker = {}
    skipped = 0
    for t in tickers:
        bars = load_daily(cache_dir, t)
        if len(bars) < MIN_HISTORY + 1:
            skipped += 1
            continue
        m = per_day_metrics(bars)
        if m:
            metrics_by_ticker[t] = m
    emit(f"Tickers med nok historik: {len(metrics_by_ticker)}   (sprunget over: {skipped})")

    validated = load_validated(args.validated_universe) if args.validated_universe else None
    if args.validated_universe:
        emit(f"Valideret univers til overlap: {'indlæst' if validated else 'IKKE fundet — overlap springes over'}")
    emit("")

    # Karakterisér hver definition
    emit("─" * 78)
    emit("  SAMMENLIGNING AF PULJEDEFINITIONER")
    emit("─" * 78)
    header = f"  {'definition':<18}{'navne/dag':>10}{'distinkte':>11}{'navn-dage':>11}"
    if validated:
        header += f"{'pulje∩val%':>12}{'val∩pulje%':>12}"
    emit(header)
    results = {}
    for defn in POOL_DEFS:
        pool = build_pool(metrics_by_ticker, defn)
        r = characterize(pool, sector_map, validated)
        results[defn["name"]] = (defn, pool, r)
        row = (f"  {defn['name']:<18}{r['avg_per_day']:>10.1f}{r['distinct']:>11}"
               f"{r['name_days']:>11}")
        if validated:
            row += f"{r.get('overlap_pool_in_validated',0)*100:>11.0f}%{r.get('overlap_validated_in_pool',0)*100:>11.0f}%"
        emit(row)
    emit("")

    # Sektorfordeling pr. definition
    emit("─" * 78)
    emit("  SEKTORFORDELING (andel af navn-dage) — SAMMENSÆTNING, ikke edge")
    emit("─" * 78)
    for name, (defn, pool, r) in results.items():
        emit(f"  [{name}]  ({r['name_days']} navn-dage, {r['distinct']} distinkte)")
        tot = r["name_days"] or 1
        for sec, cnt in list(r["sectors"].items())[:8]:
            emit(f"     {sec:<28}{cnt/tot*100:>5.1f}%  ({cnt})")
        if len(r["sectors"]) > 8:
            emit(f"     … og {len(r['sectors'])-8} flere sektorer")
        emit("")

    # Valgfrit: skriv pulje-JSON til Fase B
    if args.emit:
        if args.emit not in results:
            emit(f"--emit: ukendt definition '{args.emit}'. Vælg én af: {', '.join(results)}")
        else:
            _, pool, _ = results[args.emit]
            payload = {d.isoformat(): ticks for d, ticks in sorted(pool.items())}
            ep = cache_dir.parent / f"pool_{args.emit}.json"
            ep.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            emit(f"Skrev pulje-JSON: {ep}  ({len(payload)} dage)")
            emit(f"  → Fase B: download_midcap_bars.py for navnene, så washout_reclaim_backtest.")
    emit("")

    emit("─" * 78)
    emit("  LÆSNING")
    emit("─" * 78)
    emit("  Fase A snævrer ind: vælg 2-3 definitioner der ser fornuftige ud (rimelig")
    emit("  pulje-størrelse, ikke for få distinkte, og — hvis overlap vises — nok lighed")
    emit("  med det validerede univers til at vi tør stole på dem). Sektorfordelingen her")
    emit("  er hvad puljen INDEHOLDER. Om edgen klumper sig sektorvist afgøres i Fase B.")
    emit("")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    emit(f"Fil: {out_dir / 'summary.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())