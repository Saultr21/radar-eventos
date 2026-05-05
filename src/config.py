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

# ── Proveedor LLM ─────────────────────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", SETTINGS.get("llm_provider", "anthropic")).lower()

# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ── OpenRouter ────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("API_KEY")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL")
OPENROUTER_SITE_NAME = os.environ.get("OPENROUTER_SITE_NAME", "canarias-eventos")

# ── LM Studio ─────────────────────────────────────────────────────────────────
LMSTUDIO_BASE_URL = os.environ.get(
    "LMSTUDIO_BASE_URL", SETTINGS.get("lmstudio_base_url", "http://localhost:1234/v1")
)
LMSTUDIO_API_TOKEN = os.environ.get("LMSTUDIO_API_TOKEN")
LMSTUDIO_API_MODE = os.environ.get(
    "LMSTUDIO_API_MODE", SETTINGS.get("lmstudio_api_mode", "project-mcp")
).lower()
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

# ── Extractor (modo structured) ───────────────────────────────────────────────
EXTRACTOR_MODE = os.environ.get(
    "EXTRACTOR_MODE", SETTINGS.get("extractor_mode", "structured")
).lower()
EXTRACTOR_MAX_SUBPAGES = max(
    0,
    int(os.environ.get("EXTRACTOR_MAX_SUBPAGES", SETTINGS.get("extractor_max_subpages", 3))),
)
EXTRACTOR_PER_PAGE_CHARS = max(
    1000,
    int(os.environ.get("EXTRACTOR_PER_PAGE_CHARS", SETTINGS.get("extractor_per_page_chars", 6000))),
)
LMSTUDIO_REQUEST_TIMEOUT = int(os.environ.get("LMSTUDIO_REQUEST_TIMEOUT", SETTINGS.get("lmstudio_request_timeout", 300)))
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
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

# ── Teams ─────────────────────────────────────────────────────────────────────
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")

# ── Escaneo ───────────────────────────────────────────────────────────────────
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", SETTINGS.get("days_ahead", 30)))
MAX_WORKERS = max(1, int(os.environ.get("MAX_WORKERS", SETTINGS.get("max_workers", 6))))
RETRY_ATTEMPTS = max(1, int(os.environ.get("RETRY_ATTEMPTS", SETTINGS.get("retry_attempts", 3))))
RETRY_BACKOFF = max(1, int(os.environ.get("RETRY_BACKOFF", SETTINGS.get("retry_backoff", 2))))
CACHE_RETENTION_DAYS = max(
    DAYS_AHEAD,
    int(os.environ.get("CACHE_RETENTION_DAYS", SETTINGS.get("cache_retention_days", 90))),
)
MODEL_NAME = os.environ.get("MODEL_NAME", SETTINGS.get("model", "claude-sonnet-4-20250514"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", SETTINGS.get("max_tokens", 1500)))
NOTIFICATION_CHANNEL = os.environ.get(
    "NOTIFICATION_CHANNEL", SETTINGS.get("notification_channel", "teams")
).lower()

# ── Rutas ─────────────────────────────────────────────────────────────────────
KNOWN_EVENTS_FILE = resolve_path(SETTINGS.get("known_events_file", "data/known_events.json"))
SOURCES = load_json_file(resolve_path(SETTINGS["sources_file"]))

# ── Plantillas ────────────────────────────────────────────────────────────────
_prompt_path = resolve_path(SETTINGS["prompt_file"])
PROMPT_TEMPLATE = load_text_file(_prompt_path) if _prompt_path.exists() else ""
_lmstudio_prompt_path = resolve_path(SETTINGS.get("lmstudio_prompt_file", SETTINGS["prompt_file"]))
LMSTUDIO_PROMPT_TEMPLATE = load_optional_text_file(_lmstudio_prompt_path, PROMPT_TEMPLATE)
if LLM_PROVIDER != "lmstudio" and not PROMPT_TEMPLATE:
    raise FileNotFoundError(
        f"prompt_file requerido para provider '{LLM_PROVIDER}': {_prompt_path}"
    )
EMAIL_SUBJECT_TEMPLATE = load_text_file(resolve_path(SETTINGS["email_subject_file"]))
EMAIL_HTML_TEMPLATE = load_text_file(resolve_path(SETTINGS["email_html_file"]))
EMAIL_PLAIN_TEMPLATE = load_text_file(resolve_path(SETTINGS["email_plain_file"]))
TEAMS_TITLE_TEMPLATE = load_text_file(resolve_path(SETTINGS["teams_title_file"]))
TEAMS_BODY_TEMPLATE = load_text_file(resolve_path(SETTINGS["teams_body_file"]))
REPORT_HTML_TEMPLATE = load_text_file(resolve_path(SETTINGS["report_html_file"]))
