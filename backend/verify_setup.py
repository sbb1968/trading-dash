"""
verify_setup.py
───────────────
Kør dette script fra backend/-mappen for at verificere at den nye
strategi-arkitektur er installeret korrekt.

Det tjekker fire ting:
  1. Alle forventede filer ligger de rigtige steder
  2. Alle imports virker (ingen syntaks- eller path-fejl)
  3. Strategi-registry kan finde momentum_orb
  4. En lille smoke-test: kør exit-logik gennem et helt scenario

Kør:
    cd C:\\Projects\\Trading_Dash\\backend
    python verify_setup.py
"""

import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────
# Helper til pæn output
# ─────────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
DIM   = "\033[2m"
RESET = "\033[0m"

# På Windows uden Colorama bliver disse til tom streng — det er fint
try:
    import colorama
    colorama.just_fix_windows_console()
except ImportError:
    pass


def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):  print(f"  {RED}✗{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}!{RESET} {msg}")
def note(msg):  print(f"    {DIM}{msg}{RESET}")


# ─────────────────────────────────────────────────────────────────────
# Tjek 1 — Filer på plads
# ─────────────────────────────────────────────────────────────────────

EXPECTED_FILES = [
    "strategies/__init__.py",
    "strategies/base.py",
    "strategies/momentum_orb/__init__.py",
    "strategies/momentum_orb/config.py",
    "strategies/momentum_orb/entry.py",
    "strategies/momentum_orb/exit.py",
    "strategies/momentum_orb/strategy.py",
    "algo_momentum.py",
    "backtest_momentum.py",
]


def check_files() -> bool:
    """Returnér True hvis alle filer findes."""
    print(f"\n[1/4] Filer på plads")
    backend_dir = Path(__file__).parent

    all_ok = True
    for rel_path in EXPECTED_FILES:
        full = backend_dir / rel_path
        if full.exists():
            size_kb = full.stat().st_size / 1024
            ok(f"{rel_path} ({size_kb:.1f} KB)")
        else:
            fail(f"{rel_path} — MANGLER")
            all_ok = False

    return all_ok


# ─────────────────────────────────────────────────────────────────────
# Tjek 2 — Imports virker
# ─────────────────────────────────────────────────────────────────────

def check_imports() -> bool:
    """Tjek at alle moduler kan importeres uden fejl."""
    print(f"\n[2/4] Imports virker")

    imports_to_try = [
        ("strategies.base", "Bar, EntrySignal, ExitDecision, Position"),
        ("strategies.momentum_orb.config", "VARIANTS, LIVE_VARIANT_KEY"),
        ("strategies.momentum_orb.entry",  "MomentumORBEntry"),
        ("strategies.momentum_orb.exit",   "MomentumORBExit, ExitState"),
        ("strategies.momentum_orb.strategy", "MomentumORBStrategy"),
        ("strategies",                     "get_strategy, list_strategies"),
    ]

    all_ok = True
    for module, names in imports_to_try:
        try:
            mod = __import__(module, fromlist=names.split(", "))
            for name in names.split(", "):
                if not hasattr(mod, name):
                    fail(f"{module}: mangler {name!r}")
                    all_ok = False
                    break
            else:
                ok(f"from {module} import {names}")
        except Exception as e:
            fail(f"{module}: {type(e).__name__}: {e}")
            all_ok = False

    return all_ok


# ─────────────────────────────────────────────────────────────────────
# Tjek 3 — Strategi-registry
# ─────────────────────────────────────────────────────────────────────

def check_registry() -> bool:
    """Tjek at registry returnerer en korrekt momentum_orb-instans."""
    print(f"\n[3/4] Strategi-registry")

    try:
        from strategies import get_strategy, list_strategies
    except ImportError as e:
        fail(f"Kan ikke importere registry: {e}")
        return False

    available = list_strategies()
    if "momentum_orb" not in available:
        fail(f"momentum_orb ikke i registry. Tilgængelige: {available}")
        return False
    ok(f"Registry kender {len(available)} strategier: {', '.join(available)}")

    try:
        strat = get_strategy("momentum_orb")
    except Exception as e:
        fail(f"get_strategy('momentum_orb') fejlede: {e}")
        return False

    # Tjek at den har de rigtige attributter (Strategy-protocol)
    required_attrs = ["name", "description", "variants", "live_variant_key",
                      "entry", "exit", "build_day_context"]
    for attr in required_attrs:
        if not hasattr(strat, attr):
            fail(f"Mangler attribut: {attr}")
            return False
    ok(f"Strategi-protocol: alle 7 attributter til stede")

    note(f"name: {strat.name}")
    note(f"varianter: {list(strat.variants.keys())}")
    note(f"live variant: {strat.live_variant_key}")

    # Tjek at de 5 forventede varianter er der
    expected_variants = {"baseline", "A", "B", "C", "D"}
    actual_variants = set(strat.variants.keys())
    missing = expected_variants - actual_variants
    extra   = actual_variants - expected_variants
    if missing:
        fail(f"Mangler varianter: {missing}")
        return False
    if extra:
        warn(f"Ekstra varianter (ikke et problem): {extra}")
    ok(f"Alle 5 varianter på plads: {sorted(actual_variants)}")

    return True


