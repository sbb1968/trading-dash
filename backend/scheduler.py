"""
scheduler.py
────────────
Autonom dagsplan for algoserveren.

På hver handelsdag:
  09:30 ET  →  Pre-flight: send påmindelse hvis TWS ikke er logget ind
  09:44 ET  →  Start MomentumORB strategi (1 min før handelsvinduet åbner)
  10:35 ET  →  Send dagens resultat-summary til Iben
  00:00 ET  →  Reset RiskManager daglige tællere

Springer weekender og US helligdage over.

Placering: C:\\Projects\\trading-dash\\backend\\scheduler.py
"""

import asyncio
import logging
from datetime import datetime, timedelta, date as date_cls, time as dtime
from typing import Optional, Callable, Awaitable

import pytz

import notifier

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")
DK = pytz.timezone("Europe/Copenhagen")   # dansk tid — til dual-visning (ET / DK) i UI

# ── US helligdage hvor markedet er lukket (NYSE) ──────────────
# Statisk liste — opdateres manuelt en gang om året.
# 2026 NYSE-lukkede dage:
US_HOLIDAYS_2026 = {
    date_cls(2026, 1, 1),    # New Year's Day
    date_cls(2026, 1, 19),   # MLK Day
    date_cls(2026, 2, 16),   # Presidents Day
    date_cls(2026, 4, 3),    # Good Friday
    date_cls(2026, 5, 25),   # Memorial Day
    date_cls(2026, 6, 19),   # Juneteenth
    date_cls(2026, 7, 3),    # Independence Day observed
    date_cls(2026, 9, 7),    # Labor Day
    date_cls(2026, 11, 26),  # Thanksgiving
    date_cls(2026, 12, 25),  # Christmas
}
US_HOLIDAYS_2027 = {
    date_cls(2027, 1, 1),
    date_cls(2027, 1, 18),
    date_cls(2027, 2, 15),
    date_cls(2027, 3, 26),
    date_cls(2027, 5, 31),
    date_cls(2027, 6, 18),
    date_cls(2027, 7, 5),
    date_cls(2027, 9, 6),
    date_cls(2027, 11, 25),
    date_cls(2027, 12, 24),
}
ALL_HOLIDAYS = US_HOLIDAYS_2026 | US_HOLIDAYS_2027


def is_trading_day(d: date_cls) -> bool:
    """Mandag-fredag, ikke helligdag."""
    if d.weekday() >= 5:
        return False
    if d in ALL_HOLIDAYS:
        return False
    return True


def next_trading_day(after: date_cls) -> date_cls:
    """Næste handelsdag efter given dato."""
    d = after + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def now_et() -> datetime:
    return datetime.now(ET)


# ─────────────────────────────────────────────────────────────
# Scheduled job
# ─────────────────────────────────────────────────────────────

class ScheduledJob:
    def __init__(self, name: str, et_time: dtime, action: Callable[[], Awaitable]):
        self.name        = name
        self.et_time     = et_time
        self.action      = action
        self.last_run_on: Optional[date_cls] = None

    def should_run_now(self, now: datetime) -> bool:
        """True hvis vi er forbi job-tiden i dag og endnu ikke har kørt det."""
        if not is_trading_day(now.date()):
            return False
        if self.last_run_on == now.date():
            return False
        job_dt = ET.localize(datetime.combine(now.date(), self.et_time))
        return now >= job_dt

    async def run(self, now: datetime):
        self.last_run_on = now.date()
        try:
            logger.info(f"[Scheduler] ▶ {self.name} ({now.strftime('%H:%M:%S')} ET)")
            await self.action()
        except Exception as e:
            logger.exception(f"[Scheduler] Fejl i job '{self.name}': {e}")
            await notifier.alert_backend_error(f"Scheduler job '{self.name}' fejlede: {e}")


# ─────────────────────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────────────────────

