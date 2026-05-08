"""
analysis.py — Aggregeringer over journalen til Studio's analyse-side.

Filosofien:
  - Læser fra events-tabellen, beregner ingenting selv
  - Filtrerer ALTID på account_id (skat-adskillelse)
  - Returnerer JSON-klar data, ingen objekter

Bruges af /analysis/summary endpointet.
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from accounts import identity

DB_PATH = Path(__file__).parent / "trading_dash.db"


# ─────────────────────────────────────────────────────────────
# Periode-håndtering
# ─────────────────────────────────────────────────────────────

def _period_to_iso_start(period: str) -> Optional[str]:
    """
    Konverterer periode-navn til ISO-formatteret start-tidspunkt.
    Returnerer None for "all" (ingen filter på tid).
    """
    now = datetime.now()
    if period == "today":
        return now.strftime("%Y-%m-%d")
    if period == "7d":
        return (now - timedelta(days=7)).isoformat()
    if period == "30d":
        return (now - timedelta(days=30)).isoformat()
    return None  # "all"


def _build_where_clause(period: str) -> tuple[str, list]:
    """Returnerer SQL WHERE-klausul + params (med account_id filter altid)."""
    where  = ["account_id = ?"]
    params = [identity.account_id]

    iso_start = _period_to_iso_start(period)
    if iso_start:
        where.append("ts_local >= ?")
        params.append(iso_start)

    return "WHERE " + " AND ".join(where), params


# ─────────────────────────────────────────────────────────────
# Hent lukkede positioner som dicts (kerne-data for alle KPIs)
# ─────────────────────────────────────────────────────────────

def _fetch_closed_positions(period: str) -> list[dict]:
    """Hent alle position_closed events i perioden, returner som flade dicts."""
    where, params = _build_where_clause(period)
    params_with_type = ["position_closed", *params]

    query = f"""
        SELECT id, ts_local, symbol, payload_json
        FROM events
        WHERE event_type = ?
          AND {where[6:]}        -- fjern "WHERE " prefix da vi har vores eget
        ORDER BY id ASC
    """

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(query, params_with_type).fetchall()
    conn.close()

    trades = []
    for r in rows:
        payload = json.loads(r[3])
        trades.append({
            "id":          r[0],
            "ts":          r[1],
            "symbol":      r[2],
            "side":        payload.get("side"),
            "quantity":    payload.get("quantity"),
            "entry_price": payload.get("entry_price"),
            "exit_price":  payload.get("exit_price"),
            "pnl":         payload.get("pnl", 0),
            "opened_at":   payload.get("opened_at"),
            "closed_at":   payload.get("closed_at"),
        })
    return trades


# ─────────────────────────────────────────────────────────────
# Aggregeringer
# ─────────────────────────────────────────────────────────────

def compute_kpis(trades: list[dict]) -> dict:
    """Beregn nøgletal: antal handler, win rate, P&L, profit factor, etc."""
    if not trades:
        return {
            "trade_count":   0,
            "win_count":     0,
            "loss_count":    0,
            "win_rate":      None,
            "total_pnl":     0.0,
            "avg_win":       None,
            "avg_loss":      None,
            "profit_factor": None,
            "biggest_win":   None,
            "biggest_loss":  None,
        }

    pnls = [t["pnl"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_wins   = sum(wins)
    total_losses = abs(sum(losses))

    return {
        "trade_count":   len(trades),
        "win_count":     len(wins),
        "loss_count":    len(losses),
        "win_rate":      round(len(wins) / len(trades) * 100, 1),
        "total_pnl":     round(sum(pnls), 2),
        "avg_win":       round(total_wins / len(wins), 2)        if wins   else None,
        "avg_loss":      round(-total_losses / len(losses), 2)   if losses else None,
        "profit_factor": round(total_wins / total_losses, 2)     if total_losses > 0 else None,
        "biggest_win":   round(max(pnls), 2),
        "biggest_loss":  round(min(pnls), 2),
    }


def daily_pnl(trades: list[dict]) -> list[dict]:
    """Grupper P&L per dato. Returner sorteret med ældste først."""
    by_date: dict[str, dict] = {}
    for t in trades:
        date = t["ts"][:10]   # ISO-format starter med YYYY-MM-DD
        if date not in by_date:
            by_date[date] = {"date": date, "trades": 0, "pnl": 0.0}
        by_date[date]["trades"] += 1
        by_date[date]["pnl"]    += t["pnl"]

    rows = sorted(by_date.values(), key=lambda r: r["date"])
    for r in rows:
        r["pnl"] = round(r["pnl"], 2)
    return rows


def per_ticker_stats(trades: list[dict]) -> list[dict]:
    """Grupper P&L per ticker. Returner sorteret efter total P&L (taberne nederst)."""
    by_sym: dict[str, dict] = {}
    for t in trades:
        s = t["symbol"]
        if s not in by_sym:
            by_sym[s] = {"symbol": s, "trades": 0, "wins": 0, "pnl": 0.0}
        by_sym[s]["trades"] += 1
        if t["pnl"] > 0:
            by_sym[s]["wins"] += 1
        by_sym[s]["pnl"] += t["pnl"]

    rows = sorted(by_sym.values(), key=lambda r: r["pnl"], reverse=True)
    for r in rows:
        r["pnl"]      = round(r["pnl"], 2)
        r["win_rate"] = round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0
    return rows


def list_trades(trades: list[dict], limit: int = 100) -> list[dict]:
    """Returner liste af enkelte handler (begrænset til de seneste N)."""
    return list(reversed(trades[-limit:]))


# ─────────────────────────────────────────────────────────────
# Topnivå-sammensætning
# ─────────────────────────────────────────────────────────────

def build_summary(period: str = "all") -> dict:
    """Saml det hele til ét JSON-svar."""
    trades = _fetch_closed_positions(period)
    return {
        "period":       period,
        "account_id":   identity.account_id,
        "kpis":         compute_kpis(trades),
        "daily_pnl":    daily_pnl(trades),
        "per_ticker":   per_ticker_stats(trades),
        "trades":       list_trades(trades, limit=100),
    }