# ─────────────────────────────────────────────────────────────────────
# Tjek 4 — Smoke-test af exit-logik
# ─────────────────────────────────────────────────────────────────────

def check_exit_logic() -> bool:
    """
    Kør et helt scenario gennem exit-engine for at bevise at den virker.

    Scenario: Variant A, entry $10.00, ORB high $10.50, ORB low $9.80.
    - Initial stop = max(ORB Mid 10.15, 1% gulv 9.90) = 10.15
    - Pris stiger til $10.30 (+3%) → BE-stage, men stop forbliver 10.15 (ratcheter)
    - Pris stiger til $10.40 (+4%) → trail aktiveres, trail-stop = 10.244
    - Pris falder til $10.20 → bar_low rammer trail-stop
    """
    print(f"\n[4/4] Exit-logik smoke-test (Variant A)")

    try:
        from strategies.momentum_orb.config import VARIANTS
        from strategies.momentum_orb.exit  import (
            MomentumORBExit, STAGE_INITIAL, STAGE_BREAKEVEN, STAGE_TRAILING,
            REASON_TRAIL, TRADE_END_TIME,
        )
        from strategies.base import EntrySignal, Bar
        from datetime import datetime, time as dtime
    except ImportError as e:
        fail(f"Kan ikke importere moduler til test: {e}")
        return False

    # Forsøg at lave tz-aware timestamps via pytz hvis tilgængelig — ellers naive.
    # check_exit_bar bruger kun bar.time_et som er .time() — så tz har ingen effekt på testen.
    try:
        import pytz
        et = pytz.timezone("America/New_York")
        def ts(h, m): return et.localize(datetime(2026, 5, 13, h, m))
    except ImportError:
        warn("pytz ikke fundet — bruger naive datetime (test virker stadig)")
        def ts(h, m): return datetime(2026, 5, 13, h, m)

    exit_engine = MomentumORBExit()

    # Lav et entry-signal
    signal = EntrySignal(
        ticker="TEST",
        entry_price=10.00,
        entry_time=ts(9, 55),
        metadata={"orb_high": 10.50, "orb_low": 9.80},
    )

    try:
        position = exit_engine.open_position(signal, shares=250, variant_key="A")
    except Exception as e:
        fail(f"open_position fejlede: {e}")
        return False

    # Verificér initial state
    if abs(position.state.stop - 10.15) > 0.001:
        fail(f"Forventede initial stop 10.15 (ORB Mid), fik {position.state.stop}")
        return False
    if abs(position.state.target - 10.40) > 0.001:
        fail(f"Forventede target 10.40, fik {position.state.target}")
        return False
    if position.state.stage != STAGE_INITIAL:
        fail(f"Forventede stage {STAGE_INITIAL}, fik {position.state.stage}")
        return False
    ok(f"Init: stop=$10.15 (ORB Mid), target=$10.40, stage=1")

    # Pris til +3% — BE aktiveres
    exit_engine.update(position, high_seen=10.30, variant_key="A")
    if position.state.stage != STAGE_BREAKEVEN:
        fail(f"Efter +3%: forventede stage {STAGE_BREAKEVEN}, fik {position.state.stage}")
        return False
    # Stop ratcheter — forbliver 10.15 (højere end break-even 10.00)
    if abs(position.state.stop - 10.15) > 0.001:
        fail(f"Stop sænkedes til {position.state.stop} — det er en ratchet-bug!")
        return False
    ok(f"+3%: stage=2 (BE), stop stadig $10.15 (ratchet virker)")

    # Pris til +4% — trail aktiveres
    exit_engine.update(position, high_seen=10.40, variant_key="A")
    if position.state.stage != STAGE_TRAILING:
        fail(f"Efter +4%: forventede stage {STAGE_TRAILING}, fik {position.state.stage}")
        return False
    if position.state.target is not None:
        fail(f"I stage 3 skal target være None, var {position.state.target}")
        return False
    expected_trail = 10.40 * (1 - 0.015)  # 10.244
    if abs(position.state.trail_stop - expected_trail) > 0.001:
        fail(f"Forventede trail-stop $10.244, fik {position.state.trail_stop}")
        return False
    ok(f"+4%: stage=3, target fjernet, trail-stop=$10.244")

    # Pris til +5% — trail flyttes med
    exit_engine.update(position, high_seen=10.50, variant_key="A")
    expected_trail = 10.50 * (1 - 0.015)  # 10.3425
    if abs(position.state.trail_stop - expected_trail) > 0.001:
        fail(f"Trail fulgte ikke med — forventede $10.3425, fik {position.state.trail_stop}")
        return False
    ok(f"+5%: trail flyttede til $10.3425 (følger highest_high)")

    # Pris falder — bar med low=$10.20 < trail-stop → EXIT
    bar = Bar(
        timestamp=ts(10, 10),
        open=10.45, high=10.50, low=10.20, close=10.25,
        volume=50_000,
    )
    decision = exit_engine.check_exit_bar(position, bar, variant_key="A")
    if decision is None:
        fail("Trail-stop ramt men ingen exit-decision!")
        return False
    if decision.reason != REASON_TRAIL:
        fail(f"Forventede reason='trail', fik {decision.reason!r}")
        return False
    if abs(decision.exit_price - 10.3425) > 0.001:
        fail(f"Forventede exit på trail-stop $10.3425, fik {decision.exit_price}")
        return False
    ok(f"Bar low $10.20 < trail $10.3425 → EXIT @ $10.3425, reason='trail'")

    # Force-close test — separat position
    signal2 = EntrySignal(
        ticker="FC",
        entry_price=10.00,
        entry_time=ts(9, 55),
        metadata={"orb_high": 10.50, "orb_low": 9.80},
    )
    pos2 = exit_engine.open_position(signal2, shares=250, variant_key="A")
    # Lille kursbevægelse — intet ramt
    exit_engine.update(pos2, high_seen=10.10, variant_key="A")
    # Bar kl. 10:30 (force-close-tid)
    bar_fc = Bar(
        timestamp=ts(10, 30),
        open=10.05, high=10.10, low=10.00, close=10.05,
        volume=10_000,
    )
    decision = exit_engine.check_exit_bar(pos2, bar_fc, variant_key="A")
    if decision is None:
        fail("Force-close kl. 10:30 ramte ikke!")
        return False
    if decision.reason != "force_close":
        fail(f"Forventede reason='force_close' kl. 10:30, fik {decision.reason!r}")
        return False
    ok(f"Force-close: 10:30 ET → EXIT, reason='force_close'")

    return True


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Trading Dash — Verifikation af strategi-arkitektur")
    print("=" * 60)

    # Sørg for at backend/-mappen er på Python path
    backend_dir = Path(__file__).parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    results = []

    results.append(("Filer", check_files()))

    # Hvis filer mangler, springer vi videre tjek over
    if not results[-1][1]:
        print(f"\n{RED}STOP — kan ikke fortsætte uden alle filer.{RESET}")
        print(f"Tjek at filerne ligger som beskrevet i README.md og kør igen.")
        return False

    results.append(("Imports",       check_imports()))
    results.append(("Registry",      check_registry()))
    results.append(("Exit-logik",    check_exit_logic()))

    # Sammendrag
    print(f"\n{'=' * 60}")
    print(f"  Sammendrag")
    print(f"{'=' * 60}")
    for name, passed in results:
        symbol = f"{GREEN}✓{RESET}" if passed else f"{RED}✗{RESET}"
        print(f"  {symbol} {name}")

    all_passed = all(p for _, p in results)
    if all_passed:
        print(f"\n{GREEN}✓ Alle tjek bestået — du er klar til at køre backtesten.{RESET}")
        print(f"\nNæste skridt:")
        print(f"  python backtest_momentum.py")
        return True
    else:
        print(f"\n{RED}✗ Et eller flere tjek fejlede — se output ovenfor.{RESET}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
