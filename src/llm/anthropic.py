"""
llm/anthropic.py
Proveedor Anthropic Claude con herramienta web_search nativa.
"""
try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

from config import ANTHROPIC_API_KEY, MODEL_NAME, MAX_TOKENS


def run_anthropic_prompt(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise ValueError("Falta ANTHROPIC_API_KEY para usar el proveedor anthropic")
    if _anthropic is None:
        raise ValueError("El paquete 'anthropic' no está instalado (pip install anthropic)")

    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=MAX_TOKENS,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    text = ""
    for block in response.content:
        if block.type == "text":
            text += block.text
    return text
