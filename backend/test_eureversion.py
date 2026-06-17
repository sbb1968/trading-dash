"""
test_eureversion.py — adfærds-lås for Europa-reversion (EUREVERSION)
───────────────────────────────────────────────────────────────────
Lukker et eksisterende hul (EUREVERSION havde ingen committet test) OG er
regressions-gaten for Trin 2-migrationen: koden skal producere byte-identisk
adfærd før/efter base-konsolideringen.

Tester den FAKTISKE algo_europa_reversion.py + strategies/europa_reversion/rule.py
med syntetiske 15-min bars + mock-journal/-conn. Ingen TWS. Kør i backend-mappen:
    python test_eureversion.py

Sektioner:
  A — z-reglen (rule.py: compute_z / entry_side / exit_reason / stop_distance)
  B — _size_contracts (1% risiko, per-trade-loft, sikkerhedsgulv)
  C — _open (futures-entry: sizing, position-dict, init_margin, MFE/MAE, forensik)
  D — _evaluate_bar exit-routing + _close (revert/stop/session_end, futures-P&L, exit_z)
  E — forensik-emission (reversion-blok ved entry+exit)
  F — isolation (scoped reconcile: instrument-klasse + journal-spor)

Stil: PASS/FAIL-print, raise SystemExit(1) ved fejl, ALLE TESTS BESTÅET til sidst.
"""

import asyncio
import types
from datetime import datetime

import pytz

import algo_europa_reversion as eur
from algo_europa_reversion import EuropaReversionLive, Bar
from strategies.europa_reversion import rule
from strategies.europa_reversion.config import (
    ENTRY_Z, EXIT_Z, STOP_Z, RISK_PCT, MULTIPLIER, INSTRUMENTS,
)
from strategy_base import StrategyStats, StrategyConfig, StrategyStatus
import trade_queries

ET = pytz.timezone("America/New_York")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


async def _noop_sleep(*a, **k):
    return None
asyncio.sleep = _noop_sleep


# ── Mocks ──────────────────────────────────────────────────────
class MockConn:
    def __init__(self, equity=100000.0, margin=1200.0, positions=None, order_ret=None):
        self.connected = True
        self.paper = True
        self.orders = []
        self.whatif_calls = []
        self._equity = equity
        self._margin = margin
        self._pos = positions or []
        self._ret = order_ret

    def get_account_summary(self):
        return {"net_liquidation": self._equity}

    def get_positions(self):
        return self._pos

    async def get_snapshot(self, sym):
        return {"last": 5000.0}

    async def what_if_init_margin(self, sym, action, qty, **kw):
        self.whatif_calls.append((sym, action, qty))
        return self._margin

    async def place_paper_order(self, sym, action, quantity, source="", await_fill_sec=0, **kw):
        self.orders.append((sym, action, quantity))
        if self._ret is None:
            return {"filled": quantity, "avg_fill": 0, "status": "Filled"}
        return self._ret(len(self.orders)) if callable(self._ret) else self._ret


class MockJournal:
    def __init__(self):
        self._db = object()
        self.db_path = ":memory:"
        self.events = []
        self.opens = []
        self.closes = []
        self.state_updates = []
        self._tid = 0

    async def log_event(self, source, event_type, payload, symbol=None):
        self.events.append((event_type, source, payload))

    async def log_trade_open(self, **kw):
        self._tid += 1
        self.opens.append(kw)
        return f"tid{self._tid}"

    async def log_trade_close(self, **kw):
        self.closes.append(kw)

    async def update_trade_state(self, **kw):
        self.state_updates.append(kw)

    def ev(self, etype):
        return [e for e in self.events if e[0] == etype]


def make_algo(conn, journal, max_open=2):
    a = EuropaReversionLive.__new__(EuropaReversionLive)
    a.conn = conn
    a._journal = journal
    a._broadcast_fn = None
    a._risk_manager = None
    a._strategy = types.SimpleNamespace(name="Europa-reversion")
    a._positions = {}
    a._bar_history = {}
    a._last_bar_processed = {}
    a._mfe = {}
    a._mae = {}
    a.trades = []
    a.total_pnl = 0.0
    a.universe = list(INSTRUMENTS)
    a.config = StrategyConfig(max_open_positions=max_open, max_loss_per_trade=170.0)
    a.stats = StrategyStats()
    a.status = StrategyStatus.RUNNING
    a._status = lambda *x, **k: None
    async def _log(m, level="info"):
        return None
    a._log = _log
    return a


