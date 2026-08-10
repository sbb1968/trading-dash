"""
test_client_ids.py — registret håndhæver sig selv
════════════════════════════════════════════════════════════════════════════════
Et register er kun værd noget hvis afvigelser fra det bliver fanget. Ellers er
det en liste nogen skal huske at læse — og det var netop sådan client-id 46 og 47
blev delt mellem flere scripts uden at nogen opdagede det.

Denne test SCANNER kodebasen og fejler på:

  1  et hardkodet client-id der ikke står i registret
  2  to filer der bruger samme id
  3  en tilfældig trækning af client-id overhovedet
  4  et script der bruger backendens reserverede blok

Punkt 3 er den vigtigste. `random.randint(10, 99)` i backenden gav omkring 17 %
kollisionsrisiko pr. forbindelse mod de femten faste id'er i samme interval — en
fejl der ikke fejler højlydt, men ligner en mistet forbindelse eller manglende
data og sender fejlsøgningen et andet sted hen.

`_archive/` er undtaget: den kode køres ikke.

    python test_client_ids.py
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import ibkr_client_ids as reg

FEJL: list[str] = []
ROD = pathlib.Path(__file__).parent


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


def kildefiler():
    for p in sorted(ROD.rglob("*.py")):
        rel = p.relative_to(ROD).as_posix()
        if rel.startswith("venv/") or rel.startswith("_archive/"):
            continue
        if rel in ("ibkr_client_ids.py", "test_client_ids.py"):
            continue
        yield rel, p.read_text(encoding="utf-8", errors="replace")


# ── 1. Registret er internt konsistent ──────────────────────────────────────
print("\n1. Registret er konsistent")
try:
    reg.kontroller()
    kraev(True, "ingen dubletter, intet i backendens blok")
except reg.KlientIdFejl as e:
    kraev(False, f"registret er selv i strid: {e}")

kraev(reg.BACKEND in reg.BACKEND_BLOK, f"backenden ({reg.BACKEND}) ligger i sin blok")
kraev(not any(v in reg.BACKEND_BLOK for v in reg.SCRIPTS.values()),
      "intet script bruger backendens blok")

# ── 2. Ingen tilfældige trækninger ──────────────────────────────────────────
print("\n2. ⚠ Ingen tilfældige client-id'er nogen steder")
tilfaeldige = []
for rel, t in kildefiler():
    for m in re.finditer(r"random\.randint\(\s*(\d+)\s*,\s*(\d+)\s*\)", t):
        omkring = t[max(0, m.start() - 200):m.end() + 60].lower()
        if "client" in omkring:
            tilfaeldige.append(f"{rel}: random.randint({m.group(1)}, {m.group(2)})")
kraev(not tilfaeldige, f"ingen fundet{'' if not tilfaeldige else ': ' + '; '.join(tilfaeldige)}")

# ── 3. Hardkodede id'er stemmer med registret ───────────────────────────────
print("\n3. Hardkodede id'er stemmer med registret")
MOENSTRE = (
    r"^\s*(?:IBKR_)?CLIENT_ID\s*=\s*(\d+)",
    r"PORT,\s*CLIENT_ID,\s*TIMEOUT\s*=\s*\d+,\s*(\d+)",
    r"clientId\s*=\s*(\d+)",
)
fundet: dict[str, set[int]] = {}
for rel, t in kildefiler():
    for m in MOENSTRE:
        for x in re.finditer(m, t, re.M):
            fundet.setdefault(rel, set()).add(int(x.group(1)))

ukendte, uenige = [], []
for rel, ids in sorted(fundet.items()):
    for kid in sorted(ids):
        if kid in reg.BACKEND_BLOK:
            continue                       # backendens egen blok, sat via konstanten
        ventet = reg.SCRIPTS.get(rel)
        if ventet is None:
            ukendte.append(f"{rel} bruger {kid} men står ikke i SCRIPTS")
        elif kid != ventet:
            uenige.append(f"{rel} bruger {kid}, registret siger {ventet}")

kraev(not ukendte, f"alle filer med et id står i registret"
                   f"{'' if not ukendte else ' — ' + '; '.join(ukendte)}")
kraev(not uenige, f"koden og registret er enige"
                  f"{'' if not uenige else ' — ' + '; '.join(uenige)}")

# ── 4. Ingen to filer deler et id ───────────────────────────────────────────
print("\n4. Ingen to filer deler et id")
pr_id: dict[int, list[str]] = {}
for rel, ids in fundet.items():
    for kid in ids:
        if kid not in reg.BACKEND_BLOK:
            pr_id.setdefault(kid, []).append(rel)
delt = {k: sorted(v) for k, v in pr_id.items() if len(v) > 1}
kraev(not delt, f"ingen delte id'er{'' if not delt else ': ' + str(delt)}")

# ── 5. ⚠ Falsifikation — kan vagten overhovedet fejle? ──────────────────────
print("\n5. ⚠ Falsifikation — vagten skal kunne sige nej")
_gemt = dict(reg.SCRIPTS)
reg.SCRIPTS["falsk_a.py"] = 48          # kolliderer med harvest_futures_1min
try:
    reg.kontroller()
    kraev(False, "en dublet blev accepteret")
except reg.KlientIdFejl as e:
    kraev("48" in str(e), f"dublet fanges: {str(e)[:70]}")
reg.SCRIPTS.clear(); reg.SCRIPTS.update(_gemt)

reg.SCRIPTS["falsk_b.py"] = reg.BACKEND
try:
    reg.kontroller()
    kraev(False, "backendens blok blev accepteret til et script")
except reg.KlientIdFejl as e:
    kraev("reserverede" in str(e), f"blok-overtrædelse fanges: {str(e)[:60]}")
reg.SCRIPTS.clear(); reg.SCRIPTS.update(_gemt)
kraev(reg.kontroller() is None, "og registret er rent igen bagefter")

try:
    reg.for_script("findes_ikke.py")
    kraev(False, "et ukendt script fik et id")
except reg.KlientIdFejl:
    kraev(True, "et script uden for registret kan ikke få et id")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print(f"Alt groent. {len(reg.SCRIPTS)} scripts registreret, backend = {reg.BACKEND}.")
