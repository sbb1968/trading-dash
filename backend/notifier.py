"""
notifier.py
───────────
Sender push-beskeder til Iben via ntfy.sh.

Setup på telefon:
  1. Installer "ntfy" app (App Store / Google Play)
  2. Abonner på topic: iben-trading-dash-x7k2  (skift til noget unikt)
  3. Test fra terminal: curl -d "Test" https://ntfy.sh/iben-trading-dash-x7k2

Topic-navnet er din "adgangskode" — alle der kender det kan sende
beskeder til din telefon, så hold det privat. Skift NTFY_TOPIC nedenfor
til noget unikt før du tager det i brug.

Placering: C:\\Projects\\trading-dash\\backend\\notifier.py
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger(__name__)

# ── MIDLERTIDIG KILL-SWITCH (2026-07-12) ───────────────────────
# Sat False mens vi er i tvivl om vi overhovedet vil bruge ntfy til mobil-push.
# Mens denne er False sender send() ALDRIG noget — ingen push når telefonen,
# uanset hvilken wrapper der kalder, hvilken priority, eller ignore_quiet_hours.
# Sæt tilbage til True for at genaktivere alle notifikationer i ét hug.
NOTIFICATIONS_ENABLED = False

# ── KONFIG — skift topic til noget unikt ───────────────────────
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC  = "fasteriben"     # ← skift dette til noget privat
NTFY_URL    = f"{NTFY_SERVER}/{NTFY_TOPIC}"

# ── Stilletid: send KUN push i tidsrummet [07:00, 23:00) dansk tid ──
# Uden for vinduet droppes beskeder (fx algoserverens TWS-offline-alert naar TWS
# auto-logger-af ~23.45 hver aften — ellers spammer den telefonen om natten).
_DK_TZ = ZoneInfo("Europe/Copenhagen")
NOTIFY_HOUR_START = 7
NOTIFY_HOUR_END   = 23


def _outside_send_window() -> bool:
    """True hvis NU er uden for sende-vinduet (07-23 dansk tid)."""
    h = datetime.now(_DK_TZ).hour
    return not (NOTIFY_HOUR_START <= h < NOTIFY_HOUR_END)

# Throttle — undgå spam hvis samme fejl gentager sig
_last_sent: dict[str, datetime] = {}
DEDUP_WINDOW_SEC = 300   # samme nøgle sendes max hver 5. min


async def send(
    message: str,
    title: str   = "Trading Dash",
    priority: int = 3,      # 1=min, 3=default, 4=high, 5=urgent (vækker telefon)
    tags: str    = "robot",
    dedup_key: Optional[str] = None,
    ignore_quiet_hours: bool = False,
) -> bool:
    """
    Send push-besked.

    priority:
      1 = lav (lyder ikke)
      3 = normal
      4 = høj (vibrerer)
      5 = urgent (lyder selv i Do Not Disturb — brug kun til kritiske ting)

    dedup_key: hvis sat, sendes samme key max én gang per DEDUP_WINDOW_SEC.
    ignore_quiet_hours: overstyr 07-23-vinduet (kun til ægte kritiske beskeder).
    """
    # MIDLERTIDIG kill-switch — ligger FØR alt andet, så INTET slipper igennem
    # (heller ikke ignore_quiet_hours-beskeder). Se NOTIFICATIONS_ENABLED øverst.
    if not NOTIFICATIONS_ENABLED:
        logger.debug(f"[Notifier] Notifikationer deaktiveret (kill-switch) — dropper: {title}")
        return False

    if not ignore_quiet_hours and _outside_send_window():
        logger.info(f"[Notifier] Uden for sende-vindue (07-23 dansk tid) — dropper: {title}")
        return False

    if dedup_key:
        last = _last_sent.get(dedup_key)
        if last and (datetime.now() - last).total_seconds() < DEDUP_WINDOW_SEC:
            logger.debug(f"[Notifier] Drop dedup'd besked: {dedup_key}")
            return False
        _last_sent[dedup_key] = datetime.now()

    headers = {
        "Title":    title,
        "Priority": str(priority),
        "Tags":     tags,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                NTFY_URL,
                data    = message.encode("utf-8"),
                headers = headers,
                timeout = aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info(f"[Notifier] Sendt: {title} — {message[:60]}")
                    return True
                else:
                    logger.warning(f"[Notifier] HTTP {resp.status}: {await resp.text()}")
                    return False
    except asyncio.TimeoutError:
        logger.warning("[Notifier] Timeout — ntfy.sh svarede ikke")
        return False
    except Exception as e:
        logger.error(f"[Notifier] Fejl: {e}")
        return False


# ── Convenience wrappers ──────────────────────────────────────

# ── AKTIVE notifikationer (2026-05-17) ────────────────────────
# Kun to push-beskeder må sendes til Iben:
#   1. TWS er ikke logget ind  (alert_tws_offline)
#   2. Dagens algo-resultat    (alert_daily_summary)
# Alle øvrige wrappers er DEAKTIVERET via early return — koden er
# bevaret så de let kan reaktiveres senere ved at fjerne return-linjen.
# Begge aktive beskeder kører på priority 3 (normal).

async def alert_tws_offline():
    """TWS er ikke logget ind."""
    await send(
        message   = "TWS er ikke logget ind. Log venligst ind på TWS Paper Trading (port 7497) — algoritmen kan ikke handle.",
        title     = "⚠ TWS skal logges ind",
        priority  = 3,
        tags      = "warning,key",
        dedup_key = "tws_offline",
    )


async def alert_no_fills(strategy: str, attempted: int):
    """Strategien forsøgte at handle, men IBKR fyldte INTET.

    AKTIV — i modsætning til de fleste øvrige alarmer. En strategi der kører uden
    at kunne handle er præcis den tilstand der ellers står ubemærket: journalen ser
    fredelig ud, fordi fantom-rækkerne lukkes med pnl=0, og ingenting råber op.
    EUREVERSION stod sådan 20/7–30/7 2026 — tre uger, ~410 afviste ordrer om dagen.
    """
    await send(
        message   = (f"{strategy} forsøgte {attempted} entry-ordre(r) i dag, men IBKR "
                     f"fyldte INGEN af dem. Strategien handler reelt ikke. "
                     f"Tjek kontodækning/margin og backend-loggen (ibkr_error)."),
        title     = "⛔ Ingen ordrer blev fyldt",
        priority  = 4,
        tags      = "warning,no_entry_sign",
        dedup_key = f"no_fills_{strategy}",
    )


async def alert_algo_started():
    # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
    return
    await send(
        message  = "Momentum ORB er startet og venter på handelsvinduet (09:45 ET).",
        title    = "🤖 Algoritme startet",
        priority = 3,
        tags     = "robot,green_circle",
    )


async def alert_algo_trade(action: str, ticker: str, shares: int, price: float, pnl: Optional[float] = None):
    """action = 'KØB' eller 'SALG'"""
    # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
    return
    if action.upper().startswith("K"):
        msg   = f"KØB {shares} {ticker} @ ${price:.2f}"
        tags  = "chart_with_upwards_trend"
    else:
        pnl_str = f" · P&L ${pnl:+.2f}" if pnl is not None else ""
        msg     = f"SALG {shares} {ticker} @ ${price:.2f}{pnl_str}"
        tags    = "chart_with_downwards_trend"
    await send(message=msg, title=f"📈 {action.upper()} {ticker}", priority=3, tags=tags)


async def alert_daily_summary(trades: int, wins: int, total_pnl: float):
    win_rate = (wins / trades * 100) if trades else 0
    emoji    = "✅" if total_pnl >= 0 else "🔴"
    await send(
        message  = f"{trades} handler · {wins} vundet ({win_rate:.0f}%) · P&L ${total_pnl:+.2f}",
        title    = f"{emoji} Dagens algo-resultat",
        priority = 3,
        tags     = "bar_chart",
    )


async def alert_emergency_stop(reason: str):
    # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
    return
    await send(
        message   = f"Nødstop aktiveret: {reason}",
        title     = "🚨 NØDSTOP",
        priority  = 5,
        tags      = "rotating_light",
        dedup_key = "emergency_stop",
    )


async def alert_backend_error(error: str):
    # DEAKTIVERET 2026-05-17 — Iben vil kun se TWS-offline og dagens resultat
    return
    await send(
        message   = f"Backend-fejl: {error}",
        title     = "⚠ Backend fejl",
        priority  = 4,
        tags      = "warning",
        dedup_key = f"backend_err_{error[:30]}",
    )


# ── Selvtest ──────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def test():
        print(f"Sender testbesked til {NTFY_URL}")
        print(f"Abonner på topic '{NTFY_TOPIC}' i ntfy-appen på din telefon først!\n")
        ok = await send(
            message  = "Hvis du ser denne besked på din telefon virker det 🎉",
            title    = "Trading Dash test",
            priority = 4,
            tags     = "white_check_mark",
        )
        print("✅ Sendt" if ok else "❌ Fejlede")

    asyncio.run(test())
