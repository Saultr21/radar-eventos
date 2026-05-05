"""
reports.py
Construcción y guardado de informes TXT y HTML de eventos detectados.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import ROOT_DIR, REPORT_HTML_TEMPLATE
from events import group_events_by_source

log = logging.getLogger(__name__)


def save_events_report(
    all_events: list[dict],
    scan_date: str,
    days_ahead: int,
    output_path: Path | None = None,
) -> None:
    """Guarda el informe de eventos en formato TXT legible en reports/."""
    reports_dir = ROOT_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = output_path or (reports_dir / "latest_new_events.txt")

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
        for source_name, events in sorted(group_events_by_source(all_events).items()):
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


def save_events_html_report(
    all_events: list[dict],
    scan_date: str,
    days_ahead: int,
    output_path: Path | None = None,
) -> None:
    """Genera reports/latest_new_events.html usando la plantilla HTML configurada."""
    reports_dir = ROOT_DIR / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = output_path or (reports_dir / "latest_new_events.html")

    horizon_label = (datetime.now() + timedelta(days=days_ahead)).strftime("%d/%m/%Y")
    clean = [
        {
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
        }
        for ev in all_events
    ]

    html = (
        REPORT_HTML_TEMPLATE
        .replace("__EVENTS_JSON__",   json.dumps(clean, ensure_ascii=False))
        .replace("__SCAN_DATE__",     scan_date)
        .replace("__HORIZON_LABEL__", horizon_label)
        .replace("__DAYS_AHEAD__",    str(days_ahead))
    )
    report_path.write_text(html, encoding="utf-8")
    log.info(f"Informe HTML guardado en: {report_path}")