class AlgoScheduler:
    """
    Autonom scheduler der kører algoritmen hver handelsdag.

    Bruger callbacks så scheduleren ikke kender til strategy_manager direkte
    — det gør den nem at teste.
    """

    def __init__(
        self,
        start_algo_fn:     Callable[[], Awaitable],
        stop_algo_fn:      Callable[[], Awaitable],
        get_summary_fn:    Callable[[], dict],          # returnerer dagens stats
        tws_is_online_fn:  Callable[[], bool],
        reset_daily_fn:    Optional[Callable[[], Awaitable]] = None,
        instance_role:     str = "algoserver",
    ):
        self._start_algo     = start_algo_fn
        self._stop_algo      = stop_algo_fn
        self._get_summary    = get_summary_fn
        self._tws_is_online  = tws_is_online_fn
        self._reset_daily    = reset_daily_fn
        # Auto-start jobs (start_algo, daily_summary) kører KUN på algoserveren.
        # På workstation skal pre_flight_check og reset_daily fortsat køre,
        # men strategien startes manuelt af brugeren via UI.
        self._instance_role  = instance_role

        self._running = False
        self._task    = None

        self._jobs = [
            ScheduledJob("pre_flight_check",  dtime( 9, 30), self._job_preflight),
            ScheduledJob("start_algo",        dtime( 9, 44), self._job_start_algo),
            ScheduledJob("daily_summary",     dtime(10, 35), self._job_daily_summary),
            ScheduledJob("reset_daily",       dtime( 0,  5), self._job_reset_daily),
        ]

    # ─────────────────────────────────────────────────────────
    # Start / Stop
    # ─────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task    = asyncio.create_task(self._loop())

        # Log næste planlagte start
        nxt = self._next_scheduled_start()
        if self._instance_role == "algoserver":
            logger.info(
                f"[Scheduler] Startet (algoserver) — næste algo-start: "
                f"{nxt.strftime('%Y-%m-%d %H:%M ET')}"
            )
        else:
            logger.info(
                f"[Scheduler] Startet ({self._instance_role}) — "
                f"auto-start af algo deaktiveret, kør manuelt fra UI"
            )

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[Scheduler] Stoppet")

    async def _loop(self):
        while self._running:
            try:
                now = now_et()
                for job in self._jobs:
                    if job.should_run_now(now):
                        await job.run(now)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"[Scheduler] Loop-fejl: {e}")
            await asyncio.sleep(20)   # tjek hvert 20. sek

    def _next_scheduled_start(self) -> datetime:
        now = now_et()
        start_time = dtime(9, 44)
        today = now.date()
        today_start = ET.localize(datetime.combine(today, start_time))

        if is_trading_day(today) and now < today_start:
            return today_start

        next_day = next_trading_day(today)
        return ET.localize(datetime.combine(next_day, start_time))

    # ─────────────────────────────────────────────────────────
    # Jobs
    # ─────────────────────────────────────────────────────────

    async def _job_preflight(self):
        """Tjek 14 min før start at TWS er logget ind."""
        if self._tws_is_online():
            logger.info("[Scheduler] Pre-flight: TWS er online ✅")
            return
        # TWS er ikke online — ping Iben med ekstra urgency
        # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
        # await notifier.send(
        #     message  = "Algoritmen starter om 14 minutter (09:44 ET / 15:44 DK) men TWS er ikke logget ind. Log ind nu.",
        #     title    = "⏰ Log ind på TWS",
        #     priority = 5,
        #     tags     = "alarm_clock,key",
        # )

    async def _job_start_algo(self):
        # ── Instance-aware guard ─────────────────────────────────
        # Auto-start må KUN ske på algoserveren. På workstation skal
        # brugeren starte strategien manuelt via UI — ellers ville BÅDE
        # workstation og algoserver køre samme strategi på samme tickers
        # og generere parallel-handler på to forskellige paper-konti.
        if self._instance_role != "algoserver":
            logger.info(
                f"[Scheduler] start_algo job sprunget over — "
                f"instance_role='{self._instance_role}' (ikke 'algoserver')"
            )
            return

        if not self._tws_is_online():
            logger.warning("[Scheduler] Kan ikke starte algoritme — TWS er offline")
            # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
            # await notifier.send(
            #     message  = "Algoritmen blev IKKE startet — TWS er ikke logget ind. Du går glip af dagens handel.",
            #     title    = "🔴 Algo ikke startet",
            #     priority = 5,
            #     tags     = "x,key",
            # )
            return
        await self._start_algo()
        await notifier.alert_algo_started()   # no-op (deaktiveret i notifier.py)

    async def _job_daily_summary(self):
        """Efter algoritmen har lukket alle positioner — send opsummering."""
        # Vent 5 min så alle exits er færdige
        await asyncio.sleep(5)
        try:
            summary = self._get_summary() or {}
            trades  = summary.get("trades", 0)
            wins    = summary.get("wins", 0)
            pnl     = summary.get("total_pnl", 0.0)
            if trades > 0:
                await notifier.alert_daily_summary(trades, wins, pnl)
            # DEAKTIVERET 2026-05-17 — "Ingen handler"-varianten sendes ikke længere;
            # Iben vil kun se dagens resultat når der ER handler.
            # else:
            #     await notifier.send(
            #         message  = "Ingen handler i dag (markedsbetingelser eller ingen breakouts).",
            #         title    = "📊 Dagens algo-resultat",
            #         priority = 2,
            #         tags     = "bar_chart",
            #     )
        except Exception as e:
            logger.exception(f"[Scheduler] Summary fejl: {e}")

    async def _job_reset_daily(self):
        """Nulstil RiskManager-tællere ved midnat ET."""
        if self._reset_daily:
            await self._reset_daily()
            logger.info("[Scheduler] Daglige tællere nulstillet")

    # ─────────────────────────────────────────────────────────
    # Status til /status endpoint
    # ─────────────────────────────────────────────────────────

    @property
    def status_dict(self) -> dict:
        nxt = self._next_scheduled_start()
        return {
            "running":        self._running,
            "now_et":         now_et().strftime("%Y-%m-%d %H:%M:%S"),
            "is_trading_day": is_trading_day(now_et().date()),
            "next_start":     nxt.strftime("%Y-%m-%d %H:%M ET"),
            # Samme tidspunkt i dansk tid (korrekt DST via pytz) — til UI-dual-visning.
            "next_start_dk":  nxt.astimezone(DK).strftime("%Y-%m-%d %H:%M"),
            "jobs": [
                {
                    "name":        j.name,
                    "et_time":     j.et_time.strftime("%H:%M"),
                    "last_run_on": j.last_run_on.isoformat() if j.last_run_on else None,
                }
                for j in self._jobs
            ],
        }


# ── Selvtest ──────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def fake_start():  print("  [TEST] start_algo() kaldt")
    async def fake_stop():   print("  [TEST] stop_algo() kaldt")
    async def fake_reset():  print("  [TEST] reset_daily() kaldt")
    def     fake_summary(): return {"trades": 3, "wins": 2, "total_pnl": 47.50}
    def     fake_online():  return True

    sched = AlgoScheduler(fake_start, fake_stop, fake_summary, fake_online, fake_reset)

    print("Status:")
    for k, v in sched.status_dict.items():
        print(f"  {k}: {v}")

    print(f"\nI dag er handelsdag: {is_trading_day(date_cls.today())}")
    print(f"Næste handelsdag:    {next_trading_day(date_cls.today())}")
