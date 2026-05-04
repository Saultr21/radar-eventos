"""
scanner.py
Escanea las webs de asociaciones empresariales de Canarias,
detecta eventos próximos y envía notificación por email al equipo.
"""

import os
import json
import hashlib
import smtplib
import logging
import re
import time
import sys
import socket
import atexit
import subprocess
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

try:
    import anthropic
except ImportError:
    anthropic = None

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent


def resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def load_json_file(path: Path):
    """Carga JSON o JSONC (admite comentarios // y /* */)."""
    text = path.read_text(encoding="utf-8")
    # Elimina comentarios sin tocar strings: primero matchea strings (las conserva),
    # luego comentarios // y /* */ (los borra).
    text = re.sub(
        r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/',
        lambda m: m.group(0) if m.group(0).startswith('"') else "",
        text,
        flags=re.DOTALL,
    )
    return json.loads(text)


def load_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_optional_text_file(path: Path, fallback: str) -> str:
    if not path.exists():
        return fallback
    return load_text_file(path)


SETTINGS_FILE = resolve_path(os.environ.get("SETTINGS_FILE", "config/settings.json"))
SETTINGS = load_json_file(SETTINGS_FILE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# CONFIGURACIÓN — ajustar en GitHub Secrets
# ──────────────────────────────────────────────
LLM_PROVIDER      = os.environ.get("LLM_PROVIDER", SETTINGS.get("llm_provider", "anthropic")).lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("API_KEY")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL")
OPENROUTER_SITE_NAME = os.environ.get("OPENROUTER_SITE_NAME", "canarias-eventos")
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", SETTINGS.get("lmstudio_base_url", "http://localhost:1234/v1"))
LMSTUDIO_API_TOKEN = os.environ.get("LMSTUDIO_API_TOKEN")
LMSTUDIO_API_MODE = os.environ.get(
    "LMSTUDIO_API_MODE",
    SETTINGS.get("lmstudio_api_mode", "project-mcp"),
).lower()
LMSTUDIO_CONTEXT_WINDOW = int(
    os.environ.get("LMSTUDIO_CONTEXT_WINDOW", SETTINGS.get("lmstudio_context_window", 34096))
)
LMSTUDIO_WEB_MCP_PORT = int(
    os.environ.get("LMSTUDIO_WEB_MCP_PORT", SETTINGS.get("lmstudio_web_mcp_port", 8765))
)
LMSTUDIO_WEB_MCP_URL = os.environ.get(
    "LMSTUDIO_WEB_MCP_URL",
    f"http://127.0.0.1:{LMSTUDIO_WEB_MCP_PORT}/mcp",
)
LMSTUDIO_TOOL_ROUNDS = max(1, int(os.environ.get("LMSTUDIO_TOOL_ROUNDS", "6")))
LMSTUDIO_FETCH_CHARS = max(1000, int(os.environ.get("LMSTUDIO_FETCH_CHARS", SETTINGS.get("lmstudio_fetch_chars", 8000))))
LMSTUDIO_TOOL_RESULT_CHARS = max(1000, int(os.environ.get("LMSTUDIO_TOOL_RESULT_CHARS", "5000")))
EMAIL_FROM        = os.environ.get("EMAIL_FROM")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO          = os.environ.get("EMAIL_TO")
SMTP_HOST         = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT         = int(os.environ.get("SMTP_PORT", "587"))
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")
DAYS_AHEAD        = int(os.environ.get("DAYS_AHEAD", SETTINGS.get("days_ahead", 30)))
MAX_WORKERS       = max(1, int(os.environ.get("MAX_WORKERS", SETTINGS.get("max_workers", 6))))
RETRY_ATTEMPTS    = max(1, int(os.environ.get("RETRY_ATTEMPTS", SETTINGS.get("retry_attempts", 3))))
RETRY_BACKOFF     = max(1, int(os.environ.get("RETRY_BACKOFF", SETTINGS.get("retry_backoff", 2))))
CACHE_RETENTION_DAYS = max(
    DAYS_AHEAD,
    int(os.environ.get("CACHE_RETENTION_DAYS", SETTINGS.get("cache_retention_days", 90))),
)
MODEL_NAME        = os.environ.get("MODEL_NAME", SETTINGS.get("model", "claude-sonnet-4-20250514"))
MAX_TOKENS        = int(os.environ.get("MAX_TOKENS", SETTINGS.get("max_tokens", 1500)))
NOTIFICATION_CHANNEL = os.environ.get(
    "NOTIFICATION_CHANNEL",
    SETTINGS.get("notification_channel", "teams"),
).lower()
KNOWN_EVENTS_FILE = resolve_path(SETTINGS.get("known_events_file", "data/known_events.json"))
PROMPT_TEMPLATE   = load_text_file(resolve_path(SETTINGS["prompt_file"]))
LMSTUDIO_PROMPT_TEMPLATE = load_optional_text_file(
    resolve_path(SETTINGS.get("lmstudio_prompt_file", SETTINGS["prompt_file"])),
    PROMPT_TEMPLATE,
)
SOURCES           = load_json_file(resolve_path(SETTINGS["sources_file"]))
EMAIL_SUBJECT_TEMPLATE = load_text_file(resolve_path(SETTINGS["email_subject_file"]))
EMAIL_HTML_TEMPLATE = load_text_file(resolve_path(SETTINGS["email_html_file"]))
EMAIL_PLAIN_TEMPLATE = load_text_file(resolve_path(SETTINGS["email_plain_file"]))
TEAMS_TITLE_TEMPLATE = load_text_file(resolve_path(SETTINGS["teams_title_file"]))
TEAMS_BODY_TEMPLATE = load_text_file(resolve_path(SETTINGS["teams_body_file"]))
REPORT_HTML_TEMPLATE = load_text_file(resolve_path(SETTINGS["report_html_file"]))

_LMSTUDIO_MCP_PROCESS: subprocess.Popen | None = None
_LMSTUDIO_MCP_LOCK = threading.Lock()


def load_known_events(today: datetime) -> dict[str, dict[str, str | None]]:
    """Carga la caché de eventos manteniendo compatibilidad con el formato legacy."""
    if KNOWN_EVENTS_FILE.exists():
        raw = json.loads(KNOWN_EVENTS_FILE.read_text())
        if isinstance(raw, list):
            first_seen = today.strftime("%Y-%m-%d")
            return {
                event_id: {"event_date": None, "first_seen": first_seen}
                for event_id in raw
            }
        if isinstance(raw, dict):
            normalized: dict[str, dict[str, str | None]] = {}
            for cached_id, metadata in raw.items():
                if isinstance(metadata, dict):
                    normalized[cached_id] = {
                        "event_date": metadata.get("event_date"),
                        "first_seen": metadata.get("first_seen"),
                    }
                else:
                    normalized[cached_id] = {"event_date": None, "first_seen": None}
            return normalized
    return {}


def save_known_events(known: dict[str, dict[str, str | None]]) -> None:
    KNOWN_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    sorted_known = dict(sorted(known.items()))
    KNOWN_EVENTS_FILE.write_text(json.dumps(sorted_known, ensure_ascii=False, indent=2))


def event_id(event: dict) -> str:
    """Genera un ID único por evento basado en título + fecha + fuente."""
    raw = f"{event.get('title','')}{event.get('date','')}{event.get('association','')}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def event_start_date(event: dict) -> datetime | None:
    raw = (event.get("date") or "").strip()
    if not raw:
        return None

    first_part = raw.split("-")[0].strip()
    try:
        return datetime.strptime(first_part, "%d/%m/%Y")
    except ValueError:
        return None


def prune_known_events(
    known: dict[str, dict[str, str | None]],
    today: datetime,
) -> dict[str, dict[str, str | None]]:
    """Elimina entradas antiguas para evitar que la caché crezca indefinidamente."""
    cutoff = today - timedelta(days=CACHE_RETENTION_DAYS)
    pruned: dict[str, dict[str, str | None]] = {}

    for cached_id, metadata in known.items():
        date_str = metadata.get("event_date") or metadata.get("first_seen")
        if not date_str:
            pruned[cached_id] = metadata
            continue

        try:
            cached_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            pruned[cached_id] = metadata
            continue

        if cached_date >= cutoff:
            pruned[cached_id] = metadata

    removed = len(known) - len(pruned)
    if removed:
        log.info(f"Caché depurada: {removed} evento(s) antiguos eliminados")

    return pruned


def parse_events_response(text: str) -> list[dict]:
    cleaned = text.strip()
    # Eliminar bloques de razonamiento <think>...</think> de modelos como Qwen
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        # Si el modelo dice explícitamente que no hay eventos, tratar como lista vacía
        lower_cleaned = cleaned.lower()
        no_events_hints = (
            "no hay eventos", "no se encontraron", "no encontré", "no encontre",
            "no existen eventos", "no events found", "no upcoming events",
            "sin eventos", "no hay cursos", "no hay actividades",
            "no se han encontrado", "no tengo información", "no tengo informacion",
            "no hay información", "no hay informacion", "[]",
        )
        if any(hint in lower_cleaned for hint in no_events_hints) or cleaned in ("", "[]"):
            return []
        raise ValueError("La respuesta no contiene un JSON array")

    events = json.loads(cleaned[start : end + 1])
    if not isinstance(events, list):
        raise ValueError("La respuesta no contiene una lista de eventos")

    return events


def fetch_source_excerpt(url: str) -> str:
    """Obtiene un fragmento de texto de la URL para proveedores sin web search nativo."""
    response = httpx.get(
        url,
        timeout=20,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    )
    response.raise_for_status()
    text = response.text.replace("\r", " ").replace("\n", " ")
    return text[:20000]


LMSTUDIO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_source_pages",
            "description": "Busca paginas relevantes del mismo sitio web de la fuente y devuelve una lista JSON con title y url.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_url": {"type": "string", "description": "URL principal de la fuente"},
                    "query": {"type": "string", "description": "Consulta de búsqueda interna del sitio"},
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
            "description": "Descarga una URL y devuelve texto util junto con enlaces descubiertos del mismo sitio.",
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


def execute_lmstudio_tool(name: str, arguments: dict) -> str:
    ensure_lmstudio_web_mcp_server()
    return asyncio.run(call_local_web_mcp_tool(name, arguments))


def compact_lmstudio_tool_result(name: str, result: str) -> str:
    try:
        payload = json.loads(result)
    except Exception:
        return result[:LMSTUDIO_TOOL_RESULT_CHARS]

    if name == "fetch_url" and isinstance(payload, dict):
        compacted = {
            "requested_url": payload.get("requested_url"),
            "final_url": payload.get("final_url"),
            "status": payload.get("status"),
            "status_code": payload.get("status_code"),
            "error": payload.get("error"),
            "content": (payload.get("content") or "")[:LMSTUDIO_TOOL_RESULT_CHARS],
            "discovered_links": (payload.get("discovered_links") or [])[:8],
        }
        return json.dumps(compacted, ensure_ascii=False)

    if name == "search_source_pages" and isinstance(payload, dict):
        compacted = {
            "source_url": payload.get("source_url"),
            "query": payload.get("query"),
            "results": (payload.get("results") or [])[:6],
        }
        return json.dumps(compacted, ensure_ascii=False)

    compacted_text = json.dumps(payload, ensure_ascii=False)
    return compacted_text[:LMSTUDIO_TOOL_RESULT_CHARS]


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def stop_lmstudio_web_mcp_server() -> None:
    global _LMSTUDIO_MCP_PROCESS
    if _LMSTUDIO_MCP_PROCESS and _LMSTUDIO_MCP_PROCESS.poll() is None:
        _LMSTUDIO_MCP_PROCESS.terminate()
    _LMSTUDIO_MCP_PROCESS = None


atexit.register(stop_lmstudio_web_mcp_server)


def ensure_lmstudio_web_mcp_server() -> None:
    global _LMSTUDIO_MCP_PROCESS

    # Comprobación rápida sin lock para el caso común (ya activo)
    if is_port_open("127.0.0.1", LMSTUDIO_WEB_MCP_PORT):
        return

    with _LMSTUDIO_MCP_LOCK:
        # Re-comprobar dentro del lock por si otro hilo lo inició mientras esperábamos
        if is_port_open("127.0.0.1", LMSTUDIO_WEB_MCP_PORT):
            return

        server_path = resolve_path("src/lmstudio_web_mcp_server.py")
        env = os.environ.copy()
        env["LMSTUDIO_WEB_MCP_PORT"] = str(LMSTUDIO_WEB_MCP_PORT)

        _LMSTUDIO_MCP_PROCESS = subprocess.Popen(
            [sys.executable, str(server_path)],
            cwd=str(ROOT_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(40):
            if is_port_open("127.0.0.1", LMSTUDIO_WEB_MCP_PORT):
                return
            if _LMSTUDIO_MCP_PROCESS.poll() is not None:
                break
            time.sleep(0.25)

        raise RuntimeError("No se pudo iniciar el servidor MCP web local para LM Studio")


async def call_local_web_mcp_tool(name: str, arguments: dict) -> str:
    async with streamable_http_client(LMSTUDIO_WEB_MCP_URL) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments=arguments)

            if result.isError:
                error_text = []
                for content in result.content:
                    text = getattr(content, "text", None)
                    if text:
                        error_text.append(text)
                raise RuntimeError(" ".join(error_text) or f"Error llamando a la tool MCP {name}")

            if result.structuredContent is not None:
                return json.dumps(result.structuredContent, ensure_ascii=False)

            text_parts = []
            for content in result.content:
                text = getattr(content, "text", None)
                if text:
                    text_parts.append(text)
            return "\n".join(text_parts)


def run_anthropic_prompt(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise ValueError("Falta ANTHROPIC_API_KEY para usar el proveedor anthropic")
    if anthropic is None:
        raise ValueError("El paquete anthropic no está instalado")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
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


def run_openrouter_prompt(prompt: str, source_url: str) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("Falta OPENROUTER_API_KEY o API_KEY para usar el proveedor openrouter")

    source_excerpt = fetch_source_excerpt(source_url)
    provider_prompt = (
        f"{prompt}\n\n"
        "CONTEXTO ADICIONAL: A continuacion tienes el contenido HTML/textual obtenido"
        f" directamente desde {source_url}. Basate prioritariamente en este contenido.\n\n"
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
    body = response.json()
    return body["choices"][0]["message"]["content"]


def extract_native_lmstudio_text(body: dict) -> str:
    messages = [item.get("content", "") for item in body.get("output", []) if item.get("type") == "message"]
    if messages:
        return messages[-1]
    raise ValueError("LM Studio no devolvió un bloque final de mensaje")


def run_lmstudio_prompt_legacy(prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if LMSTUDIO_API_TOKEN:
        headers["Authorization"] = f"Bearer {LMSTUDIO_API_TOKEN}"

    messages: list[dict] = [{"role": "user", "content": prompt}]

    for _ in range(LMSTUDIO_TOOL_ROUNDS):
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "tools": LMSTUDIO_TOOLS,
            "temperature": 0,
            "num_ctx": LMSTUDIO_CONTEXT_WINDOW,
        }
        response = httpx.post(
            f"{LMSTUDIO_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise ValueError(f"LM Studio devolvio {response.status_code} en chat/completions. Detalle: {detail}")
        response.raise_for_status()
        body = response.json()
        message = body["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            content = message.get("content") or ""
            if content:
                return content
            # Qwen en modo razonamiento devolvió solo <think> sin texto final
            # Añadir follow-up para forzar output en la siguiente ronda
            messages.append({"role": "assistant", "content": "", "tool_calls": []})
            messages.append({
                "role": "user",
                "content": "Emite el JSON array con los eventos encontrados. Si no hay eventos, emite exactamente: []",
            })
            continue

        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )

        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"] or "{}")
            result = execute_lmstudio_tool(tool_name, arguments)
            compact_result = compact_lmstudio_tool_result(tool_name, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": compact_result,
                }
            )

    # Rondas agotadas: forzar una respuesta final sin herramientas disponibles
    log.warning(
        f"LM Studio agotó {LMSTUDIO_TOOL_ROUNDS} rondas de herramientas, forzando respuesta final..."
    )
    messages.append(
        {
            "role": "user",
            "content": (
                "/nothink\n"
                "Has completado la recopilacion de informacion. "
                "Devuelve UNICAMENTE el JSON array con los eventos encontrados en el rango de fechas. "
                "Nada mas: ni razonamiento, ni markdown, solo el JSON array. "
                "Si no hay eventos en el rango, devuelve []."
            ),
        }
    )
    final_payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0,
        "num_ctx": LMSTUDIO_CONTEXT_WINDOW,
    }
    final_response = httpx.post(
        f"{LMSTUDIO_BASE_URL}/chat/completions",
        headers=headers,
        json=final_payload,
        timeout=120,
    )
    if final_response.status_code >= 400:
        raise ValueError(
            f"LM Studio devolvió {final_response.status_code} en respuesta forzada. "
            f"Detalle: {final_response.text[:300]}"
        )
    final_body = final_response.json()
    return final_body["choices"][0]["message"].get("content") or ""


def run_lmstudio_prompt_native(prompt: str) -> str:
    ensure_lmstudio_web_mcp_server()

    headers = {"Content-Type": "application/json"}
    if LMSTUDIO_API_TOKEN:
        headers["Authorization"] = f"Bearer {LMSTUDIO_API_TOKEN}"

    payload = {
        "model": MODEL_NAME,
        "input": prompt,
        "temperature": 0,
        "context_length": LMSTUDIO_CONTEXT_WINDOW,
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
                "LM Studio rechazó MCP por petición. Activa 'Allow per-request MCPs' en Server Settings. "
                f"Detalle: {detail}"
            )
        response.raise_for_status()

    return extract_native_lmstudio_text(response.json())


def run_lmstudio_prompt(prompt: str) -> str:
    if LMSTUDIO_API_MODE in {"legacy-tools", "project-mcp"}:
        return run_lmstudio_prompt_legacy(prompt)

    try:
        return run_lmstudio_prompt_native(prompt)
    except Exception as exc:
        log.warning(f"LM Studio nativo con MCP falló, se usa fallback legacy: {exc}")
        return run_lmstudio_prompt_legacy(prompt)


def scan_source(source: dict, days_ahead: int) -> list[dict]:
    """
    Llama a Claude con web_search para escanear una fuente y extraer eventos.
    Devuelve lista de dicts con campos normalizados.
    """
    today = datetime.now()
    horizon = (today + timedelta(days=days_ahead)).strftime("%d/%m/%Y")
    today_str = today.strftime("%d/%m/%Y")

    prompt_template = LMSTUDIO_PROMPT_TEMPLATE if LLM_PROVIDER == "lmstudio" else PROMPT_TEMPLATE

    prompt = prompt_template.format(
        source_name=source["name"],
        source_url=source["url"],
        source_category=source["cat"],
        today_str=today_str,
        horizon=horizon,
    )

    if LLM_PROVIDER == "lmstudio":
        prompt += (
            "\n\nRecuerda: devuelve SOLO el JSON array al final, sin markdown ni texto adicional."
        )

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            if LLM_PROVIDER == "anthropic":
                text = run_anthropic_prompt(prompt)
            elif LLM_PROVIDER == "openrouter":
                text = run_openrouter_prompt(prompt, source["url"])
            elif LLM_PROVIDER == "lmstudio":
                text = run_lmstudio_prompt(prompt)
            else:
                raise ValueError(f"Proveedor LLM no soportado: {LLM_PROVIDER}")

            return parse_events_response(text)

        except Exception as exc:
            if attempt == RETRY_ATTEMPTS:
                log.warning(f"Error escaneando {source['name']}: {exc}")
                return []

            wait_seconds = RETRY_BACKOFF ** (attempt - 1)
            log.warning(
                f"Reintento {attempt}/{RETRY_ATTEMPTS - 1} para {source['name']} tras error: {exc}"
            )
            time.sleep(wait_seconds)

    return []


def build_grouped_events(new_events: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for ev in new_events:
        src = ev.get("association", "Otros")
        grouped.setdefault(src, []).append(ev)
    return grouped


def build_email_rows(new_events: list[dict]) -> str:
    """Genera las filas HTML de la tabla del email."""

    cat_colors = {
        "formacion":  ("#EAF3DE", "#27500A"),
        "networking": ("#FAEEDA", "#633806"),
        "jornada":    ("#EEEDFE", "#3C3489"),
        "feria":      ("#E6F1FB", "#0C447C"),
        "mision":     ("#F1EFE8", "#444441"),
        "evento":     ("#E6F1FB", "#0C447C"),
        "otro":       ("#F1EFE8", "#444441"),
    }

    def badge(etype: str) -> str:
        bg, color = cat_colors.get(etype, ("#F1EFE8", "#444441"))
        return (
            f'<span style="background:{bg};color:{color};font-size:11px;'
            f'padding:2px 8px;border-radius:20px;font-weight:500">{etype}</span>'
        )

    rows = ""
    for src, events in sorted(build_grouped_events(new_events).items()):
        rows += f"""
        <tr>
          <td colspan="5" style="padding:16px 0 6px;font-size:13px;font-weight:600;
              color:#1a1a1a;border-bottom:1px solid #e5e5e5">
            {src}
          </td>
        </tr>"""
        for ev in events:
            url = ev.get("url", "#")
            title_link = (
                f'<a href="{url}" style="color:#185FA5;text-decoration:none;font-weight:500">'
                f'{ev.get("title","Sin título")}</a>'
                if url and url != "#"
                else f'<strong>{ev.get("title","Sin título")}</strong>'
            )
            deadline = ev.get("deadline", "")
            deadline_cell = (
                f'<span style="color:#854F0B;font-size:11px">⏰ Inscr. hasta {deadline}</span>'
                if deadline
                else ""
            )
            rows += f"""
        <tr style="border-bottom:0.5px solid #f0f0f0">
          <td style="padding:10px 12px 10px 0;font-size:13px;vertical-align:top;width:90px;
              color:#555;white-space:nowrap">{ev.get("date","—")}</td>
          <td style="padding:10px 12px 10px 0;vertical-align:top">
            {title_link}
            <div style="font-size:12px;color:#666;margin-top:3px">{ev.get("description","")}</div>
            {f'<div style="margin-top:3px">{deadline_cell}</div>' if deadline_cell else ""}
          </td>
          <td style="padding:10px 8px;vertical-align:top;white-space:nowrap">
            {badge(ev.get("type","otro"))}
          </td>
          <td style="padding:10px 8px;font-size:12px;color:#555;vertical-align:top;
              white-space:nowrap">📍 {ev.get("location","—")}</td>
          <td style="padding:10px 0;font-size:12px;color:#555;vertical-align:top;
              white-space:nowrap">{ev.get("price","—")}</td>
        </tr>"""

        return rows


def build_email_html(new_events: list[dict], scan_date: str, days_ahead: int) -> str:
    """Construye el HTML del email de notificación."""
    rows = build_email_rows(new_events)
    rows_html = rows or (
    '<tr><td style="padding:24px 0;text-align:center;color:#888;font-size:14px">'
    'No se han detectado eventos nuevos esta semana.</td></tr>'
    )

    total = len(new_events)
    sources_count = len(build_grouped_events(new_events))

    return EMAIL_HTML_TEMPLATE.format(
    scan_date=scan_date,
    days_ahead=days_ahead,
    total_events=total,
    sources_count=sources_count,
    rows_html=rows_html,
    )


def build_plain_event_lines(new_events: list[dict]) -> str:
    lines: list[str] = []
    for ev in new_events:
        lines.extend([
            f"[{ev.get('type', '').upper()}] {ev.get('title', '')}",
            f"  Fecha:      {ev.get('date', '—')}",
            f"  Lugar:      {ev.get('location', '—')}",
            f"  Precio:     {ev.get('price', '—')}",
            f"  Organiza:   {ev.get('association', '—')}",
            f"  Descripcion:{ev.get('description', '')}",
            f"  Link:       {ev.get('url', '—')}",
            "",
        ])
    return "\n".join(lines).strip()


def build_teams_event_lines(new_events: list[dict]) -> str:
    lines = []
    for ev in new_events:
        line = (
            f"- {ev.get('date', '—')} | {ev.get('association', '—')} | {ev.get('title', 'Sin titulo')}"
            f" | {ev.get('location', '—')} | {ev.get('price', '—')}"
        )
        url = ev.get("url")
        if url:
            line = f"{line}\n  {url}"
        lines.append(line)
    return "\n\n".join(lines)


def build_email_plain(new_events: list[dict], scan_date: str) -> str:
    """Versión texto plano del email (fallback)."""
    return EMAIL_PLAIN_TEMPLATE.format(
        scan_date=scan_date,
        total_events=len(new_events),
        sources_count=len(build_grouped_events(new_events)),
        days_ahead=DAYS_AHEAD,
        event_lines=build_plain_event_lines(new_events),
    )


def build_email_subject(total_events: int, today: datetime) -> str:
    return EMAIL_SUBJECT_TEMPLATE.format(
        total_events=total_events,
        today_date=today.strftime("%d/%m/%Y"),
    ).strip()


def build_teams_payload(new_events: list[dict], scan_date: str) -> dict:
    title = TEAMS_TITLE_TEMPLATE.format(total_events=len(new_events)).strip()
    body = TEAMS_BODY_TEMPLATE.format(
        scan_date=scan_date,
        total_events=len(new_events),
        sources_count=len(build_grouped_events(new_events)),
        days_ahead=DAYS_AHEAD,
        event_lines=build_teams_event_lines(new_events),
    ).strip()
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": title,
        "themeColor": "185FA5",
        "title": title,
        "text": body,
    }


def send_email(subject: str, html_body: str, plain_body: str) -> None:
    """Envía el email a todos los destinatarios configurados."""
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        raise ValueError("Faltan variables de email: EMAIL_FROM, EMAIL_PASSWORD o EMAIL_TO")

    recipients = [r.strip() for r in EMAIL_TO.split(",") if r.strip()]
    if not recipients:
        log.warning("No hay destinatarios configurados en EMAIL_TO")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body,  "html",  "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())

    log.info(f"Email enviado a {len(recipients)} destinatario(s)")


def send_teams(payload: dict) -> None:
    """Envía la notificación al webhook de Teams."""
    if not TEAMS_WEBHOOK_URL:
        raise ValueError("Falta la variable TEAMS_WEBHOOK_URL")

    response = httpx.post(TEAMS_WEBHOOK_URL, json=payload, timeout=20)
    response.raise_for_status()
    log.info("Notificación enviada a Teams")


def send_notification(new_events: list[dict], scan_date: str, today: datetime) -> None:
    if NOTIFICATION_CHANNEL == "teams":
        send_teams(build_teams_payload(new_events, scan_date))
        return

    if NOTIFICATION_CHANNEL == "email":
        subject = build_email_subject(len(new_events), today)
        html = build_email_html(new_events, scan_date, DAYS_AHEAD)
        plain = build_email_plain(new_events, scan_date)
        send_email(subject, html, plain)
        return

    raise ValueError(f"Canal de notificación no soportado: {NOTIFICATION_CHANNEL}")


def save_events_report(all_events: list[dict], scan_date: str, days_ahead: int) -> None:
    """Guarda todos los eventos encontrados en un TXT legible en reports/."""
    reports_dir = ROOT_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "latest_new_events.txt"

    today_label = datetime.now().strftime("%d/%m/%Y %H:%M")
    horizon_label = (datetime.now() + timedelta(days=days_ahead)).strftime("%d/%m/%Y")

    lines: list[str] = [
        "=" * 60,
        "  RADAR DE EVENTOS EMPRESARIALES — CANARIAS",
        "=" * 60,
        f"  Escaneo:       {scan_date}",
        f"  Ventana:       hasta el {horizon_label} ({days_ahead} días)",
        f"  Total eventos: {len(all_events)}",
        "=" * 60,
        "",
    ]

    if not all_events:
        lines.append("  Sin eventos nuevos en este escaneo.")
    else:
        grouped: dict[str, list[dict]] = {}
        for ev in all_events:
            src = ev.get("association", "Otros")
            grouped.setdefault(src, []).append(ev)

        for source_name, events in sorted(grouped.items()):
            lines += [
                "",
                "─" * 60,
                f"  {source_name.upper()}  ({len(events)} evento(s))",
                "─" * 60,
            ]
            for ev in events:
                lines += [
                    "",
                    f"  Título:       {ev.get('title', '—')}",
                    f"  Tipo:         {ev.get('type', '—')}",
                    f"  Fecha:        {ev.get('date', '—')}",
                    f"  Hora:         {ev.get('time', '—')}",
                    f"  Lugar:        {ev.get('location', '—')}",
                    f"  Descripción:  {ev.get('description', '—')}",
                    f"  URL:          {ev.get('url', '—')}",
                    f"  Precio:       {ev.get('price', '—')}",
                    f"  Inscripción:  {ev.get('deadline', '—')}",
                    f"  Asociación:   {ev.get('association', '—')}",
                    f"  Categoría:    {ev.get('category', '—')}",
                ]

    lines += ["", "=" * 60]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Informe guardado en: {report_path}")


def save_events_html_report(all_events: list[dict], scan_date: str, days_ahead: int) -> None:
    """Genera reports/latest_new_events.html usando config/templates/report_html.html."""
    import json as _json

    reports_dir = ROOT_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "latest_new_events.html"

    horizon_label = (datetime.now() + timedelta(days=days_ahead)).strftime("%d/%m/%Y")

    clean = []
    for ev in all_events:
        clean.append({
            "title":       ev.get("title") or "Sin título",
            "type":        ev.get("type") or "otro",
            "date":        ev.get("date") or "—",
            "time":        ev.get("time") or "",
            "location":    ev.get("location") or "—",
            "description": ev.get("description") or "",
            "url":         ev.get("url") or "",
            "price":       ev.get("price") or "—",
            "deadline":    ev.get("deadline") or "",
            "association": ev.get("association") or "Otros",
            "category":    ev.get("category") or "—",
        })

    html = (
        REPORT_HTML_TEMPLATE
        .replace("__EVENTS_JSON__",   _json.dumps(clean, ensure_ascii=False))
        .replace("__SCAN_DATE__",     scan_date)
        .replace("__HORIZON_LABEL__", horizon_label)
        .replace("__DAYS_AHEAD__",    str(days_ahead))
    )

    report_path.write_text(html, encoding="utf-8")
    log.info(f"Informe HTML guardado en: {report_path}")


def main() -> None:
    log.info("=== Iniciando escaneo de eventos empresariales de Canarias ===")

    if LLM_PROVIDER == "lmstudio" and LMSTUDIO_API_MODE == "native-mcp":
        ensure_lmstudio_web_mcp_server()

    today  = datetime.now()
    known  = prune_known_events(load_known_events(today), today)
    scan_date = today.strftime("%d/%m/%Y %H:%M")

    all_new_events: list[dict] = []
    scanned_sources = 0

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(SOURCES))) as executor:
        future_to_source = {
            executor.submit(scan_source, source, DAYS_AHEAD): source
            for source in SOURCES
        }

        for future in as_completed(future_to_source):
            source = future_to_source[future]
            scanned_sources += 1

            try:
                events = future.result()
            except Exception as exc:
                log.warning(f"Error no controlado escaneando {source['name']}: {exc}")
                continue

            log.info(
                f"Escaneando: {source['name']} ({scanned_sources}/{len(SOURCES)})"
            )
            log.info(f"  → {len(events)} eventos encontrados")

            for ev in events:
                eid = event_id(ev)
                if eid not in known:
                    event_date = event_start_date(ev)
                    known[eid] = {
                        "event_date": event_date.strftime("%Y-%m-%d") if event_date else None,
                        "first_seen": today.strftime("%Y-%m-%d"),
                    }
                    ev["_id"] = eid
                    all_new_events.append(ev)

    log.info(f"Total eventos nuevos: {len(all_new_events)}")

    # Ordenar por fecha
    def parse_date(ev):
        raw = ev.get("date", "")
        try:
            part = raw.split("-")[0].strip()
            return datetime.strptime(part, "%d/%m/%Y")
        except Exception:
            return datetime(2099, 12, 31)

    all_new_events.sort(key=parse_date)

    # Guardar caché actualizada
    save_known_events(known)

    # Guardar informes siempre (tenga o no eventos nuevos)
    save_events_report(all_new_events, scan_date, DAYS_AHEAD)
    save_events_html_report(all_new_events, scan_date, DAYS_AHEAD)

    # Enviar notificación si hay eventos nuevos
    if all_new_events:
        send_notification(all_new_events, scan_date, today)
    else:
        log.info("Sin eventos nuevos — no se envía notificación")

    log.info("=== Escaneo completado ===")


if __name__ == "__main__":
    main()
