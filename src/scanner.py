"""
scanner.py
Escanea las webs de asociaciones empresariales de Canarias,
detecta eventos proximos y envia notificacion al equipo.

Punto de entrada principal del proyecto. Orquesta:
  config        -> ajustes y plantillas
  fetcher       -> obtencion web con Scrapling
  events        -> procesamiento y cache de eventos
  llm           -> llamadas a proveedores LLM
  reports       -> generacion de informes TXT/HTML
  notifications -> envio por Teams o email
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    DAYS_AHEAD,
    EXTRACTOR_MODE,
    LMSTUDIO_API_MODE,
    LLM_PROVIDER,
    LMSTUDIO_PROMPT_TEMPLATE,
    MAX_WORKERS,
    PROMPT_TEMPLATE,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF,
    ROOT_DIR,
    SOURCES,
)
from events import (
    event_id,
    event_start_date,
    filter_events_in_window,
    load_known_events,
    parse_events_response,
    prune_known_events,
    save_known_events,
)
from log_setup import setup_logging
from llm import run_prompt
from notifications import send_notification
from reports import save_events_html_report, save_events_report

log_file = setup_logging(ROOT_DIR / "logs")
log = logging.getLogger(__name__)


def scan_source(source: dict, days_ahead: int) -> list[dict]:
    """Llama al pipeline configurado para escanear una fuente y extraer eventos."""
    today = datetime.now()
    horizon = (today + timedelta(days=days_ahead)).strftime("%d/%m/%Y")
    today_str = today.strftime("%d/%m/%Y")

    use_structured = LLM_PROVIDER == "lmstudio" and EXTRACTOR_MODE == "structured"

    if not use_structured:
        template = LMSTUDIO_PROMPT_TEMPLATE if LLM_PROVIDER == "lmstudio" else PROMPT_TEMPLATE
        prompt = template.format(
            source_name=source["name"],
            source_url=source["url"],
            source_category=source["cat"],
            today_str=today_str,
            horizon=horizon,
        )
        if LLM_PROVIDER == "lmstudio":
            prompt += "\n\nRecuerda: devuelve SOLO el JSON array al final, sin markdown ni texto adicional."

    log.info("⟳  Escaneando %-45s  %s", source["name"], source["url"])
    t0 = time.monotonic()

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            if use_structured:
                from extractor import extract_events_structured
                events = extract_events_structured(source, days_ahead)
            else:
                text = run_prompt(prompt, source_url=source["url"])
                events = parse_events_response(text)
            events = filter_events_in_window(events, today, days_ahead)
            elapsed = time.monotonic() - t0
            if events:
                log.info(
                    "✔  %-45s  %d evento(s)  (%.1fs)",
                    source["name"], len(events), elapsed,
                )
            else:
                log.info(
                    "–  %-45s  sin eventos  (%.1fs)",
                    source["name"], elapsed,
                )
            return events
        except Exception as exc:
            elapsed = time.monotonic() - t0
            # Overflow de contexto: reintentar no ayuda, mismo payload → mismo error
            if exc.__class__.__name__ == "LMStudioContextOverflow":
                log.warning(
                    "✘  %-45s  context overflow tras %.1fs — abortando reintentos",
                    source["name"], elapsed,
                )
                return []
            if attempt == RETRY_ATTEMPTS:
                log.warning(
                    "✘  %-45s  error tras %.1fs: %s",
                    source["name"], elapsed, exc,
                )
                return []
            wait = RETRY_BACKOFF ** (attempt - 1)
            log.warning(
                "↺  %-45s  reintento %d/%d (%.1fs): %s",
                source["name"], attempt, RETRY_ATTEMPTS - 1, elapsed, exc,
            )
            time.sleep(wait)

    return []


def main() -> None:
    log.info("=" * 60)
    log.info("  RADAR EVENTOS — inicio de escaneo")
    log.info("  Proveedor LLM : %s", LLM_PROVIDER)
    log.info("  Fuentes       : %d", len(SOURCES))
    log.info("  Workers       : %d", MAX_WORKERS)
    log.info("  Horizonte     : %d días", DAYS_AHEAD)
    log.info("  Log guardado en: %s", log_file)
    log.info("=" * 60)

    if LLM_PROVIDER == "lmstudio" and LMSTUDIO_API_MODE == "native-mcp":
        from llm.lmstudio import ensure_mcp_server
        log.info("Arrancando servidor MCP local...")
        ensure_mcp_server()
        log.info("Servidor MCP listo")

    today = datetime.now()
    scan_date = today.strftime("%d/%m/%Y %H:%M")
    all_found_events: list[dict] = []
    total_sources = len(SOURCES)
    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, total_sources)) as executor:
        future_to_source = {
            executor.submit(scan_source, source, DAYS_AHEAD): source
            for source in SOURCES
        }
        scanned = 0
        for future in as_completed(future_to_source):
            source = future_to_source[future]
            scanned += 1
            try:
                events = future.result()
            except Exception as exc:
                log.warning("✘  Error no controlado en %s: %s", source["name"], exc)
                continue

            for ev in events:
                ev["_id"] = event_id(ev)
                all_found_events.append(ev)

            elapsed_total = time.monotonic() - t_start
            eta_s = (elapsed_total / scanned) * (total_sources - scanned) if scanned else 0
            log.info(
                "  [%d/%d]  encontrados: %d  |  total acumulado: %d  |  ETA ~%.0fs",
                scanned, total_sources, len(events), len(all_found_events), eta_s,
            )

    elapsed = time.monotonic() - t_start
    log.info("=" * 60)
    log.info("  Escaneo completado en %.0fs", elapsed)
    log.info("  Eventos encontrados : %d", len(all_found_events))
    log.info("=" * 60)

    # ── Deduplicación contra caché de eventos ya notificados ────────────────
    known = prune_known_events(load_known_events(today), today)
    today_iso = today.strftime("%Y-%m-%d")
    new_events: list[dict] = []
    for ev in all_found_events:
        eid = ev["_id"]
        if eid in known:
            continue
        new_events.append(ev)
        start = event_start_date(ev)
        known[eid] = {
            "event_date": start.strftime("%Y-%m-%d") if start else None,
            "first_seen": today_iso,
        }
    save_known_events(known)
    log.info(
        "  Tras dedup           : %d nuevo(s) / %d total",
        len(new_events), len(all_found_events),
    )

    def _sort_key(ev: dict) -> datetime:
        try:
            return datetime.strptime(ev.get("date", "").split("-")[0].strip(), "%d/%m/%Y")
        except Exception:
            return datetime(2099, 12, 31)

    new_events.sort(key=_sort_key)

    save_events_report(new_events, scan_date, DAYS_AHEAD)
    log.info("Informe TXT  : reports/latest_new_events.txt")

    save_events_html_report(new_events, scan_date, DAYS_AHEAD)
    log.info("Informe HTML : reports/latest_new_events.html")

    if new_events:
        log.info("Enviando notificación (%d evento(s) nuevos)...", len(new_events))
        send_notification(new_events, scan_date, today)
    else:
        log.info("Sin eventos nuevos — notificación omitida")


if __name__ == "__main__":
    main()
