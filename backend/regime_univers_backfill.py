#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regime_univers_backfill.py — Trin 1 i aktie-harvesten til regime-motor v2.
════════════════════════════════════════════════════════════════════════════

Bygger point-in-time-universlister bagud (2022 →), saa aktie-metrikkerne kan
beregnes over flere aar i stedet for 43 dage.

HVORFOR DETTE FOERST: det er billigt (kun DAGLIGE barer, ét kald pr. kandidat)
og det giver det tal vi mangler for at prissaette naeste trin — hvor mange
UNIKKE navne der skal hentes intradag-barer for. Uden det tal er valget mellem
5-min og 15-min et gaet.

Udvaelgelses-logikken (gainer_score, TOP_N, pris/volumen-filtre) genbruges
UAENDRET fra build_historical_universe.py, saa universerne er defineret praecis
som de eksisterende 43 dage. Kun kandidat-saettet og perioden er udvidet.

OM KANDIDAT-SAETTET: listen er samlet i 2026 og anvendes bagud. Det er ikke et
survivorship-problem i denne sammenhaeng — vi maaler markedets KARAKTER
(spredning, follow-through, autokorrelation), ikke afkast, og percentil-
transformationen normaliserer mod metrikkens egen historik. Det der derimod ER
en reel maalefejl, er hvis daekningen skrumper bagud: faerre navne pr. dag
betyder at fx dispersion maales paa et mindre tvaersnit. Derfor rapporterer
scriptet navne-pr-dag over tid — se afsnittet DAEKNING i outputtet.

Koeres (kraever TWS paa 7497):
    python regime_univers_backfill.py --start 2022-01-01 --end 2026-07-31

Output: regime_v2_output/regime_univers_{start}_{end}.json
        regime_v2_output/regime_univers_daekning.md
        regime_v2_output/_daily_cache/{TICKER}.json   (resumérbarhed)
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock
import pytz

import build_historical_universe as BHU

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ET = pytz.timezone("America/New_York")
BACKEND = Path(__file__).resolve().parent
BAR_CACHE = BACKEND / "bar_cache"
OUT_DIR = BACKEND / "regime_v2_output"
CACHE_DIR = OUT_DIR / "_daily_cache"

PORT, CLIENT_ID, TIMEOUT = 7497, 76, 20
PACING_SLEEP = 2.0          # sekunder mellem historik-kald
FETCH_TIMEOUT = 90

