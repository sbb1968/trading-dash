#!/usr/bin/env python3
"""
halt_scanner.py — Live halt-scanner (fase 1: backend-motor).

Overvaager et movers-univers for IBKR's `halted`-felt paa den DELTE forbindelse
(ib = strategy_manager.get_ibkr().ib, sendt ind fra /halt/list — ALDRIG en egen
klient). En halt er i sig selv et signal: LULD = voldsom bevaegelse, T1/NYHED =
materiel nyhed. Listen er reelt "de mest eksplosive navne i markedet lige nu".

Spejler intradag_scanner's livscyklus (in-process asyncio.Task paa delt ib,
throttlet, best-effort) og genbruger DENS univers-funktion (fetch_momentum_universe
= TV-movers/top-gainers). EET reqTickersAsync-kald pr. cyklus daekker hele
universet; vi laeser `.halted` (samme felt som katalysator-lagets fetch_quote:
-1 ukendt / 0 nej / 1 alm./LULD / 2 nyhed), sidste pris og forrige luk.

ASCII i kode + konsol.
"""
from __future__ import annotations

import asyncio
import datetime

from intradag_scanner import fetch_momentum_universe   # genbrug univers-bygning

try:
    import pytz
    _ET = pytz.timezone("US/Eastern")
except Exception:                       # pragma: no cover
    _ET = None

# === Konstanter ============================================================
UNIVERSE_MAX         = 60      # eet reqTickers daekker dem
UNIVERSE_REFRESH_SEC = 240     # genopfrisk movers-univers hvert 4. min (gappers skifter)
CYCLE_SLEEP_SEC      = 4.0     # throttle mellem reqTickers-cyklusser
IDLE_SLEEP_SEC       = 30.0    # sov laengere udenfor session (intet at halte paa)
RESUME_KEEP_SEC      = 300     # behold genaabnede raekker ~5 min, drop derefter

# === Modul-state (lever mellem cyklusser) ==================================
_state = {
    "task":        None,    # asyncio.Task
    "running":     False,
    "universe":    [],      # list[str] symboler
    "contracts":   [],      # kvalificerede Stock-kontrakter (cachet pr. univers)
    "last_halted": {},      # ticker -> int (forrige cyklus' halted)
    "halted":      {},      # ticker -> raekke-dict (aktuelt haltede + netop genaabnede)
    "asof":        None,    # iso-utc for sidste fuldfoerte cyklus
}


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _in_session(now_utc: datetime.datetime) -> bool:
    """Premarket + RTH (04:00-16:00 ET, hverdag). Idle udenfor."""
    if _ET is None:
        return True
    et = now_utc.astimezone(_ET)
    if et.weekday() >= 5:
        return False
    mins = et.hour * 60 + et.minute
    return 4 * 60 <= mins <= 16 * 60


def _halted_int(tkr) -> int:
    h = getattr(tkr, "halted", -1)
    try:
        return int(h) if h == h else -1          # NaN-guard
    except (TypeError, ValueError):
        return -1


def _num(v):
    """float eller None (NaN/None -> None)."""
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


# === Univers-bygning (genbruger intradag_scanner) ==========================
async def _build_universe() -> list[str]:
    rows = await asyncio.to_thread(fetch_momentum_universe)
    syms: list[str] = []
    for r in rows:
        s = r.get("symbol")
        if s and s not in syms:
            syms.append(s)
    return syms[:UNIVERSE_MAX]


