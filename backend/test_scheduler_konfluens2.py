"""
test_scheduler_konfluens2.py
────────────────────────────
Lås for auto-start af Konfluens 2 kl. 09:20 ET på algoserveren.

Tester: jobbet findes på 09:20, instance-guard (kun algoserver), TWS-guard,
at det kalder start_algo("Konfluens 2"), og should_run_now-tidslogikken
(handelsdag/weekend/helligdag + før/efter 09:20).

Kør:  python test_scheduler_konfluens2.py
"""

import asyncio
from datetime import datetime, time as dtime, date as date_cls

import pytz

from scheduler import AlgoScheduler, ET

DK = pytz.timezone("Europe/Copenhagen")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        raise SystemExit(1)


def make_scheduler(role="algoserver", tws_online=True):
    """Scheduler med spions-callbacks. started[] får strategi-navnet ved start."""
    started = []
    async def fake_start(strategy_name="Momentum ORB"):
        started.append(strategy_name)
    async def fake_stop():   pass
    async def fake_reset():  pass
    def     fake_summary():  return {}
    def     fake_online():   return tws_online
    sched = AlgoScheduler(
        start_algo_fn=fake_start, stop_algo_fn=fake_stop,
        get_summary_fn=fake_summary, tws_is_online_fn=fake_online,
        reset_daily_fn=fake_reset, instance_role=role,
    )
    return sched, started


def job_named(sched, name):
    return next((j for j in sched._jobs if j.name == name), None)


def main():
    print("Test: auto-start Konfluens 2 (09:20 ET, kun algoserver)")

    # ── Jobbet findes på 09:20 ET ──
    sched, _ = make_scheduler()
    job = job_named(sched, "start_konfluens2")
    check("Job 'start_konfluens2' findes", job is not None)
    check("Job-tid = 09:20 ET", job.et_time == dtime(9, 20), job.et_time if job else None)
    check("reset_daily findes stadig", job_named(sched, "reset_daily") is not None)
    check("ORB ('start_algo') er IKKE i planen", job_named(sched, "start_algo") is None)

    # ── Instance-guard: algoserver starter K2 (returnerer True = færdig) ──
    sched, started = make_scheduler(role="algoserver", tws_online=True)
    ret = asyncio.run(sched._job_start_konfluens2())
    check("Algoserver + TWS online → start_algo('Konfluens 2') kaldt",
          started == ["Konfluens 2"], started)
    check("Algoserver + TWS online → returnerer True (færdig)", ret is True, ret)

    # ── Instance-guard: workstation starter IKKE (men returnerer True = markér færdig,
    #    så genforsøgs-løkken IKKE spammer hvert tick) ──
    sched, started = make_scheduler(role="workstation", tws_online=True)
    ret = asyncio.run(sched._job_start_konfluens2())
    check("Workstation → IKKE startet (guard mod parallel-handel)", started == [], started)
    check("Workstation → returnerer True (bevidst skip, ingen genforsøg/spam)", ret is True, ret)

    # ── TWS-guard: offline → ingen start, returnerer False (= genforsøg) ──
    sched, started = make_scheduler(role="algoserver", tws_online=False)
    ret = asyncio.run(sched._job_start_konfluens2())
    check("Algoserver + TWS OFFLINE → IKKE startet", started == [], started)
    check("Algoserver + TWS OFFLINE → returnerer False (genforsøg)", ret is False, ret)

    # ── should_run_now: tidslogik (job på 09:20 ET) ──
    sched, _ = make_scheduler()
    job = job_named(sched, "start_konfluens2")
    WED = (2026, 6, 17)   # onsdag, handelsdag
    SAT = (2026, 6, 20)   # lørdag
    HOL = (2026, 6, 19)   # Juneteenth (NYSE-lukket)

    def at(y, mo, d, hh, mm):
        return ET.localize(datetime(y, mo, d, hh, mm))

    job.last_run_on = None
    check("09:21 ET handelsdag (i genforsøgs-vinduet, ikke kørt) → should_run", job.should_run_now(at(*WED, 9, 21)))
    check("09:19 ET handelsdag (før 09:20) → IKKE endnu", not job.should_run_now(at(*WED, 9, 19)))
    check("09:39 ET (sidste minut i vinduet) → should_run", job.should_run_now(at(*WED, 9, 39)))
    check("09:45 ET (efter genforsøgs-vindue 09:40) → IKKE (giv op i dag)",
          not job.should_run_now(at(*WED, 9, 45)))

    job.last_run_on = date_cls(*WED)   # allerede kørt i dag
    check("09:30 ET men allerede kørt i dag → IKKE igen", not job.should_run_now(at(*WED, 9, 30)))

    job.last_run_on = None
    check("Lørdag 10:00 ET → IKKE (weekend)", not job.should_run_now(at(*SAT, 10, 0)))
    check("Juneteenth 10:00 ET → IKKE (US-helligdag)", not job.should_run_now(at(*HOL, 10, 0)))

    # ── run() genforsøgs-markering: TWS offline markerer IKKE kørt (prøver igen),
    #    success markerer kørt (stopper). Tester den faktiske job.run()-sti. ──
    sched, started = make_scheduler(role="algoserver", tws_online=False)
    job = job_named(sched, "start_konfluens2")
    asyncio.run(job.run(at(*WED, 9, 25)))
    check("run() m. TWS offline → last_run_on IKKE sat (genforsøges)", job.last_run_on is None, job.last_run_on)
    check("run() m. TWS offline → K2 ikke startet", started == [], started)

    sched, started = make_scheduler(role="algoserver", tws_online=True)
    job = job_named(sched, "start_konfluens2")
    asyncio.run(job.run(at(*WED, 9, 25)))
    check("run() m. TWS online → last_run_on sat (stopper genforsøg)", job.last_run_on == date_cls(*WED), job.last_run_on)
    check("run() m. TWS online → K2 startet", started == ["Konfluens 2"], started)

    sched, started = make_scheduler(role="workstation", tws_online=True)
    job = job_named(sched, "start_konfluens2")
    asyncio.run(job.run(at(*WED, 9, 25)))
    check("run() på workstation → last_run_on sat (markér færdig, ingen spam)", job.last_run_on == date_cls(*WED), job.last_run_on)

    # ── next_start = 09:20 ET / 15:20 DK ──
    sched, _ = make_scheduler()
    st = sched.status_dict
    check("status next_start viser 09:20 ET", "09:20 ET" in st["next_start"], st["next_start"])
    check("status next_start_dk viser 15:20 (DST)", st["next_start_dk"].endswith("15:20"), st["next_start_dk"])

    print("\nALLE TESTS BESTÅET ✓")


if __name__ == "__main__":
    main()
