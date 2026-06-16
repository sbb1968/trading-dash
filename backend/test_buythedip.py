"""
test_buythedip.py — gate for BuyTheDips første manuelle START
─────────────────────────────────────────────────────────────
Tester den FAKTISKE algo_buythedip.py (ikke en genimplementering) med syntetiske
1-min bar-sekvenser + mock-journal/-conn. Ingen TWS. Kør i backend-mappen:
    python test_buythedip.py

Sektioner:
  A — detektion + state-maskine (dip → bounce → setup)
  B — concurrency + prioritet (to-fase, max 3, dybeste dip_depth)   ← højeste værdi
  C — sizing (min af risiko/notional + gulv)
  D — exit (stop / target / force-close)
  E — universe-forbrug (K2's universe_selected)
  F — isolation (scoped reconcile + fill-verifikation)
  G — vindue (ingen entry efter 10:30 ET)
  H — forensik-emission (bar_evaluation + trade_forensics)

Stil: PASS/FAIL-print, raise SystemExit(1) ved fejl, ALLE TESTS BESTÅET til sidst.
"""

import asyncio
import json
import os
import sqlite3
import tempfile
import types
from datetime import datetime, timedelta

import pytz

import algo_buythedip as btd
from strategy_base import StrategyStats, StrategyConfig, StrategyStatus
import trade_queries

ET = pytz.timezone("America/New_York")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


# Retry-/wait-løkker må ikke bruge rigtig tid.
async def _noop_sleep(*a, **k):
    return None
asyncio.sleep = _noop_sleep


# ── Mocks ──────────────────────────────────────────────────────
class MockConn:
    def __init__(self, order_ret=None, positions=None, last=100.0):
        self.connected = True
        self.paper = True
        self.orders = []
        self._ret = order_ret
        self._pos = positions or []
        self._last = last

    def get_positions(self):
        return self._pos

    def get_account_summary(self):
        return {"net_liquidation": 100000.0}

    async def get_snapshot(self, ticker):
        return {"last": self._last}

    async def place_paper_order(self, ticker, action, quantity, source="",
                                await_fill_sec=0, **kw):
        self.orders.append((ticker, action, quantity))
        if self._ret is None:
            return {"filled": quantity, "avg_fill": 0, "status": "Filled"}  # fuldt fyldt
        return self._ret(len(self.orders)) if callable(self._ret) else self._ret


class MockJournal:
    def __init__(self, db_path=None):
        self._db = object()
        self.db_path = db_path
        self.events = []     # (event_type, source, payload)
        self.opens = []
        self.closes = []
        self._tid = 0

    async def log_event(self, source, event_type, payload, symbol=None):
        self.events.append((event_type, source, payload))

    async def log_trade_open(self, **kw):
        self._tid += 1
        self.opens.append(kw)
        return f"tid{self._tid}"

    async def log_trade_close(self, **kw):
        self.closes.append(kw)

    def ev(self, etype):
        return [e for e in self.events if e[0] == etype]


def make_algo(conn, journal, max_open=3):
    a = btd.BuyTheDipLive.__new__(btd.BuyTheDipLive)
    a.conn = conn
    a._journal = journal
    a._broadcast_fn = None
    a._risk_manager = None
    a._strategy = None
    a._dip_state = {}
    a._done_today = set()
    a._positions = {}
    a._bar_history = {}
    a._last_bar_processed = {}
    a._mfe = {}
    a._mae = {}
    a.trades = []
    a.total_pnl = 0.0
    a._diag_eval_count = 0
    a._diag_setups = 0
    a._diag_entries = 0
    a.universe = []
    a.config = StrategyConfig(max_open_positions=max_open, max_position_size=1000.0)
    a.stats = StrategyStats()
    a.status = StrategyStatus.RUNNING
    a._status = lambda *x, **k: None
    async def _log(m, level="info"):
        return None
    a._log = _log
    return a


def mk(ts, o, h, l, c, v=10000):
    return btd.Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def runup_hist(sym, base, n=20, start=100.0, step=0.21):
    """n bars med ~4% run-up (impuls)."""
    return [mk(base + timedelta(minutes=i),
               start + i * step, start + i * step + 0.05,
               start + i * step - 0.05, start + i * step) for i in range(n)]