# === Cyklus-bearbejdning (overgange) =======================================
def _process_cycle(tickers, now: datetime.datetime) -> None:
    last = _state["last_halted"]
    halted = _state["halted"]
    for tkr in tickers:
        try:
            sym = tkr.contract.symbol
        except Exception:
            continue
        h = _halted_int(tkr)
        prev = last.get(sym, 0)

        # ikke-haltet -> haltet: registrer halt-event
        if h in (1, 2) and prev not in (1, 2):
            prev_close = _num(getattr(tkr, "close", None))
            price = _num(getattr(tkr, "last", None))
            if price is None:
                price = prev_close
            direction = "up" if (price is not None and prev_close is not None
                                 and price >= prev_close) else "down"
            halted[sym] = {
                "ticker":         sym,
                "reason":         "NYHED" if h == 2 else "LULD",
                "direction":      direction,
                "halt_price":     price,
                "halt_ts_utc":    now.isoformat(),
                "resumed_ts_utc": None,
            }
        # haltet -> ikke-haltet: marker genaabning (behold raekken synlig et stykke)
        elif h == 0 and prev in (1, 2):
            row = halted.get(sym)
            if row and row.get("resumed_ts_utc") is None:
                row["resumed_ts_utc"] = now.isoformat()

        last[sym] = h


# === Overvaagnings-loop (in-process task paa delt ib) ======================
async def _monitor_loop(ib) -> None:
    """Best-effort: en cyklus-fejl logges og springes over, men tasken doer ALDRIG."""
    from ib_async import Stock                  # lazy (undgaa import-crash ved opstart)
    _state["running"] = True
    last_build: datetime.datetime | None = None
    print("[halt-scan] monitor startet")
    try:
        while True:
            try:
                now = _now_utc()
                if not _in_session(now):
                    await asyncio.sleep(IDLE_SLEEP_SEC)
                    continue

                # genopfrisk univers + kvalificer kontrakter (sjaeldent)
                if last_build is None or (now - last_build).total_seconds() > UNIVERSE_REFRESH_SEC:
                    syms = await _build_universe()
                    if syms:
                        contracts = [Stock(s, "SMART", "USD") for s in syms]
                        qualified = await asyncio.wait_for(
                            ib.qualifyContractsAsync(*contracts), timeout=20.0)
                        valid = [c for c in qualified if getattr(c, "conId", 0)]
                        _state["universe"] = [c.symbol for c in valid]
                        _state["contracts"] = valid
                    last_build = now

                contracts = _state["contracts"]
                if not contracts:
                    await asyncio.sleep(CYCLE_SLEEP_SEC)
                    continue

                # EET reqTickers-kald for hele universet
                tickers = await asyncio.wait_for(
                    ib.reqTickersAsync(*contracts), timeout=20.0)
                _process_cycle(tickers, now)
                _state["asof"] = now.isoformat()
            except Exception as e:
                print(f"[halt-scan] cyklus-fejl (springer over): {type(e).__name__}: {e}")
            await asyncio.sleep(CYCLE_SLEEP_SEC)
    finally:
        _state["running"] = False
        print("[halt-scan] monitor stoppet")


# === Offentligt API (bruges af /halt/list) =================================
def ensure_running(ib) -> None:
    """Start monitor-tasken hvis den ikke koerer (start-on-demand)."""
    t = _state["task"]
    if t is None or t.done():
        _state["task"] = asyncio.create_task(_monitor_loop(ib))


def is_running() -> bool:
    t = _state["task"]
    return bool(t and not t.done() and _state["running"])


def universe_size() -> int:
    return len(_state["universe"])


def asof() -> str | None:
    return _state["asof"]


def snapshot() -> list[dict]:
    """Aktuelt haltede + netop genaabnede (genaabnede droppes efter RESUME_KEEP_SEC).
    HALTET foerst, derefter nyeste halt oeverst."""
    now = _now_utc()
    out, drop = [], []
    for sym, row in _state["halted"].items():
        r_ts = row.get("resumed_ts_utc")
        if r_ts:
            try:
                age = (now - datetime.datetime.fromisoformat(r_ts)).total_seconds()
            except (ValueError, TypeError):
                age = RESUME_KEEP_SEC + 1
            if age > RESUME_KEEP_SEC:
                drop.append(sym)
                continue
            status = "GENAABNET"
        else:
            status = "HALTET"
        out.append({**row, "status": status})
    for sym in drop:
        _state["halted"].pop(sym, None)
    out.sort(key=lambda r: r["halt_ts_utc"], reverse=True)       # nyeste foerst
    out.sort(key=lambda r: 0 if r["status"] == "HALTET" else 1)  # HALTET foerst (stabil)
    return out
