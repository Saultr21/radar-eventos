"""
config.py
Carga y expone todos los ajustes del proyecto desde settings.json y variables de entorno.
"""
import os
import json
import re
from pathlib import Path

from dotenv import load_dotenv

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

# ── Modelo ────────────────────────────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", SETTINGS.get("llm_provider", "lmstudio"))
MODEL_NAME = os.environ.get("MODEL_NAME", SETTINGS.get("model", "qwen3.5-9b"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", SETTINGS.get("max_tokens", 8000)))
# ── LM Studio ─────────────────────────────────────────────────────────────────
LMSTUDIO_BASE_URL = os.environ.get(
    "LMSTUDIO_BASE_URL", SETTINGS.get("lmstudio_base_url", "http://localhost:1234/v1")
)
LMSTUDIO_API_TOKEN = os.environ.get("LMSTUDIO_API_TOKEN")
LMSTUDIO_API_MODE = os.environ.get("LMSTUDIO_API_MODE", SETTINGS.get("lmstudio_api_mode", "project-mcp"))
LMSTUDIO_CONTEXT_WINDOW = int(
    os.environ.get("LMSTUDIO_CONTEXT_WINDOW", SETTINGS.get("lmstudio_context_window", 34096))
)
LMSTUDIO_MODEL_TTL = int(
    os.environ.get("LMSTUDIO_MODEL_TTL", SETTINGS.get("lmstudio_model_ttl", 7200))
)
LMSTUDIO_WEB_MCP_PORT = int(
    os.environ.get("LMSTUDIO_WEB_MCP_PORT", SETTINGS.get("lmstudio_web_mcp_port", 8765))
)
LMSTUDIO_WEB_MCP_URL = os.environ.get(
    "LMSTUDIO_WEB_MCP_URL",
    f"http://127.0.0.1:{LMSTUDIO_WEB_MCP_PORT}/mcp",
)
LMSTUDIO_TOOL_ROUNDS = max(
    1,
    int(os.environ.get("LMSTUDIO_TOOL_ROUNDS", str(SETTINGS.get("lmstudio_tool_rounds", 6)))),
)

# ── Extractor ────────────────────────────────────────────────────────────────
EXTRACTOR_MAX_SUBPAGES = max(
    0,
    int(os.environ.get("EXTRACTOR_MAX_SUBPAGES", SETTINGS.get("extractor_max_subpages", 3))),
)
EXTRACTOR_PER_PAGE_CHARS = max(
    1000,
    int(os.environ.get("EXTRACTOR_PER_PAGE_CHARS", SETTINGS.get("extractor_per_page_chars", 6000))),
)
LMSTUDIO_REQUEST_TIMEOUT = int(os.environ.get("LMSTUDIO_REQUEST_TIMEOUT", SETTINGS.get("lmstudio_request_timeout", 300)))
# Peticiones concurrentes permitidas contra LM Studio. 0 = sin límite (lo limita
# MAX_WORKERS). LM Studio moderno hace batching; serializar todo es lo que hacía
# que el escaneo fuera lento.
LMSTUDIO_PARALLEL_CALLS = max(
    0,
    int(os.environ.get("LMSTUDIO_PARALLEL_CALLS", SETTINGS.get("lmstudio_parallel_calls", 0))),
)
LMSTUDIO_FETCH_CHARS = max(
    1000, int(os.environ.get("LMSTUDIO_FETCH_CHARS", SETTINGS.get("lmstudio_fetch_chars", 4000)))
)
LMSTUDIO_TOOL_RESULT_CHARS = max(
    1000,
    int(os.environ.get(
        "LMSTUDIO_TOOL_RESULT_CHARS",
        str(SETTINGS.get("lmstudio_tool_result_chars", LMSTUDIO_FETCH_CHARS)),
    )),
)

# ── Email ─────────────────────────────────────────────────────────────────────
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_TO   = os.environ.get("EMAIL_TO")

# ── Azure / Graph API ─────────────────────────────────────────────────────────
AZURE_TENANT_ID     = os.environ.get("AZURE_TENANT_ID")
AZURE_CLIENT_ID     = os.environ.get("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET")

# ── Escaneo ───────────────────────────────────────────────────────────────────
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", SETTINGS.get("days_ahead", 30)))
MAX_WORKERS = max(1, int(os.environ.get("MAX_WORKERS", SETTINGS.get("max_workers", 6))))
RETRY_ATTEMPTS = max(1, int(os.environ.get("RETRY_ATTEMPTS", SETTINGS.get("retry_attempts", 3))))
RETRY_BACKOFF = max(1, int(os.environ.get("RETRY_BACKOFF", SETTINGS.get("retry_backoff", 2))))
NOTIFICATION_CHANNEL = os.environ.get(
    "NOTIFICATION_CHANNEL", SETTINGS.get("notification_channel", "none")
).lower()

# ── Rutas ─────────────────────────────────────────────────────────────────────
SOURCES = load_json_file(resolve_path(SETTINGS["sources_file"]))

# ── Plantillas ────────────────────────────────────────────────────────────────
EXTRACTOR_SYSTEM_PROMPT = load_text_file(resolve_path(SETTINGS["extractor_system_prompt_file"]))
EXTRACTOR_USER_PROMPT   = load_text_file(resolve_path(SETTINGS["extractor_user_prompt_file"]))
EMAIL_SUBJECT_TEMPLATE  = load_text_file(resolve_path(SETTINGS["email_subject_file"]))
EMAIL_HTML_TEMPLATE     = load_text_file(resolve_path(SETTINGS["email_html_file"]))
REPORT_HTML_TEMPLATE    = load_text_file(resolve_path(SETTINGS["report_html_file"]))