def mk(hh, mm, c, hi=None, lo=None):
    ts = ET.localize(datetime(2026, 6, 16, hh, mm))
    hi = hi if hi is not None else c + 1
    lo = lo if lo is not None else c - 1
    return Bar(timestamp=ts, open=c, high=hi, low=lo, close=c, volume=1000)


# ── A — z-reglen ───────────────────────────────────────────────
def section_A():
    print("\nSektion A — z-reglen (rule.py)")

    # compute_z: sidste close langt over middel → z>0; flad → None; <2 → None
    res = rule.compute_z([10.0] * 19 + [16.0])
    check("A1 compute_z returnerer (z,std)", res is not None and len(res) == 2, res)
    z, sd = res
    check("A1 z>0 når sidste close > middel", z > 0, z)
    check("A1 std>0", sd > 0, sd)
    check("A1 compute_z(flad) → None (std=0)", rule.compute_z([5.0] * 20) is None)
    check("A1 compute_z(<2) → None", rule.compute_z([5.0]) is None)

    # entry_side: z≥+2→short, z≤−2→long, ellers None (grænser inkl.)
    check("A2 z=+2.5 → short", rule.entry_side(2.5) == "short")
    check("A2 z=−2.5 → long", rule.entry_side(-2.5) == "long")
    check("A2 z=+2.0 (grænse) → short", rule.entry_side(2.0) == "short")
    check("A2 z=−2.0 (grænse) → long", rule.entry_side(-2.0) == "long")
    check("A2 z=+1.0 → None", rule.entry_side(1.0) is None)

    # exit_reason: long revert z≥−0.5 / stop z≤−3.5; short revert z≤+0.5 / stop z≥+3.5
    check("A3 long z=−0.3 → revert", rule.exit_reason("long", -0.3) == "revert")
    check("A3 long z=−0.5 (grænse) → revert", rule.exit_reason("long", -0.5) == "revert")
    check("A3 long z=−4.0 → stop", rule.exit_reason("long", -4.0) == "stop")
    check("A3 long z=−1.0 → None (hold)", rule.exit_reason("long", -1.0) is None)
    check("A3 short z=+0.3 → revert", rule.exit_reason("short", 0.3) == "revert")
    check("A3 short z=+4.0 → stop", rule.exit_reason("short", 4.0) == "stop")
    check("A3 short z=+1.0 → None (hold)", rule.exit_reason("short", 1.0) is None)

    # stop_distance = (STOP_Z − ENTRY_Z) × std
    check("A4 stop_distance = (3.5−2.0)×std", abs(rule.stop_distance(4.0) - 1.5 * 4.0) < 1e-9,
          rule.stop_distance(4.0))


# ── B — _size_contracts ────────────────────────────────────────
def section_B():
    print("\nSektion B — _size_contracts (1% risiko + loft + gulv)")

    # B1 normal: equity 100k → risk_dollars 1000; std 4 → stop_dist 6, pcr 30 (×MES $5).
    #   by_risk=floor(1000/30)=33, by_cap=floor(170/30)=5 → contracts=5.
    a = make_algo(MockConn(equity=100000.0), MockJournal())
    contracts, stop_dist, pcr = a._size_contracts("MES", 4.0)
    check("B1 stop_dist = 1.5×std", abs(stop_dist - 6.0) < 1e-9, stop_dist)
    check("B1 per-contract-risk = stop_dist×$5", abs(pcr - 30.0) < 1e-9, pcr)
    check("B1 contracts = min(by_risk 33, by_cap 5) = 5", contracts == 5, contracts)

    # B2 stop for stor til kontoen → 0 (springer over).
    #   std 40 → stop_dist 60 → pcr 300 > per-trade 170 → by_cap 0, pcr>loft → 0.
    a = make_algo(MockConn(equity=100000.0), MockJournal())
    c2, _, pcr2 = a._size_contracts("MES", 40.0)
    check("B2 pcr > per-trade-loft → 0 kontrakter (skip)", c2 == 0, (c2, pcr2))

    # B3 sikkerhedsgulv: lille konto, by_risk=0 men pcr≤loft → 1 kontrakt.
    #   equity 10k → risk_dollars 100; std 16 → stop_dist 24 → pcr 120.
    #   by_risk=floor(100/120)=0, by_cap=floor(170/120)=1 → min 0 → gulv: pcr 120≤170 → 1.
    a = make_algo(MockConn(equity=10000.0), MockJournal())
    c3, _, pcr3 = a._size_contracts("MES", 16.0)
    check("B3 gulv → 1 kontrakt når pcr≤loft", c3 == 1, (c3, pcr3))


