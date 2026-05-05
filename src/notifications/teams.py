"""
notifications/teams.py
Construcción y envío de notificaciones a Microsoft Teams via webhook.
"""
import logging

import httpx

from config import DAYS_AHEAD, TEAMS_BODY_TEMPLATE, TEAMS_TITLE_TEMPLATE, TEAMS_WEBHOOK_URL
from events import group_events_by_source

log = logging.getLogger(__name__)


def build_teams_event_lines(new_events: list[dict]) -> str:
    lines = []
    for ev in new_events:
        line = (
            f"- {ev.get('date', '—')} | {ev.get('association', '—')} "
            f"| {ev.get('title', 'Sin titulo')} "
            f"| {ev.get('location', '—')} | {ev.get('price', '—')}"
        )
        url = ev.get("url")
        if url:
            line = f"{line}\n  {url}"
        lines.append(line)
    return "\n\n".join(lines)


def build_teams_payload(new_events: list[dict], scan_date: str) -> dict:
    title = TEAMS_TITLE_TEMPLATE.format(total_events=len(new_events)).strip()
    body = TEAMS_BODY_TEMPLATE.format(
        scan_date=scan_date,
        total_events=len(new_events),
        sources_count=len(group_events_by_source(new_events)),
        days_ahead=DAYS_AHEAD,
        event_lines=build_teams_event_lines(new_events),
    ).strip()
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": title,
        "themeColor": "185FA5",
        "title": title,
        "text": body,
    }


def send_teams(payload: dict) -> None:
    if not TEAMS_WEBHOOK_URL:
        raise ValueError("Falta la variable de entorno TEAMS_WEBHOOK_URL")
    response = httpx.post(TEAMS_WEBHOOK_URL, json=payload, timeout=20)
    response.raise_for_status()
    log.info("Notificación enviada a Teams")
