"""
test_k2_close_robusthed.py
──────────────────────────
Samlet test af de to K2-ændringer der deployer SAMMEN:
  - scoped reconcile-oprydning (_reconcile_orphans)  — commit b78c474
  - robust force-close (await_fill_sec + fill-verifikation + retry)

Mock-baseret, ingen TWS. Kør i backend-mappen:
    python test_k2_close_robusthed.py

Sektioner:
  A — _reconcile_orphans: 6 scenarier + fail-safe (scoped flatten, delt-aktiv-sikker)
  B — place_paper_order(await_fill_sec): poll til fyldning / terminal
  C — _close bogfører KUN ved bekræftet fyldning (filled >= shares)
  D — _close_all genforsøger ufyldte lukninger op til FORCE_CLOSE_MAX_ATTEMPTS

Stilen spejler test_reconcile_observation.py (PASS/FAIL, SystemExit(1) ved fejl).
"""

import asyncio
import sys
import types
from datetime import datetime

import ibkr_connect
import algo_confluence2 as k2mod
import trade_queries

# Neutralisér exit-forensik på den fyldte sti (testen fokuserer på pop + bogføring;
# forensik er allerede dækket andetsteds og kræver fuld bar-historik).
k2mod.build_confluence_exit_snapshot = lambda **kw: {}


# ── PASS/FAIL ──────────────────────────────────────────────────
def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


def report(name, cond, detail=""):
    """Soft-check: printer PASS/FAIL men KASTER IKKE — til Del C, hvis resultat er DATA
    (afgør næste skridt), ikke en forventet hård lås."""
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    return bool(cond)


# Retry-/poll-løkker må ikke bruge rigtig tid i testen.
async def _noop_sleep(*a, **k):
    return None
asyncio.sleep = _noop_sleep

# Fase 2 (feed-gated luk) DEAKTIVERET som standard i tests (deadline ≤ now → ingen venten),
# så de eksisterende _close_all-tests ikke busy-spinner. Sektion E aktiverer den lokalt.
k2mod.LATE_CLOSE_MAX_MIN = 0


# ── Mocks ──────────────────────────────────────────────────────
class MockConn:
    """Registrerer ALLE place_paper_order-kald i order_calls."""
    def __init__(self, order_ret=None, positions=None, last=10.0, open_orders=None):
        self.connected = True
        self.order_calls = []
        self._order_ret = order_ret            # dict | callable(call_idx)->dict | None
        self._positions = positions or []
        self._last = last
        self._open_orders = open_orders or []  # dup-vagt (get_open_orders) — default tom

    def get_positions(self):
        return self._positions

    async def get_open_orders(self):
        # Dup-vagten i _reconcile_close læser denne. Default tom -> samme adfærd som før
        # dup-vagten fandtes (de eksisterende A/C/D-scenarier placerer ordrer uændret).
        return list(self._open_orders)

    async def get_snapshot(self, ticker):
        return {"last": self._last}

    async def place_paper_order(self, ticker, action, quantity, source="",
                                await_fill_sec=0, **kw):
        self.order_calls.append((ticker, action, quantity))
        r = self._order_ret
        return r(len(self.order_calls)) if callable(r) else r


class MockJournal:
    def __init__(self):
        self._db = object()          # truthy sentinel
        self.db_path = ":memory:"
        self.closes = []             # liste af exit_reason

    async def log_trade_close(self, **kw):
        self.closes.append(kw.get("exit_reason"))

    async def log_event(self, **kw):
        pass

    async def log_trade_open(self, **kw):
        pass


def install_list_trades(rows, raise_exc=False):
    """_reconcile_orphans gør `from trade_queries import list_trades` ved kald,
    så vi monkeypatcher modulets navn."""
    async def _lt(db, **kw):
        if raise_exc:
            raise RuntimeError("boom")
        return rows
    trade_queries.list_trades = _lt


