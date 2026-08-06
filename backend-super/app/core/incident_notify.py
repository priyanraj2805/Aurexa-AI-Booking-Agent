"""Incident email notifications (Phase 6, task 5).

Sends SMTP notifications when an incident is created or escalates to
critical severity. Deliberately fail-open: any notification error is
logged and swallowed so incident CRUD is never blocked by email
infrastructure problems.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _recipients() -> list[str]:
    return [
        r.strip()
        for r in settings.INCIDENT_NOTIFY_TO.split(",")
        if r.strip()
    ]


def _send_plain_email(subject: str, body: str) -> None:
    recipients = _recipients()
    if not recipients:
        return
    if not settings.SMTP_HOST:
        logger.warning(
            "SMTP_HOST not set — incident notification skipped (%d recipient(s))",
            len(recipients),
        )
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    use_tls = settings.SMTP_PORT == 587
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as client:
        client.ehlo()
        if use_tls:
            client.starttls()
            client.ehlo()
        if settings.SMTP_USER:
            client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        client.send_message(message)


def notify_incident_created(
    incident_id: str, title: str, severity: str, component: str, message: str
) -> None:
    recipients = _recipients()
    if not recipients:
        return
    incident_url = f"{settings.FRONTEND_URL}/dashboard/incidents"
    body = (
        f"New incident filed on the DotStark platform.\n\n"
        f"Severity: {severity}\n"
        f"Component: {component}\n"
        f"Incident ID: {incident_id}\n"
        f"Title: {title}\n\n"
        f"Details:\n{message or '(none)'}\n\n"
        f"Manage: {incident_url}\n"
    )
    try:
        _send_plain_email(f"[DotStark] {severity.upper()} incident: {title}", body)
    except Exception as exc:  # fail-open: never block incident CRUD
        logger.exception("Incident notification email failed: %s", exc)


def notify_incident_escalated(
    incident_id: str, title: str, severity: str, component: str
) -> None:
    recipients = _recipients()
    if not recipients:
        return
    incident_url = f"{settings.FRONTEND_URL}/dashboard/incidents"
    body = (
        f"Incident escalated on the DotStark platform.\n\n"
        f"Severity: {severity}\n"
        f"Component: {component}\n"
        f"Incident ID: {incident_id}\n"
        f"Title: {title}\n\n"
        f"Manage: {incident_url}\n"
    )
    try:
        _send_plain_email(f"[DotStark] {severity.upper()} incident escalated: {title}", body)
    except Exception as exc:  # fail-open: never block incident CRUD
        logger.exception("Incident escalation email failed: %s", exc)
