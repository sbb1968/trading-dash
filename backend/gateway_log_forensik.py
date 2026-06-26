#!/usr/bin/env python3
"""
gateway_log_forensik.py — DEN tidsstemplede aarsag til at IBKR-forbindelsen blev kicket/
datablind (READ-ONLY). Laeser TWS/IB-Gateways EGEN log (Jts-mappen), som backend IKKE logger.
═══════════════════════════════════════════════════════════════════════════════════════════════
Backend ser kun SYMPTOMET (K2 datablind / doed forbindelse). HVORFOR staar i Gateways log:
  - "connected from a different IP" / "session is connected"  -> en ANDEN session loggede paa
    SAMME konto = det klassiske midt-paa-dagen-kick (session-isolation-sygdommen).
  - 1100 (forbindelse IB<->TWS tabt) / 1102 (genoprettet)     -> transient datafarm-blip.
  - 2103/2105/2110/2157 (datafarm inactive/broken/OK)          -> feed-degradering.
  - 162 / HMDS no data / pacing                                -> historik-datablind (K2's symptom).
  - auto-restart / logging off                                 -> planlagt/uventet genstart.

Koer paa ALGOSERVEREN. Auto-finder Jts-log-mapper; override med --log-dir. Rent read-only.

    python gateway_log_forensik.py
    python gateway_log_forensik.py --date 2026-06-26
    python gateway_log_forensik.py --log-dir "C:\\Users\\Iben\\Jts" --context 1

Placering: C:\\Projects\\trading_dash\\backend\\gateway_log_forensik.py
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

PATTERNS = [
    ("KICK — anden IP/session", re.compile(r"different IP|connected from|session is connected", re.I)),
    ("1100 forbindelse TABT",   re.compile(r"\b1100\b|connectivity .*lost", re.I)),
    ("1102 genoprettet",        re.compile(r"\b1102\b|connectivity .*restored", re.I)),
    ("1101 genoprettet (data tabt)", re.compile(r"\b1101\b", re.I)),
    ("datafarm-skift",          re.compile(r"\b(2103|2105|2110|2157)\b|farm connection.*(inactive|broken|is OK)", re.I)),
    ("162 / HMDS datablind",    re.compile(r"\b162\b|HMDS .*no data|pacing violation", re.I)),
    ("auto-restart / logoff",   re.compile(r"auto.?restart|logging off|will (restart|reconnect)|server is restarting", re.I)),
    ("clientId-konflikt",       re.compile(r"\b326\b|already connect|duplicate client", re.I)),
    ("disconnect (generisk)",   re.compile(r"\bdisconnect|socket .*clos|peer (reset|closed)", re.I)),
]


def candidate_dirs(override):
    dirs, seen, out = [], set(), []
    home = Path.home()
    if override:
        dirs.append(Path(override))
    dirs += [home / "Jts", Path("C:/Jts"), home / "Documents" / "Jts",
             Path("C:/IBKR/ibgateway"), home / "Jts" / "ibgateway"]
    for base in [home / "Jts", Path("C:/Jts")]:
        if base.is_dir():
            for sub in base.iterdir():
                if sub.is_dir():
                    dirs.append(sub)
    for d in dirs:
        try:
            rd = d.resolve()
        except Exception:
            rd = d
        if d.is_dir() and rd not in seen:
            seen.add(rd)
            out.append(d)
    return out


def find_logs(dirs, target):
    """*.log-filer modificeret paa target-dato ELLER med target i filnavnet."""
    ymd = target.strftime("%Y%m%d")
    hits = []
    for d in dirs:
        for p in d.glob("*.log"):
            try:
                mt = datetime.fromtimestamp(p.stat().st_mtime).date()
                sz = p.stat().st_size
            except OSError:
                continue
            if mt == target or ymd in p.name:
                hits.append((p, mt, sz))
    # nyeste foerst
    hits.sort(key=lambda t: t[0].stat().st_mtime if t[0].exists() else 0, reverse=True)
    return hits


def scan_file(path, ctx, emit, max_per=40):
    counts = {name: 0 for name, _ in PATTERNS}
    shown = {name: 0 for name, _ in PATTERNS}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        emit(f"   (kunne ikke laese {path.name}: {e})")
        return counts
    for i, line in enumerate(lines):
        for name, rx in PATTERNS:
            if rx.search(line):
                counts[name] += 1
                if shown[name] < max_per:
                    shown[name] += 1
                    emit(f"   [{name}]  {line.strip()[:200]}")
                    for j in range(1, ctx + 1):
                        if i + j < len(lines):
                            emit(f"        | {lines[i + j].strip()[:160]}")
                break
    return counts


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="IBKR Gateway/TWS-log forensik (read-only)")
    ap.add_argument("--log-dir", default=None, help="eksplicit Jts/log-mappe (override)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default i dag, algoserverens lokale dato)")
    ap.add_argument("--context", type=int, default=0, help="ekstra linjer efter hvert hit")
    a = ap.parse_args()

    target = (datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else date.today())

    def emit(s=""):
        print(s, flush=True)

    emit("=" * 78)
    emit(f"  GATEWAY-LOG FORENSIK — dato {target}  (READ-ONLY, Gateways egen log)")
    emit("=" * 78)

    dirs = candidate_dirs(a.log_dir)
    if not dirs:
        emit("  ⛔ Fandt ingen Jts/log-mapper. Find TWS/Gateway-loggen manuelt og koer:")
        emit('     python gateway_log_forensik.py --log-dir "<sti til mappen med .log>"')
        emit("     (TWS/Gateway: typisk C:\\Users\\<bruger>\\Jts\\ — se 'API → Settings → "
             "Logging' eller File → Global Config for stien.)")
        return 1
    emit("  Log-mapper undersoegt:")
    for d in dirs:
        emit(f"     {d}")

    logs = find_logs(dirs, target)
    if not logs:
        emit(f"\n  Ingen *.log modificeret {target} (eller med {target:%Y%m%d} i navnet).")
        emit("  Alle *.log i mapperne (nyeste 12) — find den rigtige + brug --log-dir/--date:")
        allp = []
        for d in dirs:
            allp += list(d.glob("*.log"))
        allp.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        for p in allp[:12]:
            try:
                mt = datetime.fromtimestamp(p.stat().st_mtime)
                emit(f"     {mt:%Y-%m-%d %H:%M}  {p.stat().st_size/1e6:6.1f} MB  {p}")
            except OSError:
                pass
        return 1

    emit(f"\n  Log-filer fra {target}: {len(logs)}")
    total = {name: 0 for name, _ in PATTERNS}
    for p, mt, sz in logs:
        emit("\n" + "─" * 78)
        emit(f"  {p}   ({sz/1e6:.1f} MB, modificeret {datetime.fromtimestamp(p.stat().st_mtime):%H:%M})")
        emit("─" * 78)
        c = scan_file(p, a.context, emit)
        for k, v in c.items():
            total[k] += v
        if not any(c.values()):
            emit("   (ingen relevante hits i denne fil)")

    emit("\n" + "=" * 78)
    emit("  OPSUMMERING (antal hits pr. kategori, alle filer)")
    emit("=" * 78)
    for name, _ in PATTERNS:
        emit(f"   {name:<30} {total[name]}")
    emit("")
    if total["KICK — anden IP/session"] > 0:
        emit("  -> 'anden IP/session'-hits FUNDET: en anden klient loggede paa SAMME IBKR-konto")
        emit("     og sparkede algoserverens session. Det er session-isolation-sygdommen.")
        emit("     Tjek tidsstemplet vs k2_today_forensik's datablinde vindue.")
    elif total["1100 forbindelse TABT"] > 0 and total["KICK — anden IP/session"] == 0:
        emit("  -> 1100/1102 uden IP-kick: datafarm-/netvaerks-blip (IB-side), ikke en anden session.")
        emit("     Hvis 1100 IKKE efterfulgt af 1102 -> varigt tab; ellers selv-helende hikke.")
    else:
        emit("  -> Ingen kick/1100. Datablindheden kan vaere ren HMDS/162-pacing (se 162-kategorien)")
        emit("     eller en backend-side haengning — kryds med k2_today_forensik.")
    emit("\n  Send hele outputtet + k2_today_forensik's output retur.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
