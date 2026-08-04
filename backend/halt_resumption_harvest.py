#!/usr/bin/env python3
"""
halt_resumption_harvest.py — Spor B, fase 1: NON-INVASIV data-opsamling om
trading-halts (LULD/NYHED) og deres genaabnings-adfaerd.
============================================================================

En trading-halt paa en small-cap giver en voldsom, delvist forudsigelig
genaabnings-auktion. Halts kan IKKE backtestes bagud (de er point-in-time
mikrostruktur), saa vi maa LOGGE fremadrettet. Dette script observerer et
movers-univers, opdager naar et navn skifter halted False->True (halt begynder)
og senere True->False (resumption), og skriver EEN record pr. halt-event med:
  - identitet + praecis timing (og et paalideligheds-flag),
  - konteksten FOER haltet (fra den live-tape scriptet selv foerer), og
  - adfaerden EFTER resumption (1-min bars for de foerste 15 min).

DENNE FASE BYGGER INGEN STRATEGI OG INGEN BACKTEST. Ingen entry/exit, ingen
ordrer, ingen paper-handel, ingen edge-paastand. Ren, akkumulerende observation.

NON-INVASIV (kritisk — koerer paa algoserveren SAMTIDIG med K2/BuyTheDip/
TrendJoin/EUREVERSION/Relativ Styrke paa den DELTE paper-konto):
  * EGEN, unik IBKR-forbindelse med client-id 51. Roerer ALDRIG backendens delte
    ib, strategy_manager, ordrer, positioner eller journal. Ren laesning.
    (Optaget: 1=backend, 26=probe_futures_depth, 45=trendjoin_shadow,
     46=NKD-probe, 47=asian/nikkei, 48=futures-harvest, 49=relstyrke_shadow,
     52=trendjoin-harvest. 50 optraeder i _archive/Test_scanner.py -> valgt 51.)
  * Best-effort: ENHVER fejl fanges og propagerer ALDRIG. Monitor-loopet doer
    aldrig af en enkelt cyklus-fejl. Alle IBKR-kald har asyncio.wait_for-timeout.
  * IBKR-pacing-belastning (historik) = praecis EEN kort bar-hentning pr.
    resumption-event (forsinket ~16 min saa vinduet er fuldt). Detektionen bruger
    reqTickers-snapshots (samme mekanik som halt_scanner) — ikke historik-pacing.

Genbrug: fetch_momentum_universe (daytrading_scanner) til universet;
_in_session/_halted_int/_num (halt_scanner) til session-vindue og felt-laesning.

Output (backend/halt_harvest/ — genererbar data, i .gitignore):
  halt_events.csv          — een raekke pr. event (append/upsert paa event_id).
  halt_bars/{event_id}.csv — post-resumption 1-min bars pr. event.

Koersel (fra backend/):
    python halt_resumption_harvest.py            # koerer i markedstiden, logger events
    python halt_resumption_harvest.py --replay   # offline gennemgang af dagens events

Placering: C:\\Projects\\trading_dash\\backend\\halt_resumption_harvest.py
"""
from __future__ import annotations

