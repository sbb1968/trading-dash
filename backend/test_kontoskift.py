"""
test_kontoskift.py — hvilken konto en maskine handler paa
════════════════════════════════════════════════════════════════════════════════
Sag A: konti under SAMME TWS-login. Kun order.account skifter.

Fire ting skal holde, ellers er funktionen farligere end den er nyttig:

  1  Hvidlisten i account.yaml er graensen. En konto ingen har erklaeret kan
     ikke vaelges — heller ikke ved at redigere aktiv_konto.json.
  2  ⚠ Et skift maa ALDRIG efterlade ejerloese positioner. Er der aabne
     positioner eller koerende strategier, afvises skiftet.
  3  ⚠ Kan det ikke AFGOERES om der er aabne positioner (IBKR nede), afvises
     skiftet ogsaa. "Vi ved det ikke" er ikke det samme som "der er ingen".
  4  Fejler ombindingen af forbindelsen, rulles filen tilbage. Et halvt skift —
     fil aendret, ordrer uaendret — ville faa journalen til at stemple handler
     med en konto de ikke blev lagt paa.

Afsnit 7 viser at kontrollerne kan fejle.

    python test_kontoskift.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import HTTPException

import accounts

FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


# ── Testidentitet: to konti under samme login ────────────────────────────────
import dataclasses

ORIG_IDENT = accounts.identity
ORIG_PATH = accounts.AKTIV_PATH
accounts.AKTIV_PATH = Path("test_aktiv_konto.json")
accounts.identity = dataclasses.replace(
    accounts.identity,
    ibkr_account="DUO509856",
    ibkr_konti=({"id": "DUO509856", "label": "Iben konto 1"},
                {"id": "DUQ441063", "label": "Iben konto 2"}))


def ryd():
    if accounts.AKTIV_PATH.exists():
        accounts.AKTIV_PATH.unlink()


print("\n1. Hvidlisten er graensen")
ryd()
kraev(accounts.aktiv_konto() == "DUO509856",
      "uden valgt konto bruges standarden fra account.yaml")
kraev(accounts.saet_aktiv_konto("DUQ441063") == "DUQ441063", "tilladt konto gemmes")
kraev(accounts.aktiv_konto() == "DUQ441063", "og laeses tilbage")
kraev(accounts.konto_label() == "Iben konto 2", "label foelger med")
try:
    accounts.saet_aktiv_konto("U9999999")
    kraev(False, "en konto uden for hvidlisten blev accepteret")
except ValueError as e:
    kraev("hvidlisten" in str(e), f"afvist: {str(e)[:60]}")

print("\n2. ⚠ En manipuleret fil falder tilbage — den handler ikke videre")
# Redigerer nogen filen udenom UI'en (eller aendres account.yaml senere), maa vi
# ikke handle paa en konto der ikke laengere er tilladt.
accounts.AKTIV_PATH.write_text(json.dumps({"konto": "U9999999"}), encoding="utf-8")
kraev(accounts.aktiv_konto() == "DUO509856",
      "ikke-tilladt konto i filen -> tilbage til standarden")
accounts.AKTIV_PATH.write_text("{ ikke json", encoding="utf-8")
kraev(accounts.aktiv_konto() == "DUO509856", "beskadiget fil -> tilbage til standarden")
ryd()

print("\n3. account.yaml valideres ved indlaesning")
try:
    accounts._laes_konti({"ibkr_account": "DUO509856",
                          "ibkr_konti": [{"id": "DUQ441063"}]})
    kraev(False, "standardkonto uden for sin egen hvidliste blev accepteret")
except SystemExit:
    kraev(True, "standardkontoen SKAL staa paa hvidlisten (ellers falder "
                "reserven tilbage paa noget utilladt)")
try:
    accounts._laes_konti({"ibkr_account": "A", "ibkr_konti": [{"id": "A"}, {"id": "a"}]})
    kraev(False, "dublet blev accepteret")
except SystemExit:
    kraev(True, "samme konto to gange afvises (ogsaa med anden versalisering)")
en = accounts._laes_konti({"ibkr_account": "DUO509856", "display_name": "Algo"})
kraev(len(en) == 1 and en[0]["id"] == "DUO509856",
      "uden hvidliste bliver standarden den eneste mulighed — "
      "maskiner der ikke skal skifte konto aendrer ingenting")

# ── Endpointet ───────────────────────────────────────────────────────────────
import main

main.identity = accounts.identity
main.accounts = accounts


class FalskStrategi:
    def __init__(self, navn, status, positioner=0):
        self.name = navn
        self.status = status
        self.stats = type("S", (), {"open_positions": positioner})()


class FalskIB:
    def __init__(self, styrede): self._s = styrede
    def managedAccounts(self): return self._s


class FalskConn:
    def __init__(self, positioner=None, styrede=("DUO509856", "DUQ441063")):
        self.connected = True
        self.account = "DUO509856"
        self._pos = positioner or []
        self.ib = FalskIB(list(styrede))
    async def get_positions_live(self): return self._pos


def opsaet(strategier=(), conn=None):
    main.strategy_manager._strategies = {s.name: s for s in strategier}
    main.strategy_manager.get_ibkr = lambda: conn


async def skift(konto, bekraeft=True):
    return await main.account_skift(
        main.SkiftKontoRequest(konto=konto, bekraeft=bekraeft))


print("\n4. ⚠ Skiftet spaerres naar det kan skabe ejerloese positioner")
from strategy_base import StrategyStatus

for strategier, hvad in (
        ((FalskStrategi("Konfluens 2", StrategyStatus.RUNNING),), "en strategi koerer"),
        ((FalskStrategi("BuyTheDip", StrategyStatus.PAUSED, 2),), "pauset MED positioner")):
    ryd()
    opsaet(strategier, FalskConn())
    try:
        asyncio.run(skift("DUQ441063"))
        kraev(False, f"{hvad} -> skiftet gik igennem")
    except HTTPException as e:
        kraev(e.status_code == 409, f"{hvad} -> 409 ({e.detail[:45]})")
    kraev(accounts.aktiv_konto() == "DUO509856", f"{hvad} -> kontoen er uaendret")

ryd()
opsaet((FalskStrategi("Konfluens 2", StrategyStatus.PAUSED, 0),),
       FalskConn(positioner=[{"ticker": "AAPL", "position": 100},
                             {"ticker": "MES", "position": -1}]))
try:
    asyncio.run(skift("DUQ441063"))
    kraev(False, "aabne positioner -> skiftet gik igennem")
except HTTPException as e:
    kraev(e.status_code == 409 and "AAPL" in e.detail,
          f"aabne positioner -> 409 og de naevnes: {e.detail[:60]}")

print("\n5. ⚠ Uden IBKR-forbindelse skiftes der ikke")
# Vi kan ikke afgoere om der er aabne positioner. At gaette "nej" ville vaere
# den slags tavse antagelse der producerer ejerloese positioner.
ryd()
opsaet((), None)
try:
    asyncio.run(skift("DUQ441063"))
    kraev(False, "uden forbindelse gik skiftet igennem")
except HTTPException as e:
    kraev(e.status_code == 503, f"503 (fik {e.status_code})")
kraev(accounts.aktiv_konto() == "DUO509856", "kontoen er uaendret")

print("\n6. ⚠ TWS styrer ikke kontoen -> rul tilbage")
# Sag B forsoegt ad bagvejen: en konto under et ANDET login. Filen maa ikke
# blive staaende aendret, for saa ville journalen stemple handler med en konto
# ordrerne ikke blev lagt paa.
ryd()
c = FalskConn(styrede=("DUO509856",))          # DUQ441063 findes ikke i sessionen
opsaet((), c)
try:
    asyncio.run(skift("DUQ441063"))
    kraev(False, "en konto TWS ikke styrer blev accepteret")
except HTTPException as e:
    kraev(e.status_code == 400 and "IKKE skiftet" in e.detail,
          f"400 og det siges: {e.detail[:60]}")
kraev(accounts.aktiv_konto() == "DUO509856", "filen er rullet tilbage")
kraev(c.account == "DUO509856", "og forbindelsen er urørt")

print("\n7. Det lykkelige tilfaelde")
ryd()
c = FalskConn()
opsaet((FalskStrategi("K2", StrategyStatus.STOPPED),), c)
svar = asyncio.run(skift("DUQ441063"))
kraev(svar["ok"] and svar["konto"] == "DUQ441063", f"skiftet: {svar}")
kraev(accounts.aktiv_konto() == "DUQ441063", "filen er opdateret")
kraev(c.account == "DUQ441063",
      "⚠ og den LEVENDE forbindelse er bundet om — ellers ville ordrer gaa til "
      "den gamle konto indtil naeste genstart")

print("\n8. ⚠ Falsifikation — kontrollerne er ikke afgjort paa forhaand")
# Spaerringerne i afsnit 4-6 skal komme fra tilstanden, ikke fra et endpoint
# der altid siger nej. Afsnit 7 viste allerede at det kan lykkes; her vises at
# hver enkelt spaerring loefter naar netop DEN aarsag fjernes.
ryd()
opsaet((FalskStrategi("K2", StrategyStatus.RUNNING),), FalskConn())
spaerret_med = main._konto_spaerringer()
opsaet((FalskStrategi("K2", StrategyStatus.STOPPED),), FalskConn())
spaerret_uden = main._konto_spaerringer()
kraev(spaerret_med and not spaerret_uden,
      f"RUNNING spaerrer ({spaerret_med}), STOPPED goer ikke — spaerringen "
      f"laeser faktisk status")

opsaet((FalskStrategi("K2", StrategyStatus.PAUSED, 0),), FalskConn())
kraev(not main._konto_spaerringer(),
      "pauset UDEN positioner spaerrer ikke — kontrollen ser paa positionerne, "
      "ikke bare paa ordet PAUSED")

ryd()
accounts.AKTIV_PATH = ORIG_PATH
accounts.identity = ORIG_IDENT

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")
