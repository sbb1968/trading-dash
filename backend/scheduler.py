"""
scheduler.py
────────────
Autonom dagsplan for algoserveren.

På hver handelsdag (kun algoserveren auto-starter strategier):
  09:20 ET  →  Start Konfluens 2 (10 min før US-åbning — tid til pre-flight + scan)
  00:05 ET  →  Reset RiskManager daglige tællere

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

# Auto-start af Konfluens 2 (KUN algoserveren — se _job_start_konfluens2).
# K2 handler fra 09:30 ET; vi starter lidt før, så pre-flight + scanner-warmup er klar
# før åbningen. Genforsøg: er TWS/Gateway nede præcis ved start, prøver jobbet igen hvert
# loop-tick indtil K2_RETRY_UNTIL_ET — så en Gateway der kommer lidt sent op stadig fanges.
K2_START_ET       = dtime(9, 20)   # 15:20 dansk
K2_RETRY_UNTIL_ET = dtime(9, 40)   # giv op efter dette (for sent inde i sessionen)

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
    def __init__(self, name: str, et_time: dtime, action: Callable[[], Awaitable],
                 window_end_et: Optional[dtime] = None, retry_until_success: bool = False):
        self.name        = name
        self.et_time     = et_time
        self.action      = action
        # Genforsøgs-vindue: når sat, er jobbet kørbart i [et_time, window_end_et) og
        # markeres først "kørt" når actionen returnerer truthy (success). Bruges til
        # auto-start hvor TWS kan være nede præcis ved start-tidspunktet.
        self.window_end_et       = window_end_et
        self.retry_until_success = retry_until_success
        self.last_run_on: Optional[date_cls] = None

    def should_run_now(self, now: datetime) -> bool:
        """True hvis vi er forbi job-tiden i dag og endnu ikke har kørt det.
        For genforsøgs-jobs: kørbart i [et_time, window_end_et) indtil success."""
        if not is_trading_day(now.date()):
            return False
        if self.last_run_on == now.date():
            return False
        job_dt = ET.localize(datetime.combine(now.date(), self.et_time))
        if now < job_dt:
            return False
        if self.window_end_et is not None:
            end_dt = ET.localize(datetime.combine(now.date(), self.window_end_et))
            if now >= end_dt:
                return False   # forbi genforsøgs-vinduet → giv op i dag
        return True

    async def run(self, now: datetime):
        # One-shot jobs markeres kørt FØR action (uændret). Genforsøgs-jobs markeres
        # først kørt når action returnerer truthy (success) — ellers prøves igen næste tick.
        # (Loop'et awaiter run() sekventielt, så ingen samtidig re-entry trods sen mark.)
        if not self.retry_until_success:
            self.last_run_on = now.date()
        result = None
        try:
            logger.info(f"[Scheduler] ▶ {self.name} ({now.strftime('%H:%M:%S')} ET)")
            result = await self.action()
        except Exception as e:
            logger.exception(f"[Scheduler] Fejl i job '{self.name}': {e}")
            await notifier.alert_backend_error(f"Scheduler job '{self.name}' fejlede: {e}")
        if self.retry_until_success and result:
            self.last_run_on = now.date()


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

        # ORB udfaset 2026-06-10 (start_algo/pre_flight_check/daily_summary var bundet
        # til ORB). Konfluens 2 auto-starter nu 09:20 ET (15:20 DK) — 10 min før US-
        # åbning, så pre-flight + scanner kan nå at køre før markedet åbner. Auto-start
        # sker KUN på algoserveren (instance-guard i _job_start_konfluens2); workstation
        # starter manuelt. reset_daily (midnat ET) er generisk og beholdes. De gamle
        # _job_preflight / _job_start_algo / _job_daily_summary efterlades urørte (døde).
        self._jobs = [
            ScheduledJob("start_konfluens2", K2_START_ET, self._job_start_konfluens2,
                         window_end_et=K2_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("reset_daily",      dtime( 0,  5), self._job_reset_daily),
        ]

    # ─────────────────────────────────────────────────────────
    # Start / Stop
    # ─────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task    = asyncio.create_task(self._loop())

        auto = (f"Konfluens 2 @ {K2_START_ET.strftime('%H:%M')} ET (genforsøg til "
                f"{K2_RETRY_UNTIL_ET.strftime('%H:%M')})"
                if self._instance_role == "algoserver" else "ingen (manuel start)")
        logger.info(
            f"[Scheduler] Startet ({self._instance_role}) — reset_daily aktiv, "
            f"auto-start: {auto}"
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
        start_time = K2_START_ET   # Konfluens 2 auto-start (15:20 DK)
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

    async def _job_start_konfluens2(self) -> bool:
        """Auto-start Konfluens 2 ~10 min før US-åbning. KUN på algoserveren.

        Returnerer True når jobbet er "færdigt for i dag" (startet, eller bevidst sprunget
        over på workstation), False når det bør genforsøges (TWS offline). Workstation
        springer over: K2 startes dér manuelt — ellers ville BÅDE algoserver og workstation
        køre K2 på samme tickers → parallel-handler på to konti. start_strategy har egne
        guards (kører-allerede/limits), så et genforsøgs-kald er idempotent.
        """
        if self._instance_role != "algoserver":
            logger.info(
                f"[Scheduler] start_konfluens2 sprunget over — instance_role="
                f"'{self._instance_role}' (ikke 'algoserver'); K2 startes manuelt på workstation"
            )
            return True   # bevidst skip — markér færdig, ingen genforsøg/spam
        if not self._tws_is_online():
            logger.warning(
                "[Scheduler] Kan IKKE auto-starte Konfluens 2 — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {K2_RETRY_UNTIL_ET.strftime('%H:%M')} ET")
            return False  # genforsøg inden for vinduet
        logger.info("[Scheduler] Auto-starter Konfluens 2")
        await self._start_algo("Konfluens 2")
        return True

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
