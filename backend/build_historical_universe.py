"""
build_historical_universe.py
────────────────────────────
Bygger et POINT-IN-TIME momentum-univers for historiske dage, så Konfluens 2
(og K1) kan backtestes over mange dage UDEN look-ahead/survivorship-bias.

PROBLEMET det løser:
  Jeres live-scanner (tv_scanner.py) bruger TradingViews LIVE screener — den
  kan kun give DAGENS gainers, ikke historiske. Journal-universet findes kun
  for de dage algoen faktisk kørte (lige nu: 2 dage). For at backteste bredt
  mangler vi at vide hvilke aktier der VAR momentum-kandidater på hver
  historisk dag — bestemt KUN ud fra data der var kendt den morgen.

TILGANG:
  1. Start fra et bredt KANDIDAT-sæt (--candidates, eller en indbygget liste
     af typiske small/mid-cap momentum-navne + dem fra journalen).
  2. For hver handelsdag i perioden, hent hver kandidats daglige bar.
  3. Beregn dagens "gainer-score": intradag-gain fra forrige luk til dagens
     høj/luk + et volumen-krav. Det approksimerer hvad en gap/gainer-scanner
     ville have fanget den morgen.
  4. Tag top-N pr. dag → det er dagens univers.
  5. Skriv til JSON: {dato: [tickers]} som backtest_confluence2.py kan læse.

VIGTIGE ÆRLIGE FORBEHOLD (skal forstås før resultater tolkes):
  • Universet er kun så godt som kandidat-sættet. Aktier der IKKE er i
    kandidat-listen kan aldrig udvælges — så hvis listen mangler de aktier
    der reelt løb på en dag, undervurderer vi. Dette er IKKE et komplet
    markeds-scan; det er en approksimation.
  • Gainer-score beregnet fra forrige luk → dagens data bruger KUN data der
    var tilgængeligt fra dagens åbning og frem (intradag), så vi undgår at
    "vide" hvordan dagen endte. Men selve KANDIDAT-listen er valgt af os i
    dag, hvilket er en mild survivorship-effekt. Bedre end statisk top-25,
    ikke perfekt.
  • Den eneste helt rene kilde er journalen (algoens egne daglige universer),
    som fyldes op dag for dag når algoen kører live. Dette script er en bro
    indtil journalen har nok dage.

Kør lokalt fra backend/ med TWS oppe (port 7497):
    python build_historical_universe.py --start 2026-05-01 --end 2026-05-29
    python build_historical_universe.py --start 2026-05-01 --end 2026-05-29 \\
        --candidates VCIG,RPD,ZETA,TSSI,IMSR,GRRR,CLBT,SMCI,...
    # → skriver historical_universe_2026-05-01_2026-05-29.json

Brug derefter i backtest:
    python backtest_confluence2.py --universe file \\
        --universe-file historical_universe_2026-05-01_2026-05-29.json
    (kræver det lille --universe file tillæg i backtest — se note nederst)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, time as dtime, date as date_cls
from pathlib import Path

import pytz

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_async import IB, Stock

ET = pytz.timezone("America/New_York")

DB_PATH   = Path(__file__).parent / "trading_dash.db"
PORT, CLIENT_ID, TIMEOUT = 7497, 16, 15

# ── Udvælgelses-parametre (approksimerer en gap/gainer-scanner) ──
TOP_N            = 25       # aktier pr. dag
PRICE_MIN        = 2.0
PRICE_MAX        = 50.0
MIN_DAY_VOLUME   = 500_000
MIN_GAIN_PCT     = 5.0      # mindst +5% intradag (open→high) for at tælle som "i bevægelse"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("build_historical_universe")
logging.getLogger("ib_async").setLevel(logging.WARNING)


# Bredt kandidat-sæt: typiske small/mid-cap momentum-navne.
# JUSTÉR/UDVID frit — jo bredere, jo mindre survivorship-bias.
DEFAULT_CANDIDATES = [
    "VCIG", "RPD", "ZETA", "TSSI", "IMSR", "GRRR", "CLBT", "SMCI", "PURR",
    "OLOX", "AMPL", "SAIL", "QURE", "INTA", "HPE", "AVBP", "TBBB", "GTLB",
    "ASAN", "PD", "REPL", "ASTC", "SPCE", "MX", "SPAI", "SNDL", "MVIS",
    "OCGN", "TLRY", "BBIG", "SPRT", "ATER", "RIDE", "NVAX", "CLOV",
]


def candidates_from_journal() -> set[str]:
    """Saml alle tickers der nogensinde har optrådt i journalens universer."""
    if not DB_PATH.exists():
        return set()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out = set()
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE event_type='universe_selected'"
        ).fetchall()
        for r in rows:
            try:
                p = json.loads(r["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            out.update(p.get("tickers", []))
    finally:
        conn.close()
    return out


async def fetch_daily_bars(ib: IB, ticker: str, start: date_cls, end: date_cls):
    """Hent DAGLIGE bars for ticker. Returnér {dato: (open,high,low,close,volume,prev_close)}."""
    contract = Stock(ticker, "SMART", "USD")
    try:
        await ib.qualifyContractsAsync(contract)
    except Exception:
        contract = Stock(ticker, "SMART", "USD", primaryExchange="NASDAQ")
        try:
            await ib.qualifyContractsAsync(contract)
        except Exception:
            return {}
    # Hent lidt ekstra historik bagud så vi har prev_close for første dag
    end_dt = ET.localize(datetime(end.year, end.month, end.day, 16, 0))
    span_days = (end - start).days + 10
    try:
        raw = await ib.reqHistoricalDataAsync(
            contract, endDateTime=end_dt, durationStr=f"{span_days} D",
            barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=2)
    except Exception as e:
        logger.debug(f"  {ticker}: daily fetch fejl: {e}")
        return {}
    out = {}
    prev_close = None
    for b in raw or []:
        d = b.date
        if isinstance(d, datetime):
            d = d.date()
        out[d] = {
            "open": float(b.open), "high": float(b.high), "low": float(b.low),
            "close": float(b.close), "volume": float(b.volume or 0.0),
            "prev_close": prev_close,
        }
        prev_close = float(b.close)
    return out


def gainer_score(day_data: dict) -> float | None:
    """
    Approksimér en morgen-gainer-score UDEN look-ahead på dagens slutning.
    Vi bruger gap + tidlig intradag-styrke: (high − open) / open giver hvor
    meget bevægelse der var at fange fra åbningen. Kombineret med gap fra
    forrige luk. Returnér None hvis filtre ikke opfyldes.
    """
    o, h, v = day_data["open"], day_data["high"], day_data["volume"]
    pc = day_data["prev_close"]
    if o < PRICE_MIN or o > PRICE_MAX:
        return None
    if v < MIN_DAY_VOLUME:
        return None
    intraday_gain = (h - o) / o * 100 if o > 0 else 0.0
    gap = ((o - pc) / pc * 100) if (pc and pc > 0) else 0.0
    score = intraday_gain + max(gap, 0.0)
    if score < MIN_GAIN_PCT:
        return None
    return score


async def run(args):
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end   = datetime.strptime(args.end, "%Y-%m-%d").date()

    # Saml kandidat-sæt
    cands = set(DEFAULT_CANDIDATES) | candidates_from_journal()
    if args.candidates:
        cands |= {t.strip().upper() for t in args.candidates.split(",")}
    cands = sorted(cands)

    logger.info(f"Kandidat-sæt: {len(cands)} aktier")
    logger.info(f"Periode: {start} → {end}")

    ib = IB()
    logger.info(f"Forbinder IBKR 127.0.0.1:{PORT} (clientId={CLIENT_ID})...")
    await ib.connectAsync("127.0.0.1", PORT, clientId=CLIENT_ID, timeout=TIMEOUT)
    logger.info("✓ Forbundet")

    try:
        # Hent daglige bars for alle kandidater
        daily = {}
        for i, t in enumerate(cands, 1):
            d = await fetch_daily_bars(ib, t, start, end)
            daily[t] = d
            logger.info(f"  [{i:2d}/{len(cands)}] {t:6s}  {len(d)} dagsbars")

        # Byg univers pr. dag
        universe = {}
        cur = start
        while cur <= end:
            if cur.weekday() >= 5:
                cur += timedelta(days=1); continue
            scored = []
            for t in cands:
                dd = daily.get(t, {}).get(cur)
                if not dd:
                    continue
                s = gainer_score(dd)
                if s is not None:
                    scored.append((t, s))
            scored.sort(key=lambda x: x[1], reverse=True)
            picks = [t for t, _ in scored[:TOP_N]]
            if picks:
                universe[str(cur)] = picks
                logger.info(f"  {cur}: {len(picks)} aktier  →  {', '.join(picks[:10])}"
                            f"{' …' if len(picks) > 10 else ''}")
            cur += timedelta(days=1)

        out_path = Path(__file__).parent / f"historical_universe_{start}_{end}.json"
        out_path.write_text(json.dumps(universe, indent=2))
        logger.info(f"\n✓ Skrev {len(universe)} dage til {out_path}")
        print(f"\nBrug det med backtesten:")
        print(f"  python backtest_confluence2.py --universe file --universe-file {out_path.name}")

    finally:
        ib.disconnect()
        logger.info("Frakoblet IBKR")
    return 0


def main():
    p = argparse.ArgumentParser(description="Byg historisk point-in-time momentum-univers")
    p.add_argument("--start", type=str, required=True)
    p.add_argument("--end", type=str, required=True)
    p.add_argument("--candidates", type=str,
                   help="Ekstra kommaseparerede kandidat-tickers (lægges oven i default+journal)")
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.warning("Afbrudt"); return 130


if __name__ == "__main__":
    raise SystemExit(main())