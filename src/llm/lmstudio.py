"""
llm/lmstudio.py
Proveedor LM Studio con soporte para herramientas (legacy) y MCP (native/project).
Gestiona también el ciclo de vida del servidor MCP local (src/mcp_server.py).
"""
import asyncio
import atexit
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from config import (
    LMSTUDIO_BASE_URL,
    LMSTUDIO_API_TOKEN,
    LMSTUDIO_API_MODE,
    LMSTUDIO_CONTEXT_WINDOW,
    LMSTUDIO_WEB_MCP_PORT,
    LMSTUDIO_WEB_MCP_URL,
    LMSTUDIO_TOOL_ROUNDS,
    LMSTUDIO_TOOL_RESULT_CHARS,
    LMSTUDIO_MODEL_TTL,
    LMSTUDIO_REQUEST_TIMEOUT,
    MAX_TOKENS,
    ROOT_DIR,
    MODEL_NAME,
)

log = logging.getLogger(__name__)


class LMStudioContextOverflow(RuntimeError):
    """Lanzada cuando LM Studio rechaza la petición por exceder context_length.
    No tiene sentido reintentar con el mismo payload."""

# Ruta al servidor MCP local: src/mcp_server.py
_MCP_SERVER_PATH = Path(__file__).resolve().parent.parent / "mcp_server.py"

_MCP_PROCESS: subprocess.Popen | None = None
_MCP_LOCK = threading.Lock()

_MODEL_LOADED = False
_MODEL_LOAD_LOCK = threading.Lock()
# Contexto realmente cargado en LM Studio (si difiere del configurado).
# Se rellena en ensure_model_loaded() y lo usa el extractor para truncar
# input antes de mandar peticiones que sabemos que van a desbordar.
LMSTUDIO_LOADED_CONTEXT: int = 0

# ── Definición de herramientas para el modo legacy-tools ─────────────────────

LMSTUDIO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_source_pages",
            "description": (
                "Busca paginas relevantes del mismo sitio web de la fuente "
                "y devuelve una lista JSON con title y url."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "URL principal de la fuente",
                    },
                    "query": {
                        "type": "string",
                        "description": "Consulta de búsqueda interna del sitio",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Número máximo de resultados",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["source_url", "query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Descarga una URL y devuelve texto util junto con "
                "enlaces descubiertos del mismo sitio."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL a descargar"},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
]


# ── Ciclo de vida del servidor MCP ────────────────────────────────────────────

def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _stop_mcp_server() -> None:
    global _MCP_PROCESS
    if _MCP_PROCESS and _MCP_PROCESS.poll() is None:
        _MCP_PROCESS.terminate()
    _MCP_PROCESS = None


atexit.register(_stop_mcp_server)


