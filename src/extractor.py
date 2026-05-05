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

from config import (
    EXTRACTOR_MAX_SUBPAGES,
    EXTRACTOR_PER_PAGE_CHARS,
    LMSTUDIO_API_TOKEN,
    LMSTUDIO_BASE_URL,
    LMSTUDIO_CONTEXT_WINDOW,
    LMSTUDIO_PARALLEL_CALLS,
    LMSTUDIO_REQUEST_TIMEOUT,
    MAX_TOKENS,
    MODEL_NAME,
)

# Limita peticiones concurrentes al LLM. Si LMSTUDIO_PARALLEL_CALLS=0 no se
# serializa nada (el límite efectivo lo pone MAX_WORKERS del scanner).
_LLM_SEM: threading.Semaphore | None = (
    threading.Semaphore(LMSTUDIO_PARALLEL_CALLS) if LMSTUDIO_PARALLEL_CALLS > 0 else None
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
                    "time":        {"type": "string", "description": "Hora de inicio en formato HH:MM, p.ej. 09:00. Vacío si no aparece."},
                    "location":    {"type": "string", "description": "Lugar físico o virtual donde se celebra. Vacío si no aparece."},
                    "description": {"type": "string", "maxLength": 200},
                    "url":         {"type": "string"},
                    "price":       {"type": "string", "description": "Precio o 'Gratuito'. Vacío si no aparece."},
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
    name = node.get("name")
    if name:
        parts.append(f"name: {_clean_inline(str(name))}")
    # startDate: puede ser ISO 8601 con hora (2026-05-19T10:00:00) o solo fecha
    start = node.get("startDate")
    if start:
        s = str(start)
        if "T" in s:
            date_part, time_part = s.split("T", 1)
            parts.append(f"startDate: {date_part}")
            parts.append(f"startTime: {time_part[:5]}")
        else:
            parts.append(f"startDate: {s}")
    end = node.get("endDate")
    if end:
        parts.append(f"endDate: {str(end).split('T')[0]}")
    desc = node.get("description")
    if desc:
        parts.append(f"description: {_clean_inline(str(desc))[:200]}")
    url = node.get("url")
    if url:
        parts.append(f"url: {url}")
    location = node.get("location")
    if isinstance(location, dict):
        loc_name = location.get("name") or ""
        addr = location.get("address")
        if isinstance(addr, dict):
            addr_str = ", ".join(str(addr.get(k, "")) for k in ("streetAddress", "addressLocality", "addressRegion") if addr.get(k))
            loc_full = f"{loc_name}, {addr_str}".strip(", ") if addr_str else loc_name
            if loc_full:
                parts.append(f"location: {loc_full}")
        elif loc_name:
            parts.append(f"location: {loc_name}")
    elif isinstance(location, str) and location.strip():
        parts.append(f"location: {location.strip()}")
    offers = node.get("offers")
    if isinstance(offers, dict) and offers.get("price") is not None:
        price_val = str(offers["price"])
        currency = offers.get("priceCurrency", "")
        price_str = f"{price_val} {currency}".strip() if price_val != "0" else "Gratuito"
        parts.append(f"price: {price_str}")
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
- date: DD/MM/YYYY o rango DD/MM/YYYY-DD/MM/YYYY. CONVIERTE SIEMPRE al formato DD/MM/YYYY aunque aparezca como "15 mayo 2026", "Jun 1 - 5 junio 2026", "May 21-22 2026", "2026-05-15", etc. Meses: enero=01, febrero=02, marzo=03, abril=04, mayo=05, junio=06, julio=07, agosto=08, septiembre=09, octubre=10, noviembre=11, diciembre=12.
- Si hay rango de fechas (ej. "Jun 1 - 5 junio 2026"), emite "01/06/2026-05/06/2026".
- time: hora de INICIO del evento en formato HH:MM (24h). Fuentes de hora (por orden de prioridad): (1) campo "startTime: HH:MM" en la sección ### EVENTOS ESTRUCTURADOS del contexto — úsalo directamente; (2) expresiones en el texto como "a las 18:00 h", "at 3pm", "18:30 Uhr", "de 9:00 a 14:00". Si no hay hora de inicio, deja "". NUNCA captures timestamps de publicación, "Posted at", "Actualizado el" ni metadatos.
- location: lugar físico o plataforma virtual donde se celebra el evento. Busca nombres de recintos, salas, ciudades, países, "Online", "Zoom", "Teams", "virtual" en cualquier idioma. Si no aparece, deja "".
- price: precio si aparece. Si es 0, "free", "gratuito", "kostenlos", "gratuit", escribe "Gratuito". Deja "" si no aparece.
- type ∈ {evento, formacion, networking, jornada, feria, mision, otro}.
- description: máximo 150 caracteres, sin HTML. No repitas hora ni lugar si ya los pusiste en time/location.
- association y category son fijos (te los doy abajo).
- url: URL real que aparezca literalmente en el contenido. Si no hay, deja "".
- No inventes datos. Si un campo no aparece en el contenido, déjalo vacío "".
- Si no hay eventos en el rango, devuelve {"events": []}.
- Devuelve SOLO el JSON, sin razonar, sin <think>, sin markdown.

Ejemplos de extracción de time y location (varios idiomas):
  ES: "6 de mayo, a las 18:00 horas, Teatro Príncipe Felipe, Tegueste"
  → time: "18:00", location: "Teatro Príncipe Felipe, Tegueste"

  ES: "el martes 19 de mayo a las 10:00h en el Palacio de Congresos de Madrid"
  → time: "10:00", location: "Palacio de Congresos de Madrid"

  ES: "Jornada presencial en Las Palmas de Gran Canaria, de 9:00 a 14:00"
  → time: "09:00", location: "Las Palmas de Gran Canaria"

  ES: "Webinar online. Inscríbete en..."
  → time: "", location: "Online"

  EN: "Tuesday, May 19 | 10:00 AM – 12:00 PM | Brussels, Belgium"
  → time: "10:00", location: "Brussels, Belgium"

  EN: "Join us online via Zoom at 3pm CET"
  → time: "15:00", location: "Online"

  EN: "from 9am to 5pm at the ICC Berlin"
  → time: "09:00", location: "ICC Berlin"

  DE: "Donnerstag, 14. Mai 2026, 18:30 Uhr, IHK Frankfurt"
  → time: "18:30", location: "IHK Frankfurt"

  DE: "Online-Veranstaltung ab 10:00 Uhr"
  → time: "10:00", location: "Online"

  JSON-LD estructurado: "startTime: 18:30 | location: Auditorio Tenerife, Santa Cruz"
  → time: "18:30", location: "Auditorio Tenerife, Santa Cruz"

  METADATA a ignorar: "Publicado el 14/05/2026 a las 09:57 por admin"
  → time: "", location: ""
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
    from llm import lmstudio as _lm
    _lm.ensure_model_loaded()

    # Si LM Studio tiene el modelo cargado con menos contexto que el
    # configurado, recortamos el contenido del último user-message para que
    # quepa: dejamos margen para system prompt + max_tokens de salida.
    loaded_ctx = _lm.LMSTUDIO_LOADED_CONTEXT or LMSTUDIO_CONTEXT_WINDOW
    if loaded_ctx and loaded_ctx < LMSTUDIO_CONTEXT_WINDOW:
        # ~3.5 chars/token (conservador para español). Reservamos 1500 tokens
        # para system + JSON output.
        max_input_tokens = max(512, loaded_ctx - 1500)
        max_input_chars = int(max_input_tokens * 3.5)
        user_msg = messages[-1].get("content", "")
        if len(user_msg) > max_input_chars:
            log.info(
                "Recortando input %d → %d chars (ctx LM Studio = %d)",
                len(user_msg), max_input_chars, loaded_ctx,
            )
            messages = messages[:-1] + [{"role": "user", "content": user_msg[:max_input_chars]}]

    headers = {"Content-Type": "application/json"}
    if LMSTUDIO_API_TOKEN:
        headers["Authorization"] = f"Bearer {LMSTUDIO_API_TOKEN}"
    # Margen amplio para Qwen3: razona varios cientos de tokens antes de emitir
    # contenido. Necesitamos capacity para ambos. Ver investigación: con
    # response_format=json_schema strict el modelo elige el JSON válido más
    # corto ({}) y se nos cuela. Mejor parseo manual con prompt fuerte.
    # max_tokens debe caber en lo que sobre del contexto.
    if loaded_ctx and loaded_ctx < LMSTUDIO_CONTEXT_WINDOW:
        # En contextos pequeños (4096) reservamos como mucho 30% para output.
        max_tokens_out = min(max(MAX_TOKENS, 1500), int(loaded_ctx * 0.3))
    else:
        max_tokens_out = max(MAX_TOKENS, 8000)

    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0,
        "context_length": LMSTUDIO_CONTEXT_WINDOW,
        "num_ctx": LMSTUDIO_CONTEXT_WINDOW,
        "max_tokens": max_tokens_out,
        # Desactiva thinking en modelos Qwen3 si el server lo soporta.
        "chat_template_kwargs": {"enable_thinking": False},
        # Schema enforcement: garantiza JSON válido y elimina razonamiento suelto.
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "events_response",
                "schema": EVENT_SCHEMA,
                "strict": True,
            },
        },
    }
    if _LLM_SEM is not None:
        with _LLM_SEM:
            response = httpx.post(
                f"{LMSTUDIO_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=LMSTUDIO_REQUEST_TIMEOUT,
            )
    else:
        response = httpx.post(
            f"{LMSTUDIO_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=LMSTUDIO_REQUEST_TIMEOUT,
        )
    # Fallback: si LM Studio reporta context overflow, recortamos el último
    # mensaje de usuario y reintentamos hasta 2 veces. Esto cubre el caso en
    # que el modelo está cargado con un context_length menor del configurado.
    err_lower = response.text.lower() if response.status_code >= 400 else ""
    if response.status_code == 400 and ("context length" in err_lower or "context size" in err_lower):
        for shrink in (0.5, 0.25):
            user_msg = messages[-1]["content"]
            cut = int(len(user_msg) * shrink)
            log.warning(
                "Context overflow → reintentando con %d%% del contenido (%d chars)",
                int(shrink * 100), cut,
            )
            shrunk_messages = messages[:-1] + [{"role": "user", "content": user_msg[:cut]}]
            payload["messages"] = shrunk_messages
            response = httpx.post(
                f"{LMSTUDIO_BASE_URL}/chat/completions",
                headers=headers, json=payload, timeout=LMSTUDIO_REQUEST_TIMEOUT,
            )
            if response.status_code < 400:
                break

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
    jsonld_events = main_page.get("jsonld_events") or []
    jsonld_count = len(jsonld_events)
    main_content = main_page.get("content") or ""
    main_content_len = len(main_content)

    # JSON-LD con startTime real → datos ya completos, no hace falta descargar fichas.
    # Ignoramos T00:00 porque WordPress lo usa como placeholder cuando no hay hora.
    jsonld_has_time = any(
        re.search(r"T(?!00:00)", str(n.get("startDate", "")))
        for n in jsonld_events
    )

    _MONTH_RE = re.compile(
        r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b",
        re.IGNORECASE,
    )
    month_mentions = len(_MONTH_RE.findall(main_content))

    if jsonld_count >= 1 and jsonld_has_time:
        # JSON-LD ya contiene fecha+hora: subpáginas solo añadirían ruido.
        max_subs = 0
    elif month_mentions >= 3 and jsonld_count == 0:
        # Página-listado sin JSON-LD pero con muchos eventos en texto: confiar en main.
        max_subs = 0
    elif main_content_len > 5000:
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

    # Pool de enlaces candidatos: home + subpáginas + JSON-LD events.
    candidate_links: list[dict] = list(main_page.get("discovered_links") or [])
    for sp in subpages:
        candidate_links.extend(sp.get("discovered_links") or [])
    all_jsonld: list[dict] = list(main_page.get("jsonld_events") or [])
    for sp in subpages:
        all_jsonld.extend(sp.get("jsonld_events") or [])
    jsonld_urls = [n.get("url") for n in all_jsonld if n.get("url")]

    valid_urls = {l["url"] for l in candidate_links if l.get("url")} | set(jsonld_urls)
    source_host = urlparse(source["url"]).netloc.lower()

    def _jsonld_for_title(title: str) -> dict | None:
        t = _norm(title)
        for node in all_jsonld:
            if _norm(node.get("name", "")) == t:
                return node
        return None

    def _fill_from_jsonld(ev: dict) -> dict:
        """Rellena time/location vacíos usando el nodo JSON-LD equivalente."""
        node = _jsonld_for_title(ev.get("title", ""))
        if not node:
            return ev
        if not ev.get("time"):
            start = str(node.get("startDate", ""))
            if "T" in start:
                t = start.split("T", 1)[1][:5]
                if t and t != "00:00":
                    ev["time"] = t
        if not ev.get("location"):
            loc = node.get("location")
            if isinstance(loc, str) and loc.strip():
                ev["location"] = loc.strip()
            elif isinstance(loc, dict):
                name = (loc.get("name") or "").strip()
                addr = loc.get("address") or {}
                city = (addr.get("addressLocality") or "").strip() if isinstance(addr, dict) else ""
                ev["location"] = name or city
        return ev

    def _resolve_url(ev: dict) -> str:
        raw = (ev.get("url") or "").strip()
        title = (ev.get("title") or "").strip()
        # 1. URL emitida por el LLM si es válida (mismo dominio y aparece en el pool)
        if raw and raw.startswith("http"):
            host = urlparse(raw).netloc.lower()
            if host == source_host or host.endswith(f".{source_host}") or source_host.endswith(f".{host}"):
                if raw in valid_urls or any(raw == u for u in jsonld_urls):
                    if _title_matches_url(title, raw):
                        return raw
        # 2. Buscar enlace cuyo slug matchee el título
        match = _best_link_for_title(title, candidate_links)
        if match:
            return match
        # 3. JSON-LD: intentar match por nombre
        for n in (main_page.get("jsonld_events") or []):
            if n.get("url") and _norm(n.get("name", "")) == _norm(title):
                return n["url"]
        # 4. Fallback final: la URL de la fuente
        return source["url"]

    # Normaliza y rellena campos faltantes
    normalized: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if "title" not in ev and "name" in ev:
            ev["title"] = ev.pop("name")
        if not ev.get("title") or not ev.get("date"):
            continue
        ev["association"] = source["name"]
        ev["category"] = source["cat"]
        for field in ("type", "time", "location", "description", "url", "price"):
            ev.setdefault(field, "")
        ev["url"] = _resolve_url(ev)
        ev = _fill_from_jsonld(ev)
        normalized.append(ev)

    return normalized


