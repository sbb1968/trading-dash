"""
test_watchdog_session.py
────────────────────────
Unit-test for TWSWatchdog._active_session — den rene tidslogik bag Del B
(watchdoggen blev session-bevidst, så EUREVERSIONs EU-session dækkes).

Tester KUN _active_session (US/EU/None) + at de session-tærskler Del B vælger
imellem faktisk findes med de rigtige værdier. Selve _handle_data_status afhænger
af live-DB + datetime.now → dækkes af spec'ens røgtest, ikke her.

Kør i backend-mappen:  python test_watchdog_session.py
"""

from datetime import datetime as RealDT
import pytz

import tws_watchdog as wd
from tws_watchdog import TWSWatchdog

ET = pytz.timezone("America/New_York")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


class _FakeDatetime:
    """Erstatter modulets `datetime` så .now(tz) returnerer et fast ET-tidspunkt."""
    _fixed = None
    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def session_at(y, mo, d, hh, mm):
    """Byg watchdog, frys tiden til det givne ET-tidspunkt, returnér _active_session()."""
    wd.datetime = _FakeDatetime
    _FakeDatetime._fixed = ET.localize(RealDT(y, mo, d, hh, mm))
    w = TWSWatchdog.__new__(TWSWatchdog)
    try:
        return w._active_session()
    finally:
        wd.datetime = RealDT   # gendan så andre tests ikke rammes


def main():
    print("Test: TWSWatchdog._active_session (Del B — session-bevidst watchdog)")

    # 2026-06-17 er en onsdag (hverdag); 2026-06-20 er en lørdag.
    WED = (2026, 6, 17)
    SAT = (2026, 6, 20)

    # ── US RTH (09:30–16:00 ET) ──
    check("10:00 ET (onsdag) → US", session_at(*WED, 10, 0) == "US", session_at(*WED, 10, 0))
    check("09:30 ET (åbning, inklusiv) → US", session_at(*WED, 9, 30) == "US", session_at(*WED, 9, 30))
    check("15:59 ET (lige før luk) → US", session_at(*WED, 15, 59) == "US", session_at(*WED, 15, 59))
    check("16:00 ET (luk, eksklusiv) → None", session_at(*WED, 16, 0) is None, session_at(*WED, 16, 0))

    # ── EU-session (02:00–08:00 ET) ──
    check("03:00 ET (onsdag) → EU", session_at(*WED, 3, 0) == "EU", session_at(*WED, 3, 0))
    check("02:00 ET (åbning, inklusiv) → EU", session_at(*WED, 2, 0) == "EU", session_at(*WED, 2, 0))
    check("07:59 ET (lige før EU-luk) → EU", session_at(*WED, 7, 59) == "EU", session_at(*WED, 7, 59))

    # ── Døde zoner ──
    check("08:00 ET (EU-luk, eksklusiv) → None", session_at(*WED, 8, 0) is None, session_at(*WED, 8, 0))
    check("08:30 ET (mellem EU og US) → None", session_at(*WED, 8, 30) is None, session_at(*WED, 8, 30))
    check("01:00 ET (før EU) → None", session_at(*WED, 1, 0) is None, session_at(*WED, 1, 0))
    check("20:00 ET (efter US) → None", session_at(*WED, 20, 0) is None, session_at(*WED, 20, 0))

    # ── Weekend: aldrig en session, uanset klokkeslæt ──
    check("lørdag 10:00 ET (US-tid) → None", session_at(*SAT, 10, 0) is None, session_at(*SAT, 10, 0))
    check("lørdag 03:00 ET (EU-tid) → None", session_at(*SAT, 3, 0) is None, session_at(*SAT, 3, 0))

    # ── Tærskel-konstanter (de værdier Del B vælger imellem) ──
    check("DATA_BLIND_SEC (US) = 240", wd.DATA_BLIND_SEC == 240, wd.DATA_BLIND_SEC)
    check("STRATEGY_ALIVE_SEC (US) = 120", wd.STRATEGY_ALIVE_SEC == 120, wd.STRATEGY_ALIVE_SEC)
    check("DATA_BLIND_SEC_EU = 1200 (15-min cadence)", wd.DATA_BLIND_SEC_EU == 1200, wd.DATA_BLIND_SEC_EU)
    check("STRATEGY_ALIVE_SEC_EU = 360 (> 300s heartbeat)", wd.STRATEGY_ALIVE_SEC_EU == 360, wd.STRATEGY_ALIVE_SEC_EU)
    check("EU-alive > heartbeat (kontinuerlig EU-detektion)", wd.STRATEGY_ALIVE_SEC_EU > 300)
    check("EU-blind > US-blind (15-min vs 1-min bars)", wd.DATA_BLIND_SEC_EU > wd.DATA_BLIND_SEC)

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()