def make_algo(conn, journal):
    """K2-instans uden fuld __init__ — kun det reconcile/_close rører."""
    algo = k2mod.Confluence2Live.__new__(k2mod.Confluence2Live)
    algo.conn = conn
    algo._journal = journal
    algo._strategy = types.SimpleNamespace(name="Konfluens 2")
    algo._broadcast_fn = None
    algo._positions = {}
    algo._position_data = {}
    algo._contexts = {}
    algo._bar_history = {}
    algo._variant_key = k2mod.LIVE_VARIANT_KEY
    algo._tape_buffer = None
    algo._mfe = {}
    algo._mae = {}
    algo.trades = []
    algo.total_pnl = 0.0
    algo.status = k2mod.StrategyStatus.RUNNING
    algo._status = lambda *a, **k: None
    async def _log(msg, level="info"):
        return None
    algo._log = _log
    # Stub den FYLDTE stis P&L-hook (ingen base-stats i testen).
    algo.record_position_closed = lambda ticker, price: 50.0
    return algo


def mk_position(shares=100, entry=5.0):
    return types.SimpleNamespace(
        shares=shares, entry_price=entry,
        entry_time=datetime(2026, 6, 16, 15, 30, 0),
        metadata={"trade_id": "t1"})


def row(symbol, side, shares, entry=5.0):
    return {"symbol": symbol, "side": side, "shares": shares,
            "entry_price": entry, "trade_id": f"{symbol}-1"}


FILLED = {"filled": 100, "avg_fill": 5.10, "status": "Filled", "order_id": None}


# ── SEKTION A — _reconcile_orphans ─────────────────────────────
def section_A():
    print("\nSektion A — _reconcile_orphans (scoped flatten)")

    async def run(open_rows, ibkr_pos, raise_list=False, order_ret=FILLED):
        conn = MockConn(order_ret=order_ret, positions=ibkr_pos)
        journal = MockJournal()
        algo = make_algo(conn, journal)
        install_list_trades(open_rows, raise_exc=raise_list)
        await algo._reconcile_orphans()
        return conn.order_calls, journal.closes

    # A1 ghost-match: AAA long 100, IBKR +100 → SELL 100, reconcile_flatten
    o, c = asyncio.run(run([row("AAA", "long", 100)], [{"ticker": "AAA", "position": 100}]))
    check("A1 ghost-match → SELL AAA 100", o == [("AAA", "SELL", 100)], o)
    check("A1 bogført reconcile_flatten", c == ["reconcile_flatten"], c)

    # A2 delt aktiv: BBB long 100, IBKR +150 → SELL 100 (IKKE 150)
    o, c = asyncio.run(run([row("BBB", "long", 100)], [{"ticker": "BBB", "position": 150}]))
    check("A2 delt aktiv → SELL BBB 100 (ikke 150)", o == [("BBB", "SELL", 100)], o)

    # A3 fremmed: ingen K2-row, IBKR holder CCC → 0 ordrer
    o, c = asyncio.run(run([], [{"ticker": "CCC", "position": 200.0}]))
    check("A3 fremmed position → 0 ordrer", o == [], o)
    check("A3 ingen close bogført", c == [], c)

    # A4 fantom: DDD long 100, IBKR flad → 0 ordrer, reconcile_phantom
    o, c = asyncio.run(run([row("DDD", "long", 100)], []))
    check("A4 fantom → 0 ordrer", o == [], o)
    check("A4 bogført reconcile_phantom", c == ["reconcile_phantom"], c)

    # A5 inkonsistent retning: EEE long 100, IBKR -100 → 0 ordrer
    o, c = asyncio.run(run([row("EEE", "long", 100)], [{"ticker": "EEE", "position": -100.0}]))
    check("A5 inkonsistent retning → 0 ordrer", o == [], o)
    check("A5 ingen close bogført", c == [], c)

    # A6 utilstrækkeligt netto: FFF long 100, IBKR +40 → 0 ordrer
    o, c = asyncio.run(run([row("FFF", "long", 100)], [{"ticker": "FFF", "position": 40.0}]))
    check("A6 |net|<antal → 0 ordrer", o == [], o)

    # A7 fail-safe: list_trades kaster → returnerer uden at kaste, 0 ordrer
    try:
        o, c = asyncio.run(run([row("GGG", "long", 100)],
                               [{"ticker": "GGG", "position": 100}], raise_list=True))
        ok = (o == [])
    except Exception:
        ok = False
    check("A7 fail-safe: exception i list_trades vælter ikke, 0 ordrer", ok)

    # A8 reconcile-close UFYLDT: ghost-match men lukke-ordren fylder IKKE (filled=0)
    #    → SELL afgivet, men INGEN reconcile_flatten (journal-row forbliver åben →
    #    næste sessions reconcile genforsøger). Regressions-lås for spøgelses-hullet.
    UNFILLED = {"filled": 0, "avg_fill": 0, "status": "Submitted", "order_id": None}
    o, c = asyncio.run(run([row("HHH", "long", 100)],
                           [{"ticker": "HHH", "position": 100}], order_ret=UNFILLED))
    check("A8 ufyldt reconcile-luk → SELL forsøgt", o == [("HHH", "SELL", 100)], o)
    check("A8 ufyldt → INGEN reconcile_flatten bogført (row forbliver åben)", c == [], c)

    # A8b delvis fyldt (filled<shares) → også behold åben, ingen reconcile_flatten
    PARTIAL = {"filled": 40, "avg_fill": 5.10, "status": "Submitted", "order_id": None}
    o, c = asyncio.run(run([row("III", "long", 100)],
                           [{"ticker": "III", "position": 100}], order_ret=PARTIAL))
    check("A8b delvis fyldt → INGEN reconcile_flatten", c == [], c)

    # A9 best-effort: en uventet fejl MIDT i per-row-løkken (her i _reconcile_close)
    #    må IKKE propagere ud af _reconcile_orphans (float-bug'en 0dd96b8 gjorde
    #    netop dét og dræbte loopet). Wrapper'en fanger → returnerer pænt.
    async def run_raise_midloop():
        conn = MockConn(order_ret=FILLED, positions=[{"ticker": "JJJ", "position": 100}])
        algo = make_algo(conn, MockJournal())
        install_list_trades([row("JJJ", "long", 100)])
        async def _boom(*a, **k):
            raise RuntimeError("uventet fejl midt i per-row-løkken")
        algo._reconcile_close = _boom
        await algo._reconcile_orphans()   # må IKKE kaste
        return True
    try:
        ok = asyncio.run(run_raise_midloop())
    except Exception:
        ok = False
    check("A9 best-effort: fejl i per-row-løkke propagerer IKKE", ok)


