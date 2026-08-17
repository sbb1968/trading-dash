"""
test_reconcile_job.py — reconcile som SELVSTÆNDIG kontrol efter markedslukning
════════════════════════════════════════════════════════════════════════════════
Arbejdsordrens T2c. Opstarts-reconcile har ÉT sted at fejle, og den 13-08
fejlede den dér: fem positioner fra 12-08 stod åbne i to døgn.

⚠ TRE EGENSKABER, OG DE ER ALLE TRE NEGATIVE KRAV — ting jobbet ikke må gøre:

  1. Det må IKKE køre mens en strategi handler. En afstemning midt i en session
     kan lukke en position strategien lige har åbnet. Kører nogen, UDSÆTTES
     jobbet (False → genforsøg i vinduet) — ikke "sprunget over".
  2. Det må IKKE melde sig færdigt uden at have afstemt noget. Et job der ikke
     nåede en eneste strategi og alligevel returnerer True, er en kontrol hvis
     fejl behandles som en beståelse — projektets tilbagevendende fejlklasse.
  3. Det må IKKE køre på en workstation. Algoserveren ejer handelskontoen.

    python test_reconcile_job.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scheduler as sch
import reconcile_job
from reconcile_job import reconcile_alle_strategier
from strategy_base import StrategyStatus

FEJL = 0


def kraev(ok: bool, hvad: str) -> None:
    global FEJL
    print(f"  {'OK  ' if ok else 'FEJL'} {hvad}")
    if not ok:
        FEJL += 1


class AttrapStrat:
    """Kun det jobbet rører: status og _reconcile_orphans."""

    def __init__(self, status=StrategyStatus.STOPPED, haenger=False, kaster=False):
        self.status = status
        self.afstemt = 0
        self._haenger = haenger
        self._kaster = kaster

    async def _reconcile_orphans(self):
        if self._haenger:
            await asyncio.sleep(3600)
        if self._kaster:
            raise RuntimeError("IBKR svarede ikke")
        self.afstemt += 1


# ⚠ INGEN KOPI AF LOGIKKEN. Første udgave af denne prøve genskabte main.py's
# funktion her og søgte derudover efter tekststumper i kildefilen. Den bestod,
# mens man kunne fjerne selve vagten fra main.py uden at noget blev rødt — to
# af seks falsifikationer gik lige igennem. Nu kaldes DEN RIGTIGE funktion.
def byg_job(strategier: dict):
    async def koer_job() -> bool:
        return await reconcile_alle_strategier(strategier, StrategyStatus.RUNNING)
    return koer_job


async def koer() -> None:
    # ⚠ Kort timeout i proeven — vi maaler ADFAERDEN ved en haengende
    #   reconcile, ikke hvor laenge produktionen venter.
    reconcile_job.PR_STRATEGI_TIMEOUT_SEC = 0.2
    # ── 1. Normalt: alle stoppede → afstemmes ──────────────────────────────
    s = {"K2": AttrapStrat(), "BTD": AttrapStrat()}
    kraev(await byg_job(s)() is True, "alle stoppede → jobbet gennemføres")
    kraev(s["K2"].afstemt == 1 and s["BTD"].afstemt == 1,
          "…og hver strategi blev faktisk afstemt")

    # ── 2. KERNEN: en strategi handler → UDSÆT, rør intet ──────────────────
    s = {"K2": AttrapStrat(StrategyStatus.RUNNING), "BTD": AttrapStrat()}
    kraev(await byg_job(s)() is False,
          "en strategi handler → jobbet udsættes (False = genforsøg)")
    kraev(s["BTD"].afstemt == 0,
          "…og INGEN strategi blev rørt, heller ikke den stoppede")

    # ── 3. Et job der intet nåede, må ikke melde sig færdigt ───────────────
    kraev(await byg_job({})() is False,
          "ingen strategier → False, ikke 'færdig for i dag'")
    s = {"K2": AttrapStrat(haenger=True)}
    kraev(await byg_job(s)() is False,
          "eneste strategi hænger → False, ikke stiltiende succes")
    s = {"K2": AttrapStrat(kaster=True)}
    kraev(await byg_job(s)() is False,
          "eneste strategi kaster → False")

    # ── 4. Én der fejler må ikke tage de andre med ─────────────────────────
    s = {"K2": AttrapStrat(kaster=True), "BTD": AttrapStrat()}
    kraev(await byg_job(s)() is True and s["BTD"].afstemt == 1,
          "én der kaster stopper ikke afstemningen af de øvrige")

    # ── 5. Jobbet er registreret i scheduleren, efter markedslukning ───────
    kilde = (Path(__file__).parent / "scheduler.py").read_text(encoding="utf-8")
    kraev('ScheduledJob("reconcile_efter_luk"' in kilde,
          "jobbet er registreret i scheduleren")
    kraev(sch.RECONCILE_START_ET.hour >= 16,
          f"det kører EFTER markedslukning ({sch.RECONCILE_START_ET})")
    kraev(sch.RECONCILE_RETRY_UNTIL_ET > sch.RECONCILE_START_ET,
          "…og har et genforsøgsvindue")
    kraev('if self._instance_role != "algoserver"' in
          kilde.split("_job_reconcile_efter_luk")[2].split("async def")[0],
          "KUN algoserveren kører det")

    # ── 6. main.py bruger DEN funktion prøven har kaldt ────────────────────
    # ⚠ Ikke en tekstsøgning efter logik-stumper (den kunne matche en
    # kommentar — og gjorde det). Her kontrolleres kun KOBLINGEN: at
    # scheduler-krogen kalder netop denne funktion.
    m = (Path(__file__).parent / "main.py").read_text(encoding="utf-8")
    kraev("from reconcile_job import reconcile_alle_strategier" in m,
          "main.py kalder den funktion prøven har afprøvet")
    kraev("run_reconcile_fn  = run_reconcile_efter_luk" in m,
          "…og krogen er injiceret i scheduleren")
    # ⚠ Udsnittet gaar til NÆSTE definition, ikke til et fast antal tegn. Første
    # udgave klippede ved 400 tegn — og strengen stod ved 420. Prøven var rød på
    # noget der var rigtigt, fordi målevinduet var for lille. Samme fejl som et
    # trunkeret debug-print tidligere i dag.
    krog = m.split("run_reconcile_efter_luk")[1].split("algo_scheduler = AlgoScheduler")[0]
    kraev("StrategyStatus.RUNNING" in krog,
          "…og sender den rigtige 'handler nu'-status ind")


if __name__ == "__main__":
    print("reconcile efter markedslukning\n")
    asyncio.run(koer())
    print(f"\n⚠ {FEJL} FEJL" if FEJL else "\nAlle bestod.")
    sys.exit(1 if FEJL else 0)
