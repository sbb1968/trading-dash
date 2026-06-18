"""
test_diagnostics_live.py
─────────────────────────
Målrettet test af diagnostik-loggingen (Lag A/B/C) mod den ÆGTE
BaseStrategy fra strategy_base.py på denne maskine.

Tester de bar-evaluerings/diagnostik-metoder en strategi-loop kalder:
  - log_universe         (Lag A)
  - log_rejection_change (Lag B — "kun ændringer")
  - log_daily_summary    (Lag C)
  - reset_diagnostics    (dagsstart-nulstilling af Lag B-state)

Bruger en FAKE journal der opsamler events i en liste — rører IKKE
trading_dash.db. Ingen oprydning nødvendig efter denne test.

Kør:  python test_diagnostics_live.py
"""
import asyncio
import sys

# Importér den ÆGTE BaseStrategy fra denne backend
try:
    from strategy_base import BaseStrategy, StrategyConfig
except Exception as e:
    print(f"❌ Kunne ikke importere strategy_base: {e}")
    sys.exit(1)


class FakeJournal:
    """Opsamler events i stedet for at skrive til databasen."""
    def __init__(self):
        self.events = []
    async def log_event(self, source, event_type, payload=None, symbol=None, ibkr_account=None):
        self.events.append({
            "source": source, "event_type": event_type,
            "payload": payload or {}, "symbol": symbol,
        })

    def of_type(self, et):
        return [e for e in self.events if e["event_type"] == et]


class _TestStrategy(BaseStrategy):
    """Minimal konkret subklasse — arver de ÆGTE diagnostik-metoder."""
    @property
    def name(self): return "TestStrat"
    @property
    def description(self): return "test"
    @property
    def asset_class(self): return "equity"
    async def pre_flight(self): return True, "ok"
    async def on_start(self): pass
    async def on_bar(self, ticker, bar): pass
    async def on_stop(self): pass


passed = failed = 0
def chk(name, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  ✓ {name}")
    else:
        failed += 1; print(f"  ✗ {name}  ← FEJL")


async def main():
    print("=" * 60)
    print("DIAGNOSTIK-TEST mod ægte BaseStrategy")
    print("=" * 60)

    # ── TEST 1: Lag A — universe ──────────────────────────────
    print("\n[Lag A] log_universe")
    s = _TestStrategy(StrategyConfig())
    j = FakeJournal(); s._journal = j
    await s.log_universe(["AAPL", "TSLA", "NVDA"],
                         meta={"raw_count": 5, "used_fallback": False})
    us = j.of_type("universe_selected")
    chk("præcis 1 universe_selected-event", len(us) == 1)
    chk("payload har tickers", us and us[0]["payload"].get("tickers") == ["AAPL","TSLA","NVDA"])
    chk("payload har count=3", us and us[0]["payload"].get("count") == 3)
    chk("meta (raw_count) flettet ind", us and us[0]["payload"].get("raw_count") == 5)

    # ── TEST 2: Lag B — afvisning, kun ÆNDRINGER ─────────────
    print("\n[Lag B] log_rejection_change — kun ved ændring")
    s = _TestStrategy(StrategyConfig())
    j = FakeJournal(); s._journal = j
    # Simulér en akties bevægelse over dagen: 3 → 3 → 4 → 4 → 5
    seq = [
        ("AAPL", "score 3/6 [T·R···]"),   # ny      → log
        ("AAPL", "score 3/6 [T·R···]"),   # samme   → IKKE log
        ("AAPL", "score 4/6 [TVR···]"),   # ændret  → log
        ("AAPL", "score 4/6 [TVR···]"),   # samme   → IKKE log
        ("AAPL", "score 5/6 [TVRH··]"),   # ændret  → log
    ]
    logged = []
    for tk, detail in seq:
        logged.append(await s.log_rejection_change(tk, detail))
    rej = j.of_type("entry_rejected")
    chk("præcis 3 entry_rejected-events (3→4→5)", len(rej) == 3)
    chk("returværdier: True,False,True,False,True", logged == [True,False,True,False,True])
    chk("symbol sat på event", rej and rej[0]["symbol"] == "AAPL")
    chk("detail bevaret i payload", rej and rej[-1]["payload"].get("detail") == "score 5/6 [TVRH··]")

    # ── TEST 3: To tickers blandes ikke sammen ───────────────
    print("\n[Lag B] to tickers — uafhængig sporing")
    s = _TestStrategy(StrategyConfig())
    j = FakeJournal(); s._journal = j
    await s.log_rejection_change("AAPL", "score 3/6")   # log
    await s.log_rejection_change("TSLA", "score 3/6")   # log (anden ticker)
    await s.log_rejection_change("AAPL", "score 3/6")   # samme som AAPL's sidste → ikke log
    chk("2 events (AAPL+TSLA, ikke 3)", len(j.of_type("entry_rejected")) == 2)

    # ── TEST 4: reset_diagnostics nulstiller Lag B-state ─────
    print("\n[dagsstart] reset_diagnostics")
    s = _TestStrategy(StrategyConfig())
    j = FakeJournal(); s._journal = j
    await s.log_rejection_change("AAPL", "score 4/6")   # log
    await s.log_rejection_change("AAPL", "score 4/6")   # samme → ikke log
    s.reset_diagnostics()                                # ny dag
    await s.log_rejection_change("AAPL", "score 4/6")   # samme detail, men efter reset → SKAL log igen
    chk("2 events (reset tillader gen-logging samme detail)",
        len(j.of_type("entry_rejected")) == 2)

    # ── TEST 5: Lag C — daglig opsummering ───────────────────
    print("\n[Lag C] log_daily_summary")
    s = _TestStrategy(StrategyConfig())
    j = FakeJournal(); s._journal = j
    await s.log_daily_summary({
        "universe_size": 23, "entries": 0, "peak_score": 5,
        "most_missing_condition": "volumen (manglede i 83% af scorede bars)",
    })
    ds = j.of_type("daily_diagnostics")
    chk("præcis 1 daily_diagnostics-event", len(ds) == 1)
    chk("payload bevaret", ds and ds[0]["payload"].get("peak_score") == 5)

    # ── TEST 6: ingen journal → ingen crash ──────────────────
    print("\n[robusthed] ingen journal sat")
    s = _TestStrategy(StrategyConfig())
    s._journal = None
    await s.log_universe(["X"])                 # må ikke crashe
    r = await s.log_rejection_change("X", "y")  # må ikke crashe, returnerer False
    await s.log_daily_summary({})               # må ikke crashe
    chk("log_rejection_change returnerer False uden journal", r is False)
    chk("ingen exceptions uden journal", True)

    print("\n" + "=" * 60)
    print(f"RESULTAT: {passed} bestået, {failed} fejlet")
    print("✅ ALLE DIAGNOSTIK-TESTS BESTÅET" if failed == 0
          else "❌ NOGLE TESTS FEJLEDE — se ovenfor")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