# ── SEKTION B — place_paper_order(await_fill_sec) ──────────────
class FakeStatus:
    def __init__(self, fill_after, quantity, price):
        self._reads = 0
        self._fill_after = fill_after     # int | None (None = fyldes aldrig)
        self.q = quantity
        self.p = price
        self._status = "Submitted"

    @property
    def status(self):
        return self._status

    @property
    def filled(self):
        self._reads += 1
        if self._fill_after is not None and self._reads >= self._fill_after:
            self._status = "Filled"
            return self.q
        return 0

    @property
    def avgFillPrice(self):
        return self.p if self._status == "Filled" else 0


def make_conn_B(fake_status):
    conn = ibkr_connect.IBKRConnection.__new__(ibkr_connect.IBKRConnection)
    conn._connect_attempted = True       # connected-property læser denne + ib.isConnected()
    conn.paper = True
    async def _resolve(ticker):
        return types.SimpleNamespace(symbol=ticker)
    conn._resolve_contract = _resolve
    class MockIB:
        def isConnected(self):
            return True
        def placeOrder(self, contract, order):
            order.orderId = 1
            return types.SimpleNamespace(order=order, orderStatus=fake_status)
    conn.ib = MockIB()
    return conn


def section_B():
    print("\nSektion B — place_paper_order(await_fill_sec)")

    # B1 default (await_fill_sec=0): ingen poll, returnerer status (uændret adfærd)
    fs = FakeStatus(fill_after=0, quantity=100, price=5.1)
    conn = make_conn_B(fs)
    res = asyncio.run(conn.place_paper_order("AAA", "SELL", 100, source="Konfluens 2", await_fill_sec=0))
    check("B1 await_fill_sec=0 returnerer dict", isinstance(res, dict), res)
    check("B1 ingen poll-løkke (få reads)", fs._reads <= 2, fs._reads)

    # B2 fyldes undervejs: bryder tidligt, filled == quantity
    fs = FakeStatus(fill_after=3, quantity=100, price=5.1)
    conn = make_conn_B(fs)
    res = asyncio.run(conn.place_paper_order("AAA", "SELL", 100, source="Konfluens 2", await_fill_sec=8))
    check("B2 fyldes undervejs → filled==quantity", res["filled"] == 100, res)
    check("B2 brød tidligt (ikke alle opslag)", fs._reads < 16, fs._reads)

    # B3 fyldes aldrig: timeout-graceful, filled=0, ingen exception
    fs = FakeStatus(fill_after=None, quantity=100, price=5.1)
    conn = make_conn_B(fs)
    res = asyncio.run(conn.place_paper_order("AAA", "SELL", 100, source="Konfluens 2", await_fill_sec=4))
    check("B3 fyldes aldrig → filled==0, ingen exception", res["filled"] == 0, res)


