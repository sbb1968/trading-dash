#!/usr/bin/env python3
"""
buyhold_top10.py - Buy-and-Hold Top-10-scanner.

Maal: de 10 hoejst-scorende koeb-og-hold-kandidater (final/SAMLET fra compute_buyhold)
fra et kurateret KVALITETS-univers.

Hvorfor anderledes end swing_top10/intradag_top10: se SPEC_buyhold_top10.md SS0.
Kort: ~6-8 FMP-kald pr. ticker + ~250 FMP/dag -> kun ~30-35 navne/dag. Derfor lille
kurateret univers, fuld scoring af ALLE navne (ingen ukalibreret pre-rank), PERSISTENT
cache paa tvaers af dage (kun manglende/foraeldede scores), kvote-budget pr. koersel,
ugentlig kadence. Rangering = den fulde gatede SAMLET (byte-identisk med rapporten).

Koer fra backend/ (FMP_API_KEY i miljoeet + IBKR oppe paa 7497, UDEN FOR HANDELSTID):
  $env:FMP_API_KEY="..."
  python buyhold_top10.py                  # en koersel (op til MAX_PER_RUN nye/foraeldede)
  python buyhold_top10.py --max 35         # override kvote-budget
  python buyhold_top10.py --universe-only  # kun TV-kvalitetsscreen (ingen FMP/IBKR)
  python buyhold_top10.py --fresh          # ignorer cache, score forfra (DYRT - mange dages kvote)
"""
from __future__ import annotations

import os
import sys
import csv
import json
import argparse
import asyncio
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from buyhold import compute_buyhold, _band_final   # GENBRUG - byte-identisk scoring + baand

_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_CSV   = os.path.join(_DIR, "buyhold_scores_cache.csv")    # PERSISTENT (ikke dato-stemplet)
LATEST_JSON = os.path.join(_DIR, "buyhold_top10_latest.json")
RUN_LOCK    = os.path.join(_DIR, "buyhold_top10_running.lock")

# --- Univers: tight TV-KVALITETSSCREEN (felter verificeret mod build_test_universe.py:
#     close/market_cap_basic/average_volume_30d_calc/exchange/type. De fundamentale
#     felter (return_on_equity/net_margin) er TV-standardnavne men ikke bekraeftede her
#     -> fetch_quality_universe falder graceful tilbage til den strukturelle screen,
#     hvis TV afviser et fundamentalt feltnavn (universet bliver aldrig tomt af den grund). ---
CAP_MIN      = 10_000_000_000    # >= $10B (store kvalitets-compoundere; haev/saenk efter smag)
PRICE_MIN    = 5.0
AVGVOL_MIN   = 500_000
ROE_MIN      = 8.0               # %, profitabel (TV: return_on_equity er typisk i %)
NETMARGIN_MIN= 0.0               # %, positiv bundlinje
UNIVERSE_MAX = 300               # haard kappe (screenen boer give ~150-250)
EXCHANGES    = ["NYSE", "NASDAQ", "AMEX"]

# --- Cache / kvote ---
STALE_DAYS   = 7                 # genscore hvis cache-post aeldre end dette (fundamentals = kvartalsvis)
MAX_PER_RUN  = 35                # kvote-budget pr. koersel (~250 FMP/dag / ~7 kald pr. ticker)
SCORE_DELAY  = 3.0               # sek mellem fulde scoringer (FMP-venlig + IBKR-historik-pacing)
TOP_N        = 15

CACHE_FIELDS = ["ticker", "final", "combined", "gate", "quality", "growth",
                "valuation", "trend", "oe_yield", "price", "company", "scored_utc", "error"]


def _now_pair():
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S"), datetime.datetime.now(datetime.timezone.utc).isoformat()


