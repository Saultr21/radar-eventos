"""
extractor.py
Pipeline determinista crawl-then-extract:

  1. Descarga la URL principal con fetcher.fetch_url_details (incluye JSON-LD)
  2. Selecciona top-N enlaces relevantes con score determinista
  3. Descarga esos enlaces en paralelo
  4. Concatena todo (JSON-LD + páginas en texto plano) con separadores claros
  5. Hace UNA llamada al LLM con response_format=json_schema → JSON garantizado

Diseñado para modelos pequeños (Qwen 7-9B). Sin loops de tool-use.
"""
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import urlparse

import httpx

# El modelo local atiende una petición a la vez con eficiencia. Paralelizar
# llamadas hace que respuestas se trunquen (vienen 2 chars). El lock serializa
# SOLO las llamadas al LLM; los fetchers siguen en paralelo.
_LLM_LOCK = threading.Lock()

from config import (
    EXTRACTOR_MAX_SUBPAGES,
    EXTRACTOR_PER_PAGE_CHARS,
    LMSTUDIO_API_TOKEN,
    LMSTUDIO_BASE_URL,
    LMSTUDIO_CONTEXT_WINDOW,
    LMSTUDIO_REQUEST_TIMEOUT,
    MAX_TOKENS,
    MODEL_NAME,
)
from fetcher import EVENT_HINTS, fetch_url_details

log = logging.getLogger(__name__)

# JSON Schema usado en response_format. Coincide con los campos del informe.
EVENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string"},
                    "type":        {"type": "string", "enum": ["evento", "formacion", "networking", "jornada", "feria", "mision", "otro"]},
                    "date":        {"type": "string", "description": "DD/MM/YYYY o DD/MM/YYYY-DD/MM/YYYY"},
                    "time":        {"type": "string"},
                    "location":    {"type": "string"},
                    "description": {"type": "string", "maxLength": 200},
                    "url":         {"type": "string"},
                    "price":       {"type": "string"},
                    "deadline":    {"type": "string"},
                    "association": {"type": "string"},
                    "category":    {"type": "string"},
                },
                "required": ["title", "date", "association", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}


# ── Selección de subpáginas relevantes ────────────────────────────────────────

_DATE_RE = re.compile(r"\b(\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?)\b")


_CALENDAR_VIEWS = ("/lista/", "/mes/", "/hoy/", "/dia/", "/semana/", "/agenda/", "/calendar")
_SLUG_HINTS = ("/evento/", "/eventos/", "/event/", "/events/", "/curso/", "/jornada/", "/feria/", "/formacion/")


def _link_score(link: dict, source_path: str) -> int:
    """Puntúa un enlace por su probabilidad de contener un evento individual.
    Más es mejor. Penaliza vistas de calendario y prioriza fichas individuales."""
    url = link.get("url", "")
    url_l = url.lower()
    text = f"{link.get('title','')} {url}".lower()
    score = 0
    if any(h in text for h in EVENT_HINTS):
        score += 3
    if _DATE_RE.search(text):
        score += 2
    # Vistas de calendario (lista/mes/hoy/agenda) son redundantes con la home
    if any(v in url_l for v in _CALENDAR_VIEWS):
        score -= 5
    # Fichas individuales: típicamente /evento/<slug>/ o /curso/<slug>/
    path = urlparse(url).path.lower()
    if any(h in path for h in _SLUG_HINTS):
        depth = len([s for s in path.split("/") if s])
        if depth >= 2:
            score += 4
    # Profundidad del path: páginas hijas suelen ser fichas individuales
    try:
        depth = len([s for s in urlparse(url).path.split("/") if s])
    except Exception:
        depth = 0
    score += min(depth, 4)
    # Penaliza paginación / categorías
    if any(p in url_l for p in ("/category/", "/tag/", "?page=", "page/", "/author/")):
        score -= 2
    if url.rstrip("/").endswith(source_path.rstrip("/")):
        score -= 5
    return score