# ── SEKTION C — _close fill-verifikation ───────────────────────
def section_C():
    print("\nSektion C — _close bogfører kun ved bekræftet fyldning")

    def setup(order_ret):
        conn = MockConn(order_ret=order_ret, last=5.05)
        journal = MockJournal()
        algo = make_algo(conn, journal)
        algo._positions["AAA"] = mk_position(100, 5.0)
        return algo, conn, journal

    # C1 fyldt → popper + bogfører exit_reason="test", total_pnl opdateret
    algo, conn, journal = setup({"filled": 100, "avg_fill": 5.10, "status": "Filled"})
    asyncio.run(algo._close("AAA", 5.05, "test"))
    check("C1 fyldt → AAA popped", "AAA" not in algo._positions, list(algo._positions))
    check("C1 fyldt → log_trade_close exit_reason=test", journal.closes == ["test"], journal.closes)
    check("C1 fyldt → total_pnl opdateret", algo.total_pnl == 50.0, algo.total_pnl)

    # C2 ikke fyldt (dict, filled=0) → IKKE popped, INGEN close, ingen exception
    algo, conn, journal = setup({"filled": 0, "avg_fill": 0, "status": "Submitted"})
    asyncio.run(algo._close("AAA", 5.05, "test"))
    check("C2 ufyldt → AAA STADIG åben", "AAA" in algo._positions, list(algo._positions))
    check("C2 ufyldt → INGEN log_trade_close", journal.closes == [], journal.closes)

    # C3 ikke sendt (None) → uændret: ikke popped, ingen close
    algo, conn, journal = setup(None)
    asyncio.run(algo._close("AAA", 5.05, "test"))
    check("C3 None → AAA STADIG åben", "AAA" in algo._positions, list(algo._positions))
    check("C3 None → INGEN log_trade_close", journal.closes == [], journal.closes)


# ── SEKTION D — _close_all genforsøger ─────────────────────────
def section_D():
    print("\nSektion D — _close_all genforsøger ufyldte lukninger")

    # D1 fyldes på 3. forsøg → til sidst lukket, 3 ordrer
    def ret_d1(idx):
        return {"filled": 0, "status": "Submitted"} if idx < 3 \
            else {"filled": 100, "avg_fill": 5.10, "status": "Filled"}
    conn = MockConn(order_ret=ret_d1, last=5.05)
    journal = MockJournal()
    algo = make_algo(conn, journal)
    algo._positions["AAA"] = mk_position(100, 5.0)
    asyncio.run(algo._close_all("market_close"))
    check("D1 fyldes på 3. forsøg → AAA lukket", "AAA" not in algo._positions, list(algo._positions))
    check("D1 place_paper_order kaldt 3 gange", len(conn.order_calls) == 3, conn.order_calls)

    # D2 fyldes aldrig → AAA stadig åben, kaldt FORCE_CLOSE_MAX_ATTEMPTS gange
    conn = MockConn(order_ret={"filled": 0, "status": "Submitted"}, last=5.05)
    journal = MockJournal()
    algo = make_algo(conn, journal)
    algo._positions["AAA"] = mk_position(100, 5.0)
    asyncio.run(algo._close_all("market_close"))
    check("D2 fyldes aldrig → AAA STADIG åben", "AAA" in algo._positions, list(algo._positions))
    check(f"D2 kaldt {k2mod.FORCE_CLOSE_MAX_ATTEMPTS} gange (= MAX_ATTEMPTS)",
          len(conn.order_calls) == k2mod.FORCE_CLOSE_MAX_ATTEMPTS, conn.order_calls)


