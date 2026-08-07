"""
test_vol_lag1.py — lag 1's formler, klasser og outputkontrakt
═══════════════════════════════════════════════════════════════════════════════════
Daekker acceptkriterium 1-3 i spec v2.0:

  1  L1's dato-sammenstilling, demonstreret paa 2011-09-12
  2  percentilmodulet med alle tre referencer (egen testfil)
  3  score for hver NYSE-handelsdag med komponenter eksponeret

2011-09-12 er ikke et tilfaeldigt eksempel. SPY har dagen; VIX, VIX3M og RVX har
den ikke. Sammenstilles der paa RAEKKENUMMER frem for paa dato, forskydes de tre
serier én dag i forhold til SPY for HELE den foelgende periode — femten aars
percentiler beregnet mod den forkerte dag, og intet ville fejle. Det er den
farligste enkeltfejl der kan opstaa i V2, fordi den ikke giver mistaenkelige tal,
men plausible tal der er systematisk forkerte.

    python test_vol_lag1.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vol_lag1 as l1
import vol_percentil as vp

FEJL: list[str] = []
SLUT = date(2023, 12, 31)


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


print("\n1. Realiseret volatilitet")
# Konstant afkast -> nul spredning. Enhver anden vaerdi ville betyde at
# formlen maaler niveau frem for spredning.
d0 = date(2020, 1, 1)
afk = {d0 + timedelta(days=i): 0.01 for i in range(30)}
rv = l1.realiseret_vol(afk, 20)
kraev(all(abs(v) < 1e-9 for v in rv.values()), "konstant afkast -> vol 0")

# Kendt spredning: skiftevis +1 %/-1 % giver std 0,01 (n-1) -> annualiseret
# 0,01 * sqrt(252) ≈ 0,1587.
vekslende = {d0 + timedelta(days=i): (0.01 if i % 2 == 0 else -0.01) for i in range(40)}
rv2 = l1.realiseret_vol(vekslende, 20)
vent = 0.01 * (252 ** 0.5) * (20 / 19) ** 0.5
kraev(abs(list(rv2.values())[0] - vent) < 1e-3,
      f"vekslende ±1 % -> {list(rv2.values())[0]:.4f} (ventet ~{vent:.4f})")
kraev(len(l1.realiseret_vol(afk, 20)) == 30 - 20 + 1,
      "foerste 19 dage har intet 20-dages vindue")

print("\n2. Klassegraenser — inklusive nedadtil, ingen huller")
lav, hoej, stress = l1.GRAENSER
kraev(l1.klasse_af(lav - 0.01) == "lav", f"{lav-0.01} -> lav")
kraev(l1.klasse_af(lav) == "normal", f"{lav} -> normal (graensen er inklusiv opad)")
kraev(l1.klasse_af(hoej) == "forhoejet", f"{hoej} -> forhoejet")
kraev(l1.klasse_af(stress) == "stress", f"{stress} -> stress")
kraev(l1.klasse_af(0) == "lav" and l1.klasse_af(100) == "stress",
      "yderpunkterne 0 og 100 har en klasse")

print("\n3. RVX/VIX er IKKE en komponent — den hoerer til byggeklods 6")
# Blandes divergens ind i volatilitetsaksen, maaler byggeklods 6 senere det samme
# igen, og vi tror vi har to uafhaengige signaler hvor vi har ét.
# Funktionelt frem for tekstligt: en tekstsoegning fangede modulets EGEN
# docstring — den linje der netop siger at RVX/VIX ikke er med. Kontrollen
# maalte altsaa sin egen forklaring i stedet for koden.
import vol_serier as _vs
_serier = {n: _vs.laes_serie(n) for n in l1.SERIER_LAG1}
_raa = l1.byg_raaserier(_serier)
kraev("rvx_pctl" in _raa, "RVX' NIVEAU er en komponent (K4)")
kraev(set(_raa) == set(l1.KOMPONENTER),
      f"praecis fire raaserier, ingen ekstra: {sorted(_raa)}")
_d = sorted(set(_raa["rvx_pctl"]) & set(_serier["RVX"]))[:50]
kraev(all(_raa["rvx_pctl"][d] == _serier["RVX"][d] for d in _d),
      "rvx_pctl ER RVX raa niveau — ikke RVX/VIX (det hoerer til byggeklods 6)")

print("\n4. Komponenterne er praeregistrerede og lige vaegtede")
kraev(len(l1.KOMPONENTER) == 4, f"fire komponenter: {', '.join(l1.KOMPONENTER)}")
kraev(len(set(l1.VAEGTE.values())) == 1,
      f"alle vaegte ens ({set(l1.VAEGTE.values())}) — ingen optimering")

print("\n5. config_hash reagerer paa ENHVER parameterAendring")
h0 = l1.config_hash()
_gemt = l1.GRAENSER
l1.GRAENSER = (21.0, 58.0, 80.0)
kraev(l1.config_hash() != h0, "aendret klassegraense -> ny hash")
l1.GRAENSER = _gemt
_v = dict(l1.VAEGTE)
l1.VAEGTE["vix_pctl"] = 2.0
kraev(l1.config_hash() != h0, "aendret vaegt -> ny hash")
l1.VAEGTE.update(_v)
kraev(l1.config_hash() == h0, f"gendannet -> samme hash igen ({h0})")

print("\n6. Beregning over udviklingsperioden")
dage = l1.beregn_lag1(slut=SLUT)
med = [d for d in dage if d.score is not None]
kraev(len(dage) > 3000, f"{len(dage)} NYSE-handelsdage")
kraev(all(d.dag <= SLUT for d in dage), "INTET efter udviklingsperiodens slut")
kraev(all(0 <= d.score <= 100 for d in med), "alle scorer i [0,100]")
kraev(all(d.klasse in l1.KLASSER for d in med), "alle klasser er kendte")
kraev(all(set(d.komponenter) == set(l1.KOMPONENTER) for d in dage),
      "alle fire komponenter er eksponeret paa hver dag — ogsaa naar de er None")

print("\n7. ⚠ 2011-09-12 — beviset for at der sammenstilles paa DATO")
d0912 = next((d for d in dage if d.dag == date(2011, 9, 12)), None)
kraev(d0912 is not None, "dagen findes (NYSE var aaben)")
if d0912:
    kraev(d0912.status == "DEGRADED",
          f"status = {d0912.status} — ikke OK, for tre serier mangler")
    kraev(set(d0912.manglende) == {"vix_pctl", "term_pctl", "rvx_pctl"},
          f"praecis de tre VIX-afhaengige mangler: {sorted(d0912.manglende)}")
    kraev(d0912.komponenter["rv_ekspansion_pctl"] is not None,
          "SPY-komponenten er der — den serie HAR dagen")
    kraev(0 < d0912.konfidens < 1,
          f"konfidens-nedslag: {d0912.konfidens} (ikke fuld tillid)")
    kraev(d0912.score is not None,
          "der udstedes stadig en score paa det der ER — DEGRADED, ikke tavshed")

# Og naboen maa IKKE have arvet dagens vaerdi.
naboer = [d for d in dage if d.dag in (date(2011, 9, 9), date(2011, 9, 13))]
kraev(all(n.status == "OK" for n in naboer),
      "naboerne er upaavirkede — ingen forskydning")

print("\n8. Outputkontrakten (C2)")
sidste = med[-1]
k = l1.som_kontrakt(sidste, "2026-08-07T12:00:00Z")
for felt in ("skema_version", "config_hash", "beregnet_kl", "handelsdag",
             "status", "advarsler", "lag1", "lag2", "lag3"):
    kraev(felt in k, f"felt '{felt}'")
kraev(k["lag2"] is None and k["lag3"] is None,
      "lag 2 og 3 er null — skemaet aendrer sig ikke naar de kommer til")
kraev(set(k["lag1"]["komponenter"]) == set(l1.KOMPONENTER),
      "komponenterne er med i outputtet, ikke kun scoren")
kraev(json.loads(json.dumps(k)) == k, "kontrakten er JSON-serialiserbar")

# En DEGRADED dag skal BAERE sin advarsel videre.
if d0912:
    kd = l1.som_kontrakt(d0912, "2026-08-07T12:00:00Z")
    kraev(kd["status"] == "DEGRADED" and kd["advarsler"],
          f"DEGRADED-dag baerer en advarsel: {kd['advarsler']}")

print("\n9. STALE naegter at afgive en score")
tom = l1.Lag1Dag(date(2011, 1, 3), None, None, 0.0, "STALE",
                 {k: None for k in l1.KOMPONENTER}, list(l1.KOMPONENTER))
kt = l1.som_kontrakt(tom, "2026-08-07T12:00:00Z")
kraev(kt["lag1"] is None,
      "ingen lag1-blok ved STALE — stiltiende rapportering paa foraeldede data "
      "var en konkret fejl i det forrige forsoeg")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")
