"""
risk_config.py — Konfigurerbare risiko-grænser pr. strategi (pr. konto/maskine).

Erstatter de hardkodede max daily loss + positionsstørrelser i strategierne med
værdier der kan redigeres fra Konfiguratoren i Trading Dash. Hver grænse har BÅDE
en procentsats (af kontoens Net Liquidation) OG et beløb — procenten har første
prioritet hvis den er udfyldt (>0), ellers bruges beløbet.

LAGRING: lokal fil (risk_config.json) på DENNE maskine. Da hver maskine kører præcis
én konto (account.yaml), er filen implicit pr. konto — Ibens algoserver har sine
værdier, Sørens workstation sine egne, og de blandes aldrig. Strategierne (der kører
på den handlende maskine) læser den lokale fil LIVE ved hver handel.

DEFAULTS = de værdier der gælder i koden i dag. Alle fem strategier er tunet til Ibens
lille paper-konto (2026-07-15): max dagligt tab $25, positionsstørrelse $250. Tom
config-fil → DEFAULTS. (Bruger-overrides i risk_config.json vinder stadig lokalt.)
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "risk_config.json"

# ── Standardværdier (kilden til "gældende netop nu i koden") ──────────────────
# Hver post: {"pct": <procent-tal el. None>, "amount": <beløb i $ el. None>}.
# pct som HELTAL-procent (1.0 = 1 %). pct udfyldt (>0) → pct% × NLV; ellers amount.
DEFAULTS: dict[str, dict[str, dict]] = {
    "Konfluens 2": {
        "max_daily_loss": {"pct": None, "amount": 25.0},
        "position_size":  {"pct": None, "amount": 250.0},   # kapital/handel ($)
    },
    "Europa-reversion": {
        "max_daily_loss": {"pct": None, "amount": 25.0},
        "position_size":  {"pct": None, "amount": 250.0},   # risiko/handel ($) — futures
    },
    "BuyTheDip": {
        "max_daily_loss": {"pct": None, "amount": 25.0},
        "position_size":  {"pct": None, "amount": 250.0},   # risiko/handel ($)
        "notional_cap":   {"pct": None, "amount": 1000.0},  # notional-loft ($)
    },
    "Trend Join Long": {
        "max_daily_loss": {"pct": None, "amount": 25.0},
        "position_size":  {"pct": None, "amount": 250.0},   # risiko/handel ($)
        "notional_cap":   {"pct": 10.0, "amount": None},    # notional-loft (10 % af NLV)
    },
    "Relativ Styrke": {
        "max_daily_loss": {"pct": None, "amount": 25.0},
        "position_size":  {"pct": None, "amount": 250.0},   # samlet strategi-notional ($, deles paa 3)
        "notional_cap":   {"pct": None, "amount": 1000.0},  # notional-loft pr. navn ($)
    },
}

# UI-metadata: rækkefølge + danske labels + enhed pr. nøgle (bruges af Konfiguratoren).
SCHEMA: dict[str, list[dict]] = {
    "Konfluens 2": [
        {"key": "max_daily_loss", "label": "Max dagligt tab",  "hint": "loss"},
        {"key": "position_size",  "label": "Positionsstørrelse (kapital/handel)", "hint": "capital"},
    ],
    "Europa-reversion": [
        {"key": "max_daily_loss", "label": "Max dagligt tab",  "hint": "loss"},
        {"key": "position_size",  "label": "Positionsstørrelse (risiko/handel)",  "hint": "risk"},
    ],
    "BuyTheDip": [
        {"key": "max_daily_loss", "label": "Max dagligt tab",  "hint": "loss"},
        {"key": "position_size",  "label": "Positionsstørrelse (risiko/handel)",  "hint": "risk"},
        {"key": "notional_cap",   "label": "Notional-loft",     "hint": "notional"},
    ],
    "Trend Join Long": [
        {"key": "max_daily_loss", "label": "Max dagligt tab",  "hint": "loss"},
        {"key": "position_size",  "label": "Positionsstørrelse (risiko/handel)",  "hint": "risk"},
        {"key": "notional_cap",   "label": "Notional-loft",     "hint": "notional"},
    ],
    "Relativ Styrke": [
        {"key": "max_daily_loss", "label": "Max dagligt tab",  "hint": "loss"},
        {"key": "position_size",  "label": "Positionsstørrelse (samlet notional)", "hint": "capital"},
        {"key": "notional_cap",   "label": "Notional-loft (pr. navn)", "hint": "notional"},
    ],
}


def _blank(v) -> bool:
    """True hvis en pct/amount-værdi tæller som 'ikke udfyldt' (None, tom, ≤0 for pct)."""
    return v is None or v == "" or (isinstance(v, str) and not v.strip())


# ── Fil-lagring (atomisk) ─────────────────────────────────────────────────────
def load() -> dict:
    """Læs override-config fra disk (kun de felter brugeren har ændret). {} hvis ingen."""
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[risk_config] kunne ikke læse {CONFIG_PATH.name}: {e}")
    return {}


def save(overrides: dict) -> None:
    """Skriv override-config atomisk. `overrides` = {strategi: {nøgle: {pct, amount}}}."""
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".part")
    tmp_path = tmp
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(CONFIG_PATH))
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _entry(strategy: str, key: str) -> dict:
    """Effektiv post for (strategi, nøgle): brugerens override hvis sat, ellers DEFAULT."""
    ov = load().get(strategy, {}).get(key)
    if isinstance(ov, dict):
        return ov
    return DEFAULTS.get(strategy, {}).get(key, {})


# ── Resolver: (strategi, nøgle, NLV) → effektivt $-beløb ──────────────────────
def effective(strategy: str, key: str, nlv: float = 0.0) -> float:
    """Effektivt $-beløb for en grænse. Procent har første prioritet hvis udfyldt
    (>0) OG NLV kendes; ellers beløbet; ellers DEFAULT. Returnerer 0.0 for ukendt."""
    ent = _entry(strategy, key)
    if not ent:
        return 0.0
    pct, amount = ent.get("pct"), ent.get("amount")
    if not _blank(pct):
        try:
            p = float(pct)
            if p > 0 and nlv and nlv > 0:
                return p / 100.0 * float(nlv)
        except (TypeError, ValueError):
            pass
    if not _blank(amount):
        try:
            return float(amount)
        except (TypeError, ValueError):
            pass
    # Begge tomme (eller pct sat men NLV ukendt) → fald tilbage til DEFAULT-beløb.
    d = DEFAULTS.get(strategy, {}).get(key, {})
    if not _blank(d.get("pct")) and nlv and nlv > 0:
        return float(d["pct"]) / 100.0 * float(nlv)
    return float(d.get("amount") or 0.0)


# ── API-visning: fuld config (defaults + overrides + effektive værdier) ───────
def for_api(nlv: float = 0.0) -> dict:
    """Alt Konfiguratoren skal bruge: pr. strategi/nøgle {pct, amount (override el.
    default), label, hint, effective ($ ved nuværende NLV)}. Tomme override-felter
    vises som default-beløbet, så brugeren ser 'gældende netop nu'."""
    ov = load()
    out: dict = {"nlv": nlv, "strategies": {}}
    for strat, rows in SCHEMA.items():
        out["strategies"][strat] = []
        for row in rows:
            key = row["key"]
            d = DEFAULTS.get(strat, {}).get(key, {})
            o = ov.get(strat, {}).get(key, {}) if isinstance(ov.get(strat, {}), dict) else {}
            pct = o.get("pct") if "pct" in o else d.get("pct")
            amount = o.get("amount") if "amount" in o else d.get("amount")
            out["strategies"][strat].append({
                "key":       key,
                "label":     row["label"],
                "hint":      row["hint"],
                "pct":       pct,
                "amount":    amount,
                "effective": round(effective(strat, key, nlv), 2),
            })
    return out
