"""
trade_queries.py
────────────────
SELECT-queries mod trades-tabellen.

Hvorfor et separat modul:
  - main.py forbliver simpelt — endpoints kalder bare en funktion
  - Samme queries genbruges senere af central sync og analytics
  - Lettere at teste isoleret

Designprincip: Alle queries returnerer plain dicts (ikke dataclasses)
fordi de skal serializes direkte til JSON via FastAPI.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import pytz

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


# Kolonner vi henter for én komplet trade-row.
# Defineret som konstant fordi vi gentager den i flere queries.
_TRADE_COLUMNS = (
    "trade_id, account_id, instance_id, ibkr_account, "
    "source, variant, "
    "symbol, side, shares, "
    "entry_time_utc, entry_time_et, entry_price, entry_reason, "
    "exit_time_utc, exit_time_et, exit_price, exit_reason, "
    "pnl, pnl_pct, duration_sec, "
    "capital_used, "
    "current_stop, current_target, current_stage, trail_stop, "
    "notes, payload_json"
)

_TRADE_COLUMN_NAMES = [c.strip() for c in _TRADE_COLUMNS.split(",")]


def _row_to_dict(row) -> dict:
    """
    Konvertér en SQLite-row (tuple) til dict med vores trade-felter.

    payload_json deserialiseres til et nested object så frontend ikke
    skal parse en JSON-string. Hvis payload er ugyldig JSON, returneres
    en tom dict.
    """
    d = dict(zip(_TRADE_COLUMN_NAMES, row))
    try:
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        d.pop("payload_json", None)
        d["payload"] = {}
    return d


# ─────────────────────────────────────────────────────────────────
# List med filtre
# ─────────────────────────────────────────────────────────────────

async def list_trades(
    db,
    date_from:    Optional[str] = None,   # ISO date "2026-05-19" (ET-handelsdag)
    date_to:      Optional[str] = None,   # ISO date inkl. denne dag
    source:       Optional[str] = None,   # "Momentum ORB", "Konfluens", "manual"
    symbol:       Optional[str] = None,
    status:       Optional[str] = None,   # "open", "closed", None=alle
    account_id:   Optional[str] = None,   # "soren", "iben", None=alle (på lokal: kun denne)
    instance_id:  Optional[str] = None,
    limit:        int = 200,
    offset:       int = 0,
) -> list[dict]:
    """
    Returnér trades der matcher filtre. Sorteret efter entry_time_et DESC
    (nyeste først).

    Datoer filtreres på entry_time_et (handelsdag i ET, ikke UTC).
    """
    if db is None:
        return []

    where = []
    params = []

    if date_from:
        # entry_time_et begynder med "YYYY-MM-DD" — vi sammenligner som streng
        where.append("entry_time_et >= ?")
        params.append(date_from)
    if date_to:
        # Inkluder hele date_to-dagen: alt før date_to + 1 dag
        # Simplest: brug "<=" mod "YYYY-MM-DDT23:59:59"
        where.append("entry_time_et <= ?")
        params.append(date_to + "T23:59:59")
    if source:
        where.append("source = ?")
        params.append(source)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if status == "open":
        where.append("exit_time_utc IS NULL")
    elif status == "closed":
        where.append("exit_time_utc IS NOT NULL")
    if account_id:
        where.append("account_id = ?")
        params.append(account_id)
    if instance_id:
        where.append("instance_id = ?")
        params.append(instance_id)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT {_TRADE_COLUMNS} FROM trades "
        f"{where_clause} "
        f"ORDER BY entry_time_et DESC "
        f"LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    try:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[trade_queries] list_trades fejl: {e}")
        return []


async def get_trade_by_id(db, trade_id: str) -> Optional[dict]:
    """Hent én trade med fuld payload, eller None hvis ikke fundet."""
    if db is None:
        return None
    try:
        async with db.execute(
            f"SELECT {_TRADE_COLUMNS} FROM trades WHERE trade_id = ?",
            (trade_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row) if row else None
    except Exception as e:
        logger.error(f"[trade_queries] get_trade_by_id fejl: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# Summary / aggregat
# ─────────────────────────────────────────────────────────────────

async def trades_summary(
    db,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    source:      Optional[str] = None,
    account_id:  Optional[str] = None,
    instance_id: Optional[str] = None,
) -> dict:
    """
    Aggregeret statistik over lukkede trades i intervallet.

    Returnerer:
      {
        "count":       int    — antal lukkede trades
        "wins":        int
        "losses":      int
        "win_rate":    float  — procent (0-100)
        "total_pnl":   float
        "avg_pnl":     float
        "best_trade":  float
        "worst_trade": float
        "open_count":  int    — antal stadig-åbne positioner i intervallet
      }

    Åbne positioner tælles separat — de bidrager ikke til pnl.
    """
    if db is None:
        return _empty_summary()

    where = ["exit_time_utc IS NOT NULL"]   # kun lukkede til pnl-stats
    params = []
    if date_from:
        where.append("entry_time_et >= ?")
        params.append(date_from)
    if date_to:
        where.append("entry_time_et <= ?")
        params.append(date_to + "T23:59:59")
    if source:
        where.append("source = ?")
        params.append(source)
    if account_id:
        where.append("account_id = ?")
        params.append(account_id)
    if instance_id:
        where.append("instance_id = ?")
        params.append(instance_id)

    where_clause = "WHERE " + " AND ".join(where)

    sql = f"""
        SELECT
            COUNT(*)                            AS count,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(pnl), 0)               AS total_pnl,
            COALESCE(AVG(pnl), 0)               AS avg_pnl,
            COALESCE(MAX(pnl), 0)               AS best_trade,
            COALESCE(MIN(pnl), 0)               AS worst_trade
        FROM trades
        {where_clause}
    """
    try:
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()

        count, wins, losses, total, avg, best, worst = row
        wins = wins or 0
        losses = losses or 0
        count = count or 0

        # Åbne positioner — separat query med samme filtre minus pnl-krav
        open_where = [w for w in where if w != "exit_time_utc IS NOT NULL"]
        open_where.append("exit_time_utc IS NULL")
        open_where_clause = "WHERE " + " AND ".join(open_where)
        # Genbrug params minus dem til pnl-where (de er identiske)
        async with db.execute(
            f"SELECT COUNT(*) FROM trades {open_where_clause}",
            params,
        ) as cur:
            open_row = await cur.fetchone()
        open_count = (open_row[0] if open_row else 0) or 0

        win_rate = (wins / count * 100.0) if count > 0 else 0.0

        return {
            "count":       count,
            "wins":        wins,
            "losses":      losses,
            "win_rate":    round(win_rate, 2),
            "total_pnl":   round(total or 0.0, 2),
            "avg_pnl":     round(avg   or 0.0, 2),
            "best_trade":  round(best  or 0.0, 2),
            "worst_trade": round(worst or 0.0, 2),
            "open_count":  open_count,
        }
    except Exception as e:
        logger.error(f"[trade_queries] trades_summary fejl: {e}")
        return _empty_summary()


def _empty_summary() -> dict:
    return {
        "count":       0,
        "wins":        0,
        "losses":      0,
        "win_rate":    0.0,
        "total_pnl":   0.0,
        "avg_pnl":     0.0,
        "best_trade":  0.0,
        "worst_trade": 0.0,
        "open_count":  0,
    }


# ─────────────────────────────────────────────────────────────────
# Convenience: dagens trades + summary
# ─────────────────────────────────────────────────────────────────

async def today_trades_and_summary(db) -> dict:
    """
    Dagens trades (i ET-handelsdag) + summary.

    Returnerer:
      {
        "date":    "2026-05-19",
        "trades":  [...]   — list af alle dagens trades, nyeste først
        "summary": {...}   — som trades_summary
      }
    """
    today_et = datetime.now(ET).date().isoformat()
    trades = await list_trades(db, date_from=today_et, date_to=today_et, limit=500)
    summary = await trades_summary(db, date_from=today_et, date_to=today_et)
    return {
        "date":    today_et,
        "trades":  trades,
        "summary": summary,
    }


# ─────────────────────────────────────────────────────────────────
# Open positions
# ─────────────────────────────────────────────────────────────────

async def open_positions(db) -> list[dict]:
    """
    Returnér alle åbne positioner (exit_time_utc IS NULL).
    Sorteret efter entry_time_et DESC.
    """
    return await list_trades(db, status="open", limit=100)


# ─────────────────────────────────────────────────────────────────
# Mutator: opdater notes
# ─────────────────────────────────────────────────────────────────
# Ligger her selvom det er en write, så hele trades-relateret kode
# er samlet ét sted. Faktisk SQL-write ligger stadig i Journal-klassen.

async def update_notes_via_journal(journal, trade_id: str, notes: str) -> bool:
    """Tynd wrapper omkring journal.update_trade_notes — gør den lettere at mocke i tests."""
    return await journal.update_trade_notes(trade_id, notes)