# ── SEKTION E — feed-gated genoprettelses-luk (Fase 2, markedslukke-bundet) ──
class FeedConn:
    """Mock: feedet er 'oppe' efter up_after get_snapshot-kald (None = aldrig).
    place_paper_order fylder KUN når feedet er oppe (data-farm-drop → ufyldt)."""
    def __init__(self, up_after=None, last_px=5.05):
        self.connected = True
        self._snap_calls = 0
        self._up_after = up_after
        self._last_px = last_px
        self.order_calls = []
    def _up(self):
        return self._up_after is not None and self._snap_calls >= self._up_after
    async def get_snapshot(self, ticker):
        self._snap_calls += 1
        return {"last": self._last_px} if self._up() else {"last": None}
    async def place_paper_order(self, ticker, action, quantity, source="", await_fill_sec=0, **kw):
        self.order_calls.append((ticker, action, quantity))
        if self._up():
            return {"filled": quantity, "avg_fill": self._last_px, "status": "Filled"}
        return {"filled": 0, "status": "Submitted"}


def _feed_algo(conn):
    a = make_algo(conn, MockJournal())
    a._positions["AAA"] = mk_position(100, 5.0)
    a._log_msgs = []
    async def _cap(m, level="info"):
        a._log_msgs.append(m)
    a._log = _cap
    return a


def section_E():
    print("\nSektion E — feed-gated genoprettelses-luk (Fase 2, markedslukke-bundet)")
    _save = (k2mod.LATE_CLOSE_MAX_MIN, k2mod.FORCE_CLOSE_RETRY_DELAY, k2mod.SESSION_END)
    k2mod.FORCE_CLOSE_RETRY_DELAY = 0
    try:
        # E1: feed nede HELE vinduet (marked åbent) → AAA åben + "Datafeed nede" 1× + give-up.
        k2mod.LATE_CLOSE_MAX_MIN = 0.005           # ~0.3s vindue
        k2mod.SESSION_END = k2mod.dtime(23, 59)    # marked "åbent" → deadline bindes af LATE_CLOSE_MAX_MIN
        a = _feed_algo(FeedConn(up_after=None))
        asyncio.run(a._close_all("market_close"))
        down = [m for m in a._log_msgs if "Datafeed nede" in m]
        check("E1 feed nede → AAA STADIG åben", "AAA" in a._positions, list(a._positions))
        check("E1 'Datafeed nede' netop ÉN gang (ikke spammet)", len(down) == 1, down)
        check("E1 give-up-besked logget", any("bekræftes lukket før" in m for m in a._log_msgs), a._log_msgs)

        # E2: feed tilbage før deadline → AAA flades ud + "Datafeed tilbage".
        a = _feed_algo(FeedConn(up_after=6))       # nede gennem fase 1 (4 kald), op i fase 2
        asyncio.run(a._close_all("market_close"))
        check("E2 feed tilbage → AAA fladet ud (lukket)", "AAA" not in a._positions, list(a._positions))
        check("E2 'Datafeed tilbage' logget", any("Datafeed tilbage" in m for m in a._log_msgs), a._log_msgs)

        # E3: marked allerede LUKKET (SESSION_END i fortiden) → Fase 2 sprunget over.
        k2mod.SESSION_END = k2mod.dtime(0, 1)      # markedslukning i fortiden → deadline ≤ now
        a = _feed_algo(FeedConn(up_after=None))
        asyncio.run(a._close_all("market_close"))
        check("E3 marked lukket → AAA åben (Fase 1 fejlede, Fase 2 sprunget over)", "AAA" in a._positions)
        check("E3 INGEN 'Datafeed nede'-log (Fase 2 sprunget over)",
              not any("Datafeed nede" in m for m in a._log_msgs), a._log_msgs)

        # E4: normal — feed oppe fra start → luk i fase 1, ingen Fase 2.
        k2mod.SESSION_END = k2mod.dtime(23, 59)
        a = _feed_algo(FeedConn(up_after=0))
        asyncio.run(a._close_all("market_close"))
        check("E4 feed oppe → AAA lukket i fase 1", "AAA" not in a._positions)
        check("E4 ingen 'Datafeed nede'-log", not any("Datafeed nede" in m for m in a._log_msgs))
    finally:
        k2mod.LATE_CLOSE_MAX_MIN, k2mod.FORCE_CLOSE_RETRY_DELAY, k2mod.SESSION_END = _save


