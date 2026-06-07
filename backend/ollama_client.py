"""
ollama_client.py — Tynd, robust klient til lokale Ollama-modeller.

Designprincipper:
  - Fejler ALDRIG kalderen. generate() returnerer en streng ved succes, eller
    None hvis Ollama er nede/timeout/fejler. Rapporten viser så bare tallene.
  - Tal kommer fra Python, ord kommer fra modellen. Klienten laver KUN tekst.
  - Synkron (requests, som test_ollama.py). Kald fra async via
    asyncio.to_thread(...) så event-loopet ikke blokeres (en 8B kan bruge
    10-60 sek på en rapport).

Modeller (skal være pullet på maskinen):
  - qwen3:8b        — arbejdshest til dansk prosa + klassifikation
  - deepseek-r1:8b  — reasoning-model; udsender <think>…</think> som strippes

Placering: backend/ollama_client.py
"""
from __future__ import annotations

import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

OLLAMA_HOST          = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL        = "qwen3:8b"
GENERATE_TIMEOUT     = 180   # sek — rummelig; en 8B kan være langsom
AVAILABILITY_TIMEOUT = 3     # sek — hurtigt tjek

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def is_available() -> bool:
    """True hvis Ollama svarer. Bruges til at vise tal-kun-rapport pænt."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=AVAILABILITY_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def _strip_think(text: str) -> str:
    """Fjern <think>…</think>-blokke (deepseek-r1) og trim."""
    return _THINK_RE.sub("", text).strip()


def generate(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    temperature: float = 0.3,
    num_ctx: int = 8192,
    think: bool = False,
    timeout: int = GENERATE_TIMEOUT,
) -> str | None:
    """
    Send en prompt til Ollama og returnér modellens tekst, eller None ved fejl.

    think=False slår tænkning fra hvor muligt:
      - qwen3 honorerer den bløde switch '/no_think' i prompten
      - <think>-blokke strippes altid fra svaret (uskadeligt hvis de mangler)

    temperature lav (0.3) → mere konsistente rapporter.
    """
    text_prompt = prompt
    if not think and model.lower().startswith("qwen"):
        text_prompt = "/no_think\n" + prompt

    payload = {
        "model":   model,
        "prompt":  text_prompt,
        "stream":  False,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if system:
        payload["system"] = system

    try:
        r = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[Ollama] generate fejlede ({model}): {type(e).__name__}: {e}")
        return None

    text = data.get("response")
    if not text:
        logger.warning(f"[Ollama] tomt svar fra {model}")
        return None
    return _strip_think(text)


if __name__ == "__main__":
    # Røgtest: python ollama_client.py
    print(f"Ollama tilgængelig: {is_available()}")
    print("Svar:", generate("Svar med præcis ét ord på dansk: fungerer du?"))
