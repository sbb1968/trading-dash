"""
reconcile_job.py — afstemning mod broker EFTER markedslukning
════════════════════════════════════════════════════════════════════════════════
Arbejdsordrens T2c. Opstarts-reconcile har ÉT sted at fejle, og den 13-08
fejlede den dér: fem positioner fra 12-08 stod åbne i to døgn, fordi
reconcile løb tør for tid og strategierne handlede videre.

Dette er den anden kontrol, på et andet tidspunkt, med et andet fejlmønster.

⚠ HVORFOR DEN LIGGER I SIN EGEN FIL og ikke i main.py's opstartsfunktion:
fordi den skal kunne PRØVES. Første udgave lå inde i en closure i en 200
linjers startup-rutine og kunne kun nås gennem serveren — så prøven blev
skrevet mod en KOPI af logikken plus en søgning efter tekststumper i
kildefilen. Den kopi bestod, mens man kunne fjerne selve vagten fra main.py
uden at noget blev rødt. To af seks falsifikationer gik lige igennem.

Det er tredje gang i dette projekt at en prøve ikke kunne se sit eget emne.
Løsningen er ikke en skarpere tekstsøgning; det er at gøre koden til noget
man kan kalde.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Hvor længe én strategis afstemning må tage. Rundhåndet: målt på 70 opstarter
# 3.–15. august er median 0,0 s og p90 0,4 s, så 60 s er ~150× p90. Budgettet
# er ikke det der fejler — se noten i algo_confluence2.py.
PR_STRATEGI_TIMEOUT_SEC = 60


async def reconcile_alle_strategier(strategier: Mapping[str, Any],
                                    koerende_status: Any) -> bool:
    """Afstem hver strategis journal mod broker. True = jobbet blev gennemført.

    ⚠ KØRER IKKE MENS DER HANDLES. Samme regel som manual_reconcile.py: en
    afstemning midt i en session kan lukke en position strategien lige har
    åbnet. Er nogen i gang, UDSÆTTES jobbet (False → scheduleren genforsøger i
    vinduet) frem for at blive sprunget over. Forskellen er vigtig: "sprunget
    over" er en kontrol der aldrig kørte og alligevel meldte sig færdig.

    ⚠ GENBRUGER STRATEGIENS EGEN `_reconcile_orphans`. En separat
    oprydningsrutine ville være en ANDEN adfærd end den ved opstart, og så
    kunne to kontroller være uenige om hvad der er et spøgelse. Den lukker
    kun strategiens egne journal-rows og rører aldrig positioner uden spor.

    ⚠ RETURNERER KUN True NÅR NOGET FAKTISK BLEV AFSTEMT. Et job der ikke nåede
    en eneste strategi og alligevel meldte sig færdigt, er en kontrol hvis fejl
    behandles som en beståelse — præcis den fejlklasse hele oprydningen handler
    om.

    `koerende_status` sendes ind frem for at importeres, så modulet ikke
    trækker strategy_base med sig ind i en prøve.
    """
    koerende = [n for n, s in strategier.items()
                if getattr(s, "status", None) == koerende_status]
    if koerende:
        logger.warning(f"[Reconcile-job] Udsat — disse handler stadig: {koerende}")
        return False

    n_ok = 0
    for navn, strat in list(strategier.items()):
        fn = getattr(strat, "_reconcile_orphans", None)
        if fn is None:
            continue
        try:
            await asyncio.wait_for(fn(), timeout=PR_STRATEGI_TIMEOUT_SEC)
            n_ok += 1
        except asyncio.TimeoutError:
            logger.error(f"[Reconcile-job] {navn}: timeout efter "
                         f"{PR_STRATEGI_TIMEOUT_SEC}s")
        except Exception as e:
            # ⚠ Én strategi der fejler, må ikke tage de øvrige med sig.
            logger.error(f"[Reconcile-job] {navn}: {e}")

    logger.info(f"[Reconcile-job] {n_ok} af {len(strategier)} strategi(er) afstemt")
    return n_ok > 0
