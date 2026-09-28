"""
DotStark Email Sender
Uses the new design-system-aligned email templates.
"""
import anyio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from uuid import UUID
from app.core.config import settings
from app.core.email_delivery_service import send_email_with_failover
from app.core.email_templates import (
    render_otp_email,
    render_auth_otp_email,
    render_booking_confirmation_customer,
    render_booking_confirmation_admin,
    render_booking_cancellation,
)

logger = logging.getLogger(__name__)


def _send_email_sync(to_email: str, subject: str, body_html: str):
    """Synchronous SMTP worker for auth emails (no org context)."""
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning(
            f"SMTP credentials not configured. Skipping email to {to_email}. Subject: {subject}"
        )
        logger.info(
            f"--- MOCK EMAIL BODY ({to_email}) ---\n{body_html}\n-------------------"
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.EMAILS_FROM_EMAIL
    msg["To"] = to_email

    html_part = MIMEText(body_html, "html")
    msg.attach(html_part)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        logger.info(f"Email sent successfully to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        raise


async def send_otp_email(
    to_email: str,
    code: str,
    *,
    org_id: UUID,
    request_id: Optional[str] = None,
    delivery_id: Optional[UUID] = None,
    attempt_count: int = 1,
    idempotency_key: Optional[str] = None,
):
    """Sends a 6-digit OTP code to the user (booking flow)."""
    subject, body_html = render_otp_email(code)
    delivery = await send_email_with_failover(
        org_id=org_id,
        channel="otp",
        to_email=to_email,
        subject=subject,
        body_html=body_html,
        request_id=request_id,
        delivery_id=delivery_id,
        payload={
            "code": code,
            "subject": subject,
            "body_html": body_html,
            "idempotency_key": idempotency_key,
        },
        attempt_count=attempt_count,
    )
    return {"success": True, "status": "sent", "delivery_id": str(delivery)}





async def send_auth_otp_email(to_email: str, code: str, purpose: str):
    """Sends a verification OTP for admin-panel registration or password reset.

    Unlike `send_otp_email`, this does not require an org (the account may
    not exist yet during registration), so it bypasses the delivery-log
    pipeline and sends directly via SMTP in a worker thread.
    """
    subject, body_html = render_auth_otp_email(code, purpose)
    
    await anyio.to_thread.run_sync(
        _send_email_sync, to_email, subject, body_html, abandon_on_cancel=True
    )


async def send_confirmation_emails(
    customer_email: str,
    admin_email: str,
    booking_details: dict,
    *,
    org_id: UUID,
    request_id: Optional[str] = None,
    customer_delivery_id: Optional[UUID] = None,
    admin_delivery_id: Optional[UUID] = None,
    attempt_count: int = 1,
):
    """Sends confirmation emails to both customer and organization admin."""
    safe_details = {k: str(v) if v else "" for k, v in booking_details.items()}
    frontend_url = settings.FRONTEND_URL
    customer_email = str(customer_email or "").strip()
    admin_email = str(admin_email or "").strip()

    customer_subject, customer_body = render_booking_confirmation_customer(safe_details)
    booking_id = str(safe_details.get("booking_id") or "")
    confirmation_channel = "booking_confirmation"
    customer_idempotency_key = (
        f"{booking_id}:customer:{confirmation_channel}" if booking_id else None
    )
    idempotency_cache = booking_details.get("_delivery_idempotency_cache")
    if not isinstance(idempotency_cache, dict):
        idempotency_cache = {}
        booking_details["_delivery_idempotency_cache"] = idempotency_cache

    if customer_email and (
        not customer_idempotency_key or not idempotency_cache.get(customer_idempotency_key)
    ):
        await send_email_with_failover(
            org_id=org_id,
            channel="booking_confirmation",
            to_email=customer_email,
            subject=customer_subject,
            body_html=customer_body,
            request_id=request_id,
            delivery_id=customer_delivery_id,
            payload={
                "booking_details": booking_details,
                "subject": customer_subject,
                "body_html": customer_body,
                "idempotency_key": customer_idempotency_key,
            },
            attempt_count=attempt_count,
        )
        if customer_idempotency_key:
            idempotency_cache[customer_idempotency_key] = True

    admin_subject, admin_body = render_booking_confirmation_admin(
        safe_details, frontend_url=frontend_url
    )
    admin_idempotency_key = (
        f"{booking_id}:admin:{confirmation_channel}" if booking_id else None
    )
    if admin_email and (
        not admin_idempotency_key or not idempotency_cache.get(admin_idempotency_key)
    ):
        await send_email_with_failover(
            org_id=org_id,
            channel="booking_confirmation",
            to_email=admin_email,
            subject=admin_subject,
            body_html=admin_body,
            request_id=request_id,
            delivery_id=admin_delivery_id,
            payload={
                "booking_details": booking_details,
                "subject": admin_subject,
                "body_html": admin_body,
                "idempotency_key": admin_idempotency_key,
            },
            attempt_count=attempt_count,
        )
        if admin_idempotency_key:
            idempotency_cache[admin_idempotency_key] = True


async def send_cancellation_email(
    customer_email: str,
    customer_name: str,
    booking_details: dict,
    *,
    org_id: UUID,
    request_id: Optional[str] = None,
    delivery_id: Optional[UUID] = None,
    attempt_count: int = 1,
):
    """Sends a cancellation confirmation email to the customer."""
    customer_email = str(customer_email or "").strip()
    if not customer_email:
        raise ValueError("Email recipient is required")
    safe_details = {k: str(v) if v else "" for k, v in booking_details.items()}
    subject, body_html = render_booking_cancellation(customer_name, safe_details)
    booking_id = str(safe_details.get("booking_id") or "")
    cancellation_channel = "booking_cancellation"
    idempotency_key = (
        f"{booking_id}:customer:{cancellation_channel}" if booking_id else None
    )
    idempotency_cache = booking_details.get("_delivery_idempotency_cache")
    if not isinstance(idempotency_cache, dict):
        idempotency_cache = {}
        booking_details["_delivery_idempotency_cache"] = idempotency_cache
    if idempotency_key and idempotency_cache.get(idempotency_key):
        return
    await send_email_with_failover(
        org_id=org_id,
        channel="booking_cancellation",
        to_email=customer_email,
        subject=subject,
        body_html=body_html,
        request_id=request_id,
        delivery_id=delivery_id,
        payload={
            "booking_details": booking_details,
            "subject": subject,
            "body_html": body_html,
            "idempotency_key": idempotency_key,
        },
        attempt_count=attempt_count,
    )
    if idempotency_key:
        idempotency_cache[idempotency_key] = True