# ── C — _open (futures-entry) ──────────────────────────────────
def section_C():
    print("\nSektion C — _open (entry-mekanik + forensik + init_margin)")
    conn = MockConn(equity=100000.0, margin=1320.0)
    j = MockJournal()
    a = make_algo(conn, j)
    bar = mk(3, 0, 5000.0)            # 03:00 ET, close 5000
    asyncio.run(a._open("MES", "long", bar, sd=4.0, z=-2.5))

    pos = a._positions.get("MES")
    check("C1 position oprettet (long)", pos and pos["side"] == "long", pos)
    check("C1 5 kontrakter (sizing)", pos and pos["contracts"] == 5, pos and pos["contracts"])
    check("C1 BUY-ordre 5 sendt", conn.orders == [("MES", "BUY", 5)], conn.orders)
    check("C1 what_if_init_margin kaldt", conn.whatif_calls == [("MES", "BUY", 5)], conn.whatif_calls)
    check("C1 init_margin gemt i position", pos and pos["init_margin"] == 1320.0, pos and pos.get("init_margin"))
    check("C1 MFE/MAE init = entry", a._mfe.get("MES") == 5000.0 and a._mae.get("MES") == 5000.0)
    check("C1 log_trade_open kaldt m. init_margin i payload",
          j.opens and j.opens[0]["payload"].get("init_margin") == 1320.0, j.opens)
    check("C1 stop_price under entry for long", pos and pos["stop_price"] < 5000.0, pos and pos.get("stop_price"))


# ── D — _evaluate_bar exit + _close ────────────────────────────
def _algo_with_pos(side="long", entry=5000.0, contracts=5, mult=5.0, order_ret=None):
    conn = MockConn(equity=100000.0, order_ret=order_ret)
    j = MockJournal()
    a = make_algo(conn, j)
    a._positions["MES"] = {
        "side": side, "entry_price": entry, "contracts": contracts, "multiplier": mult,
        "entry_time": ET.localize(datetime(2026, 6, 16, 3, 0)),
        "stop_price": entry - 6 if side == "long" else entry + 6, "std": 4.0,
        "reserved": contracts * 10.0, "init_margin": 1300.0, "trade_id": "tid1"}
    a.stats.open_positions = 1
    a._mfe["MES"] = entry
    a._mae["MES"] = entry
    return a, conn, j


def section_D():
    print("\nSektion D — _evaluate_bar exit-routing + _close (futures-P&L)")

    # D1 revert (long, z=−0.3 ≥ −EXIT_Z): luk @ bar.close, futures-P&L = (close−entry)×c×mult
    a, conn, j = _algo_with_pos("long", 5000.0)
    asyncio.run(a._evaluate_bar("MES", mk(3, 0, 5010.0), z=-0.3, sd=4.0))
    check("D1 revert → lukket", "MES" not in a._positions, list(a._positions))
    check("D1 reason=revert", a.trades and a.trades[-1]["reason"] == "revert", a.trades[-1] if a.trades else None)
    check("D1 futures-P&L = (5010−5000)×5×5 = 250", a.trades and abs(a.trades[-1]["pnl"] - 250.0) < 1e-9,
          a.trades[-1]["pnl"] if a.trades else None)
    check("D1 exit_z i close-payload", j.closes and j.closes[-1]["payload"].get("exit_z") == -0.3, j.closes)
    check("D1 MFE/MAE popped", "MES" not in a._mfe and "MES" not in a._mae)

    # D2 stop (long, z=−4.0 ≤ −STOP_Z)
    a, conn, j = _algo_with_pos("long", 5000.0)
    asyncio.run(a._evaluate_bar("MES", mk(3, 0, 4990.0), z=-4.0, sd=4.0))
    check("D2 stop → lukket m. reason=stop", "MES" not in a._positions
          and a.trades[-1]["reason"] == "stop", a.trades[-1] if a.trades else None)

    # D3 session_end backstop: bar ≥ LAST_SESSION_BAR_ET (07:45) lukker uanset z
    a, conn, j = _algo_with_pos("long", 5000.0)
    asyncio.run(a._evaluate_bar("MES", mk(7, 50, 5005.0), z=-1.0, sd=4.0))  # z=−1.0 ville ellers holde
    check("D3 sidste bar → session_end-luk", "MES" not in a._positions
          and a.trades[-1]["reason"] == "session_end", a.trades[-1] if a.trades else None)

    # D4 short futures-P&L: (entry−price)×c×mult
    a, conn, j = _algo_with_pos("short", 5000.0)
    asyncio.run(a._evaluate_bar("MES", mk(3, 0, 4980.0), z=0.3, sd=4.0))  # short revert
    check("D4 short revert P&L = (5000−4980)×5×5 = 500",
          a.trades and abs(a.trades[-1]["pnl"] - 500.0) < 1e-9, a.trades[-1]["pnl"] if a.trades else None)


