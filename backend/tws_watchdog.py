"""
tws_watchdog.py
───────────────
Overvåger om TWS er logget ind ved at tjekke port 7497.

Logik:
  - Tjek hvert 30. sekund om port 7497 lytter
  - Hvis port er nede: forsøg socket-handshake (ib_async connect)
  - Efter N fejl i træk → push-besked til Iben
  - Når TWS kommer tilbage → push "TWS tilbage online"

Bruger eksisterende IBKRConnection så vi genbruger samme connection-logik.

Placering: C:\\Projects\\trading-dash\\backend\\tws_watchdog.py
"""

import asyncio
import logging
import socket
from datetime import datetime, timedelta
from typing import Optional, Callable

import notifier

logger = logging.getLogger(__name__)

# ── KONFIG ─────────────────────────────────────────────────────
TWS_HOST              = "127.0.0.1"
TWS_PORT              = 7497
CHECK_INTERVAL_SEC    = 30
FAILS_BEFORE_ALERT    = 3       # 3 × 30s = 90s downtime før vi pinger
SOCKET_TIMEOUT_SEC    = 3.0
QUIET_HOURS_ET        = (22, 6) # send ikke beskeder mellem 22:00 og 06:00 ET
MAX_OFFLINE_ALERTS    = 2       # send max 2 push'er per offline-periode (ingen spam)
REMINDER_DELAY_MIN    = 5       # minutter mellem alert #1 og alert #2

# ── DATA-LIVENESS KONFIG (fang "forbundet men datablind") ──────
# Den eksisterende probe tjekker forbindelsen (port 7497). Disse tjekker DATA:
# tid siden sidste bar_evaluation i journalen. Forbindelsen kan leve mens
# HMDS-datafarmen er død — det er præcis det her fanger.
JOURNAL_DB            = "trading_dash.db"   # relativ til backend/ (uvicorn-cwd)
DATA_BLIND_SEC        = 240     # >4 min uden bar_evaluation i session = datablind
STRATEGY_ALIVE_SEC    = 120     # backend har skrevet ET event indenfor 2 min = loop lever
DATA_REMINDER_MIN     = 5       # minutter mellem datablind-alert #1 og #2
DATA_MAX_ALERTS       = 2       # max push'er per datablind-periode (ingen spam)
SESSION_OPEN_ET       = (9, 30) # US RTH start (K2 / BuyTheDip)
SESSION_CLOSE_ET      = (16, 0) # US RTH slut

# EUREVERSION-session (EU): 02:00–08:00 ET = 08:00–14:00 dansk. 15-min bars.
EU_SESSION_OPEN_ET    = (2, 0)
EU_SESSION_CLOSE_ET   = (8, 0)
# EUREVERSIONs normale afstand mellem bar_evaluation ≈ 900s (15-min bar; sec_bareval er
# MAX over BEGGE instrumenter, så enten MES eller M2K nulstiller den). DATA_BLIND_SEC=240
# ville derfor melde "datablind" ved hver normal bar-gap → falsk-alarm-flod. 1200s = 900s
# normal-gap + ~300s margin; kræver at BÅDE MES og M2K er tavse >20 min = ægte datablind.
# Hæv til 1800 (= to manglende bars) hvis du vil være ekstra konservativ.
DATA_BLIND_SEC_EU     = 1200
# EU-alive-grænse > heartbeat-intervallet (300s), så EU-detektion er kontinuerlig som K2's
# (hvor hyppig bar_evaluation holder sec_any lav). Se spec for begrundelse.
STRATEGY_ALIVE_SEC_EU = 360

