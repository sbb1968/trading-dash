"""
test_risk_per_strategy.py
─────────────────────────
Verificerer den nye per-strategi risikostyring i risk_manager.py:
  - to strategier kan dele samme ticker
  - positionsgrænse håndhæves PER STRATEGI
  - strategier er uafhængige (den enes loft blokerer ikke den anden)
  - eksponering nedskrives korrekt per strategi ved luk
  - konto-bagstopper fanger samlet overskridelse

Koer i backend-mappen EFTER Stykke 4 er implementeret:
    python test_risk_per_strategy.py
"""

import asyncio
from dataclasses import dataclass

from risk_manager import RiskManager, RiskConfig


@dataclass
class FakeOrder:
    strategy_name: str
    ticker: str
    action: str
    quantity: int
    limit_price: float = 10.0


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        raise SystemExit(1)


async def main():
    # Lavt-sat config så vi kan teste grænserne uden tusindvis af ordrer
    cfg = RiskConfig()
    cfg.max_positions_per_strategy = 3
    cfg.max_exposure_per_strategy  = 20000.0
    cfg.max_total_positions        = 50
    cfg.max_total_exposure         = 200000.0

    rm = RiskManager(cfg)

    # Test 1: to strategier deler samme ticker (duplicate-blokering fra)
    ok1, _ = await rm.approve_order(FakeOrder("ORB", "VCIG", "BUY", 200, 5.80))
    ok2, _ = await rm.approve_order(FakeOrder("Konfluens", "VCIG", "BUY", 100, 5.85))
    check("ORB åbner VCIG", ok1)
    check("Konfluens åbner OGSÅ VCIG (delt ticker tilladt)", ok2)

    # Test 2: per-strategi positionsgrænse — ORB må have 3
    await rm.approve_order(FakeOrder("ORB", "AAA", "BUY", 10))
    await rm.approve_order(FakeOrder("ORB", "BBB", "BUY", 10))  # ORB: VCIG+AAA+BBB = 3
    ok4, r4 = await rm.approve_order(FakeOrder("ORB", "CCC", "BUY", 10))
    check("ORB's 4. position afvises (per-strategi loft)", not ok4, r4)

    # Test 3: Konfluens er uafhængig af ORB's loft
    okk, _ = await rm.approve_order(FakeOrder("Konfluens", "DDD", "BUY", 10))
    check("Konfluens upåvirket af ORB's fulde loft", okk)

    # Test 4: luk frigør plads per strategi
    rm.release_exposure("ORB", "AAA", 10 * 10.0)
    okn, _ = await rm.approve_order(FakeOrder("ORB", "EEE", "BUY", 10))
    check("ORB kan åbne igen efter luk frigav plads", okn)

    # Test 5: per-strategi eksponeringsloft
    cfg2 = RiskConfig()
    cfg2.max_exposure_per_strategy = 5000.0
    cfg2.max_positions_per_strategy = 99  # tag positionsloft ud af ligningen
    rm2 = RiskManager(cfg2)
    await rm2.approve_order(FakeOrder("X", "T1", "BUY", 100, 30.0))  # $3000
    ok_exp, r_exp = await rm2.approve_order(FakeOrder("X", "T2", "BUY", 100, 30.0))  # +$3000 > $5000
    check("per-strategi eksponeringsloft håndhæves", not ok_exp, r_exp)

    # Test 6: konto-bagstopper (sæt kunstigt lavt)
    cfg3 = RiskConfig()
    cfg3.max_total_positions = 2
    cfg3.max_positions_per_strategy = 99
    rm3 = RiskManager(cfg3)
    await rm3.approve_order(FakeOrder("A", "X", "BUY", 10))
    await rm3.approve_order(FakeOrder("B", "Y", "BUY", 10))
    ok_bs, r_bs = await rm3.approve_order(FakeOrder("A", "Z", "BUY", 10))
    check("konto-bagstopper blokerer samlet loft", not ok_bs, r_bs)

    print("\nALLE TESTS BESTAAET ✓")


if __name__ == "__main__":
    asyncio.run(main())