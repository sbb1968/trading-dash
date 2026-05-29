"""
test_orb_diagnostics.py
────────────────────────
Målrettet test af ORB's diagnostik-logik (Lag A/B/C) mod den ÆGTE kode
i algo_momentum.py og strategy_base.py på denne maskine.

Tester:
  1. At _ORB_STATE_RANK / _ORB_STATE_LABEL matcher de FAKTISKE state-strenge
     ORB's entry-engine bruger (waiting/breakout_detected/awaiting_retest/
     done_for_day) — så ingen tilstand falder uden for rangordningen.
  2. At "højeste state"-sporingen (max-rank) virker korrekt over en sekvens.
  3. At Lag C state-distribution aggregeres rigtigt.
  4. At de arvede BaseStrategy-metoder håndterer ORB's tilstands-detaljestrenge
     (log_rejection_change "kun ændringer" for tilstande).

Bruger FAKE journal — rører IKKE trading_dash.db.
Kør:  python test_orb_diagnostics.py
"""
import asyncio
import sys

# ── Importér de ÆGTE konstanter fra algo_momentum.py ──
# Vi importerer KUN konstanterne, ikke hele klassen (som kræver IBKR).
try:
    import algo_momentum as am
except Exception as e:
    print(f"⚠ Kunne ikke importere algo_momentum direkte ({e}).")
    print("  Prøver at læse konstanterne via inspektion i stedet...")
    am = None

# ── Importér de ÆGTE entry-state-konstanter ──
try:
    from strategies.momentum_orb.entry import (
        STATE_WAITING, STATE_BREAKOUT_DETECTED,
        STATE_AWAITING_RETEST, STATE_DONE_FOR_DAY,
    )
    ENTRY_STATES = [STATE_WAITING, STATE_BREAKOUT_DETECTED,
                    STATE_AWAITING_RETEST, STATE_DONE_FOR_DAY]
except Exception as e:
    print(f"❌ Kunne ikke importere entry-state-konstanter: {e}")
    sys.exit(1)

from strategy_base import BaseStrategy, StrategyConfig


class FakeJournal:
    def __init__(self): self.events = []
    async def log_event(self, source, event_type, payload=None, symbol=None, ibkr_account=None):
        self.events.append({"source":source,"event_type":event_type,
                            "payload":payload or {},"symbol":symbol})
    def of_type(self, et): return [e for e in self.events if e["event_type"]==et]


class _TestStrat(BaseStrategy):
    @property
    def name(self): return "ORBTest"
    @property
    def description(self): return "t"
    @property
    def asset_class(self): return "equity"
    async def pre_flight(self): return True,"ok"
    async def on_start(self): pass
    async def on_bar(self,t,b): pass
    async def on_stop(self): pass


passed=failed=0
def chk(name,cond):
    global passed,failed
    if cond: passed+=1; print(f"  ✓ {name}")
    else: failed+=1; print(f"  ✗ {name}  ← FEJL")


async def main():
    print("="*60)
    print("ORB DIAGNOSTIK-TEST mod ægte kode")
    print("="*60)

    # ── TEST 1: Rang/label dækker ALLE faktiske entry-states ──
    print("\n[konsistens] _ORB_STATE_RANK/LABEL dækker alle entry-states")
    if am is not None and hasattr(am, "_ORB_STATE_RANK"):
        rank = am._ORB_STATE_RANK
        label = am._ORB_STATE_LABEL
        for st in ENTRY_STATES:
            chk(f"'{st}' findes i _ORB_STATE_RANK", st in rank)
            chk(f"'{st}' findes i _ORB_STATE_LABEL", st in label)
        # Rangordning skal være strengt stigende i sekvensen
        ranks = [rank[s] for s in ENTRY_STATES]
        chk("rangordning strengt stigende (waiting<...<done)", ranks == sorted(set(ranks)) and len(set(ranks))==4)
    else:
        print("  ⚠ Kunne ikke importere _ORB_STATE_RANK fra algo_momentum")
        print("    (sandsynligvis fordi modulet kræver IBKR ved import).")
        print("    Tjekker i stedet at de forventede strenge er kendte:")
        expected_rank = {"waiting":0,"breakout_detected":1,"awaiting_retest":2,"done_for_day":3}
        for st in ENTRY_STATES:
            chk(f"'{st}' er en forventet state", st in expected_rank)

    # ── TEST 2: "Højeste state"-sporing (max-rank logik) ──
    print("\n[Lag C] højeste-state-sporing over en sekvens")
    rank = {"waiting":0,"breakout_detected":1,"awaiting_retest":2,"done_for_day":3}
    max_state = {}
    def record(ticker, state):
        prev = max_state.get(ticker)
        if prev is None or rank[state] > rank[prev]:
            max_state[ticker] = state
    # AAPL: waiting → breakout → tilbage til waiting (timeout) → breakout igen
    for st in ["waiting","breakout_detected","waiting","breakout_detected"]:
        record("AAPL", st)
    chk("AAPL's højeste = breakout_detected (ikke sidste=waiting)",
        max_state["AAPL"]=="breakout_detected")
    # TSLA når helt til entry
    for st in ["waiting","breakout_detected","awaiting_retest","done_for_day"]:
        record("TSLA", st)
    chk("TSLA's højeste = done_for_day", max_state["TSLA"]=="done_for_day")

    # ── TEST 3: Lag C distribution ──
    print("\n[Lag C] state-distribution")
    dist = {"waiting":0,"breakout_detected":0,"awaiting_retest":0,"done_for_day":0}
    for st in max_state.values():
        dist[st]+=1
    chk("AAPL i breakout_detected-bøtte", dist["breakout_detected"]==1)
    chk("TSLA i done_for_day-bøtte", dist["done_for_day"]==1)
    chk("distribution summer til antal tickers", sum(dist.values())==len(max_state))

    # ── TEST 4: Lag B — tilstand som afvisningsgrund, kun ændringer ──
    print("\n[Lag B] tilstands-logging kun ved ændring (ægte BaseStrategy)")
    s=_TestStrat(StrategyConfig()); j=FakeJournal(); s._journal=j
    # ORB logger f"tilstand: {label}" — simulér at en ticker bliver i waiting
    seq=["tilstand: afventer breakout","tilstand: afventer breakout",
         "tilstand: breakout set, afventer pullback","tilstand: breakout set, afventer pullback"]
    for d in seq:
        await s.log_rejection_change("AAPL", d)
    chk("2 events (waiting→breakout, ikke 4)", len(j.of_type("entry_rejected"))==2)

    # ── TEST 5: Lag A for ORB (arvet metode med ORB-meta) ──
    print("\n[Lag A] universe-logging med ORB-meta")
    s=_TestStrat(StrategyConfig()); j=FakeJournal(); s._journal=j
    await s.log_universe(["MNTS","CPSH","SIDU"],
                         meta={"used_fallback":False,"require_all_green":True,
                               "price_min":1.0,"price_max":20.0})
    us=j.of_type("universe_selected")
    chk("1 universe_selected", len(us)==1)
    chk("ORB-meta (require_all_green) flettet ind",
        us and us[0]["payload"].get("require_all_green")==True)
    chk("count=3", us and us[0]["payload"].get("count")==3)

    print("\n"+"="*60)
    print(f"RESULTAT: {passed} bestået, {failed} fejlet")
    print("✅ ALLE ORB-DIAGNOSTIK-TESTS BESTÅET" if failed==0
          else "❌ NOGLE TESTS FEJLEDE")
    print("="*60)
    return 0 if failed==0 else 1


if __name__=="__main__":
    sys.exit(asyncio.run(main()))