# ── A — detektion + state-maskine ──────────────────────────────
def section_A():
    print("\nSektion A — detektion + state-maskine")
    base = ET.localize(datetime(2026, 6, 16, 9, 40))

    # A1: run-up + dip-bar → dip-state sat, dip_low = vinduets min-low
    a = make_algo(MockConn(), MockJournal())
    a._bar_history["X"] = runup_hist("X", base)               # ref_high ≈ 103.99
    dipbar = mk(base + timedelta(minutes=20), 103.9, 103.95, 101.5, 101.6)
    a._bar_history["X"].append(dipbar)
    r = a._detect("X", dipbar)
    win_low = min(b.low for b in a._bar_history["X"][-btd.LOOKBACK:])
    check("A1 dip-bar sætter dip-state", r is None and "X" in a._dip_state)
    check("A1 dip_low = vinduets min-low", abs(a._dip_state["X"]["dip_low"] - win_low) < 1e-9,
          (a._dip_state["X"]["dip_low"], win_low))

    # A2: run-up < 3% → ingen dip-state
    a = make_algo(MockConn(), MockJournal())
    flat = [mk(base + timedelta(minutes=i), 100, 100.3, 99.8, 100.1) for i in range(20)]  # ~0.5% range
    a._bar_history["Y"] = flat
    dipish = mk(base + timedelta(minutes=20), 100, 100.1, 98.0, 98.1)
    a._bar_history["Y"].append(dipish)
    r = a._detect("Y", dipish)
    check("A2 run-up<3% → ingen dip-state/setup", r is None and "Y" not in a._dip_state)

    # A3: dip-state sat → RØD bar → ingen entry
    a = make_algo(MockConn(), MockJournal())
    a._bar_history["X"] = runup_hist("X", base) + [dipbar]
    a._detect("X", dipbar)
    red = mk(base + timedelta(minutes=21), 101.6, 101.8, 101.0, 101.2)  # close < forrige (101.6)
    a._bar_history["X"].append(red)
    check("A3 rød bar → ingen setup (venter)", a._detect("X", red) is None)

    # A4: GRØN bar → SETUP med korrekte værdier
    green = mk(base + timedelta(minutes=22), 101.3, 102.0, 101.25, 101.9)  # close > forrige (101.2)
    a._bar_history["X"].append(green)
    r = a._detect("X", green)
    check("A4 grøn bar → SETUP", r is not None)
    _, setup, bar = r
    ref_high = a._dip_state.get("X", {}).get("ref_high") or setup["ref_high"]
    exp_depth = (setup["ref_high"] - setup["dip_low"]) / setup["ref_high"] * 100.0
    check("A4 entry = grøn bars LUK", bar.close == 101.9, bar.close)
    check("A4 stop = dip_low", setup["dip_low"] == win_low, setup["dip_low"])
    check("A4 dip_depth korrekt", abs(setup["dip_depth"] - exp_depth) < 1e-9, setup["dip_depth"])

    # A5: efter entry → ingen ny entry samme ticker samme dag (_check_ticker-gate)
    a = make_algo(MockConn(), MockJournal())
    a._done_today.add("X")
    a._dip_state["X"] = {"dip_low": 100.0, "ref_high": 104.0, "dip_depth": 3.8}
    a._bar_history["X"] = runup_hist("X", base) + [dipbar, green]
    a._fetch_latest_bar = lambda t: _ret_bar(green)
    res = asyncio.run(a._check_ticker("X", allow_entries=True))
    check("A5 done_today → ingen ny entry-kandidat", res is None)


async def _ret_bar(b):
    return b


# ── B — concurrency + prioritet (HØJESTE VÆRDI) ────────────────
def _ready_setup(a, sym, base, dip_low, ref_high):
    """Pre-arm en ticker (dip-state sat, ≥LOOKBACK bars) så _detect på en grøn
    bounce-bar giver et setup med kendt dip_depth."""
    depth = (ref_high - dip_low) / ref_high * 100.0
    a._dip_state[sym] = {"dip_low": dip_low, "ref_high": ref_high, "dip_depth": depth}
    hist = runup_hist(sym, base, n=btd.LOOKBACK)          # 20 bars
    green = mk(base + timedelta(minutes=btd.LOOKBACK),    # close > forrige bar → bounce
               103.9, 104.2, 103.8, 104.1)
    a._bar_history[sym] = hist + [green]
    return a._detect(sym, green)