def _select_subpages(links: list[dict], source_url: str, max_subpages: int) -> list[dict]:
    if not links or max_subpages <= 0:
        return []
    source_path = urlparse(source_url).path or "/"
    scored = [(l, _link_score(l, source_path)) for l in links]
    scored.sort(key=lambda x: x[1], reverse=True)
    # Filtra duplicados por URL conservando orden
    seen: set[str] = set()
    selected: list[dict] = []
    for link, _ in scored:
        url = link.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        selected.append(link)
        if len(selected) >= max_subpages:
            break
    return selected


# ── Construcción del contexto ─────────────────────────────────────────────────

def _clean_inline(value: str) -> str:
    """Decodifica entidades HTML y elimina tags inline. Para descripciones
    de schema.org/Event que a veces llegan con &lt;p&gt;..."""
    if not isinstance(value, str):
        return str(value)
    text = unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _format_jsonld_event(node: dict) -> str:
    """Reduce un nodo schema.org/Event a texto compacto en formato 'clave: valor'."""
    parts: list[str] = []
    for key in ("name", "startDate", "endDate", "description", "url"):
        v = node.get(key)
        if v:
            parts.append(f"{key}: {_clean_inline(str(v))}")
    location = node.get("location")
    if isinstance(location, dict):
        loc_name = location.get("name") or ""
        addr = location.get("address")
        if isinstance(addr, dict):
            addr_str = ", ".join(str(addr.get(k, "")) for k in ("streetAddress", "addressLocality", "addressRegion") if addr.get(k))
            parts.append(f"location: {loc_name} ({addr_str})".strip())
        elif loc_name:
            parts.append(f"location: {loc_name}")
    elif isinstance(location, str):
        parts.append(f"location: {location}")
    offers = node.get("offers")
    if isinstance(offers, dict) and offers.get("price") is not None:
        parts.append(f"price: {offers['price']} {offers.get('priceCurrency','')}".strip())
    return " | ".join(parts)


def _build_context(
    source: dict,
    main_page: dict,
    subpages: list[dict],
) -> str:
    """Concatena JSON-LD + páginas en texto plano con separadores legibles."""
    blocks: list[str] = []

    jsonld = main_page.get("jsonld_events") or []
    if jsonld:
        formatted = "\n".join(f"- {_format_jsonld_event(n)}" for n in jsonld[:20])
        blocks.append(f"### EVENTOS ESTRUCTURADOS (schema.org/Event detectados en {main_page.get('final_url')})\n{formatted}")

    main_content = (main_page.get("content") or "")[:EXTRACTOR_PER_PAGE_CHARS]
    if main_content:
        blocks.append(
            f"### PÁGINA PRINCIPAL — {main_page.get('final_url')}\n{main_content}"
        )

    for sp in subpages:
        sp_jsonld = sp.get("jsonld_events") or []
        if sp_jsonld:
            formatted = "\n".join(f"- {_format_jsonld_event(n)}" for n in sp_jsonld[:5])
            blocks.append(f"### SUBPÁGINA — {sp.get('final_url')}\n[schema.org/Event]\n{formatted}")
            continue
        sp_content = (sp.get("content") or "")[:EXTRACTOR_PER_PAGE_CHARS]
        if sp_content:
            blocks.append(f"### SUBPÁGINA — {sp.get('final_url')}\n{sp_content}")

    return "\n\n".join(blocks)


# ── Llamada al LLM con JSON schema ────────────────────────────────────────────

_SYSTEM_PROMPT = """/nothink
Eres un extractor de eventos empresariales. Recibes el contenido de una o varias páginas web de una asociación canaria y devuelves los eventos en JSON.

Reglas estrictas:
- Solo eventos cuya fecha sea HOY o posterior, dentro del rango indicado.
- date debe ser DD/MM/YYYY o un rango DD/MM/YYYY-DD/MM/YYYY.
- type ∈ {evento, formacion, networking, jornada, feria, mision, otro}.
- description: máximo 150 caracteres, sin HTML.
- association y category son fijos (te los doy abajo).
- url debe ser una URL real que aparezca literalmente en el contenido. Si no hay, deja "".
- No inventes datos. Si un campo no aparece, déjalo vacío "".
- Si no hay eventos en el rango, devuelve {"events": []}.
- Devuelve SOLO el JSON, sin razonar, sin <think>, sin markdown.
"""


