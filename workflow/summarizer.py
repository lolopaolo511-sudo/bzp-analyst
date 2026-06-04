"""Streszczenia ogłoszeń via lokalny LLM (Ollama) lub fallback tekstowy."""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger("bzp_analyst.summarizer")

_SYSTEM = (
    "Jesteś analitykiem zamówień publicznych. "
    "Streszczaj ogłoszenia przetargowe zwięźle i konkretnie po polsku."
)

_PROMPT_TMPL = """Ogłoszenie BZP:
Tytuł: {title}
Zamawiający: {org}, {city}
Typ: {order_type}
CPV: {cpv}
Termin składania: {deadline}

Zadanie: napisz 2-3 zdania (max 100 słów) — co trzeba zrobić, dla kogo, do kiedy. Bez zdań wstępnych."""


def _fallback_summary(title: str, org: str, city: str, deadline: str) -> str:
    """Szablon-based fallback gdy LLM niedostępny."""
    return f"Zamówienie na: {title}. Zamawiający: {org} ({city}). Termin składania: {deadline}."


def summarize(
    title: str,
    org: str,
    city: str,
    order_type: str,
    cpv: str,
    deadline: str,
    ollama_url: str = "http://localhost:11434",
    model: str = "deepseek-coder-v2:16b",
    fallback_model: str = "llama3.2:3b",
    timeout_s: int = 30,
    use_llm: bool = True,
) -> str:
    if not use_llm:
        return _fallback_summary(title, org, city, deadline)

    prompt = _PROMPT_TMPL.format(
        title=title, org=org, city=city,
        order_type=order_type, cpv=cpv[:120], deadline=deadline,
    )

    for mdl in [model, fallback_model]:
        try:
            resp = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": mdl,
                    "prompt": prompt,
                    "system": _SYSTEM,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 150},
                },
                timeout=timeout_s,
            )
            if resp.status_code == 200:
                text = resp.json().get("response", "").strip()
                if text:
                    return _clean(text)
        except requests.RequestException as e:
            logger.debug("Ollama (%s) niedostępna: %s", mdl, e)

    return _fallback_summary(title, org, city, deadline)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Usuń zbędne meta-wstępy LLM
    text = re.sub(r"^(Streszczenie|Oto streszczenie|Summary)[:\s]+", "", text, flags=re.IGNORECASE)
    return text