# Ikke-aktier der ligger i bar_cache og skal ud af kandidat-saettet.
IKKE_AKTIER = {"EURUSD", "GBPUSD", "USDJPY", "AUDJPY", "NIKKEI", "HSI", "A50"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("regime_univers")
logging.getLogger("ib_async").setLevel(logging.ERROR)


# ═══════════════════════════════════════════════════════════════════
def kandidatsaet() -> list[str]:
    """bar_cache-tickere + build_historical_universe's liste + journalen."""
    fra_cache = {Path(fp).name.split("_", 1)[0]
                 for fp in glob.glob(str(BAR_CACHE / "*_1min.csv"))}
    fra_cache |= {Path(fp).name.split("_", 1)[0]
                  for fp in glob.glob(str(BAR_CACHE / "*_5min.csv"))}
    fra_journal = BHU.candidates_from_journal()
    alle = (fra_cache | set(BHU.DEFAULT_CANDIDATES) | fra_journal) - IKKE_AKTIER
    alle = {t for t in alle if t.isalpha() and 1 <= len(t) <= 5}
    logger.info(f"Kandidater: {len(fra_cache)} fra bar_cache, "
                f"{len(BHU.DEFAULT_CANDIDATES)} indbyggede, {len(fra_journal)} fra journal "
                f"-> {len(alle)} unikke")
    return sorted(alle)


async def hent_daglige(ib: IB, tk: str, start: date, end: date) -> dict:
    """Daglige barer for hele perioden i ÉT kald. Cachet paa disk (resumérbar)."""
    cp = CACHE_DIR / f"{tk}.json"
    if cp.exists():
        try:
            raw = json.loads(cp.read_text())
            return {date.fromisoformat(k): v for k, v in raw.items()}
        except Exception:
            pass

    c = Stock(tk, "SMART", "USD")
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), timeout=30)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        if not q or not getattr(q[0], "conId", 0):
            cp.write_text("{}")
            return {}
        c = q[0]
    except Exception:
        cp.write_text("{}")
        return {}

    aar = max(1, (end - start).days // 365 + 1)
    end_dt = ET.localize(datetime(end.year, end.month, end.day, 16, 0))
    try:
        raw = await asyncio.wait_for(ib.reqHistoricalDataAsync(
            c, endDateTime=end_dt, durationStr=f"{aar} Y", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=2), timeout=FETCH_TIMEOUT)
    except Exception as e:
        logger.debug(f"  {tk}: {type(e).__name__}")
        cp.write_text("{}")
        return {}

    ud, prev = {}, None
    for b in raw or []:
        d = b.date.date() if isinstance(b.date, datetime) else b.date
        ud[d] = {"open": float(b.open), "high": float(b.high), "low": float(b.low),
                 "close": float(b.close), "volume": float(b.volume or 0.0),
                 "prev_close": prev}
        prev = float(b.close)
    cp.write_text(json.dumps({str(k): v for k, v in ud.items()}))
    return ud


def byg_universer(daily: dict, start: date, end: date) -> dict[str, list[str]]:
    """Top-N pr. dag efter gainer_score — samme regel som de eksisterende 43 dage."""
    pr_dag: dict[date, list[tuple[float, str]]] = defaultdict(list)
    for tk, dager in daily.items():
        for d, dd in dager.items():
            if not (start <= d <= end) or dd.get("prev_close") is None:
                continue
            s = BHU.gainer_score(dd)
            if s is not None:
                pr_dag[d].append((s, tk))
    return {str(d): [t for _, t in sorted(v, reverse=True)[:BHU.TOP_N]]
            for d, v in sorted(pr_dag.items()) if v}


def daekningsrapport(uni: dict[str, list[str]], daily: dict, kand: list[str]) -> str:
    """Navne pr. dag over tid — den reelle maalefejl at holde oeje med."""
    pr_mdr_navne, pr_mdr_dage = defaultdict(list), Counter()
    for ds, tks in uni.items():
        m = ds[:7]
        pr_mdr_navne[m].append(len(tks))
        pr_mdr_dage[m] += 1
    # Hvor mange kandidater havde overhovedet data i hver maaned?
    kand_pr_mdr = defaultdict(set)
    for tk, dager in daily.items():
        for d in dager:
            kand_pr_mdr[d.strftime("%Y-%m")].add(tk)

    alle_navne = sorted({t for v in uni.values() for t in v})
    md = ["# Regime-motor v2 — universlister bagud (harvest trin 1)", "",
          f"Genereret af `regime_univers_backfill.py`.",
          f"Kandidatsæt: **{len(kand)}** tickere. Udvælgelse: `gainer_score` ≥ "
          f"{BHU.MIN_GAIN_PCT} %, pris {BHU.PRICE_MIN}–{BHU.PRICE_MAX}, "
          f"volumen ≥ {BHU.MIN_DAY_VOLUME:,.0f}, top {BHU.TOP_N}/dag.", "",
          "---", "", "## Hovedtal", "",
          f"- Handelsdage med et univers: **{len(uni)}**",
          f"- Unikke navne i alt: **{len(alle_navne)}**  ← *dette tal afgør "
          f"omkostningen ved næste trin*",
          f"- Periode: {min(uni)} .. {max(uni)}", "",
          "---", "", "## Dækning pr. måned", "",
          "Kolonnen **navne/dag** er den vigtige. Skrumper tværsnittet bagud,",
          "måles dispersion på færre navne, og metrikken bliver ikke",
          "sammenlignelig over tid — det er en reel målefejl, til forskel fra",
          "survivorship, som ikke er et problem når vi måler markedets karakter",
          "og normaliserer med percentiler.", "",
          "| Måned | Dage | Navne/dag (median) | Kandidater m. data |",
          "|---|---|---|---|"]
    for m in sorted(pr_mdr_navne):
        v = sorted(pr_mdr_navne[m])
        med = v[len(v) // 2]
        md.append(f"| {m} | {pr_mdr_dage[m]} | {med} | {len(kand_pr_mdr.get(m, ()))} |")

    fuld = sum(1 for v in pr_mdr_navne.values() if sorted(v)[len(v)//2] >= BHU.TOP_N)
    md += ["", f"Måneder med fuldt tværsnit ({BHU.TOP_N} navne/dag): "
               f"**{fuld} af {len(pr_mdr_navne)}**", ""]
    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════════
async def run(args) -> None:
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    kand = kandidatsaet()
    if args.limit:
        kand = kand[:args.limit]
        logger.info(f"--limit: begraenser til {len(kand)} kandidater")

    logger.info(f"Periode: {start} .. {end}")
    allerede = {p.stem for p in CACHE_DIR.glob("*.json")}
    mangler = [t for t in kand if t not in allerede]
    logger.info(f"Cachet: {len(allerede)} · skal hentes: {len(mangler)}")

    ib = IB()
    if mangler:
        logger.info(f"Forbinder TWS 127.0.0.1:{PORT} (clientId={CLIENT_ID}) …")
        await ib.connectAsync("127.0.0.1", PORT, clientId=CLIENT_ID, timeout=TIMEOUT)
        logger.info("Forbundet.")

    daily = {}
    try:
        for i, tk in enumerate(kand, 1):
            d = await hent_daglige(ib, tk, start, end) if (tk in mangler or
                (CACHE_DIR / f"{tk}.json").exists()) else {}
            daily[tk] = d
            if tk in mangler:
                logger.info(f"  [{i}/{len(kand)}] {tk:<6} {len(d):>5} dagsbarer")
                await asyncio.sleep(PACING_SLEEP)
    finally:
        if ib.isConnected():
            ib.disconnect()

    m_data = sum(1 for d in daily.values() if d)
    logger.info(f"Kandidater med data: {m_data}/{len(kand)}")

    uni = byg_universer(daily, start, end)
    p_uni = OUT_DIR / f"regime_univers_{start}_{end}.json"
    p_uni.write_text(json.dumps(uni, indent=1))

    md = daekningsrapport(uni, daily, kand)
    p_md = OUT_DIR / "regime_univers_daekning.md"
    p_md.write_text(md, encoding="utf-8")

    alle = sorted({t for v in uni.values() for t in v})
    print("\n" + "=" * 62)
    print(f"  Handelsdage med univers : {len(uni)}")
    print(f"  UNIKKE NAVNE            : {len(alle)}   <-- prisen paa naeste trin")
    print(f"  Periode                 : {min(uni)} .. {max(uni)}")
    print("=" * 62)
    for p in (p_uni, p_md):
        print(f"  {p.relative_to(BACKEND)}  ({p.stat().st_size/1024:.0f} kB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-07-31")
    ap.add_argument("--limit", type=int, default=0, help="kun de N foerste kandidater (test)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
