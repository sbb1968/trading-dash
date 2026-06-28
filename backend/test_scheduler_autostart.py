#!/usr/bin/env python3
"""
test_scheduler_autostart.py
───────────────────────────
Laaser auto-start af BuyTheDip + Trend Join Long i AlgoScheduler (spejler K2/EUREVERSION).
Driver de RIGTIGE job-action-metoder med mock-callbacks — INGEN TWS, ingen rigtig session.

Verificerer:
  (a) jobbet kalder start med den EKSAKTE strategi-streng,
  (b) jobbet er NO-OP naar instance_role != 'algoserver' (instance-guarden — beskytter den
      delte konto mod dobbelt-start fra en workstation),
  (c) jobbet fyrer naar instance_role == 'algoserver',
  (d) TWS offline -> genforsoeg (returnerer False, starter intet),
  (e) begge jobs er registreret i schedulerens job-liste.

Stilen spejler test_k2_close_robusthed (PASS/FAIL, SystemExit(1) ved fejl).

    python test_scheduler_autostart.py
"""
import asyncio
import sys

import scheduler

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


def make(role, online=True):
    """AlgoScheduler m. mock-callbacks. Returnerer (scheduler, started-liste)."""
    started = []

    async def start_fn(name=""):
        started.append(name)

    async def stop_fn():
        pass

    def summary_fn():
        return {}

    def tws_online_fn():
        return online

    async def reset_fn():
        pass

    s = scheduler.AlgoScheduler(start_fn, stop_fn, summary_fn, tws_online_fn,
                                reset_fn, instance_role=role)
    return s, started


def main():
    print("Test: scheduler auto-start af BuyTheDip + Trend Join Long")

    # (c) + (a) algoserver: hvert job fyrer og kalder start med den EKSAKTE streng (frisk instans)
    s, started = make("algoserver")
    r_btd = asyncio.run(s._job_start_buythedip())
    check("a/c BuyTheDip fyrer paa algoserver + kalder start('BuyTheDip')",
          r_btd is True and started == ["BuyTheDip"], (r_btd, started))
    s, started = make("algoserver")
    r_tjl = asyncio.run(s._job_start_trendjoin())
    check("a/c Trend Join Long fyrer + kalder start('Trend Join Long')",
          r_tjl is True and started == ["Trend Join Long"], (r_tjl, started))

    # (b) workstation: NO-OP (instance-guard) — markerer faerdig, starter INTET
    s, started = make("workstation")
    r_btd = asyncio.run(s._job_start_buythedip())
    r_tjl = asyncio.run(s._job_start_trendjoin())
    check("b BuyTheDip NO-OP paa workstation (guard) — intet startet",
          r_btd is True and started == [], (r_btd, started))
    check("b Trend Join Long NO-OP paa workstation (guard) — intet startet",
          r_tjl is True and started == [], (r_tjl, started))

    # (d) algoserver men TWS offline -> genforsoeg (False), starter intet
    s, started = make("algoserver", online=False)
    r_btd = asyncio.run(s._job_start_buythedip())
    r_tjl = asyncio.run(s._job_start_trendjoin())
    check("d BuyTheDip genforsoeger naar TWS offline (False, intet startet)",
          r_btd is False and started == [], (r_btd, started))
    check("d Trend Join Long genforsoeger naar TWS offline (False, intet startet)",
          r_tjl is False and started == [], (r_tjl, started))

    # (e) begge jobs registreret i job-listen
    s, _ = make("algoserver")
    names = [j.name for j in s._jobs]
    check("e start_buythedip registreret i scheduler", "start_buythedip" in names, names)
    check("e start_trendjoin registreret i scheduler", "start_trendjoin" in names, names)

    # bonus: K2/EUREVERSION urørt (regressions-vagt)
    check("K2 + EUREVERSION stadig registreret",
          "start_konfluens2" in names and "start_europa_reversion" in names, names)

    print("\nALLE TESTS BESTAAET")


if __name__ == "__main__":
    main()
