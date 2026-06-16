#!/usr/bin/env python3
"""
fade_characterize.py — måler FADE-præmissen + overlap mod K1/washout (daily-bar proxy)
═══════════════════════════════════════════════════════════════════════════════════════
Før en linje fade-strategikode: svarer på de to spørgsmål der afgør om fade er en ægte
ny strategi eller K1 i forklædning. Læs-only på det daglige cache (køber/henter intet).

  PRÆMISSEN — bouncer ekstreme intraday-tabere, eller fortsætter de ned?
    Det er fades eksistensberettigelse. Hvis kapitulation bare fortsætter ned (knive der
    falder videre), har fade ingen edge. Måles pr. ekstrem ned-dag:
      • range-recovery = (close − low)/(high − low) — lukkede den højt i sit interval
        (bounce) eller på sit low (ingen bounce)?
      • bounce-off-low = (close − low)/low — IDEALISERET fade-fangst (køb exakt low, sælg
        close). ØVRE GRÆNSE (man rammer ikke det exakte low) — viser kun om der ER en
        intraday-bounce at fange.
      • næste-dags-afkast — markerer kapitulationen en vending (grøn næste dag) eller
        fortsættelse (rød)? Sekundært: fade er intraday, men giver multi-dags-kontekst.

  OVERLAP mod K1/washout — er fade distinkt, eller K1's oversold-bounce i forklædning?
    Den SKARPE test: fade dropper bevidst K1/washouts trend-filter. K1 tager kun
    oversold-bounces i en OPTREND (over HTF-EMA). Så vi splitter fade-kandidat-dagene på
    trend (over/under MA50):
      • UNDER MA50 (nedtrend, faldende kniv) = PUR fade — K1 rører dem aldrig.
      • OVER MA50 (optrend, skarpt dyk)      = K1-OVERLAP-zonen — K1 kan også tage dem.
    Er de fleste fade-dage UNDER MA50, er fade distinkt. Er de fleste OVER, overlapper
    den K1 tungt. Vi måler det — antager det ikke.

VIGTIGE FORBEHOLD (ærlige, så vi ikke overfortolker):
  • DAILY-BAR PROXY: "bounce" = close vs low fortæller IKKE hvornår low'et faldt, eller
    om en fade-entry nær low'et er opnåelig intraday. Den præcise intraday entry/exit-edge
    kræver 1-min bars (næste skridt HVIS præmissen overlever) — samme pipeline som K2/washout.
  • UNIVERS-BIAS: det daglige cache blev screenet generelt og kan være let momentum-biased
    (undertælle rene styrtdyk). Første læsning; et taber-inklusivt udtræk er næste skridt
    hvis lovende.

Køres fra backend/ på algoserveren (hvor daily_cache ligger):
    python fade_characterize.py
    python fade_characterize.py --price-floor 2 --thresholds 8,10,12,15
    python fade_characterize.py --cache-dir daily_cache --ma-period 50

Placering: C:\\projects\\trading_dash\\backend\\fade_characterize.py
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import date as date_cls
from pathlib import Path

OUTPUT_DIRNAME = "fade_characterize_output"
DEFAULT_CACHE  = "daily_cache"
MAX_DROP_PCT   = 60.0    # spring split/merger-artefakter over (drop dybere end dette)


def find_col(headers, *names):
    """Find kolonne case-insensitivt (samme mønster som resten af kodebasen)."""
    low = {h.lower().strip(): h for h in headers}
    for n in names:
        if n in low:
            return low[n]
    return None


def parse_date(s: str):
    s = s.strip().replace("T", " ")
    return date_cls.fromisoformat(s[:10])


def load_ticker(path: Path):
    """Læs én ticker-CSV → (ticker, [bars sorteret efter dato]). Auto-detekterer kolonner."""
    ticker = path.stem.split("_")[0].upper()
    with path.open(newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return ticker, []
        c_date = find_col(headers, "date", "datetime", "timestamp", "time")
        c_o = find_col(headers, "open", "o")
        c_h = find_col(headers, "high", "h")
        c_l = find_col(headers, "low", "l")
        c_c = find_col(headers, "close", "c", "adj close", "adjclose")
        c_v = find_col(headers, "volume", "vol", "v")
        if not all([c_date, c_o, c_h, c_l, c_c]):
            return ticker, []   # ukendt format — kan ikke bruge
        idx = {h: i for i, h in enumerate(headers)}
        bars = []
        for row in reader:
            if len(row) < len(headers):
                continue
            try:
                d = parse_date(row[idx[c_date]])
                o = float(row[idx[c_o]]); h = float(row[idx[c_h]])
                lo = float(row[idx[c_l]]); c = float(row[idx[c_c]])
                v = float(row[idx[c_v]]) if c_v else 0.0
            except (ValueError, KeyError, IndexError):
                continue
            if h <= 0 or lo <= 0 or c <= 0:
                continue
            bars.append((d, o, h, lo, c, v))
    bars.sort(key=lambda b: b[0])
    return ticker, bars


def sma(values, period, i):
    """SMA af close over [i-period, i-1] (kun fortid — point-in-time)."""
    if i < period:
        return None
    window = values[i - period:i]
    return sum(window) / period


def pct(numer, denom):
    return 100.0 * numer / denom if denom else 0.0


def main():
    ap = argparse.ArgumentParser(description="Fade-præmis + overlap-karakterisering (læs-only)")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE,
                    help=f"mappe med daglige ticker-CSV'er (default {DEFAULT_CACHE})")
    ap.add_argument("--price-floor", type=float, default=5.0,
                    help="min. prev_close (default 5.0; prøv 2.0 for flere small-caps)")
    ap.add_argument("--thresholds", default="8,10,12,15",
                    help="fald-tærskler i %% (low vs prev_close), komma-sep")
    ap.add_argument("--ma-period", type=int, default=50,
                    help="MA-periode til trend-split (default 50)")
    args = ap.parse_args()

    thresholds = sorted(float(t) for t in args.thresholds.split(",") if t.strip())
    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = Path.cwd() / cache_dir

    out_dir = Path.cwd() / OUTPUT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 80)
    emit("  FADE-KARAKTERISERING — præmis (bounce) + overlap (trend-split) · daily-bar proxy")
    emit("=" * 80)
    emit(f"Cache: {cache_dir}   prisbund: ${args.price_floor:.2f}   "
         f"MA: {args.ma_period}   tærskler: {', '.join(f'-{t:.0f}%' for t in thresholds)}")

    if not cache_dir.exists():
        emit(f"\n❌ Cache-mappen findes ikke: {cache_dir}")
        emit("   Angiv den rigtige med --cache-dir (fx 'daily_cache' eller en absolut sti).")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    files = sorted(cache_dir.glob("*.csv"))
    emit(f"Fandt {len(files)} CSV-filer i cachen.")
    if not files:
        emit("❌ Ingen CSV-filer. Tjek --cache-dir.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 1

    # ── Indlæs alt + saml fade-kandidat-dage ──
    # Pr. tærskel: liste af event-dicts. Vi samler ALLE events ved den LAVESTE tærskel
    # og filtrerer op pr. tærskel (en -15%-dag er også en -8%-dag).
    events = []   # hver: dict(ticker, date, drop, range_rec, bounce_off_low, next_ret, above_ma, has_next)
    min_thr = min(thresholds)
    loaded = 0
    fmt_shown = False
    skipped_fmt = 0

    for path in files:
        ticker, bars = load_ticker(path)
        if not bars:
            skipped_fmt += 1
            continue
        loaded += 1
        if not fmt_shown:
            with path.open(newline="") as f:
                hdr = next(csv.reader(f))
            emit(f"Format-eksempel ({ticker}): kolonner = {hdr}  ·  {len(bars)} bars "
                 f"[{bars[0][0]} .. {bars[-1][0]}]")
            fmt_shown = True

        closes = [b[4] for b in bars]
        for i in range(1, len(bars)):
            d, o, h, lo, c, v = bars[i]
            prev_close = bars[i - 1][4]
            if prev_close <= 0 or prev_close < args.price_floor:
                continue
            drop = pct(lo - prev_close, prev_close)   # negativ = faldt under prev_close
            if drop > -min_thr:
                continue
            if drop < -MAX_DROP_PCT:
                continue   # outlier/split
            rng = h - lo
            range_rec = (c - lo) / rng if rng > 0 else 0.0
            bounce_off_low = pct(c - lo, lo)
            ma = sma(closes, args.ma_period, i)
            above_ma = (c > ma) if ma is not None else None
            has_next = i + 1 < len(bars)
            next_ret = pct(bars[i + 1][4] - c, c) if has_next else None
            events.append({
                "ticker": ticker, "date": d, "drop": drop,
                "range_rec": range_rec, "bounce_off_low": bounce_off_low,
                "next_ret": next_ret, "above_ma": above_ma, "has_next": has_next,
            })

    emit(f"Indlæste {loaded} tickere"
         + (f" ({skipped_fmt} sprunget over — ukendt format/tomme)" if skipped_fmt else "")
         + f". Fade-kandidat-dage ved ≥-{min_thr:.0f}%: {len(events)}.")
    emit("")

    if not events:
        emit("Ingen fade-kandidat-dage fundet. Sænk tærsklen eller prisbunden, eller tjek cachen.")
        (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        return 0

    # ── Rapportér pr. tærskel ──
    def block(evts, label):
        n = len(evts)
        if n == 0:
            emit(f"  {label}: 0 dage")
            return
        tickers = len({e['ticker'] for e in evts})
        dates = sorted(e['date'] for e in evts)
        rr = [e['range_rec'] for e in evts]
        bl = [e['bounce_off_low'] for e in evts]
        upper_half = sum(1 for x in rr if x > 0.5)
        with_ma = [e for e in evts if e['above_ma'] is not None]
        above = sum(1 for e in with_ma if e['above_ma'])
        below = len(with_ma) - above
        nexts = [e['next_ret'] for e in evts if e['has_next']]
        green_next = sum(1 for x in nexts if x > 0)

        emit(f"  {label}:  {n} dage · {tickers} navne · [{dates[0]} .. {dates[-1]}]")
        emit(f"     PRÆMIS  median range-recovery: {statistics.median(rr):.2f}  "
             f"(lukkede i øvre halvdel af interval: {pct(upper_half, n):.0f}%)")
        emit(f"             median bounce-off-low (idealiseret ØVRE grænse): "
             f"{statistics.median(bl):.1f}%")
        if nexts:
            emit(f"             næste dag grøn: {pct(green_next, len(nexts)):.0f}%  ·  "
                 f"median næste-dags-afkast: {statistics.median(nexts):+.2f}%")
        # Overlap / trend-split
        if with_ma:
            emit(f"     OVERLAP trend-split: UNDER MA{args.ma_period} (pur fade) "
                 f"{pct(below, len(with_ma)):.0f}%  ·  OVER MA (K1-overlap-zone) "
                 f"{pct(above, len(with_ma)):.0f}%")
            # Bounce splittet på trend — hvor lever edgen?
            rr_below = [e['range_rec'] for e in with_ma if not e['above_ma']]
            rr_above = [e['range_rec'] for e in with_ma if e['above_ma']]
            if rr_below and rr_above:
                emit(f"             range-recovery UNDER MA: {statistics.median(rr_below):.2f}  ·  "
                     f"OVER MA: {statistics.median(rr_above):.2f}")

    for t in thresholds:
        sub = [e for e in events if e['drop'] <= -t]
        block(sub, f"≥ -{t:.0f}% intraday (low vs forrige luk)")
        emit("")

    emit("─" * 80)
    emit("  SÅDAN LÆSES DET")
    emit("─" * 80)
    emit("  PRÆMIS: høj range-recovery + grøn-næste-dag = kapitulation bouncer (fade har")
    emit("    en bevægelse at fange). Lav range-recovery (~lukker på low) = kniven faldt")
    emit("    videre, ingen edge. bounce-off-low er en IDEALISERET ØVRE grænse — træk en")
    emit("    realisme-rabat fra (man rammer ikke det exakte low).")
    emit("  OVERLAP: er fade-dagene mest UNDER MA = distinkt fra K1 (som kræver optrend).")
    emit("    Mest OVER MA = overlapper K1's oversold-bounce → fade er mindre ny end håbet.")
    emit("  NÆSTE: bekræftes præmissen, kræver den præcise edge 1-min bars (entry nær low,")
    emit("    exit på bounce) + et taber-inklusivt univers — denne måling er daily-bar-proxy.")
    (out_dir / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())