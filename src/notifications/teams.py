"""
notifications/teams.py
Construcción y envío de notificaciones a Microsoft Teams via webhook.
"""
import base64
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from config import DAYS_AHEAD, TEAMS_BODY_TEMPLATE, TEAMS_TITLE_TEMPLATE, TEAMS_WEBHOOK_URL
from events import group_events_by_source

log = logging.getLogger(__name__)

_MAX_PREVIEW_SOURCES = 4
_MAX_PREVIEW_EVENTS_PER_SOURCE = 3
_MAX_FLOW_EVENTS = 20
_MAX_WEBHOOK_BYTES = 28 * 1024
_TARGET_WEBHOOK_BYTES = 26 * 1024


def _trim(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _source_groups(new_events: list[dict]) -> list[tuple[str, list[dict]]]:
    grouped = group_events_by_source(new_events)
    return sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0].lower()))


def _is_power_automate_webhook(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return (
        "powerplatform.com" in host
        or "logic.azure.com" in host
        or "/automations/direct/workflows/" in path
        or "/workflows/" in path
    )


def _build_card_blocks(new_events: list[dict]) -> list[dict]:
    blocks: list[dict] = []
    grouped_sources = _source_groups(new_events)

    for source_name, events in grouped_sources[:_MAX_PREVIEW_SOURCES]:
        blocks.append({
            "type": "TextBlock",
            "text": source_name,
            "weight": "Bolder",
            "spacing": "Medium",
            "size": "Medium",
            "color": "Accent",
            "wrap": True,
        })
        for event in events[:_MAX_PREVIEW_EVENTS_PER_SOURCE]:
            price = event.get("price") or "—"
            location = event.get("location") or "—"
            date = event.get("date") or "—"
            title = _trim(event.get("title") or "Sin título", 110)
            facts = f"{date} · {location} · {price}"
            if event.get("url"):
                text = f"- [{title}]({event['url']})\n{facts}"
            else:
                text = f"- {title}\n{facts}"
            blocks.append({
                "type": "TextBlock",
                "text": text,
                "wrap": True,
                "spacing": "Small",
            })

    extra_sources = len(grouped_sources) - _MAX_PREVIEW_SOURCES
    if extra_sources > 0:
        blocks.append({
            "type": "TextBlock",
            "text": f"+ {extra_sources} fuente(s) adicional(es) en el adjunto HTML.",
            "wrap": True,
            "spacing": "Medium",
            "isSubtle": True,
        })

    return blocks


def build_teams_event_lines(new_events: list[dict]) -> str:
    lines = []
    for source_name, events in _source_groups(new_events)[:_MAX_PREVIEW_SOURCES]:
        lines.append(f"**{source_name}**")
        for ev in events[:_MAX_PREVIEW_EVENTS_PER_SOURCE]:
            line = (
                f"- {ev.get('date', '—')} | {ev.get('title', 'Sin título')} "
                f"| {ev.get('location', '—')} | {ev.get('price', '—')}"
            )
            url = ev.get("url")
            if url:
                line = f"{line}\n  {url}"
            lines.append(line)
    return "\n\n".join(lines)


def _payload_size_bytes(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build_teams_card(
    new_events: list[dict],
    scan_date: str,
    attachment_label: str = "Informe HTML incluido en el flujo",
) -> dict:
    title = TEAMS_TITLE_TEMPLATE.format(total_events=len(new_events)).strip()
    sources_count = len(group_events_by_source(new_events))
    body = TEAMS_BODY_TEMPLATE.format(
        scan_date=scan_date,
        total_events=len(new_events),
        sources_count=sources_count,
        days_ahead=DAYS_AHEAD,
        event_lines=build_teams_event_lines(new_events),
    ).strip()
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "Radar de Eventos Empresariales",
                "size": "Small",
                "weight": "Bolder",
                "color": "Accent",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": title,
                "size": "Large",
                "weight": "Bolder",
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Escaneo", "value": scan_date},
                    {"title": "Fuentes activas", "value": str(sources_count)},
                    {"title": "Ventana", "value": f"Próximos {DAYS_AHEAD} días"},
                    {"title": "Adjunto", "value": attachment_label},
                ],
            },
            {
                "type": "TextBlock",
                "text": body,
                "wrap": True,
                "spacing": "Medium",
            },
            *_build_card_blocks(new_events),
        ],
    }


