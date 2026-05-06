"""
notifications/email.py
Construcción y envío de notificaciones por email via Microsoft Graph API (OAuth2).
"""
import base64
import logging
from datetime import datetime
from pathlib import Path

import httpx

from config import (
    AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET,
    AZURE_TENANT_ID,
    DAYS_AHEAD,
    EMAIL_FROM,
    EMAIL_HTML_TEMPLATE,
    EMAIL_PLAIN_TEMPLATE,
    EMAIL_SUBJECT_TEMPLATE,
    EMAIL_TO,
)
from events import group_events_by_source

log = logging.getLogger(__name__)


def build_email_html(new_events: list[dict], scan_date: str) -> str:
    return EMAIL_HTML_TEMPLATE.format(
        scan_date=scan_date,
        total_events=len(new_events),
        sources_count=len(group_events_by_source(new_events)),
    )


def build_email_plain(new_events: list[dict], scan_date: str) -> str:
    lines: list[str] = []
    for ev in new_events:
        lines.extend([
            f"[{ev.get('type', '').upper()}] {ev.get('title', '')}",
            f"  Fecha:      {ev.get('date', '—')}",
            f"  Lugar:      {ev.get('location', '—')}",
            f"  Precio:     {ev.get('price', '—')}",
            f"  Organiza:   {ev.get('association', '—')}",
            f"  Descripcion:{ev.get('description', '')}",
            f"  Link:       {ev.get('url', '—')}",
            "",
        ])
    return EMAIL_PLAIN_TEMPLATE.format(
        scan_date=scan_date,
        total_events=len(new_events),
        sources_count=len(group_events_by_source(new_events)),
        days_ahead=DAYS_AHEAD,
        event_lines="\n".join(lines).strip(),
    )


def build_email_subject(total_events: int, today: datetime) -> str:
    return EMAIL_SUBJECT_TEMPLATE.format(
        total_events=total_events,
        today_date=today.strftime("%d/%m/%Y"),
    ).strip()


def _get_graph_token() -> str:
    if not AZURE_TENANT_ID or not AZURE_CLIENT_ID or not AZURE_CLIENT_SECRET:
        raise ValueError(
            "Faltan variables de entorno: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET"
        )
    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
    resp = httpx.post(url, data={
        "grant_type":    "client_credentials",
        "client_id":     AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
    }, timeout=20)
    resp.raise_for_status()
    return resp.json()["access_token"]


def send_email(
    subject: str,
    html_body: str,
    plain_body: str,
    attachment_path: Path | None = None,
) -> None:
    if not EMAIL_FROM or not EMAIL_TO:
        raise ValueError("Faltan variables de entorno: EMAIL_FROM, EMAIL_TO")

    recipients = [r.strip() for r in EMAIL_TO.split(",") if r.strip()]
    if not recipients:
        log.warning("No hay destinatarios configurados en EMAIL_TO")
        return

    token = _get_graph_token()

    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
    }

    if attachment_path and attachment_path.exists():
        content_bytes = base64.b64encode(attachment_path.read_bytes()).decode()
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": attachment_path.name,
                "contentType": "text/html",
                "contentBytes": content_bytes,
            }
        ]
        log.info("Adjuntando %s (%.1f KB)", attachment_path.name, attachment_path.stat().st_size / 1024)

    payload = {"message": message, "saveToSentItems": False}
    url = f"https://graph.microsoft.com/v1.0/users/{EMAIL_FROM}/sendMail"
    resp = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    log.info("Email enviado a %d destinatario(s) via Graph API", len(recipients))