def section_B():
    print("\nSektion B — concurrency + prioritet (to-fase, max 3, dybeste dip)")
    base = ET.localize(datetime(2026, 6, 16, 9, 50))

    # B1/B3: 4 setups samme bar, dybder 5/4/3/2% → kun de 3 dybeste (5,4,3) åbnes
    a = make_algo(MockConn(), MockJournal(), max_open=3)
    cands = []
    for sym, dl, rh in [("A", 95.0, 100.0),   # 5.0%
                        ("B", 96.0, 100.0),   # 4.0%
                        ("C", 97.0, 100.0),   # 3.0%
                        ("D", 98.0, 100.0)]:  # 2.0% (skal droppes)
        r = _ready_setup(a, sym, base, dl, rh)
        if r:
            cands.append(r)
    asyncio.run(a._open_candidates(cands))
    opened = set(a._positions.keys())
    check("B1 kun 3 åbnet (max_open)", len(opened) == 3, opened)
    check("B3 de 3 DYBESTE (A,B,C), D droppet", opened == {"A", "B", "C"}, opened)

    # B2: 3 åbne → 4. kandidat ikke åbnet; luk én → plads frigøres
    a = make_algo(MockConn(), MockJournal(), max_open=3)
    for sym, dl in [("A", 95.0), ("B", 96.0), ("C", 97.0)]:
        a._positions[sym] = {"side": "long", "entry_price": 100, "shares": 5,
                             "stop": dl, "target": 102, "entry_time": base,
                             "trade_id": None, "dip_depth": 1, "ref_high": 100, "dip_low": dl}
    a.stats.open_positions = 3
    r = _ready_setup(a, "E", base, 90.0, 100.0)   # 10% dyb, men ingen plads
    asyncio.run(a._open_candidates([r]))
    check("B2 fuld (3) → 4. kandidat IKKE åbnet", "E" not in a._positions, list(a._positions))


# ── C — sizing ─────────────────────────────────────────────────
def _open_with(entry_close, dip_low, max_open=3, last=None):
    base = ET.localize(datetime(2026, 6, 16, 9, 55))
    conn = MockConn(last=last if last is not None else entry_close)
    a = make_algo(conn, MockJournal(), max_open=max_open)
    a._bar_history["S"] = runup_hist("S", base)        # til forensik-snapshot
    setup = {"dip_low": dip_low, "ref_high": entry_close * 1.04,
             "dip_depth": (entry_close * 1.04 - dip_low) / (entry_close * 1.04) * 100.0}
    bar = mk(base + timedelta(minutes=20), entry_close, entry_close, dip_low - 0.01, entry_close)
    asyncio.run(a._open("S", setup, bar))
    return a, conn


def section_C():
    print("\nSektion C — sizing (min af risiko/notional + gulv)")

    # C1: notional binder. entry 101.9 / stop 100.16 → 9 shares; værst-case −$15.66
    a, conn = _open_with(101.9, 100.16)
    pos = a._positions.get("S")
    check("C1 notional binder → 9 shares", pos and pos["shares"] == 9, pos and pos["shares"])
    check("C1 ordre sendt med 9", conn.orders and conn.orders[0] == ("S", "BUY", 9), conn.orders)
    worst = (101.9 - 100.16) * 9
    check("C1 værst-case ≈ −$15.66", abs(worst - 15.66) < 0.05, worst)

    # C2: risiko-grenen binder (stop-afstand >10%): entry 100 / stop 85 → 100/15 = 6
    a, conn = _open_with(100.0, 85.0)
    pos = a._positions.get("S")
    check("C2 risiko-gren binder → 6 shares", pos and pos["shares"] == 6, pos and pos["shares"])

    # C3: tiny pris → mange shares (≥1, notional binder). entry 0.50 / stop 0.49
    a, conn = _open_with(0.50, 0.49)
    pos = a._positions.get("S")
    check("C3 tiny pris → ≥1 share (notional=2000)", pos and pos["shares"] == 2000,
          pos and pos["shares"])


# ── D — exit ───────────────────────────────────────────────────
def _algo_with_pos(stop=100.0, target=104.0, entry=102.0, shares=5, order_ret=None):
    base = ET.localize(datetime(2026, 6, 16, 10, 0))
    conn = MockConn(order_ret=order_ret)
    a = make_algo(conn, MockJournal())
    a._bar_history["S"] = runup_hist("S", base)
    a._positions["S"] = {"side": "long", "entry_price": entry, "shares": shares,
                         "stop": stop, "target": target, "entry_time": base,
                         "trade_id": "tid1", "dip_depth": 3.0, "ref_high": 104, "dip_low": stop}
    a.stats.open_positions = 1
    a._mfe["S"] = entry; a._mae["S"] = entry
    return a, conn


