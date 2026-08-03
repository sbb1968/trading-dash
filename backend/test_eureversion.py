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
import sys
import types
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")   # så PASS/FAIL-pile (→) kan printes på Windows cp1252
except (AttributeError, ValueError):
    pass

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

# Fase 2 (feed-gated luk) DEAKTIVERET som standard i tests (deadline=now → ingen venten),
# så _close_all-tests ikke busy-spinner i 20 min. Sektion K aktiverer den lokalt.
eur.LATE_CLOSE_MAX_MIN = 0


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

    async def get_positions_reliable(self):
        # Reconcile bruger nu et reliable live-read; mock leverer altid feed_ok=True
        # (samme data som get_positions) så alle eksisterende scenarier er uændrede.
        return (self._pos, True)

    async def get_open_orders(self):
        # Dup-vagten i _reconcile_close læser denne. Default tom (ingen hvilende ordrer).
        return []

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
    # Sizing læser nu risiko/handel fra risk_config (konfigurerbar). I testen fastholder
    # vi den til $170 (= det gamle per-trade-loft) så sizing-tallene er sammenlignelige,
    # og max daily loss til $300.
    a._resolve_risk = lambda key: 170.0 if key == "position_size" else 300.0
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
    #   by_risk=floor(1000/30)=33, by_cap=floor(170/30)=5 → min 5, derefter MAX_CONTRACTS.
    #   MAX_CONTRACTS blev sat til 1 (Ibens lille konto), saa testen laeser den fra
    #   config i stedet for at haardkode 5 — ellers fejler den hver gang loftet aendres.
    from strategies.europa_reversion.config import MAX_CONTRACTS
    a = make_algo(MockConn(equity=100000.0), MockJournal())
    contracts, stop_dist, pcr = a._size_contracts("MES", 4.0)
    check("B1 stop_dist = 1.5×std", abs(stop_dist - 6.0) < 1e-9, stop_dist)
    check("B1 per-contract-risk = stop_dist×$5", abs(pcr - 30.0) < 1e-9, pcr)
    forventet = min(5, MAX_CONTRACTS)
    check(f"B1 contracts = min(by_risk 33, by_cap 5, MAX_CONTRACTS {MAX_CONTRACTS}) = {forventet}",
          contracts == forventet, contracts)

    # B2 stop for stor til risiko-budgettet → GULVET giver stadig 1 kontrakt.
    #   std 40 → stop_dist 60 → pcr 300, langt over risiko/handel.
    #   Adfaerden blev bevidst aendret 17/7-2026: foer sprang den over, og saa lavede
    #   eureversion INGEN handler overhovedet paa den lille konto. Vi accepterer nu
    #   at én kontrakts risiko kan overstige risiko/handel — ellers kan strategien
    #   ikke paper-testes. Testen laaser den beslutning, saa den ikke gaar tabt.
    a = make_algo(MockConn(equity=100000.0), MockJournal())
    c2, _, pcr2 = a._size_contracts("MES", 40.0)
    check("B2 pcr over budget → gulvet giver 1 kontrakt (bevidst, 17/7-2026)",
          c2 == 1, (c2, pcr2))

    # B2b eneste vej til 0: ingen brugbar stop-afstand (pcr ≤ 0).
    a = make_algo(MockConn(equity=100000.0), MockJournal())
    c2b, _, pcr2b = a._size_contracts("MES", 0.0)
    check("B2b std=0 → pcr≤0 → 0 kontrakter (springer over)", c2b == 0, (c2b, pcr2b))

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
    from strategies.europa_reversion.config import MAX_CONTRACTS as _MAXC
    _forv = min(5, _MAXC)   # sizing giver 5, loftet skaerer ned — se B1
    check(f"C1 {_forv} kontrakter (sizing, MAX_CONTRACTS={_MAXC})",
          pos and pos["contracts"] == _forv, pos and pos["contracts"])
    check(f"C1 BUY-ordre {_forv} sendt", conn.orders == [("MES", "BUY", _forv)], conn.orders)
    check("C1 what_if_init_margin kaldt", conn.whatif_calls == [("MES", "BUY", _forv)], conn.whatif_calls)
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

    # ── Journal-pas: luk forældede åbne rows UDEN IBKR-position (2026-06-19-fix) ──
    # F6: IBKR fladt + 2 åbne journal-rows (MES, M2K) → begge lukkes journal-sync,
    #     INGEN place_paper_order (rapporteret fejl 2026-06-19).
    rows = [
        {"symbol": "MES", "side": "long",  "entry_price": 5000.0, "trade_id": "t1"},
        {"symbol": "M2K", "side": "short", "entry_price": 2000.0, "trade_id": "t2"},
    ]
    o, c = asyncio.run(run([], rows))
    check("F6 IBKR fladt + 2 åbne rows → 0 ordrer (journal-pas sender aldrig)", o == [], o)
    check("F6 begge rows lukket journal-sync",
          sorted(x.get("exit_reason") for x in c) == ["reconcile_journal_sync"] * 2, c)

    # F7: IBKR har MES-position MED row + forældet M2K-row uden position →
    #     MES lukkes via _reconcile_close (ordre sendt), M2K via journal-pas (ingen ordre).
    rows = [
        {"symbol": "MES", "side": "long",  "entry_price": 5000.0, "trade_id": "t1", "multiplier": 5.0},
        {"symbol": "M2K", "side": "short", "entry_price": 2000.0, "trade_id": "t2"},
    ]
    o, c = asyncio.run(run([{"ticker": "MES", "position": 2}], rows))
    check("F7 MES-position → præcis 1 lukke-ordre (M2K-row sender ingen)",
          [x[0] for x in o] == ["MES"], o)
    reasons = sorted(x.get("exit_reason") for x in c)
    check("F7 MES→reconcile_flatten + M2K→reconcile_journal_sync",
          reasons == ["reconcile_flatten", "reconcile_journal_sync"], reasons)

    # F8: åben row for symbol der HAR en IBKR-position → journal-pas rører den IKKE
    #     (kun reconcile_flatten fra IBKR-løkken, ingen dobbelt-luk via journal-sync).
    rows = [{"symbol": "MES", "side": "long", "entry_price": 5000.0, "trade_id": "t1", "multiplier": 5.0}]
    o, c = asyncio.run(run([{"ticker": "MES", "position": 2}], rows))
    check("F8 row m. modsvarende IBKR-position → journal-pas rører den IKKE",
          [x.get("exit_reason") for x in c] == ["reconcile_flatten"], c)


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

    # E3: IBKR flad + åben journal-row → reconcile sender INGEN ordre (intet at lukke
    #     i IBKR), men lukker den forældede row i journalen (journal-sync, 2026-06-19-fix).
    conn = MockConn(positions=[])
    j = MockJournal()
    a = make_algo(conn, j)
    trade_queries.list_trades = lambda db, **kw: _ret(
        [{"symbol": "MES", "side": "long", "shares": 2, "trade_id": "t1", "entry_price": 5000.0}])
    asyncio.run(a._reconcile_orphans())
    check("E3 IBKR flad → 0 ordrer (intet at lukke i IBKR)", conn.orders == [], conn.orders)
    check("E3 forældet row lukket journal-sync (ingen ordre)",
          [x.get("exit_reason") for x in j.closes] == ["reconcile_journal_sync"], j.closes)


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

    # F1 (FIX): EUREVERSION emitterer nu ét bar_evaluation PER evalueret bar → synlig
    # for datablind-forensik/watchdog. Tidligere hul (b2f074d) lukket. Det er
    # datablind-signalet: fyrer når data flyder, stopper når feedet dør.
    a, conn, j = _algo_with_pos("long", 5000.0)
    for px in (5001.0, 5002.0, 5003.0):
        asyncio.run(a._evaluate_bar("MES", mk(3, 0, px), z=-1.0, sd=4.0))
    be = j.ev("bar_evaluation")
    check("F1 emitterer ét bar_evaluation pr. evalueret bar (3 bars → 3)", len(be) == 3, len(be))
    check("F1 bar_evaluation har korrekt ticker (MES)",
          all(p.get("ticker") == "MES" for (_, _, p) in be), be)
    check("F1 status=in_session (03:00 ET i EU-sessionen)",
          all(p.get("status") == "in_session" for (_, _, p) in be), [p.get("status") for (_, _, p) in be])
    check("F1 bar_time_et formateret HH:MM (03:00)",
          all(p.get("bar_time_et") == "03:00" for (_, _, p) in be), [p.get("bar_time_et") for (_, _, p) in be])
    check("F1 reason bærer z+sd (forensisk værdi)",
          all("z=" in (p.get("reason") or "") and "sd=" in (p.get("reason") or "") for (_, _, p) in be), be)

    # F1b: bar UDEN for sessionen (12:00 ET) → status=out_of_session (signalet fyrer
    # stadig — det er feed-livstegnet, ikke et session-tegn).
    a, conn, j = _algo_with_pos("long", 5000.0)
    asyncio.run(a._evaluate_bar("MES", mk(12, 0, 5001.0), z=-1.0, sd=4.0))
    be = j.ev("bar_evaluation")
    check("F1b uden for session → status=out_of_session",
          len(be) == 1 and be[0][2].get("status") == "out_of_session", be)