# ── E — forensik-emission ──────────────────────────────────────
def section_E():
    print("\nSektion E — forensik (reversion-blok ved entry+exit)")
    conn = MockConn(equity=100000.0)
    j = MockJournal()
    a = make_algo(conn, j)
    asyncio.run(a._open("MES", "long", mk(3, 0, 5000.0), sd=4.0, z=-2.5))
    tf = j.ev("trade_forensics")
    check("E1 entry → trade_forensics-event", len(tf) >= 1, len(tf))
    check("E1 'reversion'-blok m. entry_z",
          any("reversion" in p and "entry_z" in p["reversion"] for (_, _, p) in tf), tf and tf[0][2].keys())

    # exit
    asyncio.run(a._evaluate_bar("MES", mk(3, 0, 5010.0), z=-0.3, sd=4.0))
    tf = j.ev("trade_forensics")
    check("E2 exit → trade_forensics m. reversion.exit_z",
          any("reversion" in p and "exit_z" in p["reversion"] for (_, _, p) in tf), len(tf))


# ── F — isolation (scoped reconcile) ───────────────────────────
async def _ret(x):
    return x


def section_F():
    print("\nSektion F — isolation (scoped reconcile: instrument-klasse + journal-spor)")

    async def run(positions, rows,
                  order_ret={"filled": 2, "avg_fill": 5000.0, "status": "Filled"}):
        conn = MockConn(positions=positions, order_ret=order_ret)
        j = MockJournal()
        a = make_algo(conn, j)
        trade_queries.list_trades = lambda db, **kw: _ret(rows)
        await a._reconcile_orphans()
        return conn.orders, j.closes

    # F1: vores futures (MES) + egen åben journal-row → scoped close + reconcile_flatten
    rows = [{"symbol": "MES", "side": "long", "shares": 2, "contracts": 2,
             "entry_price": 5000.0, "trade_id": "t1", "multiplier": 5.0}]
    o, c = asyncio.run(run([{"ticker": "MES", "position": 2}], rows))
    check("F1 MES m. egen row → lukke-ordre sendt", len(o) == 1 and o[0][0] == "MES", o)
    check("F1 bogført reconcile_flatten",
          [x.get("exit_reason") for x in c] == ["reconcile_flatten"], c)

    # F2: vores futures (MES) UDEN journal-spor → observe-only, INGEN ordre
    o, c = asyncio.run(run([{"ticker": "MES", "position": 2}], []))
    check("F2 MES uden spor → 0 ordrer", o == [], o)

    # F3: instrument-klasse-guard — aktie (AAPL, ikke i INSTRUMENTS) er usynlig → 0 ordrer
    o, c = asyncio.run(run([{"ticker": "AAPL", "position": 100}], []))
    check("F3 fremmed instrument-klasse (AAPL) → usynlig, 0 ordrer", o == [], o)

    # F4: reconcile-close UFYLDT (egen row, men lukke-ordren fylder ikke) → ordre
    #     forsøgt, men INGEN reconcile_flatten (journal-row forbliver åben → næste
    #     sessions reconcile genforsøger). Lås for reconcile-spøgelses-hullet.
    rows = [{"symbol": "MES", "side": "long", "shares": 2, "contracts": 2,
             "entry_price": 5000.0, "trade_id": "t1", "multiplier": 5.0}]
    o, c = asyncio.run(run([{"ticker": "MES", "position": 2}], rows,
                           order_ret={"filled": 0, "status": "Submitted"}))
    check("F4 ufyldt reconcile-luk → lukke-ordre forsøgt", len(o) == 1 and o[0][0] == "MES", o)
    check("F4 ufyldt → INGEN reconcile_flatten",
          [x.get("exit_reason") for x in c] == [], c)

    # F5: best-effort — en uventet fejl midt i per-row-løkken må IKKE propagere ud
    #     af _reconcile_orphans (float-bug'en gjorde netop dét). Wrapper'en fanger.
    async def run_raise_midloop():
        conn = MockConn(positions=[{"ticker": "MES", "position": 2}],
                        order_ret={"filled": 2, "avg_fill": 5000.0, "status": "Filled"})
        a2 = make_algo(conn, MockJournal())
        trade_queries.list_trades = lambda db, **kw: _ret(rows)
        async def _boom(*a, **k):
            raise RuntimeError("uventet fejl midt i per-row-løkken")
        a2._reconcile_close = _boom
        await a2._reconcile_orphans()   # må IKKE kaste
        return True
    try:
        ok = asyncio.run(run_raise_midloop())
    except Exception:
        ok = False
    check("F5 best-effort: fejl i per-row-løkke propagerer IKKE", ok)


