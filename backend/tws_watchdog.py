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


class TWSWatchdog:
    """
    Overvåger TWS-forbindelsen og notificerer Iben hvis den falder.
    """

    def __init__(self, on_status_change: Optional[Callable] = None):
        self._running         = False
        self._task            = None
        self._was_online      = None     # None = uvist, True/False = sidste kendte
        self._fail_count      = 0
        self._on_status       = on_status_change   # callback (online: bool) → broadcastes til UI
        self._last_alert_at   = None
        self._alerts_sent     = 0        # tæller offline-alerts i denne offline-periode

    # ─────────────────────────────────────────────────────────
    # Start / Stop
    # ─────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task    = asyncio.create_task(self._loop())
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
                    await notifier.send(
                        message  = "TWS er tilbage online — algoritmen kan handle igen.",
                        title    = "✅ TWS forbundet",
                        priority = 3,
                        tags     = "white_check_mark",
                    )
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
                        await notifier.send(
                            message  = "TWS er stadig ikke logget ind. Dette er sidste påmindelse — jeg holder op med at sende beskeder indtil TWS kommer online.",
                            title    = "⏰ Sidste TWS-påmindelse",
                            priority = 5,
                            tags     = "alarm_clock,key",
                        )
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
            "tws_online": self._was_online is True,
            "fails":      self._fail_count,
            "last_alert": self._last_alert_at.isoformat() if self._last_alert_at else None,
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