# ── I — fyldnings-verificeret luk (M2K-spøgelses-fix) ──────────
def section_I():
    print("\nSektion I — fyldnings-verificeret luk (mirror K2 331f898)")

    # I0: bekræftet fyldt (filled==contracts) → luk bogføres som hidtil (D-adfærd holder).
    a, conn, j = _algo_with_pos("long", 5000.0,
                                order_ret={"filled": 5, "avg_fill": 5010.0, "status": "Filled"})
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I0 fyldt (filled==contracts) → MES lukket", "MES" not in a._positions, list(a._positions))
    check("I0 fyldt → log_trade_close bogført", len(j.closes) == 1, j.closes)

    # I1: lukke-ordre IKKE bekræftet fyldt (filled=0, Submitted). Udfaldet afhaenger
    #     af hvad IBKR SIGER — ikke af ordre-svaret alene. Skaerpelsen kom med
    #     over-sell-fixet (48bff37): "ufyldt" maa ikke automatisk betyde "stadig aaben",
    #     for saa gensender vi og saelger dobbelt. Tre udfald, to af dem her:
    #
    # I1a: ufyldt OG IBKR holder stadig positionen → behold aaben (M2K-fix'en 16/6:
    #      journal sagde 'lukket' mens IBKR holdt 10 kontrakter).
    a, conn, j = _algo_with_pos("long", 5000.0,
                                order_ret={"filled": 0, "avg_fill": 0, "status": "Submitted"})
    conn._pos = [{"ticker": "MES", "position": 5, "avg_cost": 25000.0}]
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I1a ufyldt + IBKR HOLDER → MES forbliver åben", "MES" in a._positions, list(a._positions))
    check("I1a ufyldt + IBKR holder → INGEN log_trade_close", j.closes == [], j.closes)
    check("I1a ufyldt + IBKR holder → ingen handel i trades", a.trades == [], a.trades)

    # I1b: ufyldt MEN IBKR er flad → ordren fyldte alligevel; bogfoer lukningen og
    #      send IKKE en ny ordre. Uden dette opstod de ejerloese shorts 31/7.
    a, conn, j = _algo_with_pos("long", 5000.0,
                                order_ret={"filled": 0, "avg_fill": 0, "status": "Submitted"})
    conn._pos = []
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I1b ufyldt + IBKR FLAD → MES lukket (ordren fyldte alligevel)",
          "MES" not in a._positions, list(a._positions))
    check("I1b ufyldt + IBKR flad → KUN 1 ordre (ingen over-sell)",
          len(conn.orders) == 1, conn.orders)

    # I2: delvis fyldt (4/10) OG IBKR holder stadig resten → behold aaben.
    #     Samme skaerpelse som I1: IBKR er dommeren, ikke ordre-svaret.
    a, conn, j = _algo_with_pos("long", 5000.0, contracts=10,
                                order_ret={"filled": 4, "avg_fill": 5010.0, "status": "Submitted"})
    conn._pos = [{"ticker": "MES", "position": 6, "avg_cost": 30000.0}]
    asyncio.run(a._close("MES", 5010.0, "revert", z=-0.3))
    check("I2 delvis fyldt (4/10) + IBKR holder 6 → MES forbliver åben",
          "MES" in a._positions, list(a._positions))
    check("I2 delvis fyldt + IBKR holder → INGEN log_trade_close", j.closes == [], j.closes)

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
    # IBKR holder stadig positionen — ellers ville "ufyldt" med rette blive tolket
    # som "ordren fyldte alligevel" (over-sell-fixet 48bff37) og lukningen bogfoert.
    conn._pos = [{"ticker": "MES", "position": 5, "avg_cost": 25000.0}]
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


