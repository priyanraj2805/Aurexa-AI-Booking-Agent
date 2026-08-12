"""Org-admin consent notifications (Phase 13 OTP + Phase 15 consent links).

Sends the consent OTP (peek) or a 24-hour confirmation link (impersonation)
to the org admin whose consent the super admin needs. Deliberately fail-open
on the SMTP send itself (the send outcome is reported to the caller) — but
the OTP/link is NEVER returned to the caller; the caller fails the request
when SMTP is not configured or delivery fails, so the code can only ever
reach the org admin's mailbox.
"""
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

PURPOSE_LABELS = {
    "impersonation": "impersonate a user in your organization",
    "peek": "review a support conversation in your organization",
}


def _smtp_available() -> bool:
    return bool(settings.SMTP_HOST)


def _send(message: EmailMessage) -> bool:
    if not _smtp_available():
        logger.warning(
            "SMTP_HOST not set — consent email to %s skipped (fail-closed)",
            message["To"],
        )
        return False
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
    except Exception as exc:
        logger.exception("Consent email failed: %s", exc)
        return False


def send_otp_email(to_email: str, org_name: str, otp: str, purpose: str) -> bool:
    """Send the consent OTP. Returns True if the SMTP send succeeded.

    Raises nothing — the caller decides whether a failed send must fail the
    request (fail-closed is enforced by the caller when SMTP is absent).
    """
    action = PURPOSE_LABELS.get(purpose, purpose)
    message = EmailMessage()
    message["Subject"] = (
        f"[DotStark] Consent code: {action} in {org_name}"
    )
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message.set_content(
        f"DotStark — consent code request\n\n"
        f"A super admin has requested to {action}.\n\n"
        f"Your one-time code is: {otp}\n\n"
        f"This code expires in {settings.OTP_TTL_MINUTES} minutes and can be "
        f"used only once. If you did not expect this request, you can ignore "
        f"this email — the request will expire on its own.\n"
    )
    return _send(message)


def send_confirmation_link_email(
    to_email: str,
    org_name: str,
    user_email: str,
    link: str,
    ttl_hours: int,
) -> bool:
    """Send the 24-hour impersonation consent link. Returns True on success.

    The link embeds a single-use bearer token; the page it opens requires the
    org admin to click "I confirm" before the impersonation is approved.
    """
    message = EmailMessage()
    message["Subject"] = f"[DotStark] Impersonation access request for {org_name}"
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message.set_content(
        f"DotStark — impersonation access request\n\n"
        f"A super admin is requesting to impersonate {user_email} in "
        f"{org_name}.\n\n"
        f"To approve this request, open the confirmation link below and click "
        f"\"I Confirm\" on the page:\n\n{link}\n\n"
        f"This link is valid for {ttl_hours} hours and can be used only once. "
        f"If you did not expect this request, you can ignore this email and "
        f"the request will expire on its own.\n"
    )
    return _send(message)


def send_peek_confirmation_link_email(
    to_email: str,
    org_name: str,
    link: str,
    ttl_hours: int,
    channel_user_id: str | None = None,
) -> bool:
    """Send the 24-hour transcript-peek consent link. Returns True on success.

    The link embeds a single-use bearer token; the page it opens requires the
    org admin to click "I Confirm" before conversation transcripts in the
    organization are unlocked for the requesting super admin.
    """
    detail = (
        f" about a conversation with <{channel_user_id}>"
        if channel_user_id
        else ""
    )
    message = EmailMessage()
    message["Subject"] = f"[DotStark] Transcript access request for {org_name}"
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message.set_content(
        f"DotStark — transcript access request\n\n"
        f"A super admin is requesting permission to review a support "
        f"conversation{detail} in {org_name}.\n\n"
        f"To approve this request, open the confirmation link below and click "
        f"\"I Confirm\" on the page:\n\n{link}\n\n"
        f"Once you confirm, the super admin can open read-only transcripts in "
        f"{org_name}. This link is valid for {ttl_hours} hours and can be used "
        f"only once. If you did not expect this request, you can ignore this "
        f"email and the request will expire on its own.\n"
    )
    return _send(message)