def _user_prompt(source: dict, days_ahead: int, today: datetime, context: str) -> str:
    horizon = (today + timedelta(days=days_ahead)).strftime("%d/%m/%Y")
    today_str = today.strftime("%d/%m/%Y")
    return (
        f"Asociación: {source['name']}\n"
        f"Categoría: {source['cat']}\n"
        f"URL fuente: {source['url']}\n"
        f"Rango de fechas: {today_str} a {horizon}\n\n"
        f"--- CONTENIDO ---\n{context}\n--- FIN ---\n\n"
        f"Devuelve SOLO el JSON con la lista de eventos en el rango."
    )


def _fix_unescaped_newlines_in_strings(text: str) -> str:
    """Convierte newlines literales dentro de strings JSON ("...\n...") a \\n.
    El modelo a veces emite descripciones multilínea sin escapar."""
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch == "\n":
            out.append("\\n")
            continue
        if in_string and ch == "\r":
            continue
        if in_string and ch == "\t":
            out.append("\\t")
            continue
        out.append(ch)
    return "".join(out)


def _parse_json_payload(raw: str) -> dict | None:
    """Tolera <think>, fences markdown, texto previo y newlines sin escapar.
    Busca el primer { y parsea el bloque JSON con events:[...]."""
    if not raw:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    def _try(s: str):
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list):
                return {"events": obj}
        except Exception:
            return None

    # Caso fácil: ya es JSON
    obj = _try(cleaned) or _try(_fix_unescaped_newlines_in_strings(cleaned))
    if obj is not None and "events" in obj:
        return obj

    # Si hay razonamiento + JSON al final, el JSON suele estar en el último
    # bloque {...} que contiene "events". Buscamos el último candidato.
    candidates: list[str] = []
    depth = 0
    start_idx = -1
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start_idx != -1:
                candidates.append(cleaned[start_idx:i + 1])
                start_idx = -1
    # Probar candidatos del último al primero, prefiriendo los que tengan "events"
    for blob in reversed(candidates):
        if '"events"' not in blob:
            continue
        obj = _try(blob) or _try(_fix_unescaped_newlines_in_strings(blob))
        if obj is not None:
            return obj
    # Sin candidatos con "events", probar todos
    for blob in reversed(candidates):
        obj = _try(blob) or _try(_fix_unescaped_newlines_in_strings(blob))
        if obj is not None:
            return obj
    # Último recurso: array JSON suelto
    a_start, a_end = cleaned.find("["), cleaned.rfind("]")
    if a_start != -1 and a_end != -1 and a_end > a_start:
        blob = cleaned[a_start:a_end + 1]
        try:
            arr = json.loads(blob)
            if isinstance(arr, list):
                return {"events": arr}
        except Exception:
            pass
    return None


def _call_lmstudio_structured(messages: list[dict]) -> str:
    headers = {"Content-Type": "application/json"}
    if LMSTUDIO_API_TOKEN:
        headers["Authorization"] = f"Bearer {LMSTUDIO_API_TOKEN}"
    # Margen amplio para Qwen3: razona varios cientos de tokens antes de emitir
    # contenido. Necesitamos capacity para ambos. Ver investigación: con
    # response_format=json_schema strict el modelo elige el JSON válido más
    # corto ({}) y se nos cuela. Mejor parseo manual con prompt fuerte.
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0,
        "num_ctx": LMSTUDIO_CONTEXT_WINDOW,
        # Qwen3 razona mucho aunque le digas /nothink. Le damos suficiente
        # margen para razonar Y emitir el JSON final.
        "max_tokens": max(MAX_TOKENS, 8000),
        # Desactiva thinking en modelos Qwen3 si el server lo soporta.
        # Si no lo soporta lo ignora — no rompe.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with _LLM_LOCK:
        response = httpx.post(
            f"{LMSTUDIO_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=LMSTUDIO_REQUEST_TIMEOUT,
        )
    if response.status_code >= 400:
        raise ValueError(
            f"LM Studio devolvió {response.status_code}. Detalle: {response.text[:500]}"
        )
    msg = response.json()["choices"][0]["message"]
    # Algunos modelos emiten todo el JSON dentro de reasoning_content cuando
    # el server no entiende /nothink. Si content viene vacío, concatenamos.
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    if content.strip():
        return content
    return reasoning or "{}"


