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

import asyncio
from datetime import datetime as RealDT, timedelta
import pytz

import notifier
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


def _freeze(y, mo, d, hh, mm):
    wd.datetime = _FakeDatetime
    _FakeDatetime._fixed = ET.localize(RealDT(y, mo, d, hh, mm))


def session_at(y, mo, d, hh, mm):
    """Byg watchdog, frys tiden til det givne ET-tidspunkt, returnér _active_session()."""
    _freeze(y, mo, d, hh, mm)
    w = TWSWatchdog.__new__(TWSWatchdog)
    try:
        return w._active_session()
    finally:
        wd.datetime = RealDT   # gendan så andre tests ikke rammes


def mins_into(session, y, mo, d, hh, mm):
    _freeze(y, mo, d, hh, mm)
    w = TWSWatchdog.__new__(TWSWatchdog)
    try:
        return w._mins_into_session(session)
    finally:
        wd.datetime = RealDT


def _fresh_watchdog():
    """Watchdog-instans med de data-status-felter _handle_data_status rører."""
    w = TWSWatchdog.__new__(TWSWatchdog)
    w._data_was_live      = None
    w._data_blind_since   = None
    w._data_alerts_sent   = 0
    w._last_data_alert_at = None
    w._started_at         = None   # ingen restart-floor → bevarer eksisterende test-semantik
    return w


def run_data_status(y, mo, d, hh, mm, marks, started_min_ago=None):
    """Kør _handle_data_status med frosset tid + mockede data-marks. Returnér antal
    notifier.send-kald (= alarmer udløst). started_min_ago=None → ingen restart-floor
    (langtkørende process); et tal → _started_at sat så mange min før frosset nu."""
    _freeze(y, mo, d, hh, mm)
    w = _fresh_watchdog()
    if started_min_ago is not None:
        w._started_at = _FakeDatetime._fixed - timedelta(minutes=started_min_ago)
    w._read_data_marks = lambda: marks   # (sec_bareval, sec_any)
    sent = []
    orig = notifier.send
    async def _spy(**kw):
        sent.append(kw)
    notifier.send = _spy
    try:
        asyncio.run(w._handle_data_status())
    finally:
        notifier.send = orig
        wd.datetime = RealDT
    return sent


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

    # ── Del C: _mins_into_session (ren tidslogik) ──
    check("EU 02:00 ET → ~0 min inde", abs(mins_into("EU", *WED, 2, 0)) < 0.5, mins_into("EU", *WED, 2, 0))
    check("EU 02:10 ET → ~10 min inde", abs(mins_into("EU", *WED, 2, 10) - 10) < 0.5, mins_into("EU", *WED, 2, 10))
    check("EU 03:00 ET → ~60 min inde", abs(mins_into("EU", *WED, 3, 0) - 60) < 0.5, mins_into("EU", *WED, 3, 0))
    check("US 09:30 ET → ~0 min inde", abs(mins_into("US", *WED, 9, 30)) < 0.5, mins_into("US", *WED, 9, 30))
    check("US 09:45 ET → ~15 min inde", abs(mins_into("US", *WED, 9, 45) - 15) < 0.5, mins_into("US", *WED, 9, 45))

    # ── Del C: grace-gate (regressions-lås på den falske EU-åbnings-alarm) ──
    # Marks der ALENE ville udløse datablind: forældet bar_evaluation (>1200), men frisk
    # heartbeat (≤360) → passerer alive-gaten, data_live=False. Det er præcis åbnings-hullet.
    STALE_BAREVAL_FRESH_ANY = (2000.0, 100.0)
    sent = run_data_status(*WED, 2, 5, STALE_BAREVAL_FRESH_ANY)   # 5 min inde i EU (< grace 20)
    check("Grace: 5 min inde i EU + forældet bar → INGEN alarm (grace undertrykker)",
          len(sent) == 0, sent)
    sent = run_data_status(*WED, 2, 25, STALE_BAREVAL_FRESH_ANY)  # 25 min inde (> grace 20)
    check("Grace: 25 min inde i EU + forældet bar → datablind-alarm udløses",
          len(sent) == 1 and "DATABLIND" in (sent[0].get("message", "")), sent)

    # K2/US uændret mid-session: 11:00 ET (90 min inde) + forældet bar → alarm (grace irrelevant).
    # Bar-alder skal ligge mellem blind_limit (240s) og NO_STRATEGY_FLOOR (3×240=720s) —
    # dvs. en ÆGTE K2-feed-død, ikke et helt session-mellemrum (>720s floores som "ingen
    # strategi"). 400s = ~7 min uden bar = K2 gået datablind mens sessionen kører.
    US_REAL_BLIND = (400.0, 100.0)
    sent = run_data_status(*WED, 11, 0, US_REAL_BLIND)
    check("US mid-session (90 min inde) + forældet bar → alarm (K2-adfærd uændret)",
          len(sent) == 1 and "DATABLIND" in (sent[0].get("message", "")), sent)

    # Frisk bar i grace-vinduet → data_live-grenen (ingen alarm uanset grace).
    sent = run_data_status(*WED, 2, 5, (50.0, 50.0))
    check("Grace-vindue + FRISK bar → ingen alarm (data_live, ikke undertrykt fejlagtigt)",
          len(sent) == 0, sent)

    # ── Proces-restart-floor (regressions-lås på den falske opstart-datablind) ──
    # Scenarie 2026-06-19: genstart 03:07 ET (67 min inde i EU, > grace 20). Seneste
    # bar_evaluation 1329s gammel — fra FORRIGE process, slipper under NO_STRATEGY_FLOOR
    # (3600s) og er ældre end blind_limit (1200s). Frisk heartbeat (startup-events).
    RESTART_STALE = (1329.0, 5.0)
    sent = run_data_status(*WED, 3, 7, RESTART_STALE, started_min_ago=1)   # oppetid 60s
    check("Restart-floor: bar ældre end oppetid (60s) → INGEN alarm (forrige process' bar)",
          len(sent) == 0, sent)
    # Samme marks, men langtkørende process (oppetid 60 min > bar-alder) → ÆGTE datablind.
    sent = run_data_status(*WED, 3, 7, RESTART_STALE, started_min_ago=60)
    check("Restart-floor: langtkørende process + forældet bar → datablind-alarm (ægte feed-død)",
          len(sent) == 1 and "DATABLIND" in (sent[0].get("message", "")), sent)

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()
