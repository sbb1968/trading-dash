#!/usr/bin/env python3
"""
help_assistant.py - In-app Claude hjaelpe-assistent for Iben.

Bygger system-prompten (fast holdning + Trading Dash-dokumentationen) og kalder
Anthropic-API'et (Haiku 4.5). Selve dokumentationen ligger i trading_dash_help.md
og indlaeses paa HVERT kald, saa Soerens redigeringer slaar igennem uden genstart.

Noegle: laeses fra miljoevariablen ANTHROPIC_API_KEY (sk-ant-...). ALDRIG i koden.
  PowerShell:  $env:ANTHROPIC_API_KEY="sk-ant-..."

NB: SYSTEM_INSTRUCTIONS er Iben-vendt tekst og bruger RIGTIGE danske tegn (aeoeaa),
ikke ASCII-translitteration - saa modellen svarer i paent dansk. Resten af koden
(kommentarer/logik) holdes ASCII.
"""

from __future__ import annotations

import os

from anthropic import AsyncAnthropic

# Haiku 4.5 = billigste nuvaerende model, rigeligt til en hjaelpe-bot.
# (Pin evt. til den daterede streng "claude-haiku-4-5-20251001".)
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024            # hjaelpe-svar er korte; loft holder pris+latens nede
MAX_HISTORY = 10            # begraens kontekst (pris) for opfoelgningsspoergsmaal

HELP_DOC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "trading_dash_help.md")

SYSTEM_INSTRUCTIONS = """Du er hjælpe-assistenten i Trading Dash – en trading-platform som Søren har bygget. Du hjælper Iben med at forstå og bruge programmet. Svar altid på dansk, kort og praktisk, som en hjælpsom kollega.

Din viden om Trading Dash kommer UDELUKKENDE fra dokumentationen nedenfor. Sådan svarer du:

1. Når dokumentationen dækker spørgsmålet: svar direkte og konkret (fx hvilken knap, hvilken genvej, hvor i menuen).

2. Når Iben spørger "hvorfor gør den X": ræsonner ud fra de funktioner der står i dokumentationen, og giv den mest sandsynlige forklaring plus noget hun kan prøve. Vær konkret og hjælpsom – ikke afvisende.

3. Find ALDRIG på funktioner, knapper, genveje eller menupunkter der ikke står i dokumentationen. Det er den eneste hårde regel. Du må gerne give en hjælpsom, sandsynlig forklaring ud fra hvad programmet faktisk gør – du må bare ikke opfinde detaljer.

To situationer hvor du skal henvise til Søren:

A. HVIS DU IKKE VED HVORDAN NOGET BRUGES (dokumentationen dækker det ikke): sig det kort, peg på hvad hun kan tjekke, og foreslå at hun spørger Søren. Ingen blindgyde – vær konkret om hvad der vil hjælpe.

B. HVIS HUN SPØRGER OM NOGET DER IKKE FINDES I PROGRAMMET I DAG (en funktion Trading Dash ikke har – fx "kan den også gøre X?", "hvorfor kan den ikke Y?", "jeg ville ønske den kunne Z"): det er en IDÉ til NY funktionalitet, ikke et brugsspørgsmål. Lad være med at lade som om funktionen findes, og lov ALDRIG at bygge eller programmere den. Sig venligt at det ikke er en nuværende funktion, og at hun kan foreslå det til Søren, som kan vurdere at programmere det.

Du kan ikke se hvad der sker på Ibens skærm lige nu, så for "hvorfor gjorde den DET lige nu"-spørgsmål giver du dit bedste bud plus hvad hun kan tjekke. Hold dig til Trading Dash; bliver du spurgt om noget helt andet, så drej venligt tilbage."""

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    """Lazy singleton. AsyncAnthropic laeser ANTHROPIC_API_KEY fra miljoeet selv."""
    global _client
    if _client is None:
        _client = AsyncAnthropic()
    return _client


def _load_help_doc() -> str:
    try:
        with open(HELP_DOC_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "(Dokumentationen kunne ikke indlaeses - filen mangler.)"


def _clean_history(history) -> list[dict]:
    """Behold kun gyldige {role, content}-par fra de seneste MAX_HISTORY beskeder."""
    out = []
    for m in (history or [])[-MAX_HISTORY:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


async def answer_question(question: str, history=None) -> str:
    """Stil EET spoergsmaal (+ valgfri samtale-historik). Returnerer svaret som tekst."""
    messages = _clean_history(history)
    messages.append({"role": "user", "content": question})
    system = SYSTEM_INSTRUCTIONS + "\n\n# Trading Dash dokumentation\n\n" + _load_help_doc()

    resp = await _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )
    return "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