def build_teams_message(
    new_events: list[dict],
    scan_date: str,
    attachment_label: str = "Informe HTML incluido en el flujo",
) -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": build_teams_card(
                    new_events,
                    scan_date,
                    attachment_label=attachment_label,
                ),
            }
        ],
    }


def build_teams_payload(
    new_events: list[dict],
    scan_date: str,
    report_html_path: str | Path | None = None,
) -> dict:
    title = TEAMS_TITLE_TEMPLATE.format(total_events=len(new_events)).strip()
    sources_count = len(group_events_by_source(new_events))
    body = TEAMS_BODY_TEMPLATE.format(
        scan_date=scan_date,
        total_events=len(new_events),
        sources_count=sources_count,
        days_ahead=DAYS_AHEAD,
        event_lines=build_teams_event_lines(new_events),
    ).strip()
    attachment_label = "Informe HTML incluido en el flujo"
    message = build_teams_message(new_events, scan_date, attachment_label=attachment_label)
    if not TEAMS_WEBHOOK_URL or not _is_power_automate_webhook(TEAMS_WEBHOOK_URL):
        return message

    payload = {
        "title": title,
        "summary_markdown": body,
        "scan_date": scan_date,
        "days_ahead": DAYS_AHEAD,
        "total_events": len(new_events),
        "sources_count": sources_count,
        "message": message,
        "adaptive_card": build_teams_card(
            new_events,
            scan_date,
            attachment_label=attachment_label,
        ),
        "events": [
            {
                "title": ev.get("title") or "Sin título",
                "date": ev.get("date") or "—",
                "time": ev.get("time") or "",
                "location": ev.get("location") or "—",
                "price": ev.get("price") or "—",
                "association": ev.get("association") or "—",
                "type": ev.get("type") or "otro",
                "url": ev.get("url") or "",
                "description": _trim(ev.get("description") or "", 280),
            }
            for ev in new_events[:_MAX_FLOW_EVENTS]
        ],
    }

    if report_html_path:
        report_path = Path(report_html_path)
        report_file = {
            "file_name": report_path.name,
            "content_type": "text/html",
            "content_base64": base64.b64encode(report_path.read_bytes()).decode("ascii"),
        }
        payload["report_file"] = report_file

        if _payload_size_bytes(payload) > _TARGET_WEBHOOK_BYTES:
            payload.pop("report_file", None)
            attachment_label = "Omitido: el HTML supera el límite de 28 KB del webhook"
            payload["report_file_omitted"] = {
                "file_name": report_file["file_name"],
                "reason": "payload_too_large",
                "original_size_bytes": report_path.stat().st_size,
            }

    payload["message"] = build_teams_message(
        new_events,
        scan_date,
        attachment_label=attachment_label,
    )
    payload["adaptive_card"] = build_teams_card(
        new_events,
        scan_date,
        attachment_label=attachment_label,
    )

    while _payload_size_bytes(payload) > _TARGET_WEBHOOK_BYTES and payload["events"]:
        payload["events"].pop()

    if _payload_size_bytes(payload) > _MAX_WEBHOOK_BYTES:
        payload["summary_markdown"] = (
            f"Escaneo: {scan_date}\n"
            f"Eventos detectados: {len(new_events)}\n"
            f"Fuentes con actividad: {sources_count}\n"
            f"Ventana: próximos {DAYS_AHEAD} días"
        )

    return payload


def send_teams(payload: dict) -> None:
    if not TEAMS_WEBHOOK_URL:
        raise ValueError("Falta la variable de entorno TEAMS_WEBHOOK_URL")
    response = httpx.post(TEAMS_WEBHOOK_URL, json=payload, timeout=20)
    response.raise_for_status()
    log.info("Notificación enviada a Teams/Workflows")
