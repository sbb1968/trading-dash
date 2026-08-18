"""
scheduler.py
────────────
Autonom dagsplan for algoserveren.

På hver handelsdag (kun algoserveren auto-starter strategier):
  01:50 ET  →  Start Europa-reversion (10 min før EU-session 02:00-08:00 ET = 08:00-14:00 DK)
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

# Auto-start af Europa-reversion (KUN algoserveren — se _job_start_europa_reversion).
# Strategien handler i EU-sessionen 02:00-08:00 ET (= 08:00-14:00 DK); vi starter 10 min
# før, så IBKR-forbindelse + warmup er klar før sessionen. Samme genforsøgs-mønster som K2:
# er TWS/Gateway nede ved start, prøver jobbet igen hvert loop-tick indtil EUREV_RETRY_UNTIL_ET
# — sat sent (men før tvangsluk 07:55 ET) så en Gateway der kommer sent op stadig fanges og
# strategien kan handle resten af sessionen.
EUREV_START_ET       = dtime(1, 50)   # 07:50 dansk
EUREV_RETRY_UNTIL_ET = dtime(7, 30)   # giv op efter dette (for sent — session lukker 08:00 ET)

# Auto-start af BuyTheDip + Trend Join Long (KUN algoserveren — instance-guard som K2/EUREV).
# Forskudt 2-5 min EFTER K2's 09:20 — armede laenge foer entry-vinduerne (US-aaben 09:30), og
# forskudt saa de tre US-strategiers pre-flight/scan ikke rammer TWS samtidig (pacing). Samme
# genforsoegs-moenster som K2 (genforsoeg hvert loop-tick indtil *_RETRY_UNTIL_ET). Justerbare.
BTD_START_ET       = dtime(9, 22)   # 15:22 dansk
BTD_RETRY_UNTIL_ET = dtime(9, 42)
TJL_START_ET       = dtime(9, 25)   # 15:25 dansk
TJL_RETRY_UNTIL_ET = dtime(9, 45)

# Daglige friske top-15-lister — KUN algoserveren (serverer til alle maskiner; workstations
# proxy'er). To slots, fordi listerne har forskellig data-natur:
#   - Swing + Buy&Hold bygger paa EOD/fundamental-data -> friske hver MORGEN (backend genstarter
#     ~06:00 DK = 00:00 ET; reset_daily 00:05 ET). Start 00:15 ET saa TWS naar op efter genstart.
#   - Daytrading er en INTRADAG movers-scan der kun finder gappers naar markedet er aktivt ->
#     koeres taet paa US-aabning (09:00 ET = 15:00 DK), lige FOER K2 auto-start (09:20 ET), hvor
#     pre-market-movers findes. Begge har strategi-startenes genforsoegs-moenster hvis TWS er sen.
TOP15_EOD_START_ET        = dtime(0, 15)   # 06:15 dansk — swing + buyhold
TOP15_EOD_RETRY_UNTIL_ET  = dtime(1, 30)   # 07:30 dansk — foer Europa-reversion-prep (01:50 ET)
DAYTRADING_START_ET       = dtime(9,  0)   # 15:00 dansk — daytrading movers (pre-market)
DAYTRADING_RETRY_UNTIL_ET = dtime(9, 18)   # foer K2 auto-start (09:20 ET)

# ── Reconcile som SELVSTAENDIG kontrol (KUN algoserveren) ────────────────────
# ⚠ OPSTARTS-RECONCILE ER IKKE NOK. Den 13-08 loeb den toer for tid, og fem
# positioner fra 12-08 stod aabne i to doegn foer nogen opdagede det. En kontrol
# der kun koerer ét sted, har ét sted at fejle.
#
# ⚠ EFTER MARKEDSLUKNING, ikke under handel. manual_reconcile.py naegter med
# vilje at koere mens en strategi handler; det samme gaelder her. 16:15 ET er
# 15 minutter efter luk — efter tvangslukningen 15:30 og efter at strategierne
# er stoppet, men foer natten saa en aaben position ikke overlever til naeste dag.
RECONCILE_START_ET       = dtime(16, 15)   # 22:15 dansk
RECONCILE_RETRY_UNTIL_ET = dtime(18, 0)    # transient fejl genforsoeges til 18:00 ET

# Ugentligt regime-fingeraftryk (KUN algoserveren). Rent OFFLINE paa cache -> ingen TWS-afhaengighed.
# Koerer efter reset_daily (00:05 ET) og top15_eod (00:15 ET) saa de ikke overlapper. Kun ugens
# foerste handelsdag (run_on=is_first_trading_day_of_week).
REGIME_START_ET       = dtime(0, 30)   # 06:30 dansk, mandag morgen
REGIME_RETRY_UNTIL_ET = dtime(3, 0)    # transient fejl (fx fil-laas) genforsoeges til 03:00 ET

# ── Oekonomisk kalender (eco_kalender.py) ────────────────────────────────────
# Dagligt, og FOER oevevinduet aabner 08:00 dansk (= 02:00 ET). 01:00 ET er 07:00
# dansk — efter den natlige backend-genstart (~06:00 dansk) og foer Europa-reversions
# prep 01:50 ET.
#
# ⚠ KOERER PAA ALLE MASKINER, ikke kun algoserveren. Oekonomiske events er ikke
# maskinspecifikke — CPI falder samme tidspunkt uanset hvilken peer man kigger fra
# (specens §5.1). Ét HTTP-kald pr. maskine pr. dag er langt under rate-graensen, og
# til gengaeld virker Eco Calendar-siden paa en workstation uden at haenge paa at
# algoserveren svarer. En laeseflade der kraever en anden maskine, er nede naar den
# maskine er nede.
#
# ⚠ HOESTEN ER DET UIGENKALDELIGE. Feedet daekker kun indevaerende uge, saa en dag
# uden hoest er en dag der ikke kan hentes igen bagud fra dén vej. Derfor et bredt
# genforsoegs-vindue: fejler nettet kl. 07, proeves der igen indtil 11 dansk.
ECO_START_ET       = dtime(1, 0)    # 07:00 dansk
ECO_RETRY_UNTIL_ET = dtime(5, 0)    # 11:00 dansk

# Auto-start af Relativ Styrke (spor D, KUN algoserveren — instance-guard som K2/BTD/TJL).
# Forskudt EFTER de tre andre US-starter (K2 09:20, BTD 09:22, TJL 09:25) saa pre-flight/scan
# ikke rammer TWS samtidig (pacing). Strategien tager EEN beslutning ved 09:46 ET (naar 09:45-
# baren er komplet — bar-paritet), saa den skal blot vaere armet + have forud-hentet universet
# inden da; retry-vinduet slutter 09:44 (2 min margin til pre-flight foer beslutningen fyrer).
RELSTYRKE_START_ET       = dtime(9, 28)   # 15:28 dansk
RELSTYRKE_RETRY_UNTIL_ET = dtime(9, 44)   # foer beslutningen fyrer (09:46 ET)

# Auto-start af US-reversion (KUN algoserveren — instance-guard som de oevrige).
# Starter PRAECIS ved US-aabning 09:30 ET, som Soeren bad om. Modsat de fire andre
# US-strategier er der ingen grund til at starte foer: loopet venter alligevel paa
# SESSION_START_ET, og foerste mulige entry kraever to faerdige 5m-bars (tidligst
# 09:40), saa der er ti minutters luft til warmup paa begge tidsrammer. Slottet
# 09:30 ligger desuden EFTER RelStyrkes 09:28, saa pre-flight ikke rammer TWS
# samtidig med de andres (pacing).
USREV_START_ET       = dtime(9, 30)    # 15:30 dansk
USREV_RETRY_UNTIL_ET = dtime(14, 30)   # 20:30 dansk = entry-cutoff; efter den kan
                                       # strategien alligevel ikke aabne noget nyt

# Efter-luk shadow-eval af Relativ Styrke (KUN algoserveren). Maaler dagens realiserede
# selection alpha (Route B-beviset) og AKKUMULERER det. Koerer 16:05 ET (=22:05 dansk), efter
# US-luk (16:00) saa dagens event + bars er tilgaengelige. Kraever TWS (historik-pull, egen
# client-id 49 -> ingen konflikt m. backend client 1); gate paa tws_is_online + genforsoeg.
RELSTYRKE_EVAL_START_ET       = dtime(16, 5)   # 22:05 dansk
RELSTYRKE_EVAL_RETRY_UNTIL_ET = dtime(18, 0)   # transient TWS-nede genforsoeges til 18:00 ET

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


def is_first_trading_day_of_week(d: date_cls) -> bool:
    """True hvis d er ugens FOERSTE handelsdag (normalt mandag; ruller til tirsdag hvis
    mandag er en US-helligdag). Bruges til ugentlige jobs."""
    if not is_trading_day(d):
        return False
    monday = d - timedelta(days=d.weekday())      # ISO-ugens mandag
    cur = monday
    while cur < d:
        if is_trading_day(cur):
            return False                          # en tidligere handelsdag i ugen findes
        cur += timedelta(days=1)
    return True


def now_et() -> datetime:
    return datetime.now(ET)


# ─────────────────────────────────────────────────────────────
# Scheduled job
# ─────────────────────────────────────────────────────────────

class ScheduledJob:
    def __init__(self, name: str, et_time: dtime, action: Callable[[], Awaitable],
                 window_end_et: Optional[dtime] = None, retry_until_success: bool = False,
                 run_on: Optional[Callable[[date_cls], bool]] = None):
        self.name        = name
        self.et_time     = et_time
        self.action      = action
        # Genforsøgs-vindue: når sat, er jobbet kørbart i [et_time, window_end_et) og
        # markeres først "kørt" når actionen returnerer truthy (success). Bruges til
        # auto-start hvor TWS kan være nede præcis ved start-tidspunktet.
        self.window_end_et       = window_end_et
        self.retry_until_success = retry_until_success
        # Valgfrit ekstra dag-filter (fx ugentlige jobs: is_first_trading_day_of_week).
        self.run_on              = run_on
        self.last_run_on: Optional[date_cls] = None

    def should_run_now(self, now: datetime) -> bool:
        """True hvis vi er forbi job-tiden i dag og endnu ikke har kørt det.
        For genforsøgs-jobs: kørbart i [et_time, window_end_et) indtil success."""
        if not is_trading_day(now.date()):
            return False
        if self.run_on is not None and not self.run_on(now.date()):
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
        run_top15_eod_fn:  Optional[Callable[[], Awaitable]] = None,
        run_daytrading_fn: Optional[Callable[[], Awaitable]] = None,
        run_regime_fn:     Optional[Callable[[], Awaitable[bool]]] = None,
        run_reconcile_fn:  Optional[Callable[[], Awaitable[bool]]] = None,
        run_eco_kalender_fn: Optional[Callable[[], Awaitable[bool]]] = None,
        run_relstyrke_eval_fn: Optional[Callable[[], Awaitable[bool]]] = None,
        instance_role:     str = "algoserver",
    ):
        self._start_algo     = start_algo_fn
        self._stop_algo      = stop_algo_fn
        self._get_summary    = get_summary_fn
        self._tws_is_online  = tws_is_online_fn
        self._reset_daily    = reset_daily_fn
        self._run_top15_eod  = run_top15_eod_fn   # swing + buyhold (morgen)
        self._run_daytrading = run_daytrading_fn  # daytrading movers (naer US-aaben)
        self._run_regime     = run_regime_fn      # ugentligt regime-fingeraftryk (offline)
        self._run_reconcile  = run_reconcile_fn   # dagligt reconcile efter markedslukning
        self._run_eco_kalender = run_eco_kalender_fn  # daglig oekonomisk kalender (alle maskiner)
        self._run_relstyrke_eval = run_relstyrke_eval_fn  # efter-luk shadow-eval (Route B-bevis)
        # Auto-start jobs (start_algo, daily_summary) kører KUN på algoserveren.
        # På workstation skal pre_flight_check og reset_daily fortsat køre,
        # men strategien startes manuelt af brugeren via UI.
        self._instance_role  = instance_role

        self._running = False
        self._task    = None

        # ORB udfaset 2026-06-10 og FJERNET 2026-06-18 (incl. dens døde scheduler-jobs).
        # Konfluens 2 auto-starter nu 09:20 ET (15:20 DK) — 10 min før US-åbning, så
        # pre-flight + scanner kan nå at køre før markedet åbner. Auto-start sker KUN på
        # algoserveren (instance-guard i _job_start_konfluens2); workstation starter
        # manuelt. reset_daily (midnat ET) er generisk og beholdes.
        self._jobs = [
            ScheduledJob("start_europa_reversion", EUREV_START_ET, self._job_start_europa_reversion,
                         window_end_et=EUREV_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("start_konfluens2", K2_START_ET, self._job_start_konfluens2,
                         window_end_et=K2_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("start_buythedip", BTD_START_ET, self._job_start_buythedip,
                         window_end_et=BTD_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("start_trendjoin", TJL_START_ET, self._job_start_trendjoin,
                         window_end_et=TJL_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("start_relstyrke", RELSTYRKE_START_ET, self._job_start_relstyrke,
                         window_end_et=RELSTYRKE_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("start_us_reversion", USREV_START_ET, self._job_start_us_reversion,
                         window_end_et=USREV_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("relstyrke_shadow_eval", RELSTYRKE_EVAL_START_ET, self._job_relstyrke_eval,
                         window_end_et=RELSTYRKE_EVAL_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("reset_daily",      dtime( 0,  5), self._job_reset_daily),
            ScheduledJob("generate_top15_eod", TOP15_EOD_START_ET, self._job_generate_top15_eod,
                         window_end_et=TOP15_EOD_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("generate_daytrading_top15", DAYTRADING_START_ET, self._job_generate_daytrading,
                         window_end_et=DAYTRADING_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("reconcile_efter_luk", RECONCILE_START_ET,
                         self._job_reconcile_efter_luk,
                         window_end_et=RECONCILE_RETRY_UNTIL_ET,
                         retry_until_success=True),
            ScheduledJob("eco_kalender", ECO_START_ET, self._job_eco_kalender,
                         window_end_et=ECO_RETRY_UNTIL_ET, retry_until_success=True),
            ScheduledJob("generate_regime_fingerprint", REGIME_START_ET, self._job_generate_regime_fingerprint,
                         window_end_et=REGIME_RETRY_UNTIL_ET, retry_until_success=True,
                         run_on=is_first_trading_day_of_week),
        ]

    # ─────────────────────────────────────────────────────────
    # Start / Stop
    # ─────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task    = asyncio.create_task(self._loop())

        auto = (f"Europa-reversion @ {EUREV_START_ET.strftime('%H:%M')}, "
                f"Konfluens 2 @ {K2_START_ET.strftime('%H:%M')}, "
                f"BuyTheDip @ {BTD_START_ET.strftime('%H:%M')}, "
                f"Trend Join Long @ {TJL_START_ET.strftime('%H:%M')}, "
                f"Relativ Styrke @ {RELSTYRKE_START_ET.strftime('%H:%M')}, "
                f"US-reversion @ {USREV_START_ET.strftime('%H:%M')} ET (alle m. genforsøg); "
                f"relstyrke_shadow_eval @ {RELSTYRKE_EVAL_START_ET.strftime('%H:%M')} ET"
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
        """Tidligste kommende auto-start på tværs af auto-start-jobs (Europa-reversion 01:50 ET
        + Konfluens 2 09:20 ET). I dag hvis et af tidspunkterne endnu ikke er passeret og det er
        en handelsdag; ellers første tidspunkt på næste handelsdag."""
        now = now_et()
        start_times = sorted([EUREV_START_ET, K2_START_ET, BTD_START_ET, TJL_START_ET,
                              RELSTYRKE_START_ET, USREV_START_ET])

        if is_trading_day(now.date()):
            for st in start_times:
                cand = ET.localize(datetime.combine(now.date(), st))
                if now < cand:
                    return cand   # næste auto-start senere i dag

        next_day = next_trading_day(now.date())
        return ET.localize(datetime.combine(next_day, start_times[0]))

    # ─────────────────────────────────────────────────────────
    # Jobs
    # ─────────────────────────────────────────────────────────

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

    async def _job_start_europa_reversion(self) -> bool:
        """Auto-start Europa-reversion ~10 min før EU-sessionen (02:00 ET). KUN på algoserveren.

        Spejler _job_start_konfluens2: returnerer True når jobbet er "færdigt for i dag"
        (startet, eller bevidst sprunget over på workstation), False når det bør genforsøges
        (TWS offline). Workstation springer over — ellers ville BÅDE algoserver og workstation
        køre strategien på samme futures → parallel-handler på to konti. start_strategy har egne
        guards (kører-allerede/limits), så et genforsøgs-kald er idempotent.
        """
        if self._instance_role != "algoserver":
            logger.info(
                f"[Scheduler] start_europa_reversion sprunget over — instance_role="
                f"'{self._instance_role}' (ikke 'algoserver'); startes manuelt på workstation"
            )
            return True   # bevidst skip — markér færdig, ingen genforsøg/spam
        if not self._tws_is_online():
            logger.warning(
                "[Scheduler] Kan IKKE auto-starte Europa-reversion — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {EUREV_RETRY_UNTIL_ET.strftime('%H:%M')} ET")
            return False  # genforsøg inden for vinduet
        logger.info("[Scheduler] Auto-starter Europa-reversion")
        await self._start_algo("Europa-reversion")
        return True

    async def _job_start_buythedip(self) -> bool:
        """Auto-start BuyTheDip omkring US-aabning. KUN paa algoserveren (instance-guard som K2).
        Workstation springer over — ellers ville BAADE algoserver og workstation koere strategien
        paa den DELTE konto (DUO509856) -> dobbelte ordrer. start_strategy er idempotent (no-op
        hvis RUNNING), saa et genforsoegs-kald eller en manuelt startet strategi ikke dobbelt-startes."""
        if self._instance_role != "algoserver":
            logger.info(
                f"[Scheduler] start_buythedip sprunget over — instance_role="
                f"'{self._instance_role}' (ikke 'algoserver'); startes manuelt paa workstation")
            return True   # bevidst skip — markér færdig, ingen genforsøg/spam
        if not self._tws_is_online():
            logger.warning(
                "[Scheduler] Kan IKKE auto-starte BuyTheDip — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {BTD_RETRY_UNTIL_ET.strftime('%H:%M')} ET")
            return False  # genforsøg inden for vinduet
        logger.info("[Scheduler] Auto-starter BuyTheDip")
        await self._start_algo("BuyTheDip")
        return True

    async def _job_start_trendjoin(self) -> bool:
        """Auto-start Trend Join Long omkring US-aabning. KUN paa algoserveren (instance-guard som
        K2). Spejler _job_start_buythedip: workstation springer over (manuel start dér), TWS-offline
        -> genforsøg i vinduet, start_strategy idempotent."""
        if self._instance_role != "algoserver":
            logger.info(
                f"[Scheduler] start_trendjoin sprunget over — instance_role="
                f"'{self._instance_role}' (ikke 'algoserver'); startes manuelt paa workstation")
            return True
        if not self._tws_is_online():
            logger.warning(
                "[Scheduler] Kan IKKE auto-starte Trend Join Long — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {TJL_RETRY_UNTIL_ET.strftime('%H:%M')} ET")
            return False
        logger.info("[Scheduler] Auto-starter Trend Join Long")
        await self._start_algo("Trend Join Long")
        return True

    async def _job_start_relstyrke(self) -> bool:
        """Auto-start Relativ Styrke (spor D) forskudt efter de tre andre US-starter. KUN paa
        algoserveren (instance-guard som K2/BTD/TJL). Workstation springer over — ellers ville
        BAADE algoserver og workstation koere strategien paa den DELTE konto (DUO509856) ->
        dobbelte ordrer. start_strategy er idempotent (no-op hvis RUNNING). Strategien tager
        foerst sin ene beslutning 09:46 ET, saa den skal blot vaere armet inden da."""
        if self._instance_role != "algoserver":
            logger.info(
                f"[Scheduler] start_relstyrke sprunget over — instance_role="
                f"'{self._instance_role}' (ikke 'algoserver'); startes manuelt paa workstation")
            return True   # bevidst skip — markér færdig, ingen genforsøg/spam
        if not self._tws_is_online():
            logger.warning(
                "[Scheduler] Kan IKKE auto-starte Relativ Styrke — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {RELSTYRKE_RETRY_UNTIL_ET.strftime('%H:%M')} ET")
            return False  # genforsøg inden for vinduet
        logger.info("[Scheduler] Auto-starter Relativ Styrke")
        await self._start_algo("Relativ Styrke")
        return True

    async def _job_start_us_reversion(self) -> bool:
        """Auto-start US-reversion ved US-aabning (09:30 ET). KUN paa algoserveren.

        Instance-guard som de oevrige: uden den ville BAADE algoserver og workstation
        koere strategien paa den DELTE konto (DUO509856) -> dobbelte MES-ordrer. Og her
        ville det vaere saerlig grimt, fordi EUREVERSION handler samme kontrakt.
        start_strategy har egne guards (koerer-allerede/limits), saa et genforsoegs-kald
        er idempotent."""
        if self._instance_role != "algoserver":
            logger.info(
                f"[Scheduler] start_us_reversion sprunget over — instance_role="
                f"'{self._instance_role}' (ikke 'algoserver'); startes manuelt paa workstation")
            return True   # bevidst skip — markér færdig, ingen genforsøg/spam
        if not self._tws_is_online():
            logger.warning(
                "[Scheduler] Kan IKKE auto-starte US-reversion — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {USREV_RETRY_UNTIL_ET.strftime('%H:%M')} ET")
            return False  # genforsøg inden for vinduet
        logger.info("[Scheduler] Auto-starter US-reversion")
        await self._start_algo("US-reversion")
        return True

    async def _job_relstyrke_eval(self) -> bool:
        """Efter-luk shadow-eval af Relativ Styrke (Route B-beviset), akkumuleret. KUN algoserveren
        (serverer/gemmer resultatet). Kraever TWS (historik-pull) -> gate paa tws_is_online +
        genforsøg i vinduet. Returnerer True naar faerdig for i dag (koert eller bevidst sprunget
        over paa workstation), False ved transient fejl/TWS-nede (genforsøg)."""
        if self._instance_role != "algoserver":
            logger.info("[Scheduler] relstyrke_shadow_eval sprunget over — ikke algoserver")
            return True
        if self._run_relstyrke_eval is None:
            logger.warning("[Scheduler] Ingen run_relstyrke_eval_fn injiceret — springer over")
            return True
        if not self._tws_is_online():
            logger.warning(
                "[Scheduler] Kan IKKE koere relstyrke_shadow_eval — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {RELSTYRKE_EVAL_RETRY_UNTIL_ET.strftime('%H:%M')} ET")
            return False
        ok = await self._run_relstyrke_eval()   # main.py: subprocess med timeout, returnerer bool
        if ok:
            logger.info("[Scheduler] Relativ Styrke shadow-eval koert (selection alpha akkumuleret)")
        else:
            logger.warning("[Scheduler] relstyrke_shadow_eval fejlede — genforsøger i vinduet")
        return ok

    async def _job_reset_daily(self):
        """Nulstil RiskManager-tællere ved midnat ET."""
        if self._reset_daily:
            await self._reset_daily()
            logger.info("[Scheduler] Daglige tællere nulstillet")

    async def _run_top15_job(self, name: str, fn, retry_until: dtime) -> bool:
        """Faelles motor for de daglige top-15-genereringer. KUN algoserveren danner+serverer
        listerne (workstations proxy'er dertil). Returnerer True naar genereringen er sat i gang
        (eller bevidst sprunget over paa en workstation), False naar TWS er nede og jobbet boer
        genforsoeges — samme moenster som strategi-startene. Genereringerne koerer i baggrunden
        (subprocesser + asyncio-task) og er idempotente (springer over hvis en allerede koerer)."""
        if self._instance_role != "algoserver":
            logger.info(f"[Scheduler] {name} sprunget over — instance_role="
                        f"'{self._instance_role}' (kun algoserveren danner+serverer listerne)")
            return True   # bevidst skip — markér færdig
        if fn is None:
            return True   # ingen callback wired -> ingen-op
        if not self._tws_is_online():
            logger.warning(
                f"[Scheduler] Kan IKKE danne {name} endnu — TWS/Gateway offline. "
                f"Genforsøger hvert loop-tick indtil {retry_until.strftime('%H:%M')} ET")
            return False  # genforsøg inden for vinduet
        logger.info(f"[Scheduler] ▶ {name}")
        await fn()
        return True

    async def _job_generate_top15_eod(self) -> bool:
        """Swing + Buy&Hold hver morgen (EOD/fundamental-data — friske uanset markedstid)."""
        return await self._run_top15_job(
            "generate_top15_eod (swing+buyhold)", self._run_top15_eod, TOP15_EOD_RETRY_UNTIL_ET)

    async def _job_generate_daytrading(self) -> bool:
        """Daytrading movers taet paa US-aabning (intradag — kraever aktivt marked/pre-market)."""
        return await self._run_top15_job(
            "generate_daytrading_top15", self._run_daytrading, DAYTRADING_RETRY_UNTIL_ET)

    async def _job_reconcile_efter_luk(self) -> bool:
        """Afstem journal mod broker EFTER markedslukning — uafhaengigt af opstart.

        ⚠ DEN FINDES FORDI OPSTARTS-RECONCILE HAR ÉT STED AT FEJLE. Den 13-08
        loeb den toer for tid paa to strategier, og fem positioner fra 12-08 stod
        aabne i to doegn. Det her er den anden kontrol, paa et andet tidspunkt,
        med et andet fejlmoenster.

        ⚠ KUN ALGOSERVEREN. Den ejer handelskontoen; en workstation ville
        afstemme mod et feed den ikke handler paa.

        ⚠ OG DEN LUKKER IKKE BLINDT. Den genbruger hver strategis EGEN
        _reconcile_orphans (samme adfaerd som opstart): kun strategiens egne
        journal-rows, aldrig positioner uden journal-spor.

        True = faerdig for i dag. False = transient fejl, genforsoeges i vinduet.
        """
        if self._instance_role != "algoserver":
            logger.info("[Scheduler] reconcile_efter_luk sprunget over — ikke algoserver")
            return True
        if self._run_reconcile is None:
            logger.warning("[Scheduler] Ingen run_reconcile_fn injiceret — springer over")
            return True
        ok = await self._run_reconcile()
        if ok:
            logger.info("[Scheduler] Reconcile efter markedslukning gennemfoert")
        else:
            logger.warning("[Scheduler] Reconcile efter luk fejlede — genforsoeger i vinduet")
        return ok

    async def _job_eco_kalender(self) -> bool:
        """Daglig hoest af oekonomiske events. Ingen instance-guard — se noten ved
        ECO_START_ET om hvorfor alle maskiner hoester deres egen kopi.

        ⚠ EN FEJLET HOEST SKAL SES. Returnerer False ved fejl, saa jobbet
        genforsoeges i vinduet, og fejler den hele vejen, staar den i eco_hoest med
        ok=0 og faar /eco/status til at melde stale. Fejler jobbet stille i en uge,
        mister vi en uges historik permanent — det er praecis den fejlklasse
        fingeraftryks-jobbet ramte 3/8 og 10/8 uden at nogen opdagede det.
        """
        if self._run_eco_kalender is None:
            logger.warning("[Scheduler] Ingen run_eco_kalender_fn injiceret — springer over")
            return True
        ok = await self._run_eco_kalender()
        if ok:
            logger.info("[Scheduler] Oekonomisk kalender hoestet")
        else:
            logger.warning("[Scheduler] Eco-kalender-hoest fejlede — genforsoeger i vinduet")
        return ok

    async def _job_generate_regime_fingerprint(self) -> bool:
        """Ugentligt regime-fingeraftryk. KUN algoserveren (serverer til alle maskiner, som
        top15). Rent offline -> ingen TWS-afhaengighed. Returnerer True naar faerdig for i dag
        (koert eller bevidst sprunget over paa workstation), False ved transient fejl (genforsoeg
        i vinduet)."""
        if self._instance_role != "algoserver":
            logger.info("[Scheduler] generate_regime_fingerprint sprunget over — ikke algoserver")
            return True
        if self._run_regime is None:
            logger.warning("[Scheduler] Ingen run_regime_fn injiceret — springer over")
            return True
        ok = await self._run_regime()      # main.py: subprocess med timeout, returnerer bool
        if ok:
            logger.info("[Scheduler] Regime-fingeraftryk genereret")
        else:
            logger.warning("[Scheduler] Regime-fingeraftryk fejlede — genforsoeger i vinduet")
        return ok

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
