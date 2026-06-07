"""
ai_prompts.py — Versionerede prompt-skabeloner til AI-funktioner.

Alt brugervendt tekst er dansk. Skabelonerne får KUN færdige tal. Hver skabelon
har eksplicit anti-hallucinations- og ingen-anbefaling-regel.

Placering: backend/ai_prompts.py
"""
from __future__ import annotations

# Bump ved ændringer, så vi kan spore hvilken prompt en rapport blev lavet med.
DAILY_REPORT_PROMPT_VERSION = "daglig-rapport-v1"

DAILY_REPORT_SYSTEM = (
    "Du er en erfaren, nøgtern trading-coach. Du skriver en kort aftenrapport "
    "på dansk ud fra dagens FÆRDIGE tal fra en algoritmisk paper-trading-platform. "
    "Du regner ALDRIG selv — alle tal er givet i beskeden. "
    "Du finder ikke på tal, tickers eller handler der ikke står i beskeden. "
    "Du giver IKKE købs- eller salgsanbefalinger, position-størrelser eller løfter "
    "om fremtidig performance — rapporten er et tilbageskuende lærings-værktøj. "
    "Forveksl ALDRIG exit-årsager: 'stop' er et tab-stop, 'trail' er en trailing-stop "
    "der typisk LÅSER GEVINST, og 'target' er et opnået kursmål. En handel er kun et "
    "tab hvis dens P&L er negativ; en 'trail'- eller 'target'-handel med positiv P&L "
    "er en vinder. Brug listerne 'Bedste handler' og 'Tabende handler' fra tallene som "
    "facit for hvad der var vindere og tabere. "
    "Når der er få handler (under ~20), så understreg eksplicit at udsnittet er for "
    "lille til sikre konklusioner, og overfortolk ikke nøgletal som profit factor. "
    "Tone: rolig, konkret, ærlig. Skriv ren prosa i korte afsnit — INGEN markdown, "
    "ingen overskrifter, ingen ** (stjerner) og ingen nummererede lister (1., 2., 3.)."
)


def daily_report_prompt(numbers_block: str) -> str:
    """Bruger-prompt til den daglige rapport. numbers_block er en kompakt,
    dansk-labellet tekst-gengivelse af fleet-rapportens tal."""
    return (
        "Her er dagens tal på tværs af alle maskiner:\n\n"
        f"{numbers_block}\n\n"
        "Skriv en kort aftenrapport på dansk med:\n"
        "1) Én linje samlet vurdering af dagen for hele flåden.\n"
        "2) Pr. maskine/strategi: hvad sprang i øjnene (kun ud fra tallene ovenfor).\n"
        "3) Hvilke handler gik godt/skidt, og om exit-årsagerne viser et mønster "
        "(fx mange 'stop' kan tyde på for tidlige entries; mange 'force_close' "
        "kan tyde på at handler ikke nåede at resolvere).\n"
        "4) 1-3 konkrete, forsigtige forslag til hvad der kunne undersøges.\n\n"
        "Hvis en maskine ikke har handler i dag, så skriv det kort. "
        "Brug KUN tallene ovenfor."
    )
