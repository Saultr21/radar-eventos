"""
llm/openrouter.py
Proveedor OpenRouter. Obtiene contexto web de la fuente mediante fetcher
y lo incluye en el prompt antes de llamar a la API.
"""
import httpx

from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_SITE_URL,
    OPENROUTER_SITE_NAME,
    MODEL_NAME,
)
from fetcher import fetch_url_details


def run_openrouter_prompt(prompt: str, source_url: str) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("Falta OPENROUTER_API_KEY o API_KEY para usar el proveedor openrouter")

    source_excerpt = _fetch_source_excerpt(source_url)
    provider_prompt = (
        f"{prompt}\n\n"
        "CONTEXTO ADICIONAL: A continuacion tienes el contenido obtenido directamente"
        f" desde {source_url}. Basate prioritariamente en este contenido.\n\n"
        f"{source_excerpt}"
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_SITE_NAME:
        headers["X-Title"] = OPENROUTER_SITE_NAME

    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": provider_prompt}],
        "temperature": 0.1,
    }
    response = httpx.post(OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _fetch_source_excerpt(url: str, max_chars: int = 20_000) -> str:
    """Obtiene el contenido textual de la URL para proveer contexto al LLM."""
    result = fetch_url_details(url, max_chars=max_chars)
    return result.get("content", "")[:max_chars]