# Session-start-grace: minutter efter sessions-åbning hvor en forældet bar_evaluation
# IKKE regnes som datablind (strategien har ikke produceret sin første LIVE bar endnu).
# EUREVERSION: warmup sætter _last_bar_processed + emitterer intet bar_evaluation → første
# live-bar kommer op til ~15 min (én 15-min bar) inde; heartbeatet (5 min) kommer før.
# Uden grace → falsk "EUREVERSION er DATABLIND" ~5-15 min efter EU-åbning hver dag.
US_DATA_GRACE_MIN     = 3      # K2: 1-min bars → første live-bar ~1 min inde
EU_DATA_GRACE_MIN     = 20     # EUREVERSION: 15-min bars → op til ~15 min + margin

# Auto-recovery er OPT-IN og som standard SLÅET FRA. Når True kalder watchdogen
# KUN on_data_blind-callbacken (wired i main.py) — den dræber aldrig selv Gateway.
DATA_AUTO_RECOVER          = False
DATA_RECOVER_AFTER_SEC     = 300   # vent 5 min datablind før recovery forsøges
DATA_RECOVER_COOLDOWN_MIN  = 15    # mindst 15 min mellem recovery-forsøg
DATA_RECOVER_MAX_PER_DAY   = 3


class TWSWatchdog:
    """
    Overvåger TWS-forbindelsen og notificerer Iben hvis den falder.
    """

    def __init__(self, on_status_change: Optional[Callable] = None,
                 on_data_blind: Optional[Callable] = None):
        self._running         = False
        self._task            = None
        self._started_at      = None     # proces-opstart (sættes i start()) — bruges til
                                         # restart-floor mod falsk datablind ved genstart
        self._was_online      = None     # None = uvist, True/False = sidste kendte
        self._fail_count      = 0
        self._on_status       = on_status_change   # callback (online: bool) → broadcastes til UI
        self._last_alert_at   = None
        self._alerts_sent     = 0        # tæller offline-alerts i denne offline-periode

        # ── Data-liveness state (datablind-detektion) ──
        self._on_data_blind        = on_data_blind   # async callback til OPT-IN recovery
        self._data_was_live        = None            # None=uvist, True/False=sidste kendte
        self._data_blind_since      = None
        self._data_alerts_sent     = 0
        self._last_data_alert_at   = None
        self._last_recover_at      = None
        self._recover_count_today  = 0
        self._recover_count_date   = None

    # ─────────────────────────────────────────────────────────
    # Start / Stop
    # ─────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running    = True
        self._started_at = datetime.now()   # ref for proces-restart-floor (se _handle_data_status)
        self._task       = asyncio.create_task(self._loop())
        logger.info(f"[Watchdog] Startet — tjekker {TWS_HOST}:{TWS_PORT} hvert {CHECK_INTERVAL_SEC}s")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[Watchdog] Stoppet")

    # ─────────────────────────────────────────────────────────
    # Loop
    # ─────────────────────────────────────────────────────────

    async def _loop(self):
        while self._running:
            try:
                online = await self._check_tws()
                await self._handle_status(online)
                # Data-liveness: fang "forbundet men datablind" (isoleret, så en
                # data-tjek-fejl aldrig vælter forbindelses-tjekken).
                try:
                    await self._handle_data_status()
                except Exception as e:
                    logger.debug(f"[Watchdog] Data-tjek fejl: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"[Watchdog] Loop-fejl: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SEC)

    async def _check_tws(self) -> bool:
        """
        Returnerer True hvis TWS lytter på port 7497.

        Bruger rå socket — meget hurtigere end fuld ib_async connect,
        og undgår at oprette flere IB-instances der spammer logfilen.
        """
        loop = asyncio.get_event_loop()
        try:
            future = loop.run_in_executor(None, self._socket_probe)
            return await asyncio.wait_for(future, timeout=SOCKET_TIMEOUT_SEC + 1)
        except asyncio.TimeoutError:
            return False
        except Exception as e:
            logger.debug(f"[Watchdog] Probe fejl: {e}")
            return False

    def _socket_probe(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(SOCKET_TIMEOUT_SEC)
        try:
            sock.connect((TWS_HOST, TWS_PORT))
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False
        finally:
            sock.close()

    # ─────────────────────────────────────────────────────────
    # Data-liveness — "forbundet men datablind"
    # ─────────────────────────────────────────────────────────

    def _active_session(self) -> Optional[str]:
        """Hvilken overvåget session er aktiv NU?
        "US" (K2/BuyTheDip, 09:30-16:00 ET), "EU" (EUREVERSION, 02:00-08:00 ET),
        eller None (ingen → overvåg ikke). Man-fre. Vinduerne overlapper ikke;
        08:00-09:30 ET er bevidst dødt (ingen strategi handler der)."""
        import pytz
        et = datetime.now(pytz.timezone("America/New_York"))
        if et.weekday() >= 5:
            return None
        mins = et.hour * 60 + et.minute

        def _within(open_et, close_et):
            return (open_et[0] * 60 + open_et[1]) <= mins < (close_et[0] * 60 + close_et[1])

        if _within(SESSION_OPEN_ET, SESSION_CLOSE_ET):
            return "US"
        if _within(EU_SESSION_OPEN_ET, EU_SESSION_CLOSE_ET):
            return "EU"
        return None

    def _mins_into_session(self, session: str) -> float:
        """Minutter siden den aktive sessions åbning (ET)."""
        import pytz
        et = datetime.now(pytz.timezone("America/New_York"))
        open_et  = EU_SESSION_OPEN_ET if session == "EU" else SESSION_OPEN_ET
        mins_now = et.hour * 60 + et.minute + et.second / 60.0
        return mins_now - (open_et[0] * 60 + open_et[1])

    def _read_data_marks(self):
        """
        Læs journalen READ-ONLY: sekunder siden seneste bar_evaluation (data-puls)
        og sekunder siden seneste event overhovedet (backend-loop lever).
        Returnerer (sec_since_bareval, sec_since_any_event); None hvis ukendt.
        Åbner/lukker forbindelsen pr. kald, så vi ALDRIG pinner WAL'en.
        """
        import sqlite3
        from datetime import timezone
        try:
            con = sqlite3.connect(f"file:{JOURNAL_DB}?mode=ro", uri=True, timeout=2)
        except sqlite3.OperationalError:
            return None, None
        try:
            now = datetime.now(timezone.utc)

            def age(sql):
                row = con.execute(sql).fetchone()
                if not row or not row[0]:
                    return None
                try:
                    ts = datetime.fromisoformat(str(row[0]))
                except ValueError:
                    return None
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return (now - ts).total_seconds()

            bareval = age("SELECT MAX(ts_utc) FROM events WHERE event_type='bar_evaluation'")
            anyev   = age("SELECT MAX(ts_utc) FROM events")
            return bareval, anyev
        finally:
            con.close()

    async def _handle_data_status(self):
        # Kun relevant i en overvåget session (US = K2/BuyTheDip, EU = EUREVERSION)
        session = self._active_session()
        if session is None:
            if self._data_was_live is False:
                self._data_was_live    = None
                self._data_blind_since = None
                self._data_alerts_sent = 0
            return

        # Session-bevidste grænser/etiket. EU har 15-min bars → meget længere tilladt
        # gap end K2's 1-min cadence (ellers falsk alarm ved hver normal bar). EU-alive-
        # grænsen > heartbeat (300s) → kontinuerlig EU-detektion som K2's.
        if session == "EU":
            blind_limit = DATA_BLIND_SEC_EU
            alive_limit = STRATEGY_ALIVE_SEC_EU
            strat_label = "EUREVERSION"
        else:
            blind_limit = DATA_BLIND_SEC
            alive_limit = STRATEGY_ALIVE_SEC
            strat_label = "K2"

        sec_bareval, sec_any = self._read_data_marks()
        # Kan vi ikke læse, eller har ingen strategi skrevet noget for nylig
        # (pulsen er forældet), antager vi "ingen strategi kører" → ingen alarm.
        if sec_bareval is None or sec_any is None or sec_any > alive_limit:
            return

        # ── NO-STRATEGY FLOOR (fix mod falsk datablind ved opstart) ──────────
        # sec_any er en for svag proxy for "en strategi kører": ved backend-opstart
        # skriver Journal/Replication m.fl. startup-events i events-tabellen, så sec_any
        # er frisk (få sekunder) — MENS sec_bareval stadig peger på forrige sessions
        # sidste bar_evaluation (timer/dage gammel, fordi ingen strategi er startet endnu).
        # Den gamle guard (sec_any > alive_limit) fanger derfor IKKE "ingen strategi kører",
        # og resultatet var en falsk "DATABLIND"-push til Iben ved hver out-of-session-
        # genstart (set 2026-06-19: 818859s gap meldt i opstartssekundet, før EUREVERSION
        # var startet).
        #
        # En ÆGTE datablind (strategi kørte, leverede bars for 5-20 min siden, og stoppede)
        # har sec_bareval lige over blind_limit. En urealistisk gammel sec_bareval betyder
        # "ingen strategi har kørt i denne session" → ikke startet, ikke datablind.
        # 3 × blind_limit (EU: 3600s, US: 720s) ligger godt over enhver kørende-men-haltende
        # strategi, men langt under et session-mellemrum.
        NO_STRATEGY_FLOOR_SEC = 3 * blind_limit
        if sec_bareval > NO_STRATEGY_FLOOR_SEC:
            return
        # ────────────────────────────────────────────────────────────────────

        # ── PROCES-RESTART-FLOOR (fix mod falsk datablind LIGE EFTER opstart) ─
        # NO_STRATEGY_FLOOR fanger kun en MEGET gammel bar (et helt session-mellemrum).
        # Ved en genstart MIDT i en session ligger forrige process' sidste bar_evaluation
        # typisk kun 1-20 min tilbage — frisk nok til at slippe under floor'en, men ældre
        # end blind_limit, og sec_any er frisk pga. startup-events (Journal/Replication).
        # Resultat: falsk "DATABLIND" i selve opstartssekundet, før denne process' strategi
        # har nået sin første live-bar (set 2026-06-19: 1329s gap meldt 0 sek efter
        # uvicorn-start, EUREVERSION ikke engang auto-startet på workstation).
        #
        # En ÆGTE datablind kræver at den nu-forældede bar blev produceret AF DENNE process
        # — altså efter opstart. Er seneste bar_evaluation ældre end vores egen oppetid, har
        # ingen strategi i denne process leveret en live-bar endnu → ikke startet / i warmup,
        # ikke datablind. (Næste tick fanger ægte feed-død, så snart oppetiden > bar-alderen.)
        if self._started_at is not None:
            uptime_sec = (datetime.now() - self._started_at).total_seconds()
            if sec_bareval > uptime_sec:
                return
        # ────────────────────────────────────────────────────────────────────

        data_live = sec_bareval <= blind_limit

        if data_live:
            if self._data_was_live is False:
                # ── Data er tilbage ──
                blind_min = ((datetime.now() - self._data_blind_since).total_seconds() / 60.0
                             if self._data_blind_since else 0)
                logger.info(f"[Watchdog] ✅ Datafeed tilbage efter ~{blind_min:.0f} min datablind")
                if self._data_alerts_sent > 0:
                    try:
                        await notifier.send(
                            message  = f"Datafeed er tilbage efter ~{blind_min:.0f} min. {strat_label} evaluerer igen.",
                            title    = "✅ Data tilbage",
                            priority = 3,
                            tags     = "white_check_mark",
                        )
                    except Exception as e:
                        logger.debug(f"[Watchdog] recovery-push fejl: {e}")
            self._data_was_live      = True
            self._data_blind_since   = None
            self._data_alerts_sent   = 0
            self._last_data_alert_at = None
            return

        # Session-start-grace: i opvarmnings-vinduet efter åbning er en forældet
        # bar_evaluation IKKE datablindhed — strategien har bare ikke produceret sin
        # første LIVE bar endnu (EUREVERSIONs warmup emitterer intet bar_evaluation, så
        # den første kommer op til ~15 min inde; heartbeatet kommer før og ville ellers
        # udløse falsk alarm). Efter grace fanges feed-dødt-fra-åbning stadig.
        grace_min = EU_DATA_GRACE_MIN if session == "EU" else US_DATA_GRACE_MIN
        if self._mins_into_session(session) < grace_min:
            return

        # ── DATABLIND (forbundet, men ingen friske bars) ──
        if self._data_blind_since is None:
            self._data_blind_since = datetime.now()
        blind_sec = (datetime.now() - self._data_blind_since).total_seconds()

        # Alert #1
        if self._data_alerts_sent == 0:
            logger.warning(f"[Watchdog] ⚠ DATABLIND — {sec_bareval:.0f}s uden bar_evaluation "
                           f"(loop lever, data død)")
            try:
                await notifier.send(
                    message  = f"{strat_label} er DATABLIND — forbundet, men ingen kurser i "
                               f"{sec_bareval/60:.0f}+ min. Genstart Gateway + backend.",
                    title    = "⚠ Datafeed nede",
                    priority = 5,
                    tags     = "warning",
                )
            except Exception as e:
                logger.debug(f"[Watchdog] datablind-push fejl: {e}")
            self._last_data_alert_at = datetime.now()
            self._data_alerts_sent  += 1

        # Reminder (alert #2)
        elif (self._data_alerts_sent < DATA_MAX_ALERTS and self._last_data_alert_at
              and (datetime.now() - self._last_data_alert_at) > timedelta(minutes=DATA_REMINDER_MIN)):
            logger.warning(f"[Watchdog] ⚠ Stadig datablind efter ~{blind_sec/60:.0f} min")
            try:
                await notifier.send(
                    message  = f"{strat_label} stadig datablind efter ~{blind_sec/60:.0f} min. Sidste påmindelse.",
                    title    = "⏰ Datafeed stadig nede",
                    priority = 5,
                    tags     = "alarm_clock",
                )
            except Exception as e:
                logger.debug(f"[Watchdog] datablind-reminder fejl: {e}")
            self._last_data_alert_at = datetime.now()
            self._data_alerts_sent  += 1

        # ── OPT-IN auto-recovery (default FRA — kalder kun din callback) ──
        if DATA_AUTO_RECOVER and self._on_data_blind and blind_sec >= DATA_RECOVER_AFTER_SEC:
            today = datetime.now().date()
            if self._recover_count_date != today:
                self._recover_count_date  = today
                self._recover_count_today = 0
            cooldown_ok = (self._last_recover_at is None
                           or (datetime.now() - self._last_recover_at)
                           > timedelta(minutes=DATA_RECOVER_COOLDOWN_MIN))
            if cooldown_ok and self._recover_count_today < DATA_RECOVER_MAX_PER_DAY:
                logger.warning(f"[Watchdog] Auto-recovery: kalder on_data_blind "
                               f"(forsøg {self._recover_count_today + 1}/{DATA_RECOVER_MAX_PER_DAY})")
                self._last_recover_at      = datetime.now()
                self._recover_count_today += 1
                try:
                    result = self._on_data_blind()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.exception(f"[Watchdog] on_data_blind fejl: {e}")

        self._data_was_live = False

    # ─────────────────────────────────────────────────────────
    # Status-håndtering
    # ─────────────────────────────────────────────────────────

    async def _handle_status(self, online: bool):
        # Første gennemløb — bare gem status
        if self._was_online is None:
            self._was_online = online
            if not online:
                logger.warning("[Watchdog] TWS er offline ved opstart")
            return

        if online:
            # ── TWS er online ─────────────────────────────────
            if not self._was_online:
                logger.info("[Watchdog] ✅ TWS er tilbage online")
                if self._fail_count >= FAILS_BEFORE_ALERT and self._in_active_hours():
                    # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
                    # await notifier.send(
                    #     message  = "TWS er tilbage online — algoritmen kan handle igen.",
                    #     title    = "✅ TWS forbundet",
                    #     priority = 3,
                    #     tags     = "white_check_mark",
                    # )
                    pass
                self._fail_count  = 0
                self._alerts_sent = 0       # reset så næste offline-periode får sine 2 push'er
            self._was_online = True
        else:
            # ── TWS er offline ────────────────────────────────
            self._fail_count += 1

            # Alert #1: ved 3. fejl i træk (90 sek downtime)
            if self._fail_count == FAILS_BEFORE_ALERT and self._alerts_sent == 0:
                logger.warning(f"[Watchdog] TWS offline i {FAILS_BEFORE_ALERT} tjek — pinger Iben (alert 1/{MAX_OFFLINE_ALERTS})")
                if self._in_active_hours():
                    await notifier.alert_tws_offline()
                    self._last_alert_at = datetime.now()
                    self._alerts_sent  += 1

            # Alert #2 (sidste): 5 min efter alert #1
            elif self._fail_count > FAILS_BEFORE_ALERT and self._alerts_sent < MAX_OFFLINE_ALERTS:
                if self._last_alert_at and (datetime.now() - self._last_alert_at) > timedelta(minutes=REMINDER_DELAY_MIN):
                    if self._in_active_hours():
                        logger.warning(f"[Watchdog] Reminder — TWS stadig offline (alert {self._alerts_sent + 1}/{MAX_OFFLINE_ALERTS})")
                        # DEAKTIVERET 2026-05-17 — Iben vil kun se den primære TWS-offline besked,
                        # ikke sidste-påmindelse-varianten.
                        # await notifier.send(
                        #     message  = "TWS er stadig ikke logget ind. Dette er sidste påmindelse — jeg holder op med at sende beskeder indtil TWS kommer online.",
                        #     title    = "⏰ Sidste TWS-påmindelse",
                        #     priority = 5,
                        #     tags     = "alarm_clock,key",
                        # )
                        self._last_alert_at = datetime.now()
                        self._alerts_sent  += 1

            # Efter MAX_OFFLINE_ALERTS er nået: hold helt op med at sende
            # Stille periode indtil TWS kommer online igen

            self._was_online = False

        if self._on_status:
            try:
                result = self._on_status(online)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug(f"[Watchdog] Callback fejl: {e}")

    def _in_active_hours(self) -> bool:
        """Send ikke beskeder midt om natten."""
        import pytz
        et   = datetime.now(pytz.timezone("America/New_York"))
        hour = et.hour
        start, end = QUIET_HOURS_ET
        # Quiet hours går fra start (aften) til end (morgen) næste dag
        if start > end:
            return not (hour >= start or hour < end)
        return not (start <= hour < end)

    # ─────────────────────────────────────────────────────────
    # Public status (til /status endpoint)
    # ─────────────────────────────────────────────────────────

    @property
    def is_online(self) -> bool:
        return self._was_online is True

    @property
    def status_dict(self) -> dict:
        return {
            "tws_online":       self._was_online is True,
            "fails":            self._fail_count,
            "last_alert":       self._last_alert_at.isoformat() if self._last_alert_at else None,
            "data_live":        self._data_was_live is True,
            "data_blind_since": self._data_blind_since.isoformat() if self._data_blind_since else None,
            "data_alerts":      self._data_alerts_sent,
        }


# ── Selvtest ──────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def test():
        print("Starter watchdog — tjekker TWS hvert 30. sek")
        print("Stop med Ctrl+C\n")
        wd = TWSWatchdog()
        await wd.start()
        try:
            while True:
                await asyncio.sleep(30)
                print(f"Status: {'✅ online' if wd.is_online else '❌ offline'} · fejl: {wd._fail_count}")
        except KeyboardInterrupt:
            await wd.stop()

    asyncio.run(test())