# === Trin 1: TV-kvalitetsunivers (sync; kald via to_thread fra async) =========
def fetch_quality_universe() -> list[dict]:
    """Tight kvalitetsscreen fra TradingViews screener (moenster fra build_test_universe).
    Fejler ALDRIG kalderen -> tom liste. Forsoeger fundamental screen (ROE+net-margin);
    afviser TV et fundamentalt feltnavn, falder den tilbage til den strukturelle screen
    (pris/cap/vol/exchange/type) saa universet ikke bliver tomt pga. et ukendt feltnavn."""
    try:
        from tradingview_screener import Query, col
    except ImportError:
        return []

    base = [
        col("close") > PRICE_MIN,
        col("market_cap_basic") > CAP_MIN,
        col("average_volume_30d_calc") > AVGVOL_MIN,
        col("exchange").isin(EXCHANGES),
        col("type").isin(["stock"]),
    ]
    fund = [col("return_on_equity") > ROE_MIN, col("net_margin") > NETMARGIN_MIN]
    sel_full = ["name", "close", "market_cap_basic", "return_on_equity", "net_margin",
                "gross_margin", "debt_to_equity", "price_earnings_ttm", "sector",
                "average_volume_30d_calc"]
    sel_base = ["name", "close", "market_cap_basic", "price_earnings_ttm", "sector",
                "average_volume_30d_calc"]

    def _run(where, sel):
        _, df = (Query().select(*sel).where(*where)
                 .order_by("market_cap_basic", ascending=False)
                 .limit(UNIVERSE_MAX).get_scanner_data())
        return df

    df = None
    try:
        df = _run(base + fund, sel_full)
    except Exception as e:
        print(f"[bh-top10] TV-query med fundamentale filtre fejlede ({type(e).__name__}: {e}); "
              f"falder tilbage til strukturel screen (pris/cap/vol).")
        try:
            df = _run(base, sel_base)
        except Exception as e2:
            print(f"[bh-top10] TV-query fejlede helt: {type(e2).__name__}: {e2}")
            return []
    if df is None or df.empty:
        return []

    def _f(v):
        try:
            v = float(v)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    rows = []
    for _, r in df.iterrows():
        full = r.get("ticker", "") or ""
        sym = full.split(":")[-1] if ":" in full else full
        if not sym or len(sym) > 5 or any(c in sym for c in (".", "/", "-")):
            continue
        rows.append({
            "ticker": sym, "name": r.get("name"),
            "cap": _f(r.get("market_cap_basic")), "roe": _f(r.get("return_on_equity")),
            "net_margin": _f(r.get("net_margin")), "pe": _f(r.get("price_earnings_ttm")),
            "sector": r.get("sector"),
        })
    return rows


# === Cache (persistent, keyed paa ticker) =====================================
def load_cache() -> dict:
    cache = {}
    if os.path.exists(CACHE_CSV):
        with open(CACHE_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = (r.get("ticker") or "").strip().upper()
                if t:
                    cache[t] = r
    return cache


def save_cache(cache: dict):
    """Skriv hele cachen (upsert sker i hukommelsen, gemmes her). Atomisk via .tmp."""
    tmp = CACHE_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CACHE_FIELDS)
        w.writeheader()
        for t in sorted(cache):
            row = {k: cache[t].get(k, "") for k in CACHE_FIELDS}
            w.writerow(row)
    os.replace(tmp, CACHE_CSV)


def _is_stale(row: dict | None, now: datetime.datetime) -> bool:
    if row is None:
        return True
    ts = (row.get("scored_utc") or "").strip()
    if not ts:
        return True
    try:
        age_days = (now - datetime.datetime.fromisoformat(ts)).total_seconds() / 86400.0
    except ValueError:
        return True
    return age_days > STALE_DAYS


def _row_from_core(t: str, core: dict) -> dict:
    c, L, tiles = core["c"], core["layers"], core["tiles"]
    oy = tiles.get("oe_yield")
    return {
        "ticker": t,
        "final": round(c["final"], 2),
        "combined": round(c["combined"], 2),
        "gate": round(c["gate"], 2),
        "quality": round(L["quality"]["lag_score"], 1),
        "growth": round(L["growth"]["lag_score"], 1),
        "valuation": round(L["valuation"]["lag_score"], 1),
        "trend": round(L["trend"]["lag_score"], 1),
        "oe_yield": round(oy * 100, 2) if oy is not None else "",
        "price": round(c["price"], 2) if c.get("price") is not None else "",
        "company": c.get("company_name") or "",
        "scored_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "error": "",
    }


# === Lock / progress (UI laeser begge) ========================================
def _write_lock(total: int):
    _, started_utc = _now_pair()
    try:
        with open(RUN_LOCK, "w", encoding="utf-8") as f:
            json.dump({"started_utc": started_utc, "pid": os.getpid(),
                       "done": 0, "total": total}, f)
    except OSError:
        pass


