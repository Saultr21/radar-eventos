"""
config.py
Carga y expone todos los ajustes del proyecto desde variables de entorno (.env).
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


# ── Modelo / LLM ──────────────────────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "lmstudio")
MODEL_NAME   = os.environ.get("MODEL_NAME", "qwen3.5-9b")
MAX_TOKENS   = int(os.environ.get("MAX_TOKENS", "8000"))

# ── LM Studio ─────────────────────────────────────────────────────────────────
LMSTUDIO_BASE_URL        = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
LMSTUDIO_API_TOKEN       = os.environ.get("LMSTUDIO_API_TOKEN")
LMSTUDIO_API_MODE        = os.environ.get("LMSTUDIO_API_MODE", "project-mcp")
LMSTUDIO_CONTEXT_WINDOW  = int(os.environ.get("LMSTUDIO_CONTEXT_WINDOW", "34096"))
LMSTUDIO_MODEL_TTL       = int(os.environ.get("LMSTUDIO_MODEL_TTL", "7200"))
LMSTUDIO_WEB_MCP_PORT    = int(os.environ.get("LMSTUDIO_WEB_MCP_PORT", "8765"))
LMSTUDIO_WEB_MCP_URL     = os.environ.get("LMSTUDIO_WEB_MCP_URL", f"http://127.0.0.1:{LMSTUDIO_WEB_MCP_PORT}/mcp")
LMSTUDIO_TOOL_ROUNDS     = max(1, int(os.environ.get("LMSTUDIO_TOOL_ROUNDS", "6")))
LMSTUDIO_REQUEST_TIMEOUT = int(os.environ.get("LMSTUDIO_REQUEST_TIMEOUT", "300"))
LMSTUDIO_PARALLEL_CALLS  = max(0, int(os.environ.get("LMSTUDIO_PARALLEL_CALLS", "1")))
LMSTUDIO_FETCH_CHARS     = max(1000, int(os.environ.get("LMSTUDIO_FETCH_CHARS", "12000")))
LMSTUDIO_TOOL_RESULT_CHARS = max(1000, int(os.environ.get("LMSTUDIO_TOOL_RESULT_CHARS", str(LMSTUDIO_FETCH_CHARS))))

# ── Extractor ────────────────────────────────────────────────────────────────
EXTRACTOR_MAX_SUBPAGES   = max(0, int(os.environ.get("EXTRACTOR_MAX_SUBPAGES", "3")))
EXTRACTOR_PER_PAGE_CHARS = max(1000, int(os.environ.get("EXTRACTOR_PER_PAGE_CHARS", "6000")))

# ── Email ─────────────────────────────────────────────────────────────────────
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_TO   = os.environ.get("EMAIL_TO")

# ── Azure / Graph API ─────────────────────────────────────────────────────────
AZURE_TENANT_ID     = os.environ.get("AZURE_TENANT_ID")
AZURE_CLIENT_ID     = os.environ.get("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET")

# ── Escaneo ───────────────────────────────────────────────────────────────────
DAYS_AHEAD           = int(os.environ.get("DAYS_AHEAD", "30"))
MAX_WORKERS          = max(1, int(os.environ.get("MAX_WORKERS", "8")))
RETRY_ATTEMPTS       = max(1, int(os.environ.get("RETRY_ATTEMPTS", "3")))
RETRY_BACKOFF        = max(1, int(os.environ.get("RETRY_BACKOFF", "2")))
NOTIFICATION_CHANNEL = os.environ.get("NOTIFICATION_CHANNEL", "none").lower()

# ── Rutas ─────────────────────────────────────────────────────────────────────
SOURCES = load_json_file(resolve_path(os.environ.get("SOURCES_FILE", "config/sources.json")))

# ── Plantillas ────────────────────────────────────────────────────────────────
EXTRACTOR_SYSTEM_PROMPT = load_text_file(resolve_path(os.environ.get("EXTRACTOR_SYSTEM_PROMPT_FILE", "config/prompts/system_prompt.txt")))
EXTRACTOR_USER_PROMPT   = load_text_file(resolve_path(os.environ.get("EXTRACTOR_USER_PROMPT_FILE",   "config/prompts/user_prompt.txt")))
EMAIL_SUBJECT_TEMPLATE  = load_text_file(resolve_path(os.environ.get("EMAIL_SUBJECT_FILE",           "config/templates/email_subject.txt")))
EMAIL_HTML_TEMPLATE     = load_text_file(resolve_path(os.environ.get("EMAIL_HTML_FILE",              "config/templates/email_html.html")))
REPORT_HTML_TEMPLATE    = load_text_file(resolve_path(os.environ.get("REPORT_HTML_FILE",             "config/templates/report_html.html")))