# ── J — warmup-emit af bar_evaluation (datablind-åbnings-fix) ──
def section_J():
    print("\nSektion J — warmup-emit af bar_evaluation (fjern falsk åbnings-alarm)")

    def _warmup_bars(n):
        base = ET.localize(datetime(2026, 6, 16, 3, 0))   # 03:00 ET = i EU-sessionen
        return [Bar(timestamp=base + timedelta(minutes=i),
                    open=5000.0 + i, high=5001.0 + i, low=4999.0 + i,
                    close=5000.0 + i, volume=1000.0) for i in range(n)]

    # J1: ≥ LOOKBACK warmup-bars → _prepare() emitterer ét bar_evaluation pr.
    #     instrument med "warmup" i reason (stiller watchdog-uret friskt ved start).
    conn = MockConn(equity=100000.0)
    j = MockJournal()
    a = make_algo(conn, j)
    async def _full(sym):
        return _warmup_bars(eur.LOOKBACK + 2)
    a._fetch_warmup_bars = _full
    asyncio.run(a._prepare())
    warmup_evs = [(et, src, p) for (et, src, p) in j.events
                  if et == "bar_evaluation" and "warmup" in str(p.get("reason", ""))]
    check("J1 _prepare() emitterer warmup-bar_evaluation", len(warmup_evs) >= 1, warmup_evs)
    check("J1 warmup-event har source Europa-reversion",
          all(src == "Europa-reversion" for (_, src, _) in warmup_evs), warmup_evs)
    check("J1 reason bærer z+sd (forensisk værdi)",
          any("z=" in str(p.get("reason", "")) and "sd=" in str(p.get("reason", ""))
              for (_, _, p) in warmup_evs), warmup_evs)

    # J2: < LOOKBACK warmup-bars → INTET warmup-emit, ingen crash (grace dækker den case).
    conn = MockConn(equity=100000.0)
    j = MockJournal()
    a = make_algo(conn, j)
    async def _short(sym):
        return _warmup_bars(3)   # langt under LOOKBACK
    a._fetch_warmup_bars = _short
    asyncio.run(a._prepare())   # må ikke kaste
    warmup_evs = [p for (et, _, p) in j.events
                  if et == "bar_evaluation" and "warmup" in str(p.get("reason", ""))]
    check("J2 < LOOKBACK → intet warmup-emit (ingen crash)", warmup_evs == [], warmup_evs)


