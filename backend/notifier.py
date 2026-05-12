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

import aiohttp

logger = logging.getLogger(__name__)

# ── KONFIG — skift topic til noget unikt ───────────────────────
NTFY_SERVER = "https://ntfy.sh"
NTFY_TOPIC  = "fasteriben"     # ← skift dette til noget privat
NTFY_URL    = f"{NTFY_SERVER}/{NTFY_TOPIC}"

# Throttle — undgå spam hvis samme fejl gentager sig
_last_sent: dict[str, datetime] = {}
DEDUP_WINDOW_SEC = 300   # samme nøgle sendes max hver 5. min


async def send(
    message: str,
    title: str   = "Trading Dash",
    priority: int = 3,      # 1=min, 3=default, 4=high, 5=urgent (vækker telefon)
    tags: str    = "robot",
    dedup_key: Optional[str] = None,
) -> bool:
    """
    Send push-besked.

    priority:
      1 = lav (lyder ikke)
      3 = normal
      4 = høj (vibrerer)
      5 = urgent (lyder selv i Do Not Disturb — brug kun til kritiske ting)

    dedup_key: hvis sat, sendes samme key max én gang per DEDUP_WINDOW_SEC.
    """
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

async def alert_tws_offline():
    """TWS er ikke logget ind — kritisk, vækker telefonen."""
    await send(
        message   = "TWS er ikke logget ind. Log venligst ind på TWS Paper Trading (port 7497) — algoritmen kan ikke handle.",
        title     = "⚠ TWS skal logges ind",
        priority  = 5,
        tags      = "warning,key",
        dedup_key = "tws_offline",
    )


async def alert_algo_started():
    await send(
        message  = "Momentum ORB er startet og venter på handelsvinduet (09:45 ET).",
        title    = "🤖 Algoritme startet",
        priority = 3,
        tags     = "robot,green_circle",
    )


async def alert_algo_trade(action: str, ticker: str, shares: int, price: float, pnl: Optional[float] = None):
    """action = 'KØB' eller 'SALG'"""
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
    await send(
        message   = f"Nødstop aktiveret: {reason}",
        title     = "🚨 NØDSTOP",
        priority  = 5,
        tags      = "rotating_light",
        dedup_key = "emergency_stop",
    )


async def alert_backend_error(error: str):
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