# ── Resolución de URLs por matching título ↔ slug ─────────────────────────────

_SLUG_NORMALIZE = re.compile(r"[^a-z0-9]+")
_STOPWORDS = {
    "de", "del", "la", "las", "el", "los", "y", "a", "en", "con", "para", "por",
    "un", "una", "unos", "unas", "sobre", "the", "of", "to", "and", "for",
}


def _norm(text: str) -> str:
    """Normaliza texto: minúsculas, sin acentos básicos, separadores fuera."""
    if not text:
        return ""
    t = text.lower()
    repl = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
    for k, v in repl.items():
        t = t.replace(k, v)
    return _SLUG_NORMALIZE.sub("-", t).strip("-")


def _title_tokens(title: str) -> set[str]:
    norm = _norm(title)
    return {tok for tok in norm.split("-") if tok and tok not in _STOPWORDS and len(tok) >= 3}


def _title_matches_url(title: str, url: str) -> bool:
    """Tolerante: con que se solape al menos 1 token significativo basta."""
    if not title or not url:
        return False
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug_tokens = {tok for tok in _norm(slug).split("-") if tok and len(tok) >= 3}
    title_tokens = _title_tokens(title)
    if not title_tokens or not slug_tokens:
        return False
    return bool(title_tokens & slug_tokens)


def _best_link_for_title(title: str, links: list[dict]) -> str | None:
    """Elige el enlace cuyo slug comparte más tokens con el título."""
    title_tokens = _title_tokens(title)
    if not title_tokens:
        return None
    best_url: str | None = None
    best_score = 0
    for l in links:
        url = l.get("url", "")
        if not url:
            continue
        slug = urlparse(url).path.rstrip("/").split("/")[-1]
        slug_tokens = {tok for tok in _norm(slug).split("-") if tok and len(tok) >= 3}
        score = len(title_tokens & slug_tokens)
        if score > best_score:
            best_score = score
            best_url = url
    return best_url if best_score >= 1 else None
