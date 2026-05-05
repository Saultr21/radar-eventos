"""
events.py
Procesamiento de eventos: hashing, parseo de respuestas LLM, gestión de caché.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

from config import KNOWN_EVENTS_FILE, CACHE_RETENTION_DAYS

log = logging.getLogger(__name__)


def event_id(event: dict) -> str:
    """Genera un ID único por evento basado en título + fecha + fuente."""
    raw = f"{event.get('title', '')}{event.get('date', '')}{event.get('association', '')}"
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


def parse_events_response(text: str) -> list[dict]:
    """Parsea la respuesta de texto del LLM y extrae la lista de eventos JSON."""
    cleaned = re.sub(r"<think>.*?</think>", "", text.strip(), flags=re.DOTALL).strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")

    if start == -1 or end == -1:
        lower = cleaned.lower()
        no_events_hints = (
            "no hay eventos", "no se encontraron", "no encontré", "no encontre",
            "no existen eventos", "no events found", "no upcoming events",
            "sin eventos", "no hay cursos", "no hay actividades",
            "no se han encontrado", "no tengo información", "no tengo informacion",
            "no hay información", "no hay informacion", "[]",
            "no se encontró", "no se encontro", "ningún evento", "ningun evento",
            "no se han identificado", "no dispongo", "lo siento",
        )
        if any(hint in lower for hint in no_events_hints) or cleaned in ("", "[]"):
            return []
        # Respuesta sin [] y sin pistas claras: lo tratamos como "sin eventos"
        # (mejor que fallar y tirar todo el escaneo de la fuente al carajo).
        log.warning("Respuesta del LLM sin JSON array — asumiendo 0 eventos. Texto: %r", cleaned[:200])
        return []

    events = json.loads(cleaned[start: end + 1])
    if not isinstance(events, list):
        raise ValueError("La respuesta no contiene una lista de eventos")
    return events


def filter_events_in_window(
    events: list[dict],
    today: datetime,
    days_ahead: int,
) -> list[dict]:
    """Descarta eventos cuya fecha quede fuera de [today, today+days_ahead].
    Eventos sin fecha parseable se mantienen (no penalizamos al modelo)."""
    horizon = today + timedelta(days=days_ahead)
    today_floor = datetime(today.year, today.month, today.day)
    kept: list[dict] = []
    for ev in events:
        start = event_start_date(ev)
        if start is None:
            kept.append(ev)
            continue
        if today_floor <= start <= horizon:
            kept.append(ev)
        else:
            log.debug(
                "Evento descartado fuera de ventana: %s (%s)",
                ev.get("title"), ev.get("date"),
            )
    return kept


def group_events_by_source(events: list[dict]) -> dict[str, list[dict]]:
    """Agrupa los eventos por nombre de asociación/fuente."""
    grouped: dict[str, list[dict]] = {}
    for ev in events:
        src = ev.get("association", "Otros")
        grouped.setdefault(src, []).append(ev)
    return grouped


def load_known_events(today: datetime) -> dict[str, dict[str, str | None]]:
    """Carga la caché de eventos conocidos manteniendo compatibilidad con formato legacy."""
    if KNOWN_EVENTS_FILE.exists():
        raw = json.loads(KNOWN_EVENTS_FILE.read_text())
        if isinstance(raw, list):
            first_seen = today.strftime("%Y-%m-%d")
            return {eid: {"event_date": None, "first_seen": first_seen} for eid in raw}
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
    KNOWN_EVENTS_FILE.write_text(
        json.dumps(dict(sorted(known.items())), ensure_ascii=False, indent=2)
    )


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