import asyncio
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Genbrug eksisterende helpers (ingen side-effekter ved import — halt_scanner
# starter foerst en task via ensure_running, som vi ALDRIG kalder).
from daytrading_scanner import fetch_momentum_universe
from halt_scanner import _in_session, _halted_int, _num

# === Konfiguration =========================================================
IBKR_HOST      = "127.0.0.1"
IBKR_PORT      = 7497
CLIENT_ID      = 51                # se docstring — 51 er ledigt

UNIVERSE_MAX         = 40          # eet reqTickers daekker dem. Bevidst konservativt: market-
                                   # data-linjer er en DELT konto-ressource (~100 linjer paa
                                   # tvaers af alle client-ids), saa vi holder peak-forbruget lavt
                                   # ift. de koerende strategier. Top-40-movers daekker de navne
                                   # hvor halts reelt sker. Skru op hvis linje-budgettet tillader.
UNIVERSE_REFRESH_SEC = 240         # genopfrisk movers-univers hvert 4. min
CYCLE_SLEEP_SEC      = 4.0         # throttle mellem reqTickers-cyklusser
IDLE_SLEEP_SEC       = 30.0        # sov laengere udenfor session
CONNECT_TIMEOUT      = 15
TICKERS_TIMEOUT      = 20.0
QUALIFY_TIMEOUT      = 20.0
HIST_TIMEOUT         = 60.0
SLEEP_BETWEEN        = 0.6         # pacing-hoeflighed efter en historik-hentning

POST_WINDOW_MIN      = 15          # vindue efter resumption vi karakteriserer
FETCH_DELAY_SEC      = (POST_WINDOW_MIN + 1) * 60   # vent til vinduet er fuldt (~16 min)

SOURCE_UNIVERSE      = "TV-movers"

OUT_DIR   = Path.cwd() / "halt_harvest"
BARS_DIR  = OUT_DIR / "halt_bars"
EVENTS_CSV = OUT_DIR / "halt_events.csv"

# Fast kolonne-orden for halt_events.csv (upsert bevarer den).
HEADERS = [
    "event_id", "ticker", "source_universe", "halt_reason",
    "halt_ts_observed", "halt_ts_reliable", "resume_ts_observed", "halt_duration_sec",
    "prior_close", "open_0930", "last_price_pre_halt",
    "intraday_move_pre_halt_pct", "gap_pct", "rvol_pre_halt", "halt_direction_proxy",
    "reopen_price", "ret_1m", "ret_2m", "ret_5m", "ret_15m",
    "max_favorable_15m", "max_adverse_15m",
    "bars_missing", "n_post_bars", "logged_at_utc",
]

# === Modul-state (lever paa tvaers af cyklusser + forsinkede tasks) ========
STATE = {
    "ib":       None,   # den aktuelle IB-instans (kan skifte ved reconnect)
    "ctx":      {},     # ticker -> per-symbol live-kontekst + halt-in-progress
    "logged":   set(),  # (ticker, halt_ts_observed_iso) allerede skrevet
    "tasks":    set(),  # aktive forsinkede bar-hentnings-tasks (undgaa GC)
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _pct(a, b):
    """(a - b) / b i decimal (ikke procent-tal), eller None ved manglende/nul."""
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b


def _s(v):
    """CSV-celle: None -> tom; float -> 6 decimaler; ellers str."""
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


# === CSV-lagring (append-only via upsert paa event_id) =====================
def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BARS_DIR.mkdir(parents=True, exist_ok=True)


def load_logged() -> None:
    """Fyld STATE['logged'] fra evt. eksisterende halt_events.csv, saa en genstart
    ikke dobbelt-logger igangvaerende/tidligere events (dedup: ticker+halt_ts)."""
    if not EVENTS_CSV.exists():
        return
    try:
        with EVENTS_CSV.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tkr = r.get("ticker")
                hts = r.get("halt_ts_observed")
                if tkr and hts:
                    STATE["logged"].add((tkr, hts))
    except Exception as e:
        print(f"[halt-harvest] kunne ikke laese eksisterende {EVENTS_CSV.name}: {e}")


def upsert_event(row: dict) -> None:
    """Skriv/erstat een raekke i halt_events.csv paa event_id. Robust: hele filen
    laeses, raekken saettes, filen skrives igen (faa events -> billigt). Bruges
    baade til den foreloebige (bars_missing=true) og den endelige raekke."""
    try:
        rows: dict = {}
        if EVENTS_CSV.exists():
            with EVENTS_CSV.open(newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    eid = r.get("event_id")
                    if eid:
                        rows[eid] = r
        rows[row["event_id"]] = {h: _s(row.get(h)) for h in HEADERS}
        ordered = sorted(rows.values(), key=lambda r: (r.get("resume_ts_observed", ""),
                                                        r.get("event_id", "")))
        tmp = EVENTS_CSV.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
            w.writeheader()
            w.writerows(ordered)
        tmp.replace(EVENTS_CSV)
    except Exception as e:
        print(f"[halt-harvest] upsert_event fejl (springer over): {type(e).__name__}: {e}")


def write_bars(event_id: str, bars: list[dict]) -> None:
    try:
        path = BARS_DIR / f"{event_id}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
            w.writeheader()
            for b in bars:
                w.writerow(b)
    except Exception as e:
        print(f"[halt-harvest] write_bars fejl ({event_id}): {type(e).__name__}: {e}")


# === Univers-bygning + metadata (genbruger daytrading_scanner) =============
async def build_universe():
    """Returnerer (symboler, meta) hvor meta[sym] = {rvol, avg_vol, price}.
    rvol/avg_vol bruges som pre-halt-RVOL-proxy (TV-scanens tal ved seneste
    universe-refresh — markeret som proxy i outputtet)."""
    rows = await asyncio.to_thread(fetch_momentum_universe)
    syms, meta = [], {}
    for r in rows:
        s = r.get("symbol")
        if s and s not in meta:
            syms.append(s)
            meta[s] = {"rvol": r.get("rvol"), "avg_vol": r.get("avg_vol"), "price": r.get("price")}
    return syms[:UNIVERSE_MAX], meta


# === Live-kontekst + halt-overgange ========================================
def _new_ctx() -> dict:
    return {
        "prev_halted":        0,      # forrige cyklus' halted-int
        "ever_unhalted":      False,  # har vi bekraeftet navnet HANDLEDE (h==0)?
        "prior_close":        None,
        "open_0930":          None,
        "last_price_unhalted": None,  # sidste pris set mens ikke-haltet
        "volume":             None,   # kumulativ dag-volumen (proxy-input)
        "halt_begin_ts":      None,   # UTC datetime naar haltet begyndte
        "halt_reason":        None,   # LULD / NYHED
        "halt_reliable":      False,
        "snap":               None,   # frossen pre-halt kontekst
    }


def process_cycle(tickers, now: datetime, meta: dict) -> None:
    """Opdater per-symbol live-kontekst og opdag halt-begin/resumption-overgange.
    Ren observation — ingen ordrer, ingen delt state beroeres."""
    ctx = STATE["ctx"]
    for tkr in tickers:
        try:
            sym = tkr.contract.symbol
        except Exception:
            continue
        c = ctx.get(sym)
        if c is None:
            c = ctx[sym] = _new_ctx()

        h = _halted_int(tkr)
        last  = _num(getattr(tkr, "last", None))
        close = _num(getattr(tkr, "close", None))
        opn   = _num(getattr(tkr, "open", None))
        vol   = _num(getattr(tkr, "volume", None))

        # Rullende kontekst (behold seneste ikke-None-vaerdier)
        if close is not None:
            c["prior_close"] = close
        if opn is not None:
            c["open_0930"] = opn
        if vol is not None:
            c["volume"] = vol
        if h == 0:
            c["ever_unhalted"] = True
            if last is not None:
                c["last_price_unhalted"] = last

        prev = c["prev_halted"]

        # ikke-haltet -> haltet: frys pre-halt kontekst
        if h in (1, 2) and prev not in (1, 2):
            c["halt_begin_ts"] = now
            c["halt_reason"]   = "NYHED" if h == 2 else "LULD"
            # Paalidelig KUN hvis vi selv saa navnet handle foer haltet.
            c["halt_reliable"] = bool(c["ever_unhalted"])
            m = meta.get(sym, {})
            last_pre = c["last_price_unhalted"]
            if last_pre is None:
                last_pre = last if last is not None else c["prior_close"]
            c["snap"] = {
                "prior_close":         c["prior_close"],
                "open_0930":           c["open_0930"],
                "last_price_pre_halt": last_pre,
                "rvol":                m.get("rvol"),
            }

        # haltet -> ikke-haltet: resumption -> finaliser event
        elif h == 0 and prev in (1, 2):
            try:
                _handle_resumption(sym, c, now, last)
            except Exception as e:
                print(f"[halt-harvest] resumption-fejl {sym} (springer over): "
                      f"{type(e).__name__}: {e}")
            c["halt_begin_ts"] = None
            c["snap"] = None

        c["prev_halted"] = h


def _handle_resumption(sym: str, c: dict, now: datetime, last) -> None:
    """Skriv EEN foreloebig event-raekke NU (saa recorden aldrig mistes) og
    planlaeg EEN forsinket bar-hentning der udfylder post-resumption-adfaerden."""
    begin = c["halt_begin_ts"]
    begin_iso = begin.isoformat() if begin else ""
    key = (sym, begin_iso)
    if begin and key in STATE["logged"]:
        return

    snap = c.get("snap") or {}
    prior_close = snap.get("prior_close")
    open_0930   = snap.get("open_0930")
    last_pre    = snap.get("last_price_pre_halt")
    rvol        = snap.get("rvol")

    gap_pct       = _pct(open_0930, prior_close)
    intraday_move = _pct(last_pre, open_0930)
    # Retning er en PROXY (udledt af pre-halt-bevaegelse, ikke IBKRs aegte
    # halt-retning): "up" hvis pre-halt-pris >= forrige luk, ellers "down".
    if last_pre is not None and prior_close is not None:
        direction = "up" if last_pre >= prior_close else "down"
    else:
        direction = "unknown"

    duration = (now - begin).total_seconds() if begin else None
    event_id = f"{sym}_{now:%Y%m%dT%H%M%S}"
    reopen_guess = last if last is not None else last_pre

    row = {
        "event_id":                   event_id,
        "ticker":                     sym,
        "source_universe":            SOURCE_UNIVERSE,
        "halt_reason":                c.get("halt_reason") or "?",
        "halt_ts_observed":           begin_iso,
        "halt_ts_reliable":           str(bool(c.get("halt_reliable"))).lower(),
        "resume_ts_observed":         now.isoformat(),
        "halt_duration_sec":          None if duration is None else round(duration, 1),
        "prior_close":                prior_close,
        "open_0930":                  open_0930,
        "last_price_pre_halt":        last_pre,
        "intraday_move_pre_halt_pct": intraday_move,
        "gap_pct":                    gap_pct,
        "rvol_pre_halt":              rvol,          # proxy: TV-scan RVOL v. seneste refresh
        "halt_direction_proxy":       direction,
        "reopen_price":               reopen_guess,  # foreloebig; overskrives af bar-hentning
        "ret_1m": None, "ret_2m": None, "ret_5m": None, "ret_15m": None,
        "max_favorable_15m": None, "max_adverse_15m": None,
        "bars_missing":               "true",        # indtil bar-hentningen lykkes
        "n_post_bars":                0,
        "logged_at_utc":              _now_utc().isoformat(),
    }
    upsert_event(row)
    if begin:
        STATE["logged"].add(key)
    print(f"[halt-harvest] EVENT logget: {event_id} reason={row['halt_reason']} "
          f"reliable={row['halt_ts_reliable']} dir={direction} "
          f"dur={row['halt_duration_sec']}s -> venter paa post-bars")

    t = asyncio.create_task(_delayed_bar_fetch(sym, event_id, now, row))
    STATE["tasks"].add(t)
    t.add_done_callback(STATE["tasks"].discard)


# === Post-resumption bars (EEN historik-hentning, forsinket) ===============
def _bar_ts_utc(d):
    """Bar-dato -> tz-aware UTC datetime. Haandterer formatDate=2 (epoch/int) og
    datetime (naiv antages UTC)."""
    if isinstance(d, (int, float)):
        return datetime.fromtimestamp(int(d), tz=timezone.utc)
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            return d.astimezone(timezone.utc)
        return d.replace(tzinfo=timezone.utc)
    return None


async def _fetch_post_bars(sym: str, resume_ts: datetime):
    """EEN reqHistoricalData for 1-min bars omkring resumption. Returnerer liste af
    bar-dict inden for [minut-gulv(resume), resume+15min), eller None."""
    ib = STATE.get("ib")
    if ib is None or not getattr(ib, "connected", False):
        return None
    from ib_async import Stock
    try:
        q = await asyncio.wait_for(
            ib.qualifyContractsAsync(Stock(sym, "SMART", "USD")), timeout=QUALIFY_TIMEOUT)
        # conId-tjek: qualifyContractsAsync er truthy ogsaa ved fejl (se ibkr_kvalificer)
        contract = q[0] if (q and getattr(q[0], "conId", 0)) else None
    except Exception as e:
        print(f"[halt-harvest] qualify fejl {sym}: {type(e).__name__}: {e}")
        return None
    if contract is None or not getattr(contract, "conId", 0):
        return None

    end = resume_ts + timedelta(minutes=POST_WINDOW_MIN + 1)
    end_str = end.strftime("%Y%m%d %H:%M:%S") + " UTC"
    try:
        bars = await asyncio.wait_for(ib.reqHistoricalDataAsync(
            contract, endDateTime=end_str, durationStr="1800 S", barSizeSetting="1 min",
            whatToShow="TRADES", useRTH=False, formatDate=2), timeout=HIST_TIMEOUT)
    except Exception as e:
        print(f"[halt-harvest] reqHistoricalData fejl {sym}: {type(e).__name__}: {e}")
        return None
    finally:
        await asyncio.sleep(SLEEP_BETWEEN)   # pacing-hoeflighed

    win_start = resume_ts.replace(second=0, microsecond=0)
    win_end   = win_start + timedelta(minutes=POST_WINDOW_MIN)
    out = []
    for b in bars or []:
        ts = _bar_ts_utc(getattr(b, "date", None))
        if ts is None or ts < win_start or ts >= win_end:
            continue
        out.append({
            "timestamp": ts.isoformat(),
            "open":  _num(b.open), "high": _num(b.high), "low": _num(b.low),
            "close": _num(b.close), "volume": int(b.volume) if b.volume else 0,
        })
    out.sort(key=lambda r: r["timestamp"])
    return out or None


def _derive(bars: list[dict], reopen: float) -> dict:
    """Afledte afkast fra reopen. ret_Nm = (close paa N-te minut - reopen)/reopen;
    ved for faa bars bruges sidste tilgaengelige bar (n_post_bars afsloerer helhed).
    max_favorable/adverse er BEGGE retninger, saa den senere backtest kan teste
    momentum vs fade look-ahead-rent uden gen-hentning."""
    closes = [b["close"] for b in bars if b["close"] is not None]
    highs  = [b["high"] for b in bars if b["high"] is not None]
    lows   = [b["low"] for b in bars if b["low"] is not None]

    def ret_at(idx):
        if not closes or reopen in (None, 0):
            return None
        c = closes[idx] if idx < len(closes) else closes[-1]
        return (c - reopen) / reopen

    max_fav = max(((h - reopen) / reopen for h in highs), default=None) if reopen else None
    max_adv = min(((l - reopen) / reopen for l in lows), default=None) if reopen else None
    return {
        "ret_1m": ret_at(0), "ret_2m": ret_at(1), "ret_5m": ret_at(4), "ret_15m": ret_at(14),
        "max_favorable_15m": max_fav, "max_adverse_15m": max_adv,
    }


async def _delayed_bar_fetch(sym: str, event_id: str, resume_ts: datetime, base_row: dict) -> None:
    """Vent til post-vinduet er fuldt, hent EEN gang, udfyld event-raekken. Mister
    ALDRIG recorden: den foreloebige raekke er allerede skrevet (bars_missing=true)."""
    try:
        await asyncio.sleep(FETCH_DELAY_SEC)
        bars = await _fetch_post_bars(sym, resume_ts)
        if not bars:
            print(f"[halt-harvest] {event_id}: ingen post-bars (beholder bars_missing=true)")
            return
        write_bars(event_id, bars)
        reopen = bars[0]["open"]
        row = dict(base_row)
        row["reopen_price"] = reopen
        row.update(_derive(bars, reopen))
        row["n_post_bars"] = len(bars)
        row["bars_missing"] = "false"
        upsert_event(row)
        print(f"[halt-harvest] {event_id}: {len(bars)} post-bars, "
              f"ret_15m={_s(row.get('ret_15m'))} -> faerdig")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[halt-harvest] delayed-fetch fejl {event_id} (record bevaret): "
              f"{type(e).__name__}: {e}")


# === IBKR-forbindelse (egen, unik client-id; best-effort reconnect) ========
async def connect(host, port, client_id):
    from ib_async import IB
    ib = IB()
    await asyncio.wait_for(
        ib.connectAsync(host, port, clientId=client_id, timeout=CONNECT_TIMEOUT),
        timeout=CONNECT_TIMEOUT + 5)
    try:
        ib.reqMarketDataType(1)   # live (halted-flag kraever live-feed); egen conn
    except Exception:
        pass
    STATE["ib"] = ib
    return ib


# === Monitor-loop ==========================================================
async def monitor(args) -> None:
    from ib_async import Stock
    print(f"[halt-harvest] start · client-id {args.client_id} · univers<= {UNIVERSE_MAX} · "
          f"post-vindue {POST_WINDOW_MIN} min · output {OUT_DIR}")
    ib = None
    contracts: list = []
    meta: dict = {}
    last_build: datetime | None = None

    while True:
        try:
            now = _now_utc()

            # Forbind / genforbind ved behov (best-effort)
            if ib is None or not getattr(ib, "connected", False):
                try:
                    ib = await connect(args.host, args.port, args.client_id)
                    print(f"[halt-harvest] forbundet TWS (client-id {args.client_id}); "
                          f"konto: {', '.join(ib.managedAccounts()) or '(ingen)'}")
                    contracts, last_build = [], None
                except Exception as e:
                    print(f"[halt-harvest] forbindelse fejlede (proever igen): "
                          f"{type(e).__name__}: {e}")
                    await asyncio.sleep(IDLE_SLEEP_SEC)
                    continue

            if not _in_session(now):
                await asyncio.sleep(IDLE_SLEEP_SEC)   # forbindelsen holdes i live
                continue

            # Genopfrisk univers + kvalificer kontrakter (sjaeldent)
            if last_build is None or (now - last_build).total_seconds() > UNIVERSE_REFRESH_SEC:
                syms, new_meta = await build_universe()
                if syms:
                    raw = [Stock(s, "SMART", "USD") for s in syms]
                    qualified = await asyncio.wait_for(
                        ib.qualifyContractsAsync(*raw), timeout=QUALIFY_TIMEOUT)
                    valid = [c for c in qualified if getattr(c, "conId", 0)]
                    contracts = valid
                    meta = new_meta
                last_build = now

            if not contracts:
                await asyncio.sleep(CYCLE_SLEEP_SEC)
                continue

            tickers = await asyncio.wait_for(
                ib.reqTickersAsync(*contracts), timeout=TICKERS_TIMEOUT)
            process_cycle(tickers, now, meta)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[halt-harvest] cyklus-fejl (springer over): {type(e).__name__}: {e}")
        await asyncio.sleep(CYCLE_SLEEP_SEC)


# === --replay (offline gennemgang) =========================================
def run_replay() -> int:
    print("=" * 74)
    print("  HALT-RESUMPTION HARVEST — OFFLINE REPLAY (ingen IBKR)")
    print("=" * 74)
    if not EVENTS_CSV.exists():
        print(f"  (intet at vise — {EVENTS_CSV} findes ikke endnu)")
        return 0
    today = _now_utc().date().isoformat()
    try:
        with EVENTS_CSV.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        print(f"  Kunne ikke laese {EVENTS_CSV.name}: {e}")
        return 1
    todays = [r for r in rows if (r.get("resume_ts_observed") or "")[:10] == today]
    print(f"  Fil: {EVENTS_CSV}  ·  events i alt: {len(rows)}  ·  i dag ({today}): {len(todays)}")
    show = todays or rows[-15:]
    if not todays:
        print("  (ingen events i dag — viser de seneste)")
    print(f"\n  {'event_id':<26}{'reason':>7}{'rel':>6}{'dir':>6}{'dur_s':>8}"
          f"{'ret_1m':>9}{'ret_15m':>9}{'bars?':>7}")
    for r in show:
        print(f"  {r.get('event_id',''):<26}{r.get('halt_reason',''):>7}"
              f"{r.get('halt_ts_reliable',''):>6}{r.get('halt_direction_proxy',''):>6}"
              f"{r.get('halt_duration_sec',''):>8}{r.get('ret_1m',''):>9}"
              f"{r.get('ret_15m',''):>9}"
              f"{('mangler' if r.get('bars_missing')=='true' else 'ok'):>7}")
    miss = sum(1 for r in rows if r.get("bars_missing") == "true")
    print(f"\n  Bars udestaaende (bars_missing=true): {miss}/{len(rows)}")
    return 0


# === main ==================================================================
def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(
        description="Halt-resumption data-harvest (spor B, fase 1 — non-invasiv, read-only)")
    ap.add_argument("--host", default=IBKR_HOST)
    ap.add_argument("--port", type=int, default=IBKR_PORT)
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--replay", action="store_true",
                    help="offline gennemgang af dagens loggede events (intet IBKR)")
    args = ap.parse_args()

    ensure_dirs()
    if args.replay:
        return run_replay()

    load_logged()
    try:
        asyncio.run(monitor(args))
    except KeyboardInterrupt:
        print("\n[halt-harvest] afbrudt.")
    finally:
        ib = STATE.get("ib")
        try:
            if ib is not None and getattr(ib, "connected", False):
                ib.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