# ── G — gates + vindue + reconcile-edge ────────────────────────
def section_G():
    print("\nSektion G — gates (max_open, vindue) + reconcile-edge")

    # A4-gate: open_positions ≥ max_open → _open springer over (ingen entry)
    a = make_algo(MockConn(equity=100000.0), MockJournal(), max_open=2)
    a.stats.open_positions = 2
    asyncio.run(a._open("MES", "long", mk(3, 0, 5000.0), sd=4.0, z=-2.5))
    check("G/A4 max_open nået → ingen entry", "MES" not in a._positions, list(a._positions))

    # G1: bar UDEN for EU-sessionen (12:00 ET) + flad + |z|≥entry → ingen entry
    a = make_algo(MockConn(equity=100000.0), MockJournal())
    asyncio.run(a._evaluate_bar("MES", mk(12, 0, 5000.0), z=-2.5, sd=4.0))
    check("G1 uden for session → ingen entry", "MES" not in a._positions, list(a._positions))

    # C2: _close_all lukker åbne positioner
    a, conn, j = _algo_with_pos("long", 5000.0)
    asyncio.run(a._close_all("session_end"))
    check("C2 _close_all lukker positionen", "MES" not in a._positions, list(a._positions))
    check("C2 lukke-ordre sendt", any(o[1] == "SELL" for o in conn.orders), conn.orders)

    # E3: IBKR flad (ingen futures-positioner) → reconcile rører intet (ingen ordre)
    conn = MockConn(positions=[])
    a = make_algo(conn, MockJournal())
    trade_queries.list_trades = lambda db, **kw: _ret(
        [{"symbol": "MES", "side": "long", "shares": 2, "trade_id": "t1"}])
    asyncio.run(a._reconcile_orphans())
    check("E3 IBKR flad → 0 ordrer (intet at lukke)", conn.orders == [], conn.orders)


# ── H — live P&L + hold + bar_evaluation-status ────────────────
def section_H():
    print("\nSektion H — live P&L (update_trade_state) + hold + bar_evaluation")

    # H1 + B4: åben position, z i hold-zone (long z=−1.0 → exit_reason None) →
    #   position FORBLIVER åben OG update_trade_state kaldes med bar.close.
    a, conn, j = _algo_with_pos("long", 5000.0)
    asyncio.run(a._evaluate_bar("MES", mk(3, 0, 5003.0), z=-1.0, sd=4.0))
    check("B4 hold-zone (z=−1.0) → position forbliver åben", "MES" in a._positions)
    check("H1 update_trade_state(current_price=bar.close) kaldt",
          j.state_updates and j.state_updates[-1].get("current_price") == 5003.0, j.state_updates)

    # F1 (KENDT HUL): EUREVERSION emitterer IKKE bar_evaluation → usynlig for
    # datablind-watchdoggen. Vi LÅSER den nuværende adfærd (Trin 2 må ikke ændre
    # den utilsigtet) og flagger den i rapporten som et separat fix.
    a, conn, j = _algo_with_pos("long", 5000.0)
    for px in (5001.0, 5002.0, 5003.0):
        asyncio.run(a._evaluate_bar("MES", mk(3, 0, px), z=-1.0, sd=4.0))
    check("F1 (baseline) EUREVERSION emitterer p.t. INGEN bar_evaluation [KENDT HUL]",
          j.ev("bar_evaluation") == [], j.ev("bar_evaluation"))