def ensure_mcp_server() -> None:
    """Arranca src/mcp_server.py si el puerto MCP no está ya activo."""
    global _MCP_PROCESS

    if _is_port_open("127.0.0.1", LMSTUDIO_WEB_MCP_PORT):
        return

    with _MCP_LOCK:
        if _is_port_open("127.0.0.1", LMSTUDIO_WEB_MCP_PORT):
            return

        env = os.environ.copy()
        env["LMSTUDIO_WEB_MCP_PORT"] = str(LMSTUDIO_WEB_MCP_PORT)

        _MCP_PROCESS = subprocess.Popen(
            [sys.executable, str(_MCP_SERVER_PATH)],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(40):
            if _is_port_open("127.0.0.1", LMSTUDIO_WEB_MCP_PORT):
                return
            if _MCP_PROCESS.poll() is not None:
                break
            time.sleep(0.25)

        raise RuntimeError("No se pudo iniciar el servidor MCP web local para LM Studio")


# ── Llamadas MCP ──────────────────────────────────────────────────────────────

async def _call_mcp_tool(name: str, arguments: dict) -> str:
    async with streamable_http_client(LMSTUDIO_WEB_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments=arguments)

            if result.isError:
                errors = [
                    getattr(c, "text", "")
                    for c in result.content
                    if getattr(c, "text", None)
                ]
                raise RuntimeError(
                    " ".join(errors) or f"Error llamando a la tool MCP '{name}'"
                )

            if result.structuredContent is not None:
                return json.dumps(result.structuredContent, ensure_ascii=False)

            return "\n".join(
                getattr(c, "text", "")
                for c in result.content
                if getattr(c, "text", None)
            )


def _execute_tool(name: str, arguments: dict) -> str:
    ensure_mcp_server()
    return asyncio.run(_call_mcp_tool(name, arguments))


def _compact_tool_result(name: str, result: str) -> str:
    """Recorta y simplifica el resultado de una tool para no saturar el contexto."""
    try:
        payload = json.loads(result)
    except Exception:
        return result[:LMSTUDIO_TOOL_RESULT_CHARS]

    if name == "fetch_url" and isinstance(payload, dict):
        return json.dumps(
            {
                "requested_url": payload.get("requested_url"),
                "final_url": payload.get("final_url"),
                "status": payload.get("status"),
                "status_code": payload.get("status_code"),
                "error": payload.get("error"),
                "content": (payload.get("content") or "")[:LMSTUDIO_TOOL_RESULT_CHARS],
                "discovered_links": (payload.get("discovered_links") or [])[:8],
            },
            ensure_ascii=False,
        )

    if name == "search_source_pages" and isinstance(payload, dict):
        return json.dumps(
            {
                "source_url": payload.get("source_url"),
                "query": payload.get("query"),
                "results": (payload.get("results") or [])[:6],
            },
            ensure_ascii=False,
        )

    return json.dumps(payload, ensure_ascii=False)[:LMSTUDIO_TOOL_RESULT_CHARS]


# ── Modos de llamada al LLM ───────────────────────────────────────────────────

def _lmstudio_root() -> str:
    """URL raíz de LM Studio sin el sufijo /v1 (ej. http://host:1234)."""
    return LMSTUDIO_BASE_URL.rstrip("/").removesuffix("/v1")


def _uses_local_lmstudio(base: str) -> bool:
    """Indica si la URL de LM Studio apunta a esta máquina.
    Solo en ese caso tiene sentido invocar el CLI local `lms`."""
    host = (urlparse(base).hostname or "").lower()
    if not host:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def _get_loaded_context(base: str, headers: dict) -> tuple[int, int]:
    """Devuelve (loaded_ctx, max_ctx) de la instancia cuyo identifier es
    exactamente MODEL_NAME (el que va a usar la API OpenAI-compat).
    Si no existe match exacto, busca uno parcial. (0, 0) si no hay."""
    try:
        resp = httpx.get(f"{base}/api/v0/models", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for m in data:
                if m.get("id") == MODEL_NAME and m.get("state") == "loaded":
                    return (
                        int(m.get("loaded_context_length") or 0),
                        int(m.get("max_context_length") or 0),
                    )
            for m in data:
                if MODEL_NAME in m.get("id", "") and m.get("state") == "loaded":
                    return (
                        int(m.get("loaded_context_length") or 0),
                        int(m.get("max_context_length") or 0),
                    )
    except Exception as exc:
        log.debug("No se pudo consultar /api/v0/models: %s", exc)
    return 0, 0


def _lms_reload_with_context(target_ctx: int, base: str, headers: dict) -> bool:
    """Recarga el modelo vía `lms` CLI con el context_length deseado.
    Descarga PRIMERO todas las instancias del modelo (LM Studio puede tener
    cargadas variantes con el mismo nombre y distinto context_length, p.ej.
    'qwen3.5-9b' y 'qwen/qwen3.5-9b'). Luego carga una sola con el ctx OK.
    Returns True si se ejecutó con éxito."""
    lms_bin = shutil.which("lms") or shutil.which("lms.exe")
    if not lms_bin:
        log.warning("CLI 'lms' no encontrado en PATH — no se puede ajustar contexto automáticamente")
        return False

    # Descubre todos los identifiers cargados que casen con MODEL_NAME y los
    # descarga uno por uno (lms unload solo descarga uno a la vez).
    loaded_ids: list[str] = []
    try:
        resp = httpx.get(f"{base}/api/v0/models", headers=headers, timeout=10)
        if resp.status_code == 200:
            for m in resp.json().get("data", []):
                mid = m.get("id", "")
                if MODEL_NAME in mid and m.get("state") == "loaded":
                    loaded_ids.append(mid)
    except Exception as exc:
        log.debug("No se pudo listar modelos cargados: %s", exc)

    log.info(
        "Recargando '%s' vía lms (descargando %d instancia(s) previas: %s)…",
        MODEL_NAME, len(loaded_ids), loaded_ids,
    )
    for mid in loaded_ids:
        try:
            subprocess.run(
                [lms_bin, "unload", mid],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
            )
        except Exception as exc:
            log.debug("unload %s falló: %s", mid, exc)

    try:
        # --parallel=1: un solo slot de inferencia. LM Studio divide el ctx
        # entre slots, así que parallel=4 con ctx=34096 deja ~8500 por slot.
        # --identifier MODEL_NAME: garantiza que la API OpenAI-compat encuentre
        # esta instancia con el nombre exacto que pedimos.
        result = subprocess.run(
            [lms_bin, "load", MODEL_NAME,
             "--context-length", str(target_ctx),
             "--parallel", "1",
             "--identifier", MODEL_NAME,
             "--yes"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
        )
        if result.returncode == 0:
            log.info("Modelo '%s' cargado con ctx=%d", MODEL_NAME, target_ctx)
            return True
        log.warning(
            "lms load falló (rc=%d): %s",
            result.returncode, (result.stderr or b"").decode("utf-8", "replace")[:300],
        )
    except Exception as exc:
        log.warning("Error invocando lms load: %s", exc)
    return False


def ensure_model_loaded() -> None:
    """Asegura que el modelo está en LM Studio con el context_length deseado.
    Si ya está cargado con contexto suficiente, no toca nada.
    Si no, intenta recargar vía `lms` CLI (la API REST OpenAI-compat no
    permite cambiar el contexto en caliente)."""
    global _MODEL_LOADED, LMSTUDIO_LOADED_CONTEXT
    if _MODEL_LOADED:
        return

    with _MODEL_LOAD_LOCK:
        if _MODEL_LOADED:
            return

        base = _lmstudio_root()
        headers = {"Content-Type": "application/json"}
        if LMSTUDIO_API_TOKEN:
            headers["Authorization"] = f"Bearer {LMSTUDIO_API_TOKEN}"

        loaded_ctx, max_ctx = _get_loaded_context(base, headers)
        LMSTUDIO_LOADED_CONTEXT = loaded_ctx

        if loaded_ctx >= LMSTUDIO_CONTEXT_WINDOW:
            log.info(
                "Modelo '%s' cargado en LM Studio (ctx=%d, max=%d)",
                MODEL_NAME, loaded_ctx, max_ctx,
            )
            _MODEL_LOADED = True
            return

        if not _uses_local_lmstudio(base):
            log.info(
                "LM Studio remoto detectado en %s. Se omite `lms load` local; "
                "usa el modelo '%s' cargado en ese servidor.",
                base, MODEL_NAME,
            )
            if loaded_ctx > 0:
                log.warning(
                    "El servidor remoto reporta ctx=%d (< %d requerido). "
                    "El extractor truncará el input hasta que recargues el modelo allí.",
                    loaded_ctx, LMSTUDIO_CONTEXT_WINDOW,
                )
            else:
                log.warning(
                    "No se pudo verificar un modelo cargado en el servidor remoto %s. "
                    "No se intentará `lms load` desde esta máquina.",
                    base,
                )
            _MODEL_LOADED = True
            return

        log.info(
            "Modelo '%s' tiene ctx=%d (< %d requerido). Recargando…",
            MODEL_NAME, loaded_ctx, LMSTUDIO_CONTEXT_WINDOW,
        )
        if _lms_reload_with_context(LMSTUDIO_CONTEXT_WINDOW, base, headers):
            new_ctx, _ = _get_loaded_context(base, headers)
            LMSTUDIO_LOADED_CONTEXT = new_ctx
            if new_ctx >= LMSTUDIO_CONTEXT_WINDOW:
                log.info("Modelo recargado: ctx=%d", new_ctx)
            else:
                log.warning(
                    "Tras recargar, ctx=%d sigue < %d. El extractor truncará el input.",
                    new_ctx, LMSTUDIO_CONTEXT_WINDOW,
                )
        else:
            log.warning(
                "No se pudo recargar el modelo. El extractor truncará el input para "
                "encajar en ctx=%d. Carga manualmente con "
                "`lms load %s --context-length=%d` para usar la ventana completa.",
                loaded_ctx, MODEL_NAME, LMSTUDIO_CONTEXT_WINDOW,
            )

        _MODEL_LOADED = True

def _truncate_old_tool_results(messages: list[dict], keep_last: int = 1, max_chars: int = 400) -> int:
    """Recorta el contenido de tool results antiguos para liberar contexto.
    Conserva intactos los `keep_last` más recientes; el resto los reduce a `max_chars`.
    Devuelve cuántos mensajes se han truncado."""
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= keep_last:
        return 0
    truncated = 0
    for idx in tool_indices[:-keep_last]:
        content = messages[idx].get("content") or ""
        if len(content) > max_chars:
            messages[idx]["content"] = content[:max_chars] + " …[truncado]"
            truncated += 1
    return truncated


def _strip_thinking(text: str) -> str:
    """Elimina bloques <think>...</think> que Qwen genera en modo razonamiento.
    Evita que se acumulen en el historial y saturen el contexto."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


_HERMES_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
# Tolerante a truncaciones: el modelo puede no cerrar </function> o </tool_call>
_HERMES_FUNC_RE = re.compile(
    r"<tool_call>\s*<function=([\w_]+)>(.*?)(?:</function>|</tool_call>|\Z)",
    re.DOTALL,
)
# El valor del parámetro puede no cerrarse — capturar hasta </parameter>, otro
# <parameter=, </function>, </tool_call> o final del string.
_HERMES_PARAM_RE = re.compile(
    r"<parameter=([\w_]+)>\s*(.*?)\s*(?:</parameter>|(?=<parameter=)|</function>|</tool_call>|\Z)",
    re.DOTALL,
)


def _parse_hermes_tool_calls(content: str) -> list[dict]:
    """Algunos Qwen3 emiten tool_calls como texto Hermes en lugar de la
    estructura OpenAI. Soporta dos variantes:
      - JSON dentro de <tool_call>{"name": ..., "arguments": {...}}</tool_call>
      - <tool_call><function=NAME><parameter=k>v</parameter>...</function></tool_call>
    Devuelve una lista vacía si no encuentra ninguno parseable."""
    calls: list[dict] = []

    for idx, match in enumerate(_HERMES_TOOL_CALL_RE.finditer(content)):
        try:
            obj = json.loads(match.group(1))
            name = obj.get("name") or obj.get("function") or ""
            args = obj.get("arguments") or obj.get("parameters") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args}
            if name:
                calls.append({
                    "id": f"hermes_{idx}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                })
        except Exception:
            continue

    if calls:
        return calls

    for idx, match in enumerate(_HERMES_FUNC_RE.finditer(content)):
        name = match.group(1)
        body = match.group(2)
        params = {k: v.strip() for k, v in _HERMES_PARAM_RE.findall(body)}
        if name:
            calls.append({
                "id": f"hermes_fn_{idx}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(params, ensure_ascii=False)},
            })

    return calls


def run_lmstudio_prompt_legacy(prompt: str) -> str:
    """Modo legacy-tools / project-mcp: ciclo de herramientas vía chat/completions."""
    ensure_model_loaded()
    headers = {"Content-Type": "application/json"}
    if LMSTUDIO_API_TOKEN:
        headers["Authorization"] = f"Bearer {LMSTUDIO_API_TOKEN}"

    messages: list[dict] = [{"role": "user", "content": prompt}]
    _fetch_cache: dict[str, str] = {}  # cache URL→resultado para evitar re-fetches

    for _ in range(LMSTUDIO_TOOL_ROUNDS):
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "tools": LMSTUDIO_TOOLS,
            "temperature": 0,
            "num_ctx": LMSTUDIO_CONTEXT_WINDOW,
            "max_tokens": MAX_TOKENS,
        }
        response = httpx.post(
            f"{LMSTUDIO_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=LMSTUDIO_REQUEST_TIMEOUT,
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            if "Context size" in detail or "context_length" in detail.lower():
                # Truncar tool results antiguos y reintentar UNA vez sin reabrir reintento exterior
                trimmed = _truncate_old_tool_results(messages)
                if trimmed:
                    log.warning("Contexto saturado; truncados %d tool results antiguos", trimmed)
                    payload["messages"] = messages
                    response = httpx.post(
                        f"{LMSTUDIO_BASE_URL}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=LMSTUDIO_REQUEST_TIMEOUT,
                    )
                    if response.status_code >= 400:
                        raise LMStudioContextOverflow(
                            f"LM Studio context overflow tras truncado: {response.text[:300]}"
                        )
                else:
                    raise LMStudioContextOverflow(
                        f"LM Studio context overflow sin posibilidad de truncar: {detail}"
                    )
            else:
                raise ValueError(
                    f"LM Studio devolvió {response.status_code}. Detalle: {detail}"
                )

        body = response.json()
        message = body["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        raw_content = message.get("content") or ""

        if not tool_calls and "<tool_call>" in raw_content:
            tool_calls = _parse_hermes_tool_calls(raw_content)
            log.info(
                "Hermes tool_call detectado en content (%d chars) → parseados %d calls",
                len(raw_content), len(tool_calls),
            )

        if not tool_calls:
            content = raw_content
            if content:
                return content
            # Qwen en modo razonamiento: añadir follow-up para forzar output
            messages.append({"role": "assistant", "content": "", "tool_calls": []})
            messages.append({
                "role": "user",
                "content": (
                    "Emite el JSON array con los eventos encontrados. "
                    "Si no hay eventos, emite exactamente: []"
                ),
            })
            continue

        messages.append({
            "role": "assistant",
            "content": _strip_thinking(message.get("content") or ""),
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            arguments = json.loads(tc["function"]["arguments"] or "{}")
            log.debug("  → tool: %s  args: %s", tool_name, str(arguments)[:120])
            # Evitar re-fetch de URLs ya visitadas en esta sesión
            cache_key = f"{tool_name}:{arguments.get('url', '')}" if tool_name == "fetch_url" else None
            if cache_key and cache_key in _fetch_cache:
                log.debug("  ← %s  [caché] %d chars", tool_name, len(_fetch_cache[cache_key]))
                result = _fetch_cache[cache_key]
            else:
                result = _execute_tool(tool_name, arguments)
                if cache_key:
                    _fetch_cache[cache_key] = result
            log.debug("  ← %s  resultado: %d chars", tool_name, len(result))
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": _compact_tool_result(tool_name, result),
            })

    # Rondas agotadas → forzar respuesta final sin herramientas
    log.warning(
        f"LM Studio agotó {LMSTUDIO_TOOL_ROUNDS} rondas de herramientas, "
        "forzando respuesta final..."
    )
    messages.append({
        "role": "user",
        "content": (
            "/nothink\n"
            "Has completado la recopilacion de informacion. "
            "Devuelve UNICAMENTE el JSON array con los eventos encontrados en el rango de fechas. "
            "Nada mas: ni razonamiento, ni markdown, solo el JSON array. "
            "Si no hay eventos en el rango, devuelve []."
        ),
    })
    final = httpx.post(
        f"{LMSTUDIO_BASE_URL}/chat/completions",
        headers=headers,
        json={
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0,
            "num_ctx": LMSTUDIO_CONTEXT_WINDOW,
            "max_tokens": MAX_TOKENS,
        },
        timeout=LMSTUDIO_REQUEST_TIMEOUT,
    )
    if final.status_code >= 400:
        raise ValueError(
            f"LM Studio devolvió {final.status_code} en respuesta forzada. "
            f"Detalle: {final.text[:300]}"
        )
    return final.json()["choices"][0]["message"].get("content") or ""


def _extract_native_text(body: dict) -> str:
    messages = [
        item.get("content", "")
        for item in body.get("output", [])
        if item.get("type") == "message"
    ]
    if messages:
        return messages[-1]
    raise ValueError("LM Studio no devolvió un bloque final de mensaje")


def run_lmstudio_prompt_native(prompt: str) -> str:
    """Modo native-mcp: usa el endpoint /api/v1/chat con integración MCP por petición."""
    ensure_mcp_server()
    ensure_model_loaded()

    headers = {"Content-Type": "application/json"}
    if LMSTUDIO_API_TOKEN:
        headers["Authorization"] = f"Bearer {LMSTUDIO_API_TOKEN}"

    payload = {
        "model": MODEL_NAME,
        "input": prompt,
        "temperature": 0,
        "context_length": LMSTUDIO_CONTEXT_WINDOW,
        "ttl": LMSTUDIO_MODEL_TTL,
        "store": False,
        "integrations": [
            {
                "type": "ephemeral_mcp",
                "server_label": "canarias-web-tools",
                "server_url": LMSTUDIO_WEB_MCP_URL,
                "allowed_tools": ["search_source_pages", "fetch_url"],
            }
        ],
    }

    response = httpx.post(
        f"{LMSTUDIO_BASE_URL.replace('/v1', '/api/v1')}/chat",
        headers=headers,
        json=payload,
        timeout=180,
    )
    if response.status_code >= 400:
        detail = response.text
        if "per-request MCP" in detail or "MCP" in detail:
            raise ValueError(
                "LM Studio rechazó MCP por petición. "
                "Activa 'Allow per-request MCPs' en Server Settings. "
                f"Detalle: {detail}"
            )
        response.raise_for_status()

    return _extract_native_text(response.json())


def run_lmstudio_prompt(prompt: str) -> str:
    """Punto de entrada: selecciona el modo según LMSTUDIO_API_MODE."""
    if LMSTUDIO_API_MODE in {"legacy-tools", "project-mcp"}:
        return run_lmstudio_prompt_legacy(prompt)

    try:
        return run_lmstudio_prompt_native(prompt)
    except Exception as exc:
        log.warning(f"LM Studio nativo con MCP falló, usando fallback legacy: {exc}")
        return run_lmstudio_prompt_legacy(prompt)
