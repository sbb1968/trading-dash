"""
orders_tracker.py
─────────────────
Holder styr på alle ordrer placeret af Trading Dash — både manuelle
fra watchlist og automatiske fra algoritmen.

Når en ordre placeres via place_paper_order(), gemmer vi dens IBKR-order-id
plus metadata (kilde, ticker, action, mængde, tidspunkt). Hver gang
get_all_orders() kaldes, beriger vi vores stored entries med live status
fra IBKR (Submitted, Filled, Cancelled osv.) via ib.trades().

Designprincipper:
  - In-memory + filsystem-persistent (orders_log.json) så data overlever restart
  - Ingen krav om journal-databasen — fungerer standalone
  - Cancel-funktionen bruger IBKR's cancelOrder() med vores gemte order_id

Placering: C:\\Projects\\trading-dash\\backend\\orders_tracker.py
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ORDERS_LOG = Path(__file__).parent / "orders_log.json"

# ⚠ Naar DENNE proces startede. Ordrer lagt FOER dette tidspunkt, som aldrig
# er blevet bekraeftet af en live-aflaesning, kan vi ikke udtale os om — se
# get_all_orders. ib.trades() daekker kun den nuvaerende session.
_OPSTART = datetime.now()

# IBKR-statusser opdelt i overordnede grupper for UI
STATUS_OPEN = {"PendingSubmit", "PendingCancel", "PreSubmitted", "Submitted",
               "ApiPending", "Inactive"}
STATUS_DONE = {"Filled"}
STATUS_CANCEL = {"Cancelled", "ApiCancelled"}


def _load_log() -> list[dict]:
    """Indlæs persistent log fra disk."""
    if not ORDERS_LOG.exists():
        return []
    try:
        with open(ORDERS_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[OrdersTracker] Kunne ikke læse log: {e}")
        return []


def _save_log(entries: list[dict]) -> None:
    """Skriv log til disk (overskriv hele fil)."""
    try:
        with open(ORDERS_LOG, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"[OrdersTracker] Kunne ikke skrive log: {e}")


class OrdersTracker:
    """
    Singleton der holder vores eget register af ordrer placeret af Trading Dash.

    Brug:
        tracker.record_placed(order_id=123, source="manual_watchlist",
                              ticker="GME", action="BUY", shares=100, order_type="MKT")
        all_orders = await tracker.get_all_orders(ibkr_conn, period_hours=24)
        result = await tracker.cancel(ibkr_conn, order_id=123)
    """

    def __init__(self):
        self._entries: list[dict] = _load_log()
        # Trim entries ældre end 30 dage så filen ikke vokser uendeligt
        self._prune_old(days=30)

    def _prune_old(self, days: int = 30) -> None:
        cutoff = datetime.now() - timedelta(days=days)
        before = len(self._entries)
        self._entries = [
            e for e in self._entries
            if _parse_ts(e.get("placed_at")) >= cutoff
        ]
        if before != len(self._entries):
            _save_log(self._entries)

    def record_placed(
        self,
        order_id: int,
        source: str,                # "manual_watchlist" eller "Momentum ORB" osv.
        ticker: str,
        action: str,                # "BUY" eller "SELL"
        shares: int,
        order_type: str = "MKT",
        limit_price: Optional[float] = None,
        ibkr_account: Optional[str] = None,
    ) -> None:
        """Registrer en nyplaceret ordre.

        ⚠ `ibkr_account` er ikke pynt. Uden den kan en senere aflaesning ikke
        vide OM den overhovedet kan kende ordren: en ordre lagt gennem
        ordre-Gatewayen paa DUQ441063 findes ikke i en session der styrer
        DUN748991, og fravaeret af svar ligner "ingen aendring".
        """
        entry = {
            "order_id":    int(order_id),
            "source":      source,
            "ticker":      ticker.upper(),
            "action":      action.upper(),
            "shares":      int(shares),
            "order_type":  order_type,
            "limit_price": limit_price,
            "ibkr_account": (ibkr_account or "").upper() or None,
            "placed_at":   datetime.now().isoformat(),
            # ⚠ ET GAET, IKKE EN MAALING. Status saettes optimistisk her og
            # rettes af live-afstemningen — men KUN hvis afstemningen naar den.
            # `bekraeftet` skiller de to, saa et uafstemt gaet ikke kan staa som
            # faktum. Maalt 11-08: to MES-ordrer fra i gaar stod som "2 aabne"
            # med filled=0. De blev fyldt og lukket samme aften.
            "status":      "Submitted",
            "bekraeftet":  False,
            "filled":      0,
            "remaining":   int(shares),
            "avg_fill":    0,
        }
        self._entries.append(entry)
        _save_log(self._entries)
        logger.info(f"[OrdersTracker] Registreret ordre {order_id}: {action} {shares} {ticker}")

    async def get_all_orders(
        self,
        ibkr_conn,
        period_hours: int = 24,
        sources=None,
    ) -> list[dict]:
        """
        Returnér alle ordrer fra de seneste `period_hours` timer, beriget
        med live status fra IBKR.

        Hvis IBKR ikke er forbundet, returneres vores stored entries uden
        live-status (status="UNKNOWN").
        """
        cutoff = datetime.now() - timedelta(hours=period_hours)
        recent = [
            e for e in self._entries
            if _parse_ts(e.get("placed_at")) >= cutoff
            and (sources is None or e.get("source") in sources)
        ]

        if not recent:
            return []

        # Byg lookup fra order_id → live status hvis IBKR er online
        live_status: dict[int, dict] = {}
        if ibkr_conn is not None and ibkr_conn.connected:
            try:
                for trade in ibkr_conn.ib.trades():
                    oid = trade.order.orderId
                    live_status[oid] = {
                        "status":    trade.orderStatus.status,
                        "filled":    float(trade.orderStatus.filled or 0),
                        "remaining": float(trade.orderStatus.remaining or 0),
                        "avg_fill":  float(trade.orderStatus.avgFillPrice or 0),
                    }
            except Exception as e:
                logger.warning(f"[OrdersTracker] Kunne ikke hente live status: {e}")

        # Persistér live-status ind i entryen mens ordren stadig er i sessionen.
        # recent-entries er REFERENCER ind i self._entries, saa mutationen rammer
        # den gemte liste. Skriv kun ved aendring (ikke paa hver poll). Saa viser
        # terminale ordrer fra tidligere sessioner deres GEMTE status efter en
        # natlig genforbindelse/genstart i stedet for "UNKNOWN".
        dirty = False
        for e in recent:
            live = live_status.get(e["order_id"])
            if live:
                if live["status"] != e.get("status") or not e.get("bekraeftet"):
                    e["status"]    = live["status"]
                    e["filled"]    = live["filled"]
                    e["remaining"] = live["remaining"]
                    e["avg_fill"]  = live["avg_fill"]
                    e["bekraeftet"] = True
                    dirty = True
        if dirty:
            _save_log(self._entries)

        # ⚠ ET UBEKRAEFTET GAET MAA IKKE STAA SOM "AABEN".
        #
        # `status` skrives optimistisk til "Submitted" ved placering. Naar
        # afstemningen aldrig naar ordren — fordi ib.trades() kun daekker den
        # nuvaerende session, eller fordi forbindelsen styrer en ANDEN konto —
        # bliver gaettet staaende, og UI'et taeller det som en aaben ordre.
        #
        # Maalt 11-08: to MES-ordrer fra 10-08 stod som "2 aabne · Afsendt ·
        # 0 fyldt". De blev fyldt og lukket samme aften; de laa bare paa
        # DUQ441063, og maskinen spurgte DUN748991 om dem.
        #
        # Her skilles de to. Uafstemt + fra en tidligere koersel = UKENDT, med
        # en note der siger hvorfor. "Vi ved det ikke" er et daarligere svar end
        # sandheden, men et bedre svar end en paastand.
        konto_nu = ""
        if ibkr_conn is not None and getattr(ibkr_conn, "connected", False):
            konto_nu = (getattr(ibkr_conn, "account", "") or "").upper()

        out = []
        for e in sorted(recent, key=lambda x: x.get("placed_at", ""), reverse=True):
            status = e.get("status", "UNKNOWN")
            note = None

            if not e.get("bekraeftet") and _parse_ts(e.get("placed_at")) < _OPSTART:
                e_konto = (e.get("ibkr_account") or "").upper()
                if e_konto and konto_nu and e_konto != konto_nu:
                    note = (f"lagt paa {e_konto}; denne forbindelse styrer "
                            f"{konto_nu} og kan ikke se den")
                elif not konto_nu:
                    note = "ingen IBKR-forbindelse til at bekraefte status"
                else:
                    note = ("aldrig bekraeftet af en live-aflaesning — "
                            "ib.trades() daekker kun den nuvaerende session")
                status = "UNKNOWN"

            out.append({
                **e,
                "status":       status,
                "note":         note,
                "filled":       e.get("filled", 0),
                "remaining":    e.get("remaining", 0),
                "avg_fill":     e.get("avg_fill", 0),
                "status_group": _status_group(status),
            })
        return out

    async def cancel(self, ibkr_conn, order_id: int) -> dict:
        """
        Annullér en åben ordre via IBKR.

        Returnerer dict med {success, error?} så frontend kan vise besked.
        """
        if ibkr_conn is None or not ibkr_conn.connected:
            return {"success": False, "error": "IBKR ikke forbundet"}

        try:
            # Find selve Trade-objektet via order_id
            for trade in ibkr_conn.ib.trades():
                if trade.order.orderId == order_id:
                    ibkr_conn.ib.cancelOrder(trade.order)
                    logger.info(f"[OrdersTracker] Cancel anmodet for ordre {order_id}")
                    return {"success": True}
            return {"success": False, "error": f"Ordre {order_id} ikke fundet i IBKR"}
        except Exception as e:
            logger.exception(f"[OrdersTracker] Cancel fejl: {e}")
            return {"success": False, "error": str(e)}


def _parse_ts(s) -> datetime:
    """Parsér ISO timestamp, fallback til lang fortid hvis ugyldig."""
    if not s:
        return datetime.min
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return datetime.min


def _status_group(status: str) -> str:
    """Klassificér en IBKR-status til en af 4 grupper for UI-farve."""
    if status in STATUS_DONE:
        return "filled"
    if status in STATUS_OPEN:
        return "open"
    if status in STATUS_CANCEL:
        return "cancelled"
    return "unknown"


# ── Singleton ─────────────────────────────────────────────────
_tracker: Optional[OrdersTracker] = None


def get_tracker() -> OrdersTracker:
    global _tracker
    if _tracker is None:
        _tracker = OrdersTracker()
    return _tracker