def section_D():
    print("\nSektion D — exit")
    base = ET.localize(datetime(2026, 6, 16, 10, 5))

    # D1: low ≤ stop → stop-exit
    a, conn = _algo_with_pos(stop=100.0, target=104.0, entry=102.0)
    bar = mk(base, 101, 101.5, 99.9, 100.5)       # low 99.9 ≤ 100
    asyncio.run(a._check_exit("S", bar))
    check("D1 low≤stop → lukket", "S" not in a._positions, list(a._positions))
    check("D1 reason=stop, exit≈stop", a.trades and a.trades[-1]["reason"] == "stop"
          and abs(a.trades[-1]["exit_price"] - 100.0) < 1e-9, a.trades[-1] if a.trades else None)

    # D2: high ≥ target → target-exit
    a, conn = _algo_with_pos(stop=100.0, target=104.0, entry=102.0)
    bar = mk(base, 103, 104.2, 102.8, 104.0)      # high 104.2 ≥ 104
    asyncio.run(a._check_exit("S", bar))
    check("D2 high≥target → lukket", "S" not in a._positions, list(a._positions))
    check("D2 reason=target, exit≈target", a.trades and a.trades[-1]["reason"] == "target"
          and abs(a.trades[-1]["exit_price"] - 104.0) < 1e-9, a.trades[-1] if a.trades else None)

    # D3: force-close (sessionsslut) lukker åbne positioner
    a, conn = _algo_with_pos()
    asyncio.run(a._close_all("market_close"))
    check("D3 force-close lukker positionen", "S" not in a._positions, list(a._positions))
    check("D3 SELL-ordre sendt", any(o[1] == "SELL" for o in conn.orders), conn.orders)


# ── E — universe-forbrug ───────────────────────────────────────
def _events_db(rows):
    """Temp sqlite med events(ts_local, source, event_type, symbol, payload_json)."""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE events (ts_local TEXT, source TEXT, event_type TEXT, "
              "symbol TEXT, payload_json TEXT)")
    c.executemany("INSERT INTO events VALUES (?,?,?,?,?)", rows)
    c.commit(); c.close()
    return path


def section_E():
    print("\nSektion E — universe-forbrug")
    today = datetime.now().isoformat()

    # E1: K2-univers i dag → upper-cased + dedupliceret
    payload = json.dumps({"tickers": ["aapl", "MSFT", "aapl", "tsla"]})
    path = _events_db([(today, "Konfluens 2", "universe_selected", None, payload)])
    a = make_algo(MockConn(), MockJournal(db_path=path))
    tickers = a._load_k2_universe()
    check("E1 tickers upper+dedup", tickers == ["AAPL", "MSFT", "TSLA"], tickers)
    os.unlink(path)

    # E2: intet K2-univers → ingen handel, ingen exception, pæn log
    path = _events_db([])
    j = MockJournal(db_path=path)
    a = make_algo(MockConn(), j)
    asyncio.run(a._prepare_universe())
    check("E2 intet univers → tom, ingen crash", a.universe == [], a.universe)
    os.unlink(path)

    # E3: BuyTheDip logger sit EGET universe_selected (source 'BuyTheDip')
    payload = json.dumps({"tickers": ["NVDA", "AMD"]})
    path = _events_db([(today, "Konfluens 2", "universe_selected", None, payload)])
    j = MockJournal(db_path=path)
    a = make_algo(MockConn(), j)
    asyncio.run(a._prepare_universe())
    own = j.ev("universe_selected")
    check("E3 eget universe_selected logget (source BuyTheDip)",
          any(src == "BuyTheDip" and p.get("tickers") == ["NVDA", "AMD"]
              for (_, src, p) in own), own)
    os.unlink(path)