def _update_progress(done: int, total: int):
    try:
        with open(RUN_LOCK, encoding="utf-8") as f:
            d = json.load(f)
        d["done"], d["total"] = done, total
        with open(RUN_LOCK, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _remove_lock():
    try:
        os.remove(RUN_LOCK)
    except OSError:
        pass


# === Trin 2: scoring af manglende/foraeldede (kvote-budget, resumerbar) =======
async def score_universe(api_key: str, max_per_run: int, fresh: bool):
    uni = await asyncio.to_thread(fetch_quality_universe)
    if not uni:
        print("[bh-top10] tomt univers fra TradingView - afbryder.")
        return False
    print(f"[bh-top10] kvalitetsunivers: {len(uni)} navne.")

    cache = {} if fresh else load_cache()
    now = datetime.datetime.now(datetime.timezone.utc)
    uni_syms = [u["ticker"] for u in uni]
    todo = [t for t in uni_syms if _is_stale(cache.get(t), now)][:max_per_run]
    print(f"[bh-top10] {len(todo)} navne at score i denne koersel "
          f"(kvote-budget {max_per_run}; {len(uni)-len(todo)} friske/udskudt).")
    _write_lock(len(todo))

    # Egen IBKR-klient (subprocess), som buyhold.py CLI. ib=None -> Lag 4 udelades paent.
    ib = None
    conn = None
    try:
        from ibkr_connect import IBKRConnection
        conn = IBKRConnection(paper_trading=True)
        ib = conn.ib if await conn.connect() else None
        if ib is None:
            print("[bh-top10] ADVARSEL: IBKR ikke forbundet - trend-laget (15%) udelades.")
        for i, t in enumerate(todo, 1):
            try:
                core = await compute_buyhold(ib, t, api_key)
                row = _row_from_core(t, core)
                tag = f"SAMLET {row['final']:+.1f}"
            except Exception as e:
                row = {**{k: "" for k in CACHE_FIELDS}, "ticker": t,
                       "scored_utc": now.isoformat(), "error": f"{type(e).__name__}: {e}"}
                tag = f"FEJL: {row['error']}"
            cache[t] = row
            save_cache(cache)              # skriv hele cachen straks -> RESUMERBAR
            _update_progress(i, len(todo))
            print(f"  [{i}/{len(todo)}] {t:<6} {tag}")
            if SCORE_DELAY:
                await asyncio.sleep(SCORE_DELAY)
    finally:
        if conn is not None:
            conn.disconnect()
    emit_top(uni, cache)
    return True


# === Trin 3: top-10 af cachen (kun navne i NUVAERENDE univers) ================
def emit_top(uni: list[dict], cache: dict):
    info = {u["ticker"]: u for u in uni}
    rows = []
    for t in info:
        c = cache.get(t)
        if not c or (c.get("error") or "").strip():
            continue
        try:
            c["_final"] = float(c["final"])
        except (TypeError, ValueError):
            continue
        rows.append(c)
    rows.sort(key=lambda r: r["_final"], reverse=True)
    top = rows[:TOP_N]

    gen_local, gen_utc = _now_pair()
    payload = {
        "generated_local": gen_local, "generated_utc": gen_utc,
        "source": "FMP+IBKR", "universe_size": len(uni),
        "scored_cached": len(rows), "count": len(top),
        "rows": [{
            "rank": i, "ticker": r["ticker"],
            "company": r.get("company") or info[r["ticker"]].get("name") or "",
            "price": r.get("price", ""),
            "final": round(r["_final"], 2), "band": _band_final(r["_final"]),
            "gate": r.get("gate", ""), "quality": r.get("quality", ""),
            "growth": r.get("growth", ""), "valuation": r.get("valuation", ""),
            "trend": r.get("trend", ""), "oe_yield": r.get("oe_yield", ""),
        } for i, r in enumerate(top, 1)],
    }
    with open(LATEST_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n=== BUY-AND-HOLD TOP {TOP_N} (SAMLET) ===  "
          f"[{len(rows)}/{len(uni)} scoret i cachen]")
    for r in payload["rows"]:
        print(f"  {r['rank']:>2}  {r['ticker']:<6} SAMLET {float(r['final']):>+7.2f}  "
              f"[{r['band']}]  Kv {r['quality']} / Va {r['growth']} / "
              f"Vae {r['valuation']} / Tr {r['trend']}")
    print(f"[bh-top10] JSON til UI: {LATEST_JSON}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Buy-and-Hold Top-10 (kurateret kvalitetsunivers)")
    ap.add_argument("--max", type=int, default=MAX_PER_RUN, help=f"kvote-budget pr. koersel (default {MAX_PER_RUN})")
    ap.add_argument("--universe-only", action="store_true", help="kun TV-kvalitetsscreen (ingen FMP/IBKR)")
    ap.add_argument("--fresh", action="store_true", help="ignorer cache, score forfra (DYRT)")
    ap.add_argument("--api-key", default=os.environ.get("FMP_API_KEY", ""))
    a = ap.parse_args()

    if a.universe_only:
        uni = fetch_quality_universe()
        print(f"[bh-top10] {len(uni)} navne i kvalitetsuniverset:")
        for u in uni:
            print(f"   {u['ticker']:<6} cap={u['cap']} roe={u['roe']} pe={u['pe']} {u.get('sector')}")
        return

    if not a.api_key:
        print("FEJL: fuld scoring kraever FMP_API_KEY (miljoe eller --api-key).")
        print("      Brug --universe-only hvis du kun vil se kvalitetsscreenen.")
        sys.exit(1)

    _write_lock(0)   # vis 'koerer' straks (TV-query foer score_universe saetter rigtigt total)
    try:
        ok = asyncio.run(score_universe(a.api_key, a.max, a.fresh))
        sys.exit(0 if ok else 1)
    finally:
        _remove_lock()


if __name__ == "__main__":
    main()
