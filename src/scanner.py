"""
scanner.py
Escanea las webs de asociaciones empresariales de Canarias,
detecta eventos proximos y envia notificacion al equipo.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from config import (
    DAYS_AHEAD,
    LLM_PROVIDER,
    MAX_WORKERS,
    MODEL_NAME,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF,
    ROOT_DIR,
    SOURCES,
)
from events import filter_events_in_window
from log_setup import setup_logging
from notifications import send_notification
from reports import save_events_html_report, save_events_report

log_file = setup_logging(ROOT_DIR / "logs")
log = logging.getLogger(__name__)


def scan_source(source: dict, days_ahead: int) -> list[dict]:
    """Escanea una fuente y extrae eventos mediante el pipeline estructurado."""
    from extractor import extract_events_structured
    today = datetime.now()

    log.info("⟳  Escaneando %-45s  %s", source["name"], source["url"])
    t0 = time.monotonic()

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            events = extract_events_structured(source, days_ahead)
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
    log.info("  Proveedor LLM : %s", MODEL_NAME)
    log.info("  Fuentes       : %d", len(SOURCES))
    log.info("  Workers       : %d", MAX_WORKERS)
    log.info("  Horizonte     : %d días", DAYS_AHEAD)
    log.info("  Log guardado en: %s", log_file)
    log.info("=" * 60)

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

            all_found_events.extend(events)

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

    def _sort_key(ev: dict) -> datetime:
        try:
            return datetime.strptime(ev.get("date", "").split("-")[0].strip(), "%d/%m/%Y")
        except Exception:
            return datetime(2099, 12, 31)

    all_found_events.sort(key=_sort_key)

    save_events_report(all_found_events, scan_date, DAYS_AHEAD)
    log.info("Informe TXT  : reports/latest_new_events.txt")

    report_html_path = save_events_html_report(all_found_events, scan_date, DAYS_AHEAD)
    log.info("Informe HTML : reports/latest_new_events.html")

    if all_found_events:
        log.info("Enviando notificación (%d evento(s))...", len(all_found_events))
        send_notification(all_found_events, scan_date, today, report_html_path=report_html_path)
    else:
        log.info("Sin eventos encontrados — notificación omitida")


if __name__ == "__main__":
    main()