# ── API pública ───────────────────────────────────────────────────────────────

def extract_events_structured(source: dict, days_ahead: int) -> list[dict]:
    """Pipeline completo crawl-then-extract para una fuente.
    Devuelve la lista de eventos (ya sin metadatos LLM)."""
    today = datetime.now()

    # 1. Página principal
    log.debug("[extractor] fetching main: %s", source["url"])
    main_page = fetch_url_details(
        source["url"],
        max_chars=EXTRACTOR_PER_PAGE_CHARS,
        max_links=20,
    )

    if main_page.get("status") != "ok":
        log.warning(
            "[extractor] %s: fetch falló (%s) — %s",
            source["name"], main_page.get("status"), main_page.get("error"),
        )
        return []

    # 2. Selección de subpáginas
    # Si la home ya tiene JSON-LD con eventos, normalmente la información está
    # completa — descargar subpáginas solo añade ruido y confunde al modelo.
    jsonld_count = len(main_page.get("jsonld_events") or [])
    main_content_len = len(main_page.get("content") or "")
    if jsonld_count >= 1:
        max_subs = 0
    elif main_content_len > 5000:
        # Suficiente contenido en la home → con 1-2 subpáginas basta
        max_subs = min(2, EXTRACTOR_MAX_SUBPAGES)
    else:
        max_subs = EXTRACTOR_MAX_SUBPAGES

    selected_links = _select_subpages(
        main_page.get("discovered_links") or [],
        main_page.get("final_url") or source["url"],
        max_subs,
    )

    # 3. Descarga subpáginas en paralelo
    subpages: list[dict] = []
    if selected_links:
        log.debug(
            "[extractor] fetching %d subpáginas: %s",
            len(selected_links),
            [l["url"] for l in selected_links],
        )
        with ThreadPoolExecutor(max_workers=min(4, len(selected_links))) as pool:
            futures = [
                pool.submit(fetch_url_details, l["url"], EXTRACTOR_PER_PAGE_CHARS, 0)
                for l in selected_links
            ]
            for f in futures:
                try:
                    res = f.result()
                    if res.get("status") == "ok":
                        subpages.append(res)
                except Exception as exc:
                    log.debug("[extractor] subpágina falló: %s", exc)

    # 4. Construye contexto
    context = _build_context(source, main_page, subpages)
    if not context.strip():
        log.warning("[extractor] %s: contexto vacío", source["name"])
        return []

    log.info(
        "[extractor] %s: contexto %d chars (main=%d, subs=%d, jsonld=%d)",
        source["name"], len(context),
        len(main_page.get("content") or ""),
        len(subpages),
        len(main_page.get("jsonld_events") or []),
    )

    # 5. Llamada al LLM con schema enforcement
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(source, days_ahead, today, context)},
    ]
    raw = _call_lmstudio_structured(messages)
    log.info("[extractor] %s: respuesta LLM (%d chars)", source["name"], len(raw))
    log.debug("[extractor] %s: raw: %s", source["name"], raw[:800])

    parsed = _parse_json_payload(raw)
    if parsed is None:
        log.warning("[extractor] %s: JSON inválido. Raw: %s", source["name"], raw[:300])
        return []

    events = parsed.get("events") if isinstance(parsed, dict) else None
    if not isinstance(events, list):
        return []

    # Normaliza y rellena campos faltantes
    normalized: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        # Algunos modelos devuelven 'name' en vez de 'title'
        if "title" not in ev and "name" in ev:
            ev["title"] = ev.pop("name")
        if not ev.get("title") or not ev.get("date"):
            continue  # evento sin datos clave: descartar
        ev["association"] = source["name"]
        ev["category"] = source["cat"]
        for field in ("type", "time", "location", "description", "url", "price", "deadline"):
            ev.setdefault(field, "")
        normalized.append(ev)

    return normalized
