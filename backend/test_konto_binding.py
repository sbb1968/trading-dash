"""
test_konto_binding.py — ét IBKR-login, flere konti
═══════════════════════════════════════════════════════════════════════════════════
Med ÉN konto pr. login udleder IBKR selv hvor en ordre hoerer hjemme, saldi er
entydige og positions() er kun vores. Med TO konti — Ibens konto 2 er DUQ441063 —
holder ingen af delene, og INTET af det fejler hoejlydt:

  · ordre uden `account`   -> IBKR afviser eller gaetter
  · accountValues()        -> begge kontis noegler i én dict; sidste vinder
  · positions()            -> blander konti

Testen daekker ogsaa det farlige tilfaelde: en account.yaml der peger paa en konto
TWS ikke styrer. Dér maa vi IKKE filtrere saldi ned til nul eller staemple ordrer
med en ukendt konto — en algoserver der gaar i staa midt paa dagen er vaerre.

    python test_konto_binding.py
"""
from __future__ import annotations
import sys
from types import SimpleNamespace
from ibkr_connect import IBKRConnection

FEJL: list[str] = []


def kraev(b, hvad):
    print(f"  {'OK  ' if b else 'FEJL'} {hvad}")
    if not b:
        FEJL.append(hvad)


def forbindelse(konto: str, styrede: list[str]):
    c = IBKRConnection.__new__(IBKRConnection)
    c.account = (konto or "").strip().upper()
    c.ib = SimpleNamespace(managedAccounts=lambda: styrede)
    return c


def vaerdi(tag, val, konto):
    return SimpleNamespace(tag=tag, value=val, account=konto, currency="USD")


def pos(sym, antal, konto):
    return SimpleNamespace(contract=SimpleNamespace(symbol=sym, secType="FUT",
                                                    multiplier="5"),
                           position=antal, avgCost=100.0, account=konto)


print("\n1. Kontoen bekraeftes mod det TWS faktisk styrer")
c = forbindelse("DUQ441063", ["DUO509856", "DUQ441063"])
c._verificer_konto()
kraev(c.account == "DUQ441063", "konto 2 findes i loginet -> bindes")

c = forbindelse("DUN748991", ["DUN748991"])
c._verificer_konto()
kraev(c.account == "DUN748991", "ét login, én konto -> bindes ogsaa")

c = forbindelse("duq441063", ["DUQ441063"])
c._verificer_konto()
kraev(c.account == "DUQ441063", "smaa bogstaver i yaml accepteres")

print("\n2. Forkert konto maa IKKE spaerre ordrestien")
c = forbindelse("DUQ441063", ["DUN748991"])
c._verificer_konto()
kraev(c.account == "", "ukendt konto -> binding slaas FRA (koerer som hidtil)")

c = forbindelse("DUQ441063", [])
c._verificer_konto()
kraev(c.account == "", "TWS melder ingen konti -> binding slaas FRA")

print("\n3. Saldi filtreres til vores konto")
vals = [vaerdi("NetLiquidation", "1500", "DUQ441063"),
        vaerdi("NetLiquidation", "99999", "DUO509856"),
        vaerdi("BuyingPower", "6000", "DUQ441063"),
        vaerdi("BuyingPower", "400000", "DUO509856")]
c = forbindelse("DUQ441063", ["DUO509856", "DUQ441063"])
c._verificer_konto()
c.ib = SimpleNamespace(accountValues=lambda: vals,
                       managedAccounts=lambda: ["DUO509856", "DUQ441063"])
s = c.get_account_summary()
kraev(s["net_liquidation"] == 1500.0,
      f"NetLiquidation = vores konto ({s['net_liquidation']}, ikke 99999)")
kraev(s["buying_power"] == 6000.0,
      f"BuyingPower = vores konto ({s['buying_power']}, ikke 400000)")

print("\n   uden binding (én konto pr. login) skal ALT stadig med:")
c2 = forbindelse("", ["DUN748991"])
c2.ib = SimpleNamespace(accountValues=lambda: [vaerdi("NetLiquidation", "1500", "DUN748991")],
                        managedAccounts=lambda: ["DUN748991"])
kraev(c2.get_account_summary()["net_liquidation"] == 1500.0,
      "tom konto -> ufiltreret, som hidtil")

print("\n4. Positioner filtreres")
c = forbindelse("DUQ441063", ["DUO509856", "DUQ441063"])
c._verificer_konto()
c.ib = SimpleNamespace(positions=lambda: [pos("MES", 1, "DUQ441063"),
                                          pos("M2K", 7, "DUO509856")],
                       managedAccounts=lambda: ["DUO509856", "DUQ441063"])
p = c.get_positions()
kraev(len(p) == 1 and p[0]["ticker"] == "MES",
      f"kun vores kontos position ({[x['ticker'] for x in p]})")

c3 = forbindelse("", ["DUN748991"])
c3.ib = SimpleNamespace(positions=lambda: [pos("MES", 1, "DUN748991"),
                                           pos("M2K", 7, "DUN748991")])
kraev(len(c3.get_positions()) == 2, "tom konto -> begge med, som hidtil")

print("\n" + "=" * 70)
if FEJL:
    print(f"{len(FEJL)} FEJL:")
    for f in FEJL:
        print("  -", f)
    sys.exit(1)
print("Alt groent.")