# ════════════════════════════════════════════════════════════════════════════
# REGRESSIONS-LÅS MOD RECONCILE OVER-SELL (SPEC_reconcile_oversell)
# Tester den DELTE get_open_orders + det fælles _reconcile_close-mønster, så
# COGT-over-sell-mekanismen er låst for ALLE fire algoer (K2 som repræsentant).
# ════════════════════════════════════════════════════════════════════════════

class PersistentConn:
    """Persistent ordrebog + position der overlever 'genstarter' (friske algo-instanser
    deler SAMME conn = TWS-state overlever). Markedet lukket: place_paper_order fylder
    IKKE og lader ordren ligge resting (get_open_orders ser den næste pas). Markedet
    åbent: fylder + opdaterer positionen. stale_positions overstyrer get_positions
    (simulerer et forsinket positions-feed)."""
    def __init__(self, position=0, market_open=False, stale_positions=None, last=9.0):
        self.connected = True
        self.position = position             # reel net IBKR-position i COGT
        self.market_open = market_open
        self._stale = stale_positions        # hvis sat: get_positions returnerer denne
        self.orderbook = []                  # resting orders (dup-vagtens kilde)
        self.order_calls = []                # ALLE place_paper_order-kald
        self._last = last

    def get_positions(self):
        if self._stale is not None:
            return self._stale
        return [{"ticker": "COGT", "position": self.position}] if self.position else []

    async def get_snapshot(self, ticker):
        return {"last": self._last}

    async def get_open_orders(self):
        return list(self.orderbook)

    async def place_paper_order(self, ticker, action, quantity, source="",
                                await_fill_sec=0, **kw):
        self.order_calls.append((ticker, action, quantity))
        if self.market_open:                 # fylder straks
            self.position += (-quantity if action.upper() == "SELL" else quantity)
            return {"filled": quantity, "avg_fill": self._last, "status": "Filled",
                    "order_id": len(self.order_calls)}
        # markedet lukket -> ordren ligger resting (ufyldt), som en GTC fra en tidl. session
        self.orderbook.append({"symbol": ticker.upper(), "action": action.upper(),
                               "remaining": float(quantity), "status": "PreSubmitted",
                               "orderRef": source})
        return {"filled": 0, "avg_fill": 0, "status": "PreSubmitted",
                "order_id": len(self.order_calls)}

    def fill_resting(self):
        """Simulér at markedet åbner og de hvilende ordrer fylder: anvend dem på
        positionen og ryd ordrebogen (de forsvinder så fra openTrades)."""
        for o in self.orderbook:
            self.position += (-int(o["remaining"]) if o["action"] == "SELL"
                              else int(o["remaining"]))
        self.orderbook = []


# ── SEKTION F (Del A) — get_open_orders læser openTrades (lås commit 5fc4aba) ──
def section_F():
    print("\nSektion F (Del A) — get_open_orders læser openTrades (ikke den tomme returværdi)")
    conn = ibkr_connect.IBKRConnection.__new__(ibkr_connect.IBKRConnection)
    conn._connect_attempted = True
    conn.paper = True
    resting = types.SimpleNamespace(
        order=types.SimpleNamespace(action="SELL", totalQuantity=14, orderRef="Konfluens 2"),
        orderStatus=types.SimpleNamespace(status="PreSubmitted", remaining=14),
        contract=types.SimpleNamespace(symbol="COGT"))

    class MockIB:
        async def reqAllOpenOrdersAsync(self):
            return []                        # upålidelig returværdi (tom for cross-client)
        def openTrades(self):
            return [resting]                 # akkumuleret state = cross-client sandhed

    conn.ib = MockIB()
    out = asyncio.run(conn.get_open_orders())
    cogt = [o for o in out if o["symbol"] == "COGT" and o["action"] == "SELL"]
    check("F1 get_open_orders fandt COGT-SELL via openTrades (ikke tom returværdi)",
          len(cogt) == 1 and cogt[0]["remaining"] == 14.0, out)
    # Hvis nogen reverterer til 'return await reqAllOpenOrdersAsync()' fejler F1 — pointen.


