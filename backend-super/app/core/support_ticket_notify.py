"""Support ticket email notifications (Phase 12).

Notifies the org admin who filed a ticket when the super admin team changes
its status or replies. Deliberately fail-open: SMTP problems log and are
ignored so triage never blocks on email delivery.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_ticket_status_email(
    to_email: str,
    org_name: str,
    subject: str,
    status: str,
    reply: str | None = None,
) -> bool:
    """Send the status update to the ticket reporter. Returns True when the
    send succeeded (or was skipped cleanly because no SMTP is configured)."""
    if not settings.SMTP_HOST:
        logger.warning(
            "SMTP_HOST not set — support ticket update email to %s skipped "
            "(ticket subject: %r, status: %s)",
            to_email,
            subject,
            status,
        )
        return False

    message = EmailMessage()
    message["Subject"] = f"[DotStark Support] {subject} — {status.replace('_', ' ').title()}"
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    body = (
        f"DotStark Support — your request has been updated\n\n"
        f"Organization: {org_name}\n"
        f"Subject: {subject}\n"
        f"Status: {status.replace('_', ' ').title()}\n"
    )
    if reply:
        body += f"\nReply from our team:\n{reply}\n"
    body += (
        "\nYou can track this request in your DotStark admin dashboard "
        "under Support.\n"
    )
    message.set_content(body)

    use_tls = settings.SMTP_PORT == 587
    try:
        with smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=10
        ) as client:
            client.ehlo()
            if use_tls:
                client.starttls()
                client.ehlo()
            if settings.SMTP_USER:
                client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            client.send_message(message)
        return True
    except Exception as exc:  # fail-open
        logger.exception("Support ticket email failed: %s", exc)
        return False