# ── F — isolation ──────────────────────────────────────────────
def section_F():
    print("\nSektion F — isolation (scoped reconcile + fill-verifikation)")

    async def run_reconcile(open_rows, positions):
        conn = MockConn(order_ret={"filled": 100, "avg_fill": 5.1, "status": "Filled"},
                        positions=positions)
        j = MockJournal()
        a = make_algo(conn, j)
        trade_queries.list_trades = lambda db, **kw: _ret(open_rows)
        await a._reconcile_orphans()
        return conn.orders, j.closes

    # F1: egen åben row + IBKR samme retning ≥ qty → scoped close (reconcile_flatten)
    rows = [{"symbol": "AAA", "side": "long", "shares": 100, "entry_price": 5.0, "trade_id": "t1"}]
    o, c = asyncio.run(run_reconcile(rows, [{"ticker": "AAA", "position": 100}]))
    check("F1 scoped close af egen (SELL 100)", o == [("AAA", "SELL", 100)], o)
    check("F1 bogført reconcile_flatten",
          [x.get("exit_reason") for x in c] == ["reconcile_flatten"], c)

    # F2: fremmed position uden BuyTheDip-spor → observe-only, INGEN ordre
    o, c = asyncio.run(run_reconcile([], [{"ticker": "ZZZ", "position": 50}]))
    check("F2 fremmed → 0 ordrer", o == [], o)

    # F3: force-close fill-verifikation — ufyldt → bevares åben, INGEN close
    base = ET.localize(datetime(2026, 6, 16, 10, 0))
    conn = MockConn(order_ret={"filled": 0, "status": "Submitted"})  # aldrig fyldt
    a = make_algo(conn, MockJournal())
    a._positions["S"] = {"side": "long", "entry_price": 102, "shares": 5, "stop": 100,
                         "target": 104, "entry_time": base, "trade_id": "t1",
                         "dip_depth": 3, "ref_high": 104, "dip_low": 100}
    a.stats.open_positions = 1
    a._mfe["S"] = 102; a._mae["S"] = 102
    asyncio.run(a._close("S", 100.0, "stop"))
    check("F3 ufyldt → STADIG åben", "S" in a._positions, list(a._positions))
    check("F3 ufyldt → INGEN log_trade_close", a._journal.closes == [], a._journal.closes)


async def _ret(x):
    return x


# ── G — vindue ─────────────────────────────────────────────────
def section_G():
    print("\nSektion G — vindue (ingen entry efter 10:30 ET)")
    base = ET.localize(datetime(2026, 6, 16, 10, 0))
    a = make_algo(MockConn(), MockJournal())
    a._dip_state["X"] = {"dip_low": 100.0, "ref_high": 104.0, "dip_depth": 3.8}
    a._bar_history["X"] = [mk(base, 100, 100.1, 99.9, 100.0),
                           mk(base + timedelta(minutes=1), 100, 100.2, 99.9, 100.1)]  # grøn
    green = a._bar_history["X"][-1]
    a._fetch_latest_bar = lambda t: _ret_bar(green)
    # allow_entries=False (vindue lukket) → ingen kandidat
    res_closed = asyncio.run(a._check_ticker("X", allow_entries=False))
    check("G1 efter 10:30 (allow_entries=False) → ingen entry", res_closed is None)


# ── H — forensik-emission ──────────────────────────────────────
def section_H():
    print("\nSektion H — forensik-emission (source BuyTheDip)")
    base = ET.localize(datetime(2026, 6, 16, 9, 45))

    # H1: log_bar_evaluation pr. bar-evaluering
    j = MockJournal()
    a = make_algo(MockConn(), j)
    a._bar_history["X"] = runup_hist("X", base)
    seq = [mk(base + timedelta(minutes=20 + i), 102, 102.2, 101.8, 102.0 + i * 0.01)
           for i in range(3)]
    for b in seq:
        a._fetch_latest_bar = (lambda bb: (lambda t: _ret_bar(bb)))(b)
        asyncio.run(a._check_ticker("X", allow_entries=False))
    check("H1 log_bar_evaluation pr. bar (3)", len(j.ev("bar_evaluation")) == 3,
          len(j.ev("bar_evaluation")))

    # H2: entry → trade_forensics m. 'buythedip'-blok
    a, conn = _open_with(101.9, 100.16)
    tf = a._journal.ev("trade_forensics")
    check("H2 entry → trade_forensics-event", len(tf) >= 1, len(tf))
    check("H2 'buythedip'-blok i snapshot",
          any("buythedip" in p for (_, _, p) in tf), tf[0][2].keys() if tf else None)

    # H3: exit → trade_forensics exit-snapshot
    a, conn = _algo_with_pos(stop=100.0, target=104.0, entry=102.0)
    bar = mk(base, 103, 104.3, 102.8, 104.1)
    asyncio.run(a._check_exit("S", bar))
    tf = a._journal.ev("trade_forensics")
    check("H3 exit → trade_forensics-event", len(tf) >= 1, len(tf))


if __name__ == "__main__":
    print("Test: BuyTheDip (gate for første manuelle START)")
    section_A()
    section_B()
    section_C()
    section_D()
    section_E()
    section_F()
    section_G()
    section_H()
    print("\nALLE TESTS BESTÅET ✓")
