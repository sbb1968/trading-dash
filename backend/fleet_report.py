"""
fleet_report.py — Tal på tværs af hele flåden (alle maskiner) til rapporter.

Læser denne maskines live-journal PLUS hver replikeret kildes arkiv
(backend/archives/<source>/trading_dash.db, read-only). Beregner ALT i Python
og returnerer en JSON-klar struktur. Ingen AI — kun tal.

Gruppering:
  - machines[]   : pr. maskine (kilde)
  - by_account[] : pr. account_id (SKAT) på tværs af maskiner
  - fleet        : samlet for hele flåden

Placering: backend/fleet_report.py
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from statistics import mean
from typing import Optional

import aiosqlite
import pytz

import replication_store
import trade_queries
from accounts import identity

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

# Lukkede-trade cap pr. kilde. Rigeligt til today/7d/30d; "all" er kun
# tilnærmet hvis en kilde har flere end dette over hele sin historik.
CLOSED_LIMIT = 5000


def _period_bounds(period: str) -> tuple[Optional[str], Optional[str]]:
    """(date_from, date_to) som ISO-datoer i ET. 'today' = aktuel ET-handelsdag.
    'all' → (None, None). Trades filtreres på entry_time_et (ET-handelsdag)."""
    today_et = datetime.now(ET).strftime("%Y-%m-%d")
    if period == "today":
        return today_et, today_et
    if period == "7d":
        return (datetime.now(ET) - timedelta(days=7)).strftime("%Y-%m-%d"), today_et
    if period == "30d":
        return (datetime.now(ET) - timedelta(days=30)).strftime("%Y-%m-%d"), today_et
    return None, None  # all


def _summarize(closed: list[dict]) -> dict:
    """Standard-nøgletal fra en liste lukkede trades (dicts med 'pnl')."""
    pnls   = [t.get("pnl") for t in closed if t.get("pnl") is not None]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_loss = abs(sum(losses))
    return {
        "count":         len(pnls),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        "total_pnl":     round(sum(pnls), 2),
        "avg_win":       round(mean(wins), 2)   if wins   else None,
        "avg_loss":      round(mean(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / total_loss, 2) if total_loss > 0 else None,
        "best_trade":    round(max(pnls), 2) if pnls else 0.0,
        "worst_trade":   round(min(pnls), 2) if pnls else 0.0,
    }


def _exit_reason_breakdown(closed: list[dict]) -> dict:
    out: dict[str, int] = {}
    for t in closed:
        reason = t.get("exit_reason")
        if reason:
            out[reason] = out.get(reason, 0) + 1
    return out


def _trade_row(t: dict) -> dict:
    return {
        "symbol":        t.get("symbol"),
        "side":          t.get("side"),
        "source":        t.get("source"),
        "entry_price":   t.get("entry_price"),
        "exit_price":    t.get("exit_price"),
        "pnl":           round(t["pnl"], 2) if t.get("pnl") is not None else None,
        "entry_time_et": t.get("entry_time_et"),
        "exit_time_et":  t.get("exit_time_et"),
        "exit_reason":   t.get("exit_reason"),
        "account_id":    t.get("account_id"),
    }


async def _ibkr_account_for(db, account_id: str) -> Optional[str]:
    """Hyppigste ibkr_account for en account_id (fra events). Kun til label —
    fejler stille til None."""
    try:
        async with db.execute(
            "SELECT ibkr_account FROM events "
            "WHERE account_id = ? AND ibkr_account IS NOT NULL "
            "GROUP BY ibkr_account ORDER BY COUNT(*) DESC LIMIT 1",
            (account_id,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


async def _source_block(db, source_id: str, name: str, is_self: bool,
                        date_from: Optional[str], date_to: Optional[str],
                        last_updated: str, reported_today: bool) -> dict:
    """Rapport-blok for én kilde ud fra dens db-handle.
    Antagelse: én kilde = ét account_id (source_id = account_id + role)."""
    closed   = await trade_queries.list_trades(
        db, status="closed", date_from=date_from, date_to=date_to, limit=CLOSED_LIMIT)
    open_pos = await trade_queries.list_trades(db, status="open", limit=2000)

    today_et = datetime.now(ET).strftime("%Y-%m-%d")
    old_open = [p for p in open_pos if (p.get("entry_time_et") or "")[:10] < today_et]

    if is_self:
        account_id   = identity.account_id
        ibkr_account = identity.ibkr_account
    else:
        ids = sorted({(t.get("account_id") or "?") for t in closed} |
                     {(p.get("account_id") or "?") for p in open_pos})
        account_id   = ids[0] if ids else "?"
        ibkr_account = await _ibkr_account_for(db, account_id)

    per_strategy = []
    for strat in sorted({(t.get("source") or "?") for t in closed}):
        strat_closed = [t for t in closed if (t.get("source") or "?") == strat]
        per_strategy.append({"strategy": strat, "summary": _summarize(strat_closed)})

    return {
        "source_id":      source_id,
        "name":           name,
        "is_self":        is_self,
        "account_id":     account_id,
        "ibkr_account":   ibkr_account,
        "last_updated":   last_updated,
        "reported_today": reported_today,
        "summary":        _summarize(closed),
        "per_strategy":   per_strategy,
        "exit_reasons":   _exit_reason_breakdown(closed),
        "open_count":     len(open_pos),
        "old_open_count": len(old_open),    # gamle åbne positioner
        "trades":         [_trade_row(t) for t in closed],
    }


def _combine(blocks: list[dict]) -> dict:
    """Saml summary på tværs af kilde-blokke ved at slå deres trade-rækker
    sammen og genberegne."""
    all_trades = [t for b in blocks for t in b.get("trades", [])]
    return _summarize(all_trades)


async def build_fleet_report(local_db, period: str = "today") -> dict:
    """
    Byg den fulde fleet-rapport.

    local_db: aiosqlite-handle til denne maskines journal.db (fra main: journal.db).
    period:   "today" | "7d" | "30d" | "all".
    """
    date_from, date_to = _period_bounds(period)
    now_iso      = datetime.now().astimezone().isoformat()
    today_local  = date.today().isoformat()

    machines: list[dict] = []

    # 1) Denne maskine (live journal.db)
    machines.append(await _source_block(
        local_db, identity.source_id, identity.instance_display_name,
        is_self=True, date_from=date_from, date_to=date_to,
        last_updated=now_iso, reported_today=True,
    ))

    # 2) Hver replikeret kilde (arkiv, read-only — samme som _resolve_db)
    for source in replication_store.list_sources():
        if source == identity.source_id:
            continue
        path = replication_store.archive_db_path(source)
        if path is None:
            continue
        mtime          = datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone()
        last_updated   = mtime.isoformat()
        reported_today = (mtime.date().isoformat() == today_local)
        name           = source.replace("_", " ").title()
        try:
            conn = await aiosqlite.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                machines.append(await _source_block(
                    conn, source, name, is_self=False,
                    date_from=date_from, date_to=date_to,
                    last_updated=last_updated, reported_today=reported_today,
                ))
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"[Fleet] Kunne ikke læse arkiv for {source}: {e}")
            machines.append({
                "source_id": source, "name": name, "is_self": False,
                "account_id": "?", "ibkr_account": None,
                "last_updated": last_updated, "reported_today": reported_today,
                "summary": _summarize([]), "per_strategy": [], "exit_reasons": {},
                "open_count": 0, "old_open_count": 0, "trades": [], "error": str(e),
            })

    # 3) Pr. account_id (SKAT) på tværs af maskiner
    by_account: list[dict] = []
    for aid in sorted({m["account_id"] for m in machines}):
        blocks = [m for m in machines if m["account_id"] == aid]
        ibkr   = next((m["ibkr_account"] for m in blocks if m.get("ibkr_account")), None)
        by_account.append({
            "account_id":   aid,
            "ibkr_account": ibkr,
            "summary":      _combine(blocks),
            "machine_ids":  [m["source_id"] for m in blocks],
        })

    # 4) Hele flåden + liste over ikke-friske arkiver
    fleet = {"summary": _combine(machines)}
    stale = [m["source_id"] for m in machines
             if not m["is_self"] and not m["reported_today"]]

    return {
        "period":         period,
        "date_from":      date_from,
        "date_to":        date_to,
        "generated_at":   now_iso,
        "machines":       machines,
        "by_account":     by_account,
        "fleet":          fleet,
        "stale_machines": stale,
    }


# ── Kompakt tekst til AI-prompten (dansk, labellet) ────────────

def _fmt_summary(s: dict) -> str:
    pf = f"{s['profit_factor']}" if s["profit_factor"] is not None else "—"
    return (f"{s['count']} handler, {s['wins']}V/{s['losses']}T, "
            f"winrate {s['win_rate']}%, P&L ${s['total_pnl']:+.2f}, "
            f"PF {pf}, bedste ${s['best_trade']:+.2f}, værste ${s['worst_trade']:+.2f}")


def report_to_text(report: dict) -> str:
    """Kompakt dansk tekst-blok modellen kan læse. Bevidst kompakt — små
    modeller læser labellet tekst bedre end dyb JSON."""
    period_lbl = {"today": "I dag", "7d": "Sidste 7 dage",
                  "30d": "Sidste 30 dage", "all": "Hele perioden"}.get(
                      report["period"], report["period"])
    lines = [
        f"PERIODE: {period_lbl}  (ET {report['date_from']}..{report['date_to']})",
        f"FLÅDE I ALT: {_fmt_summary(report['fleet']['summary'])}",
        "",
    ]
    for m in report["machines"]:
        head = f"MASKINE: {m['name']} (konto {m['account_id']}"
        if m.get("ibkr_account"):
            head += f"/{m['ibkr_account']}"
        head += ")"
        if not m["reported_today"] and not m["is_self"]:
            head += "  [ARKIV IKKE OPDATERET I DAG]"
        lines.append(head)
        lines.append("  " + _fmt_summary(m["summary"]))
        if m.get("open_count"):
            extra = f" (heraf {m['old_open_count']} gamle)" if m.get("old_open_count") else ""
            lines.append(f"  Åbne positioner: {m['open_count']}{extra}")
        for ps in m["per_strategy"]:
            lines.append(f"  - {ps['strategy']}: {_fmt_summary(ps['summary'])}")
        if m["exit_reasons"]:
            er = ", ".join(f"{k}={v}" for k, v in sorted(m["exit_reasons"].items()))
            lines.append(f"  Exit-årsager: {er}")
        closed = [t for t in m["trades"] if t.get("pnl") is not None]
        if closed:
            winners = [t for t in sorted(closed, key=lambda x: x["pnl"], reverse=True)
                       if t["pnl"] > 0][:3]
            losers  = [t for t in sorted(closed, key=lambda x: x["pnl"])
                       if t["pnl"] < 0][:3]
            if winners:
                lines.append("  Bedste handler (vindere):")
                for t in winners:
                    lines.append(f"    + {t['symbol']} {t.get('source','')} "
                                 f"${t['pnl']:+.2f} ({t.get('exit_reason','?')})")
            if losers:
                lines.append("  Tabende handler:")
                for t in losers:
                    lines.append(f"    - {t['symbol']} {t.get('source','')} "
                                 f"${t['pnl']:+.2f} ({t.get('exit_reason','?')})")
        lines.append("")
    return "\n".join(lines).strip()
