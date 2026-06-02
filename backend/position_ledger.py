from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _open_rows(db_path: str) -> list[dict]:
    """Hent alle AABNE journal-raekker (exit_time_utc IS NULL) som dicts.

    Ren laesning, read-only forbindelse. Returnerer [] hvis db mangler."""
    if not db_path or not Path(db_path).exists():
        return []
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        logger.error(f"position_ledger: kunne ikke aabne db: {e}")
        return []
    try:
        rows = conn.execute(
            "SELECT source, symbol, side, shares, entry_price "
            "FROM trades WHERE exit_time_utc IS NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        logger.error(f"position_ledger: query fejlede: {e}")
        return []
    finally:
        conn.close()


def positions_by_source(db_path: str) -> dict:
    """Hvad ejer hver strategi? Noegle = (symbol, source).

    Returnerer:
        {(symbol, source): {"shares": int, "side": str, "avg_entry": float}}

    Hvis samme (symbol, source) har flere aabne raekker, summeres shares og
    avg_entry vaegtes. Hvis en source har BAADE long og short i samme symbol
    → advarsel (kan ikke isoleres rent).
    """
    rows = _open_rows(db_path)
    agg: dict = {}
    # foerst: saml pr. (symbol, source, side) for at opdage modsatrettede
    by_key: dict = {}
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        src = r.get("source") or "?"
        side = r.get("side") or "long"
        shares = int(r.get("shares") or 0)
        entry = float(r.get("entry_price") or 0.0)
        if shares <= 0:
            continue
        k = (sym, src, side)
        if k not in by_key:
            by_key[k] = {"shares": 0, "cost": 0.0}
        by_key[k]["shares"] += shares
        by_key[k]["cost"]   += shares * entry

    # Opdag modsatrettede sides for samme (symbol, source)
    seen_sides: dict = {}
    for (sym, src, side) in by_key:
        seen_sides.setdefault((sym, src), set()).add(side)
    for (sym, src), sides in seen_sides.items():
        if len(sides) > 1:
            logger.warning(
                f"position_ledger: {src} har BAADE {sides} i {sym} — "
                f"kan ikke isoleres rent paa én konto. Tjek bogfoering."
            )

    # byg endeligt resultat pr. (symbol, source) — summér paa tvaers af side
    # (men normalt kun én side pr. nuvaerende long-only antagelse)
    for (sym, src, side), v in by_key.items():
        key = (sym, src)
        if key not in agg:
            agg[key] = {"shares": 0, "side": side, "avg_entry": 0.0, "_cost": 0.0}
        agg[key]["shares"]    += v["shares"]
        agg[key]["_cost"]     += v["cost"]
        agg[key]["side"]       = side  # sidste vinder; long-only → uproblematisk
    for key, v in agg.items():
        v["avg_entry"] = round(v["_cost"] / v["shares"], 4) if v["shares"] else 0.0
        del v["_cost"]
    return agg


def account_net_by_symbol(db_path: str) -> dict:
    """Hvad BURDE kontoen holde pr. symbol ifoelge journalen?

    Summerer paa tvaers af alle sources. Long positivt, short negativt.
    Returnerer {symbol: net_shares}.
    """
    rows = _open_rows(db_path)
    net: dict = {}
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        side = r.get("side") or "long"
        shares = int(r.get("shares") or 0)
        if shares <= 0:
            continue
        signed = shares if side == "long" else -shares
        net[sym] = net.get(sym, 0) + signed
    # fjern nul-poster
    return {s: n for s, n in net.items() if n != 0}


def reconcile_against_ibkr(db_path: str, ibkr_positions: list[dict]) -> dict:
    """Sammenlign journalens forventede nettoposition mod IBKR's faktiske.

    ibkr_positions: liste fra conn.get_positions(), hver med
        {"ticker": str, "position": float, "avg_cost": float}

    Returnerer en rapport:
        {
          "match":      [symbol, ...],            # journal == IBKR
          "divergence": [{"symbol","journal","ibkr"}, ...],  # uenige
          "ibkr_only":  [{"symbol","ibkr"}, ...], # IBKR holder, journal kender ikke
          "journal_only":[{"symbol","journal"}, ...], # journal aaben, IBKR holder ikke
        }
    """
    journal_net = account_net_by_symbol(db_path)
    ibkr_net = {}
    for p in ibkr_positions or []:
        sym = (p.get("ticker") or "").upper()
        pos = int(p.get("position") or 0)
        if pos != 0:
            ibkr_net[sym] = pos

    report = {"match": [], "divergence": [], "ibkr_only": [], "journal_only": []}
    all_syms = set(journal_net) | set(ibkr_net)
    for sym in sorted(all_syms):
        j = journal_net.get(sym, 0)
        i = ibkr_net.get(sym, 0)
        if j == i and j != 0:
            report["match"].append(sym)
        elif j != 0 and i == 0:
            report["journal_only"].append({"symbol": sym, "journal": j})
        elif i != 0 and j == 0:
            report["ibkr_only"].append({"symbol": sym, "ibkr": i})
        elif j != i:
            report["divergence"].append({"symbol": sym, "journal": j, "ibkr": i})
    return report