# ── I — fyldnings-verificeret luk (M2K-spøgelses-fix) ──────────
def section_I():
    print("\nSektion I — fyldnings-verificeret luk (mirror K2 331f898)")

    # I0: bekræftet fyldt (filled==contracts) → luk bogføres som hidtil (D-adfærd holder).
    a, conn, j = _algo_with_pos("long", 5000.0,
                                order_ret={"filled": 5, "avg_fill": 5010.0, "status": "Filled"})
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I0 fyldt (filled==contracts) → MES lukket", "MES" not in a._positions, list(a._positions))
    check("I0 fyldt → log_trade_close bogført", len(j.closes) == 1, j.closes)

    # I1: lukke-ordre IKKE bekræftet fyldt (filled=0, Submitted) → position FORBLIVER
    #     åben, INGEN journal-close, _close popper IKKE. Dette er M2K-fix'en (16/6:
    #     journal sagde 'lukket' mens IBKR holdt 10 kontrakter).
    a, conn, j = _algo_with_pos("long", 5000.0,
                                order_ret={"filled": 0, "avg_fill": 0, "status": "Submitted"})
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I1 ufyldt → MES FORBLIVER åben (popper IKKE)", "MES" in a._positions, list(a._positions))
    check("I1 ufyldt → INGEN log_trade_close bogført", j.closes == [], j.closes)
    check("I1 ufyldt → ingen handel tilføjet trades", a.trades == [], a.trades)

    # I2: delvis fyldt (filled<contracts) → også behold åben (samme sikre retning).
    a, conn, j = _algo_with_pos("long", 5000.0, contracts=10,
                                order_ret={"filled": 4, "avg_fill": 5010.0, "status": "Submitted"})
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I2 delvis fyldt (4/10) → MES FORBLIVER åben", "MES" in a._positions, list(a._positions))
    check("I2 delvis fyldt → INGEN log_trade_close", j.closes == [], j.closes)

    # I3: _close passerer await_fill_sec=CLOSE_FILL_WAIT_SEC til place_paper_order.
    captured = {}
    a, conn, j = _algo_with_pos("long", 5000.0)
    orig = conn.place_paper_order
    async def _spy(sym, action, quantity, source="", await_fill_sec=0, **kw):
        captured["await_fill_sec"] = await_fill_sec
        return await orig(sym, action, quantity, source=source, await_fill_sec=await_fill_sec, **kw)
    conn.place_paper_order = _spy
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I3 _close bruger await_fill_sec=CLOSE_FILL_WAIT_SEC",
          captured.get("await_fill_sec") == eur.CLOSE_FILL_WAIT_SEC,
          (captured.get("await_fill_sec"), eur.CLOSE_FILL_WAIT_SEC))

    # I4: _close_all genforsøger en ufyldt lukning op til FORCE_CLOSE_MAX_ATTEMPTS
    #     og forbliver åben hvis den ALDRIG fylder. (sleep gøres til no-op for fart.)
    a, conn, j = _algo_with_pos("long", 5000.0,
                                order_ret=lambda n: {"filled": 0, "avg_fill": 0, "status": "Submitted"})
    _real_sleep = asyncio.sleep
    async def _no_sleep(*_a, **_k):
        return None
    asyncio.sleep = _no_sleep
    try:
        asyncio.run(a._close_all("session_end"))
    finally:
        asyncio.sleep = _real_sleep
    check("I4 aldrig fyldt → MES STADIG åben efter _close_all", "MES" in a._positions, list(a._positions))
    check(f"I4 forsøgte {eur.FORCE_CLOSE_MAX_ATTEMPTS}× (= MAX_ATTEMPTS)",
          len([o for o in conn.orders if o[1] == "SELL"]) == eur.FORCE_CLOSE_MAX_ATTEMPTS,
          [o for o in conn.orders if o[1] == "SELL"])

    # I5: _close_all — ufyldt de første forsøg, fylder på sidste → lukkes (ingen spøgelse
    #     når likviditeten omsider er der).
    last = eur.FORCE_CLOSE_MAX_ATTEMPTS
    a, conn, j = _algo_with_pos("long", 5000.0,
                                order_ret=lambda n: ({"filled": 5, "avg_fill": 5010.0, "status": "Filled"}
                                                     if n >= last else
                                                     {"filled": 0, "avg_fill": 0, "status": "Submitted"}))
    asyncio.sleep = _no_sleep
    try:
        asyncio.run(a._close_all("session_end"))
    finally:
        asyncio.sleep = _real_sleep
    check("I5 fylder på sidste forsøg → MES lukket", "MES" not in a._positions, list(a._positions))
    check("I5 → log_trade_close bogført", len(j.closes) == 1, j.closes)


if __name__ == "__main__":
    print("Test: Europa-reversion (adfærds-lås + Trin 2 regressions-gate)")
    section_A()
    section_B()
    section_C()
    section_D()
    section_E()
    section_F()
    section_G()
    section_H()
    section_I()
    print("\nALLE TESTS BESTÅET ✓")