# ── SEKTION G (Del B) — cross-restart: ingen over-sell (alle algoer) ───────────
def section_G():
    print("\nSektion G (Del B) — cross-restart: dup-vagt forhindrer over-sell over 5 genstarter")
    conn = PersistentConn(position=14, market_open=False)   # COGT long +14, marked lukket
    journal = MockJournal()
    install_list_trades([row("COGT", "long", 14)])

    for _ in range(5):                       # 5 'genstarter', frisk algo, SAMME conn
        algo = make_algo(conn, journal)
        asyncio.run(algo._reconcile_orphans())

    sells = [c for c in conn.order_calls if c[1] == "SELL"]
    check("G1 pas 1 placerede SELL COGT 14", ("COGT", "SELL", 14) in conn.order_calls, conn.order_calls)
    check("G2 dup-vagt: KUN én SELL over 5 genstarter (ingen over-sell)", len(sells) == 1, conn.order_calls)
    check("G3 total SELL-mængde == 14 (aldrig 28+)", sum(c[2] for c in sells) == 14, sells)
    check("G4 ufyldt → INGEN reconcile_flatten (row forbliver åben)", journal.closes == [], journal.closes)

    conn.fill_resting()                      # den hvilende SELL fylder -> position 0
    algo = make_algo(conn, journal)
    asyncio.run(algo._reconcile_orphans())   # 6. pas
    sells2 = [c for c in conn.order_calls if c[1] == "SELL"]
    check("G5 efter fyldning: 6. pas placerer INGEN ny ordre", len(sells2) == 1, conn.order_calls)
    check("G6 net==0 → bogført lukket præcis ÉN gang (reconcile_phantom)",
          journal.closes == ["reconcile_phantom"], journal.closes)


# ── SEKTION H (Del C) — residual: stale position + fyldt-og-forsvundet ordre ───
#    DATA, ikke en hård lås: resultatet afgør om handelsstien skal røres.
def section_H():
    print("\nSektion H (Del C) — residual-kant: STALE +14 + tom ordrebog  [DATA, ikke hård lås]")
    # Reel position = 0 (en tidl. reconcile-close fyldte ALLEREDE), MEN feedet er bagud:
    conn = PersistentConn(position=0, market_open=False,
                          stale_positions=[{"ticker": "COGT", "position": 14}])  # stale +14
    journal = MockJournal()
    install_list_trades([row("COGT", "long", 14)])           # journal-row endnu ikke lukket
    algo = make_algo(conn, journal)
    asyncio.run(algo._reconcile_orphans())

    extra_sell = [c for c in conn.order_calls if c[1] == "SELL"]
    safe = (len(extra_sell) == 0)
    return report("H1 stale +14 + tom ordrebog → INGEN ny SELL (reel position er flad)",
                  safe, f"placerede {extra_sell}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print("Samlet test: K2 reconcile-oprydning + robust force-close + over-sell-regressionslås")
    section_A()
    section_B()
    section_C()
    section_D()
    section_E()
    section_F()
    section_G()
    print("\nHÅRDE LÅSE BESTÅET (Sektion A–E + Del A + Del B) ✓")

    delc_ok = section_H()
    print("\n" + "=" * 72)
    if delc_ok:
        print("  DEL C: PASS — stale+tom-residualen er IKKE exploitbar på nuværende kode.")
        print("  → Sløjfen er lukket; ingen ændring i handelsstien nødvendig.")
    else:
        print("  DEL C: FAIL (DATA, ikke en regression) — reconcile placerer en SELL på en")
        print("  STALE +14 mens den reelle position er flad. Residualen er ægte under et")
        print("  forsinket positions-feed (reconcile læser positionen som sandhed; _dups er")
        print("  eneste anden vagt, og ordrebogen er tom fordi closen allerede fyldte).")
        print("  → Næste skridt: SPEC_reconcile_idempotens.md (stabil reconcile-close-nøgle")
        print("    pr. trade_id, så der afgives højst ÉN reconcile-close pr. position —")
        print("    uafhængigt af feed-timing). Del C (H1) er accept-kriteriet: skal vende")
        print("    fra FAIL til PASS når fixet er på plads.")
    print("=" * 72)
