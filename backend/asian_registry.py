#!/usr/bin/env python3
"""
asian_registry.py — fem-instrument-registret til asian_harvest_1min + asian_sweep_precondition.
═════════════════════════════════════════════════════════════════════════════════════════════
Fem DISTINKTE asiatiske markeder, hvert maalt i dets EGEN asiatiske session, saa futures og FX
bliver sammenlignelige. Kontrakt-valg: taettest 1-min volumen (laeringen fra "stor"-faelden —
ikke den stoerste/tyndeste). Kontraktdefinitionerne er loeftet fra asian_data_probe.py.

Felter:
  label    : filnavn-noegle (data_harvest/{label}_1min.csv) + .contract-markoer
  kind     : "futures" | "fx"
  futures  : symbol/exchange/currency (front-maaned kvalificeres ved hoest)
  fx       : pair (Forex)
  what     : whatToShow — TRADES (futures, rigtig volumen) | MIDPOINT (spot-FX har ingen trades)
  tz       : instrumentets hjemme-tidszone (IANA) til session-gating
  windows  : aktive sessions-vinduer i hjemme-tid (HH:MM) — HSI har to (frokostpause imellem)
  open     : aabnings-tid (futures; til OR-braek/gap) — None for FX (kontinuerlig)
"""
from datetime import time

REGISTRY = [
    dict(label="NIKKEI", kind="futures", symbol="N225M", exchange="OSE.JPN", currency="JPY",
         what="TRADES", tz="Asia/Tokyo",
         windows=[(time(9, 0), time(15, 15))], open=time(9, 0)),
    # Hang Seng: HSI std (mult 50) har DYBEST historik (probe: 2023-11) og er HK's flagskib-
    # kontrakt -> taet. THIN-flag i sweepet fanger det hvis bar-volumen alligevel er tynd.
    dict(label="HSI", kind="futures", symbol="HSI", exchange="HKFE", currency="HKD",
         what="TRADES", tz="Asia/Hong_Kong",
         windows=[(time(9, 15), time(12, 0)), (time(13, 0), time(16, 30))], open=time(9, 15)),
    dict(label="A50", kind="futures", symbol="XINA50", exchange="SGX", currency="USD",
         what="TRADES", tz="Asia/Singapore",
         windows=[(time(9, 0), time(16, 30))], open=time(9, 0)),
    # FX: MIDPOINT (ingen rigtig volumen), Tokyo-vinduet 00:00-08:00 UTC = naar JPY er likvid.
    dict(label="USDJPY", kind="fx", pair="USDJPY",
         what="MIDPOINT", tz="UTC",
         windows=[(time(0, 0), time(8, 0))], open=None),
    dict(label="AUDJPY", kind="fx", pair="AUDJPY",
         what="MIDPOINT", tz="UTC",
         windows=[(time(0, 0), time(8, 0))], open=None),
]
