"""
notifications/__init__.py
Punto de entrada unificado para el envío de notificaciones.
"""
from config import NOTIFICATION_CHANNEL


def send_notification(new_events: list[dict], scan_date: str, today) -> None:
    """Envía la notificación al canal configurado (teams | email | none)."""
    if NOTIFICATION_CHANNEL == "none":
        import logging
        logging.getLogger(__name__).info(
            "Notificación omitida (notification_channel=none)"
        )
        return

    if NOTIFICATION_CHANNEL == "teams":
        from notifications.teams import build_teams_payload, send_teams
        send_teams(build_teams_payload(new_events, scan_date))
        return

    if NOTIFICATION_CHANNEL == "email":
        from notifications.email import (
            build_email_html,
            build_email_plain,
            build_email_subject,
            send_email,
        )
        from config import DAYS_AHEAD

        subject = build_email_subject(len(new_events), today)
        html = build_email_html(new_events, scan_date, DAYS_AHEAD)
        plain = build_email_plain(new_events, scan_date)
        send_email(subject, html, plain)
        return

    raise ValueError(
        f"Canal de notificación no soportado: '{NOTIFICATION_CHANNEL}'. "
        "Valores válidos: teams | email"
    )
