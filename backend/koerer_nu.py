#!/usr/bin/env python3
"""
koerer_nu.py — hvad koerer paa algoserveren NETOP NU? (READ-ONLY snapshot)
═══════════════════════════════════════════════════════════════════════════
Ét billede af kørselstilstanden uden Studio: PER-STRATEGI (kører/status + dagens
handler/pnl/aabne), scheduler-auto-starts (kørte de i dag?), TWS/IBKR-forbindelse og
risiko-budget. Samler de to endpoints backenden allerede har:
  GET /status      (aaben)               — scheduler, tws_watchdog, identitet, risiko
  GET /algo/list   (X-Internal-Key)      — per-strategi status + dagens stats

Noeglen (auth.internal_key) laeses fra account.yaml via accounts.load_identity — saa
koer den paa selve algoserveren (localhost). --host kan pege paa en fjern-instans
(virker hvis flaaden deler internal_key, jf. Tailscale-fan-out).

Brug (fra backend/):
    python koerer_nu.py
    python koerer_nu.py --host 100.x.y.z      # fjern-algoserver
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

GREEN, GREY, RED, YEL = "✅", "⚪", "❌", "⚠️"
ET = ZoneInfo("America/New_York")
DK = ZoneInfo("Europe/Copenhagen")


def _et_to_dk(et_str):
    """'2026-06-25 03:21:07' (ET) -> DK-datetime (korrekt DST). None ved fejl."""
    try:
        return datetime.strptime(et_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET).astimezone(DK)
    except (ValueError, TypeError):
        return None


def _hhmm_et_to_dk(hhmm, et_date_str):
    """ET-klokkeslaet 'HH:MM' paa dagens ET-dato -> DK 'HH:MM' (DST-korrekt)."""
    try:
        y, mo, d = (int(x) for x in et_date_str.split("-"))
        h, mi = (int(x) for x in hhmm.split(":"))
        return datetime(y, mo, d, h, mi, tzinfo=ET).astimezone(DK).strftime("%H:%M")
    except (ValueError, TypeError, AttributeError):
        return f"{hhmm} ET"


def _get(url: str, key: str | None = None):
    req = urllib.request.Request(url)
    if key:
        req.add_header("X-Internal-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}"


def _money(v):
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Hvad koerer paa algoserveren netop nu? (read-only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    base = f"http://{a.host}:{a.port}"

    key = None
    try:
        from accounts import load_identity
        key = load_identity().internal_key or None
    except Exception:
        pass

    status, serr = _get(f"{base}/status")
    if status is None:
        print(f"{RED} Kunne ikke naa backenden paa {base} ({serr}). Koerer den?")
        return 1
    algo, aerr = _get(f"{base}/algo/list", key)

    ident = status.get("identity", {})
    print("=" * 70)
    print(f"  HVAD KOERER NETOP NU?  ·  {ident.get('instance', '?')}  ·  snapshot")
    print("=" * 70)
    print(f"  Konto: {ident.get('ibkr', '?')} ({'paper' if ident.get('paper') else 'LIVE'})"
          f" · rolle {ident.get('role', '?')}")
    ibkr_ok = (status.get("ibkr", {}) or {}).get("connected")
    tws = status.get("tws_watchdog", {}) or {}
    print(f"  IBKR forbundet: {GREEN if ibkr_ok else RED}   "
          f"TWS-watchdog online: {GREEN if tws.get('tws_online') else RED}"
          f"{'  ' + RED + ' DATABLIND' if tws.get('data_blind_since') else ''}")
    sched = status.get("scheduler", {}) or {}
    now_et_str = sched.get("now_et", "") or ""
    et_date = now_et_str[:10]                    # ET-dato (last_run_on er ET-baseret)
    now_dk = _et_to_dk(now_et_str)
    now_disp = now_dk.strftime("%Y-%m-%d %H:%M:%S") if now_dk else (now_et_str or "?")
    print(f"  Tid: {now_disp} DK · handelsdag: {'ja' if sched.get('is_trading_day') else 'nej'}")

    # ── Per-strategi ──
    print("\n  STRATEGIER:")
    if algo and algo.get("strategies"):
        for s in algo["strategies"]:
            run = s.get("running")
            icon = GREEN if run else GREY
            st = s.get("status", "?")
            line = f"   {icon} {s.get('name', '?'):<20}{st:<9}"
            if run:
                stt = s.get("stats", {}) or {}
                line += (f" · {stt.get('trades_today', 0)} handler"
                         f" · P&L {_money(stt.get('pnl_today'))}"
                         f" · {stt.get('open_positions', 0)} aabne")
            print(line)
    elif aerr:
        # Fald tilbage til /status (kun ÉN koerende strategi + global flag)
        al = status.get("algo", {}) or {}
        any_run = al.get("running")
        print(f"   ({YEL} /algo/list utilgaengelig: {aerr} — viser kun /status-overblik)")
        print(f"   Nogen strategi koerer: {GREEN if any_run else RED}"
              + (f" — {(al.get('stats') or {})}" if any_run else ""))
    else:
        print("   (ingen strategier registreret)")

    # ── Scheduler: auto-starts + kørte de i dag? ──
    print("\n  AUTO-START (scheduler) — sidst kørt:")
    for j in sched.get("jobs", []):
        nm, et, last = j.get("name"), j.get("et_time"), j.get("last_run_on")
        dk = _hhmm_et_to_dk(et, et_date)         # job-tid i DK
        mark = GREEN if last == et_date else GREY  # kørte i dag? (ET-dato)
        print(f"   {mark} {nm:<24} @ {dk} DK — sidst {last or 'aldrig'}")
    print(f"   Naeste auto-start: {sched.get('next_start_dk', '?')} DK")

    # ── Risiko ──
    risk = status.get("risk", {}) or {}
    if risk:
        print(f"\n  RISIKO: dagens P&L {_money(risk.get('total_pnl_today'))}"
              f" / limit ${risk.get('daily_loss_limit', 0):.0f}"
              f"{'  ' + RED + ' LIMIT RAMT' if risk.get('daily_limit_hit') else ''}"
              f" · eksponering ${risk.get('total_exposure', 0):,.0f}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
