"""
ibkr_kvalificer.py — én sikker maade at kvalificere en IBKR-kontrakt paa
═══════════════════════════════════════════════════════════════════════════════════
⚠ HVORFOR DETTE MODUL FINDES

`ib.qualifyContractsAsync(c)` returnerer en **truthy liste ogsaa naar kvalificeringen
mislykkedes**. Den laegger blot den ukvalificerede kontrakt i listen med `conId = 0`.
Foelgende moenster er derfor ALTID sandt og udgoer et falsk positivt:

    q = await ib.qualifyContractsAsync(c)
    if q:                      # <- altid True
        return q[0]            # <- kan vaere en tom skal med conId=0

Verificeret 2026-08-04 paa MES 202406 (en purget kontrakt): `len(q) == 1` og
`bool(q) is True`, mens `q[0].conId` er 0 og en efterfoelgende barhentning fejler
med "No security definition has been found".

Konsekvensen er ikke en fejl der styrter — det er en der SVARER FORKERT. En
kortlaegning bygget paa `if q:` meldte at samtlige futures-kontrakter tilbage til
2022 var i live, mens sandheden var at alt aeldre end 202409 er purget. Havde et
tidligere fund ikke modsagt det, var vi gaaet videre i den tro at der ikke var
noget arkiveringsproblem — og havde mistet data permanent.

REGLEN: kvalificering beviser ingenting. Kun `conId != 0` — eller endnu bedre en
faktisk barhentning — er evidens.

Brug:
    from ibkr_kvalificer import kvalificer_eller_none
    c = await kvalificer_eller_none(ib, Stock("SPY", "SMART", "USD"))
    if c is None:
        ...  # kontrakten findes ikke hos IBKR
"""
from __future__ import annotations

import asyncio
from typing import Optional


async def kvalificer_eller_none(ib, contract, timeout: float = 15.0) -> Optional[object]:
    """Kvalificér ÉN kontrakt. Returnér den kvalificerede kontrakt, eller None.

    None betyder "IBKR kender ikke denne kontrakt" — ikke "der er ingen data".
    De to er forskellige, og at forveksle dem er praecis den fejl modulet findes for.
    """
    try:
        q = await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=timeout)
    except Exception:
        return None
    if not q:
        return None
    c = q[0]
    # DET AFGOERENDE TJEK. Uden det er returvaerdien vaerdiloes.
    if not getattr(c, "conId", 0):
        return None
    return c


def er_kvalificeret(contract) -> bool:
    """Har denne kontrakt faktisk faaet et conId? Til kald der kvalificerer in-place."""
    return bool(getattr(contract, "conId", 0))