# ── K — feed-gated genoprettelses-luk (Fase 2 mod IBKR data-farm-drop) ──
class FeedConn:
    """Mock IBKR: feedet er 'oppe' efter `up_after` get_snapshot-kald (None = aldrig).
    place_paper_order fylder KUN når feedet er oppe (data-farm-drop → ufyldt)."""
    def __init__(self, up_after=None):
        self.connected = True
        self._snap_calls = 0
        self._up_after = up_after
        self.orders = []
    def _feed_up(self):
        return self._up_after is not None and self._snap_calls >= self._up_after
    def get_account_summary(self):
        return {"net_liquidation": 100000.0}
    async def get_snapshot(self, sym):
        self._snap_calls += 1
        return {"last": 5010.0} if self._feed_up() else {"last": None}
    async def place_paper_order(self, sym, action, quantity, source="", await_fill_sec=0, **kw):
        self.orders.append((sym, action, quantity))
        if self._feed_up():
            return {"filled": quantity, "avg_fill": 5010.0, "status": "Filled"}
        return {"filled": 0, "status": "Submitted"}


def _algo_feed(conn):
    j = MockJournal()
    a = make_algo(conn, j)
    a._positions["MES"] = {
        "side": "long", "entry_price": 5000.0, "contracts": 5, "multiplier": 5.0,
        "entry_time": ET.localize(datetime(2026, 6, 16, 3, 0)),
        "stop_price": 4994.0, "std": 4.0, "reserved": 50.0, "init_margin": 1300.0,
        "trade_id": "tid1", "last_z": -0.3}
    a.stats.open_positions = 1
    a._mfe["MES"] = 5000.0
    a._mae["MES"] = 5000.0
    a._log_msgs = []
    async def _cap(m, level="info"):
        a._log_msgs.append(m)
    a._log = _cap
    return a, j


