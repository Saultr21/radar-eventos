"""
notifications/email.py
Construcción y envío de notificaciones por email (HTML + texto plano).
"""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    DAYS_AHEAD,
    EMAIL_FROM,
    EMAIL_HTML_TEMPLATE,
    EMAIL_PASSWORD,
    EMAIL_PLAIN_TEMPLATE,
    EMAIL_SUBJECT_TEMPLATE,
    EMAIL_TO,
    SMTP_HOST,
    SMTP_PORT,
)
from events import group_events_by_source

log = logging.getLogger(__name__)

_CAT_COLORS: dict[str, tuple[str, str]] = {
    "formacion":  ("#EAF3DE", "#27500A"),
    "networking": ("#FAEEDA", "#633806"),
    "jornada":    ("#EEEDFE", "#3C3489"),
    "feria":      ("#E6F1FB", "#0C447C"),
    "mision":     ("#F1EFE8", "#444441"),
    "evento":     ("#E6F1FB", "#0C447C"),
    "otro":       ("#F1EFE8", "#444441"),
}


def _badge(etype: str) -> str:
    bg, color = _CAT_COLORS.get(etype, ("#F1EFE8", "#444441"))
    return (
        f'<span style="background:{bg};color:{color};font-size:11px;'
        f'padding:2px 8px;border-radius:20px;font-weight:500">{etype}</span>'
    )


def build_email_rows(new_events: list[dict]) -> str:
    """Genera las filas HTML de la tabla del email."""
    rows = ""
    for src, events in sorted(group_events_by_source(new_events).items()):
        rows += f"""
        <tr>
          <td colspan="5" style="padding:16px 0 6px;font-size:13px;font-weight:600;
              color:#1a1a1a;border-bottom:1px solid #e5e5e5">
            {src}
          </td>
        </tr>"""
        for ev in events:
            url = ev.get("url", "#")
            title_link = (
                f'<a href="{url}" style="color:#185FA5;text-decoration:none;font-weight:500">'
                f'{ev.get("title", "Sin título")}</a>'
                if url and url != "#"
                else f'<strong>{ev.get("title", "Sin título")}</strong>'
            )
            deadline = ev.get("deadline", "")
            deadline_cell = (
                f'<span style="color:#854F0B;font-size:11px">⏰ Inscr. hasta {deadline}</span>'
                if deadline
                else ""
            )
            rows += f"""
        <tr style="border-bottom:0.5px solid #f0f0f0">
          <td style="padding:10px 12px 10px 0;font-size:13px;vertical-align:top;width:90px;
              color:#555;white-space:nowrap">{ev.get("date", "—")}</td>
          <td style="padding:10px 12px 10px 0;vertical-align:top">
            {title_link}
            <div style="font-size:12px;color:#666;margin-top:3px">{ev.get("description", "")}</div>
            {f'<div style="margin-top:3px">{deadline_cell}</div>' if deadline_cell else ""}
          </td>
          <td style="padding:10px 8px;vertical-align:top;white-space:nowrap">
            {_badge(ev.get("type", "otro"))}
          </td>
          <td style="padding:10px 8px;font-size:12px;color:#555;vertical-align:top;
              white-space:nowrap">📍 {ev.get("location", "—")}</td>
          <td style="padding:10px 0;font-size:12px;color:#555;vertical-align:top;
              white-space:nowrap">{ev.get("price", "—")}</td>
        </tr>"""
    return rows


def build_email_html(new_events: list[dict], scan_date: str, days_ahead: int) -> str:
    rows = build_email_rows(new_events)
    rows_html = rows or (
        '<tr><td style="padding:24px 0;text-align:center;color:#888;font-size:14px">'
        "No se han detectado eventos nuevos esta semana.</td></tr>"
    )
    return EMAIL_HTML_TEMPLATE.format(
        scan_date=scan_date,
        days_ahead=days_ahead,
        total_events=len(new_events),
        sources_count=len(group_events_by_source(new_events)),
        rows_html=rows_html,
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


def send_email(subject: str, html_body: str, plain_body: str) -> None:
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        raise ValueError(
            "Faltan variables de entorno para email: EMAIL_FROM, EMAIL_PASSWORD o EMAIL_TO"
        )

    recipients = [r.strip() for r in EMAIL_TO.split(",") if r.strip()]
    if not recipients:
        log.warning("No hay destinatarios configurados en EMAIL_TO")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())

    log.info(f"Email enviado a {len(recipients)} destinatario(s)")
