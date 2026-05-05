"""
llm/__init__.py
Despachador de proveedores LLM. Importa el proveedor configurado en tiempo de ejecución
para evitar importar dependencias innecesarias (ej: anthropic cuando se usa lmstudio).
"""
from config import LLM_PROVIDER


def run_prompt(prompt: str, source_url: str = "") -> str:
    """Ejecuta el prompt con el proveedor configurado y devuelve el texto de respuesta."""
    if LLM_PROVIDER == "anthropic":
        from llm.anthropic import run_anthropic_prompt
        return run_anthropic_prompt(prompt)

    if LLM_PROVIDER == "openrouter":
        from llm.openrouter import run_openrouter_prompt
        return run_openrouter_prompt(prompt, source_url)

    if LLM_PROVIDER == "lmstudio":
        from llm.lmstudio import run_lmstudio_prompt
        return run_lmstudio_prompt(prompt)

    raise ValueError(f"Proveedor LLM no soportado: '{LLM_PROVIDER}'. "
                     "Valores válidos: anthropic | openrouter | lmstudio")