def section_K():
    print("\nSektion K — feed-gated genoprettelses-luk (Fase 2)")
    _save_max, _save_delay = eur.LATE_CLOSE_MAX_MIN, eur.FORCE_CLOSE_RETRY_DELAY
    eur.LATE_CLOSE_MAX_MIN = 0.005      # ~0.3s vindue (testbart, ikke 20 min)
    eur.FORCE_CLOSE_RETRY_DELAY = 0     # ingen rigtige sleeps
    try:
        # K1: feed nede HELE vinduet → MES forbliver åben; "Datafeed nede" logget ÉN gang.
        a, j = _algo_feed(FeedConn(up_after=None))
        asyncio.run(a._close_all("session_end"))
        down_logs = [m for m in a._log_msgs if "Datafeed nede" in m]
        check("K1 feed nede hele vinduet → MES STADIG åben", "MES" in a._positions, list(a._positions))
        check("K1 'Datafeed nede' logget netop ÉN gang (ikke spammet)", len(down_logs) == 1, down_logs)

        # K2: feed kommer tilbage midt i vinduet → MES flades ud + "Datafeed tilbage" logget.
        a, j = _algo_feed(FeedConn(up_after=6))   # nede gennem fase 1 (4 kald), op i fase 2
        asyncio.run(a._close_all("session_end"))
        check("K2 feed tilbage → MES fladet ud (lukket)", "MES" not in a._positions, list(a._positions))
        check("K2 'Datafeed tilbage' logget", any("Datafeed tilbage" in m for m in a._log_msgs), a._log_msgs)

        # K3: feed oppe fra start → lukker i fase 1, ingen feed-down-log, ingen Fase 2.
        a, j = _algo_feed(FeedConn(up_after=0))
        asyncio.run(a._close_all("session_end"))
        check("K3 feed oppe → MES lukket i fase 1", "MES" not in a._positions, list(a._positions))
        check("K3 ingen 'Datafeed nede'-log (feed var oppe)",
              not any("Datafeed nede" in m for m in a._log_msgs), a._log_msgs)
    finally:
        eur.LATE_CLOSE_MAX_MIN, eur.FORCE_CLOSE_RETRY_DELAY = _save_max, _save_delay


# ── L — entry-bekraeftelse (3/8-2026) ──────────────────────────
def section_L():
    """Den raa |z|>=2-regel gik ind paa selve udstraekningsbaren — en bar der per
    definition lukkede i straekkets retning. Bekraeftelsen kraever at seneste bar
    lukkede TILBAGE mod middel. Backtest MES+M2K, europaeisk, 2 bp:
    PF 1,52 -> 5,05 og stop-andel 8,3 % -> 2,0 %."""
    print("\nSektion L — entry-bekraeftelse (reversionen skal vaere begyndt)")
    from strategies.europa_reversion import rule as R
    from strategies.europa_reversion import config as C

    # L1: den raa regel er uaendret — den er stadig sandhedskilden for z
    check("L1 raa entry_side: z=+2.5 -> short", R.entry_side(2.5) == "short")
    check("L1 raa entry_side: z=-2.5 -> long", R.entry_side(-2.5) == "long")
    check("L1 raa entry_side: z=+1.5 -> None", R.entry_side(1.5) is None)

    # L2: strakt OP (short). DEN PRAECISE FAELDE er den anden linje: strakt op OG
    # stadig stigende betyder at vi gaar ind mens straekket vokser. Gammel adfaerd.
    check("L2 short + faldende luk -> ENTRY",
          R.confirmed_entry_side([100.0, 99.0], 2.5) == "short")
    check("L2 short + STIGENDE luk -> INGEN entry (afventer)",
          R.confirmed_entry_side([99.0, 100.0], 2.5) is None)

    # L3: strakt NED (long) — spejlvendt
    check("L3 long + stigende luk -> ENTRY",
          R.confirmed_entry_side([99.0, 100.0], -2.5) == "long")
    check("L3 long + FALDENDE luk -> INGEN entry (afventer)",
          R.confirmed_entry_side([100.0, 99.0], -2.5) is None)

    # L4: bekraeftelse kan ikke redde et manglende z-signal
    check("L4 |z| under graensen -> None uanset vending",
          R.confirmed_entry_side([100.0, 99.0], 1.5) is None)

    # L5: uaendret luk er IKKE en vending (streng ulighed)
    check("L5 flad luk -> ingen vending (short)",
          R.turned_back("short", 100.0, 100.0) is False)
    check("L5 flad luk -> ingen vending (long)",
          R.turned_back("long", 100.0, 100.0) is False)

    # L6: for lidt historik -> ingen entry (ikke crash, ikke gaet)
    check("L6 kun een close -> None", R.confirmed_entry_side([100.0], 2.5) is None)
    check("L6 tom liste -> None", R.confirmed_entry_side([], 2.5) is None)

    # L7: kontakten kan slaas fra uden kodeaendring
    check("L7 require_confirm=False -> gammel raa adfaerd",
          R.confirmed_entry_side([99.0, 100.0], 2.5, require_confirm=False) == "short")

    # L8: og den ER slaaet til live (ellers er alt ovenstaaende ligegyldigt)
    check("L8 REQUIRE_CONFIRM slaaet til i config", C.REQUIRE_CONFIRM is True)


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
    section_J()
    section_K()
    section_L()
    print("\nALLE TESTS BESTÅET ✓")
