"""
daily_report.py — AI-narrativ ovenpå fleet-rapportens tal.

Henter fleet-rapporten (kun tal), bygger en dansk prompt og lader en lokal
Ollama-model skrive aftenrapporten. Tallene returneres ALTID; narrativet er
None hvis Ollama er nede.

CLI:  python daily_report.py [today|7d|30d|all]
      (kører mod journal.db + arkiverne; printer rapporten — brug til at
       verificere på algoserveren før Studio røres)

Placering: backend/daily_report.py
"""
from __future__ import annotations

import asyncio
import logging

import ai_prompts
import fleet_report
import ollama_client

logger = logging.getLogger(__name__)


async def generate_daily_report(local_db, period: str = "today",
                               model: str = ollama_client.DEFAULT_MODEL) -> dict:
    """Fleet-tal + (forsøg på) AI-narrativ. Returnerer dict med begge dele."""
    report        = await fleet_report.build_fleet_report(local_db, period=period)
    numbers_block = fleet_report.report_to_text(report)

    narrative = None
    ai_ok     = ollama_client.is_available()
    if ai_ok:
        narrative = await asyncio.to_thread(
            ollama_client.generate,
            ai_prompts.daily_report_prompt(numbers_block),
            model=model,
            system=ai_prompts.DAILY_REPORT_SYSTEM,
        )
        if narrative is None:
            ai_ok = False   # kald slog fejl trods tilgængelighed

    return {
        "period":         period,
        "generated_at":   report["generated_at"],
        "model":          model,
        "prompt_version": ai_prompts.DAILY_REPORT_PROMPT_VERSION,
        "ai_available":   ai_ok,
        "narrative":      narrative,
        "numbers":        report,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path

    period  = sys.argv[1] if len(sys.argv) > 1 else "today"
    db_path = Path(__file__).parent / "trading_dash.db"

    async def _main():
        db = await aiosqlite.connect(db_path.as_posix())
        try:
            result = await generate_daily_report(db, period=period)
        finally:
            await db.close()

        print("=" * 72)
        print(f"DAGSRAPPORT — {period}  (genereret {result['generated_at']})")
        print(f"AI: {'ja' if result['ai_available'] else 'NEJ (kun tal)'} "
              f"· model {result['model']}")
        print("=" * 72)
        print("\n── TAL ──")
        print(fleet_report.report_to_text(result["numbers"]))
        print("\n── AI-KOMMENTAR ──")
        print(result["narrative"] or "(AI-kommentar utilgængelig)")

    import aiosqlite
    asyncio.run(_main())
