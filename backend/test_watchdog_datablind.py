"""
test_watchdog_datablind.py
──────────────────────────
Deterministisk test af datablind-detektionen i TWSWatchdog._handle_data_status,
med fokus på PROCES-RESTART-FLOOR-fixet (2026-06-19).

Tester at:
  1. Falsk datablind ved opstart (stale bar fra forrige proces) → INGEN alarm
  2. Ingen strategi kører (bar ældre end uptime, vokser i takt) → INGEN alarm
  3. ÆGTE datablind efter baseline (frisk bar set, så feed dør) → alarm FYRER
  4. Feed dødt fra genstart (kendt falsk-negativ-grænse) → INGEN alarm (dokumenteret)

Sender ALDRIG en rigtig push: notifier.send monkeypatches til at registrere kald.
Rører ikke IBKR, ingen strategier, ingen handler. Ren logik-test.

Kør i backend-mappen:  python test_watchdog_datablind.py
"""

import asyncio
from datetime import datetime, timedelta

import notifier
import tws_watchdog as wd
from tws_watchdog import TWSWatchdog


# ── Fang alle notifier-kald i stedet for at sende dem ──────────
_sent_pushes = []

async def _fake_send(*args, **kwargs):
    _sent_pushes.append(kwargs or {"args": args})

# Patch BEGGE de notifier-funktioner watchdog kan kalde i data-stien
notifier.send = _fake_send


def _make_watchdog(uptime_sec: float) -> TWSWatchdog:
    """Byg en watchdog uden at køre start() (ingen baggrundsloop, ingen IBKR).
    Sæt _started_at så uptime bliver præcis uptime_sec ved kaldstidspunktet."""
    w = TWSWatchdog.__new__(TWSWatchdog)
    # Minimal state som _handle_data_status forventer
    w._started_at        = datetime.now() - timedelta(seconds=uptime_sec)
    w._data_was_live     = None
    w._data_blind_since  = None
    w._data_alerts_sent  = 0
    w._last_data_alert_at = None
    w._on_data_blind     = None
    w._last_recover_at   = None
    w._recover_count_today = 0
    w._recover_count_date  = None
    return w


def _drive(w: TWSWatchdog, *, session: str, sec_bareval: float, sec_any: float,
           mins_into: float):
    """Tving session, data-mærker og minutter-inde, og kør ÉT _handle_data_status-tick."""
    w._active_session    = lambda: session
    w._read_data_marks   = lambda: (sec_bareval, sec_any)
    w._mins_into_session = lambda s: mins_into
    _sent_pushes.clear()
    asyncio.run(w._handle_data_status())
    return list(_sent_pushes)


def _check(name: str, fired: bool, expect_fire: bool):
    ok = (fired == expect_fire)
    status = "✅ PASS" if ok else "❌ FAIL"
    forventet = "alarm" if expect_fire else "INGEN alarm"
    faktisk   = "alarm fyret" if fired else "ingen alarm"
    print(f"  {status}  {name}")
    print(f"          forventet: {forventet:<12} | faktisk: {faktisk}")
    return ok


def main():
    print("=" * 70)
    print("Test: TWSWatchdog datablind — PROCES-RESTART-FLOOR (2026-06-19)")
    print("=" * 70)
    print(f"  EU blind_limit = {wd.DATA_BLIND_SEC_EU}s, alive_limit = {wd.STRATEGY_ALIVE_SEC_EU}s")
    print()

    results = []

    # ── Scenarie 1: Falsk datablind ved opstart ───────────────────────────
    # Stale bar fra forrige proces (1329s), uptime kun 2s. Restart-floor skal
    # blokere (1329 > 2). Dette er PRÆCIS den rapporterede fejl 09:07.
    print("Scenarie 1 — genstart midt i EU-session, INGEN strategi startet endnu")
    print("  (sec_bareval=1329 fra forrige proces, uptime=2s)")
    w = _make_watchdog(uptime_sec=2)
    pushes = _drive(w, session="EU", sec_bareval=1329, sec_any=5, mins_into=43)
    results.append(_check("ingen falsk datablind ved opstart", bool(pushes), expect_fire=False))
    print()

    # ── Scenarie 2: Ingen strategi (bar vokser i takt med uptime) ─────────
    # 10 min senere: bar nu 1929s, uptime 602s. Differens konstant → stadig blok.
    print("Scenarie 2 — 10 min senere, stadig ingen strategi (bar 1929s, uptime 602s)")
    w = _make_watchdog(uptime_sec=602)
    pushes = _drive(w, session="EU", sec_bareval=1929, sec_any=5, mins_into=53)
    results.append(_check("ingen alarm uden kørende strategi", bool(pushes), expect_fire=False))
    print()

    # ── Scenarie 3: ÆGTE datablind efter baseline ────────────────────────
    # Strategien har kørt længe (uptime 10000s) og leveret bars → baseline sat.
    # Nu dør feedet: bar 1300s gammel (>1200 blind_limit, men <<uptime → floor slipper),
    # forbi grace. Alarm SKAL fyre — beviser at vi ikke bare slog alarmen fra.
    print("Scenarie 3 — ÆGTE datablind: strategi kørt længe, så feed dør")
    print("  (uptime=10000s, sec_bareval=1300s > blind_limit, forbi grace)")
    w = _make_watchdog(uptime_sec=10000)
    pushes = _drive(w, session="EU", sec_bareval=1300, sec_any=30, mins_into=120)
    results.append(_check("ægte datablind fyrer stadig", bool(pushes), expect_fire=True))
    print()

    # ── Scenarie 4: Feed dødt fra genstart (kendt falsk-negativ) ──────────
    # Strategi startet, men feed dødt fra t=0. Bar-alder − uptime konstant → blok forever.
    # Dette ER den dokumenterede grænse for uptime-floor-tilgangen. Vi asserter den
    # eksplicit, så adfærden er KENDT, ikke en overraskelse.
    print("Scenarie 4 — feed dødt fra genstart (DOKUMENTERET grænse, ikke en regression)")
    print("  (sec_bareval=1929, uptime=600 → differens konstant → blok)")
    w = _make_watchdog(uptime_sec=600)
    pushes = _drive(w, session="EU", sec_bareval=1929, sec_any=30, mins_into=120)
    results.append(_check("feed-dødt-fra-genstart blokeres (kendt grænse)",
                          bool(pushes), expect_fire=False))
    print()

    # ── Bonus: weekend/ingen session → aldrig alarm ──────────────────────
    print("Scenarie 5 — ingen aktiv session (None) → aldrig alarm")
    w = _make_watchdog(uptime_sec=10000)
    w._active_session  = lambda: None
    w._read_data_marks = lambda: (999999, 999999)
    _sent_pushes.clear()
    asyncio.run(w._handle_data_status())
    results.append(_check("ingen alarm uden for session", bool(_sent_pushes), expect_fire=False))
    print()

    print("=" * 70)
    if all(results):
        print(f"  ✅ ALLE {len(results)} TESTS BESTÅET")
        print("     Falsk datablind undertrykt; ægte datablind fanges stadig.")
    else:
        failed = sum(1 for r in results if not r)
        print(f"  ❌ {failed}/{len(results)} TESTS FEJLEDE — se ovenfor")
    print("=" * 70)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())