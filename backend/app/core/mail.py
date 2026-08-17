import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape as html_escape
import anyio
import logging
from typing import Optional
from uuid import UUID
from app.core.config import settings
from app.core.email_delivery_service import send_email_with_failover

logger = logging.getLogger(__name__)


def _build_meeting_status_block(safe: dict[str, str], *, audience: str) -> str:
    meeting_type = safe.get("meeting_type_snapshot") or "OFFLINE"
    meeting_link_status = safe.get("meeting_link_status") or "NOT_REQUIRED"
    meeting_link_url = safe.get("meeting_link_url")
    meeting_link_provider = safe.get("meeting_link_provider") or "Meeting Provider"

    if meeting_type != "ONLINE":
        return """<div style=\"background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px; margin-bottom: 20px;\"><p style=\"margin: 0; color: #334155; font-size: 13px;\"><strong>Meeting Type:</strong> Offline</p></div>"""

    if meeting_link_status == "READY" and meeting_link_url:
        return f"""
<div style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 12px; padding: 16px; margin-bottom: 20px;">
    <p style="margin: 0 0 10px; color: #1e3a8a; font-size: 13px;"><strong>Meeting Type:</strong> Online ({meeting_link_provider})</p>
    <p style="margin: 0 0 12px; color: #1e40af; font-size: 13px;"><strong>Meeting link status:</strong> READY</p>
    <a href="{meeting_link_url}" style="display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none; padding: 10px 14px; border-radius: 8px; font-size: 13px; font-weight: 600;">Join Meeting</a>
</div>
        """

    if meeting_link_status == "UNAVAILABLE":
        guidance = (
            "Your meeting link is temporarily unavailable. Please retry shortly or contact support if this persists."
            if audience == "customer"
            else "Meeting link is temporarily unavailable. Please follow up with the customer and retry provider connection if needed."
        )
        return f"""
<div style="background: #fffbeb; border: 1px solid #fde68a; border-radius: 12px; padding: 16px; margin-bottom: 20px;">
    <p style="margin: 0 0 8px; color: #92400e; font-size: 13px;"><strong>Meeting Type:</strong> Online ({meeting_link_provider})</p>
    <p style="margin: 0 0 8px; color: #b45309; font-size: 13px;"><strong>Meeting link status:</strong> UNAVAILABLE</p>
    <p style="margin: 0; color: #92400e; font-size: 13px; line-height: 1.5;">{guidance}</p>
</div>
        """

    return f"""
<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; margin-bottom: 20px;">
    <p style="margin: 0 0 8px; color: #334155; font-size: 13px;"><strong>Meeting Type:</strong> Online ({meeting_link_provider})</p>
    <p style="margin: 0; color: #64748b; font-size: 13px;"><strong>Meeting link status:</strong> {meeting_link_status}</p>
</div>
    """


def _build_dual_time_block(safe: dict[str, str]) -> str:
    user_timezone = safe.get("user_timezone")
    user_slot_start_at = safe.get("user_slot_start_at")
    user_slot_end_at = safe.get("user_slot_end_at")
    org_timezone_snapshot = safe.get("org_timezone_snapshot")
    org_slot_start_at = safe.get("org_slot_start_at")
    org_slot_end_at = safe.get("org_slot_end_at")

    has_user = bool(user_timezone and user_slot_start_at and user_slot_end_at)
    has_org = bool(org_timezone_snapshot and org_slot_start_at and org_slot_end_at)

    if not has_user and not has_org:
        return ""

    user_row = (
        f'<tr><td style="padding: 6px 0; color: #374151; font-size: 13px;"><strong>User time ({user_timezone})</strong></td><td style="padding: 6px 0; color: #111827; font-size: 13px; text-align: right;">{user_slot_start_at} → {user_slot_end_at}</td></tr>'
        if has_user
        else ""
    )
    org_row = (
        f'<tr><td style="padding: 6px 0; color: #374151; font-size: 13px;"><strong>Business time ({org_timezone_snapshot})</strong></td><td style="padding: 6px 0; color: #111827; font-size: 13px; text-align: right;">{org_slot_start_at} → {org_slot_end_at}</td></tr>'
        if has_org
        else ""
    )

    return f"""
<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; margin-bottom: 20px;">
    <p style="margin: 0 0 10px; color: #111827; font-size: 13px;"><strong>Time Context</strong></p>
    <table style="width: 100%; border-collapse: collapse;">{user_row}{org_row}</table>
</div>
    """


def _build_cancellation_meeting_block(safe: dict[str, str]) -> str:
    meeting_type = safe.get("meeting_type_snapshot") or "OFFLINE"
    meeting_link_status = safe.get("meeting_link_status") or "NOT_REQUIRED"
    meeting_link_url = safe.get("meeting_link_url")
    meeting_link_provider = safe.get("meeting_link_provider") or "Meeting Provider"

    if meeting_type != "ONLINE":
        return """
<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px; margin: 0 auto 24px; max-width: 420px; text-align: left;">
    <p style="margin: 0; color: #334155; font-size: 13px;"><strong>Meeting Type:</strong> Offline</p>
</div>
        """

    if meeting_link_status == "READY" and meeting_link_url:
        return f"""
<div style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 12px; padding: 16px; margin: 0 auto 24px; max-width: 420px; text-align: left;">
    <p style="margin: 0 0 10px; color: #1e3a8a; font-size: 13px;"><strong>Meeting Type:</strong> Online ({meeting_link_provider})</p>
    <p style="margin: 0 0 12px; color: #1e40af; font-size: 13px;"><strong>Meeting link status:</strong> READY</p>
    <a href="{meeting_link_url}" style="display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none; padding: 10px 14px; border-radius: 8px; font-size: 13px; font-weight: 600;">Join Meeting</a>
</div>
        """

    if meeting_link_status in {"UNAVAILABLE", "FAILED"}:
        return f"""
<div style="background: #fffbeb; border: 1px solid #fde68a; border-radius: 12px; padding: 16px; margin: 0 auto 24px; max-width: 420px; text-align: left;">
    <p style="margin: 0 0 8px; color: #92400e; font-size: 13px;"><strong>Meeting Type:</strong> Online ({meeting_link_provider})</p>
    <p style="margin: 0 0 8px; color: #b45309; font-size: 13px;"><strong>Meeting link status:</strong> {meeting_link_status}</p>
    <p style="margin: 0; color: #92400e; font-size: 13px; line-height: 1.5;">Your meeting link is temporarily unavailable. Please retry shortly or contact support if this persists.</p>
</div>
        """

    return f"""
<div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px; margin: 0 auto 24px; max-width: 420px; text-align: left;">
    <p style="margin: 0 0 6px; color: #334155; font-size: 13px;"><strong>Meeting Type:</strong> Online ({meeting_link_provider})</p>
    <p style="margin: 0; color: #64748b; font-size: 13px;"><strong>Meeting link status:</strong> {meeting_link_status}</p>
</div>
    """


def _send_email_sync(to_email: str, subject: str, body_html: str):
    """Synchronous SMTP worker."""
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
    """Sends a 6-digit OTP code to the user."""
    subject = f"{code} - Your Verification Code"
    body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f8fafc;">
    <div style="max-width: 420px; margin: 40px auto; background: #ffffff; border-radius: 20px; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.08);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 40px 32px; text-align: center;">
            <div style="width: 64px; height: 64px; background: rgba(255,255,255,0.2); border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 16px;">
                <span style="font-size: 32px;">&#128272;</span>
            </div>
            <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.5px;">Verification Required</h1>
            <p style="color: rgba(255,255,255,0.85); margin: 8px 0 0; font-size: 14px;">Enter this code to continue</p>
        </div>
        
        <!-- Code Box -->
        <div style="padding: 40px 32px; text-align: center;">
            <div style="background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%); border: 2px dashed #e2e8f0; border-radius: 16px; padding: 28px 40px; margin-bottom: 24px; display: inline-block;">
                <span style="font-size: 42px; font-weight: 800; letter-spacing: 12px; color: #1e293b; font-family: 'SF Mono', 'Fira Code', Monaco, monospace; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;">{code}</span>
            </div>
            
            <div style="background: #fef3c7; border-radius: 10px; padding: 12px 16px; margin-bottom: 20px;">
                <p style="margin: 0; color: #92400e; font-size: 13px;">
                    <span style="margin-right: 6px;">&#9203;</span>
                    <strong>This code expires in 5 minutes</strong>
                </p>
            </div>
            
            <p style="color: #64748b; font-size: 13px; line-height: 1.6; margin: 0;">
                If you didn't request this code, please ignore this email. This is an automated message from the DotStark AI Booking System.
            </p>
        </div>
        
        <!-- Footer -->
        <div style="background: #f8fafc; padding: 20px 32px; border-top: 1px solid #e2e8f0;">
            <p style="color: #94a3b8; font-size: 11px; margin: 0; text-align: center;">
                &#169; {2026} DotStark AI Booking System
            </p>
        </div>
    </div>
</body>
</html>
    """
    await send_email_with_failover(
        org_id=org_id,
        channel="otp",
        to_email=to_email,
        subject=subject,
        body_html=body,
        request_id=request_id,
        delivery_id=delivery_id,
        payload={
            "code": code,
            "subject": subject,
            "body_html": body,
            "idempotency_key": idempotency_key,
        },
        attempt_count=attempt_count,
    )


async def send_auth_otp_email(to_email: str, code: str, purpose: str):
    """Sends a verification OTP for admin-panel registration or password reset.

    Unlike `send_otp_email`, this does not require an org (the account may
    not exist yet during registration), so it bypasses the delivery-log
    pipeline and sends directly via SMTP in a worker thread.
    """
    if purpose == "register":
        headline = "Verify Your Email"
        subtitle = "Enter this code to finish creating your account"
        subject = f"{code} - Verify your email"
    elif purpose == "password_reset":
        headline = "Reset Your Password"
        subtitle = "Enter this code to set a new password"
        subject = f"{code} - Password Reset Code"
    else:
        headline = "Sign-In Verification"
        subtitle = "Enter this code to confirm it's really you"
        subject = f"{code} - Your Sign-In Code"

    body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f8fafc;">
    <div style="max-width: 420px; margin: 40px auto; background: #ffffff; border-radius: 20px; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.08);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #ff6b00 0%, #e55f00 100%); padding: 40px 32px; text-align: center;">
            <div style="width: 64px; height: 64px; background: rgba(255,255,255,0.2); border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 16px;">
                <span style="font-size: 32px;">&#128272;</span>
            </div>
            <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.5px;">{headline}</h1>
            <p style="color: rgba(255,255,255,0.85); margin: 8px 0 0; font-size: 14px;">{subtitle}</p>
        </div>

        <!-- Code Box -->
        <div style="padding: 40px 32px; text-align: center;">
            <div style="background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%); border: 2px dashed #e2e8f0; border-radius: 16px; padding: 28px 40px; margin-bottom: 24px; display: inline-block;">
                <span style="font-size: 42px; font-weight: 800; letter-spacing: 12px; color: #1e293b; font-family: 'SF Mono', 'Fira Code', Monaco, monospace; background: linear-gradient(135deg, #ff6b00 0%, #e55f00 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;">{code}</span>
            </div>

            <div style="background: #fef3c7; border-radius: 10px; padding: 12px 16px; margin-bottom: 20px;">
                <p style="margin: 0; color: #92400e; font-size: 13px;">
                    <span style="margin-right: 6px;">&#9203;</span>
                    <strong>This code expires in 15 minutes</strong>
                </p>
            </div>

            <p style="color: #64748b; font-size: 13px; line-height: 1.6; margin: 0;">
                If you didn't request this code, please ignore this email. This is an automated message from the DotStark AI Booking System.
            </p>
        </div>

        <!-- Footer -->
        <div style="background: #f8fafc; padding: 20px 32px; border-top: 1px solid #e2e8f0;">
            <p style="color: #94a3b8; font-size: 11px; margin: 0; text-align: center;">
                &#169; 2026 DotStark AI Booking System
            </p>
        </div>
    </div>
</body>
</html>
    """
    await anyio.to_thread.run_sync(
        _send_email_sync, to_email, subject, body, abandon_on_cancel=True
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
    safe = {k: html_escape(str(v)) if v else "" for k, v in booking_details.items()}

    name = safe.get("name", "Customer")
    email = safe.get("email", "")
    phone = safe.get("phone", "Not provided")
    date = safe.get("date", "TBD")
    time = safe.get("time", "TBD")
    source = safe.get("source_channel", "Web Widget")
    meeting_section_customer = _build_meeting_status_block(safe, audience="customer")
    meeting_section_admin = _build_meeting_status_block(safe, audience="admin")
    dual_time_section = _build_dual_time_block(safe)

    source_emoji = "&#128187;" if source == "Web Widget" else "&#128172;"

    booking_id = str(safe.get("booking_id") or "")
    confirmation_channel = "booking_confirmation"
    customer_idempotency_key = (
        f"{booking_id}:customer:{confirmation_channel}" if booking_id else None
    )
    admin_idempotency_key = (
        f"{booking_id}:admin:{confirmation_channel}" if booking_id else None
    )
    idempotency_cache = booking_details.get("_delivery_idempotency_cache")
    if not isinstance(idempotency_cache, dict):
        idempotency_cache = {}
        booking_details["_delivery_idempotency_cache"] = idempotency_cache

    # Customer Email
    customer_subject = "✔ Booking Confirmed!"
    customer_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #ecfdf5;">
    <div style="max-width: 520px; margin: 40px auto; background: #ffffff; border-radius: 24px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.1);">
        <!-- Success Header -->
        <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 48px 32px; text-align: center; position: relative; overflow: hidden;">
            <div style="position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, transparent 60%);"></div>
            <div style="width: 80px; height: 80px; background: rgba(255,255,255,0.2); border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 20px; border: 3px solid rgba(255,255,255,0.3);">
                <span style="font-size: 40px;">&#10004;</span>
            </div>
            <h1 style="color: #ffffff; margin: 0; font-size: 32px; font-weight: 700;">You're All Set!</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 12px 0 0; font-size: 16px;">Your appointment has been confirmed</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 32px;">
            <p style="color: #374151; margin: 0 0 8px; font-size: 16px;">Hello <strong style="color: #059669;">{name}</strong>,</p>
            <p style="color: #6b7280; margin: 0 0 32px; font-size: 15px; line-height: 1.6;">Great news! Your appointment has been successfully booked. We can't wait to see you!</p>
            
            <!-- Appointment Card -->
            <div style="background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%); border: 1px solid #a7f3d0; border-radius: 20px; padding: 28px; margin-bottom: 28px;">
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px dashed #a7f3d0;">
                    <div style="width: 48px; height: 48px; background: #10b981; border-radius: 12px; display: flex; align-items: center; justify-content: center;">
                        <span style="font-size: 24px;">&#128197;</span>
                    </div>
                    <div>
                        <p style="margin: 0; color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Appointment Details</p>
                        <p style="margin: 4px 0 0; color: #1f2937; font-size: 18px; font-weight: 600;">{date}</p>
                    </div>
                </div>
                
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                    <div>
                        <p style="margin: 0 0 4px; color: #6b7280; font-size: 12px;">&#9202; Time</p>
                        <p style="margin: 0; color: #1f2937; font-size: 15px; font-weight: 600;">{time}</p>
                    </div>
                    <div>
                        <p style="margin: 0 0 4px; color: #6b7280; font-size: 12px;">{source_emoji} Booked via</p>
                        <p style="margin: 0; color: #1f2937; font-size: 15px; font-weight: 600;">{source}</p>
                    </div>
                </div>
            </div>
            {meeting_section_customer}
            {dual_time_section}
            
            <!-- Info Box -->
            <div style="background: #fef3c7; border-radius: 12px; padding: 16px 20px; margin-bottom: 24px;">
                <p style="margin: 0; color: #92400e; font-size: 13px; line-height: 1.5;">
                    <span style="margin-right: 6px;">&#9432;</span>
                    We'll send you a reminder before your appointment. Need to make changes? Contact us directly.
                </p>
            </div>
            
            <p style="color: #9ca3af; font-size: 12px; margin: 0; text-align: center;">
                This is an automated booking confirmation from DotStark AI
            </p>
        </div>
    </div>
</body>
</html>
    """
    if not customer_idempotency_key or not idempotency_cache.get(customer_idempotency_key):
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

    # Admin/Org Email
    admin_subject = f"📅 New Booking: {name} - {date}"
    frontend_url = settings.FRONTEND_URL
    admin_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #eff6ff;">
    <div style="max-width: 560px; margin: 40px auto; background: #ffffff; border-radius: 24px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.1);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); padding: 40px 32px; text-align: center;">
            <div style="width: 72px; height: 72px; background: rgba(255,255,255,0.2); border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 16px;">
                <span style="font-size: 36px;">&#128197;</span>
            </div>
            <h1 style="color: #ffffff; margin: 0; font-size: 26px; font-weight: 700;">New Booking Received!</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0; font-size: 14px;">via DotStark AI Booking System</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 36px 32px;">
            <p style="color: #374151; margin: 0 0 28px; font-size: 15px; line-height: 1.6;">
                A new appointment has been booked through your AI Assistant. Details below:
            </p>
            
            <!-- Customer Card -->
            <div style="background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%); border: 1px solid #bfdbfe; border-radius: 16px; padding: 24px; margin-bottom: 20px;">
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px;">
                    <div style="width: 44px; height: 44px; background: #3b82f6; border-radius: 50%; display: flex; align-items: center; justify-content: center;">
                        <span style="font-size: 20px;">&#128100;</span>
                    </div>
                    <div>
                        <p style="margin: 0; color: #6b7280; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Customer</p>
                        <p style="margin: 4px 0 0; color: #1f2937; font-size: 18px; font-weight: 600;">{name}</p>
                    </div>
                    <div style="margin-left: auto;">
                        <span style="background: #dbeafe; color: #1d4ed8; padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 500;">New Customer</span>
                    </div>
                </div>
                
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                    <div style="background: white; border-radius: 10px; padding: 12px;">
                        <p style="margin: 0 0 4px; color: #6b7280; font-size: 11px;">&#9993; Email</p>
                        <p style="margin: 0; color: #1f2937; font-size: 13px; font-weight: 500; word-break: break-all;">{email}</p>
                    </div>
                    <div style="background: white; border-radius: 10px; padding: 12px;">
                        <p style="margin: 0 0 4px; color: #6b7280; font-size: 11px;">&#128222; Phone</p>
                        <p style="margin: 0; color: #1f2937; font-size: 13px; font-weight: 500;">{phone}</p>
                    </div>
                </div>
            </div>
            
            <!-- Appointment Card -->
            <div style="background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%); border: 1px solid #86efac; border-radius: 16px; padding: 24px; margin-bottom: 28px;">
                <div style="display: flex; align-items: center; gap: 16px;">
                    <div style="text-align: center;">
                        <p style="margin: 0; color: #059669; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Date</p>
                        <p style="margin: 6px 0 0; color: #1f2937; font-size: 20px; font-weight: 700;">{date}</p>
                    </div>
                    <div style="width: 1px; height: 40px; background: #86efac;"></div>
                    <div style="text-align: center;">
                        <p style="margin: 0; color: #059669; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Time</p>
                        <p style="margin: 6px 0 0; color: #1f2937; font-size: 20px; font-weight: 700;">{time}</p>
                    </div>
                    <div style="width: 1px; height: 40px; background: #86efac;"></div>
                    <div style="text-align: center;">
                        <p style="margin: 0; color: #059669; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;">Via</p>
                        <p style="margin: 6px 0 0; color: #1f2937; font-size: 14px; font-weight: 600;">{source}</p>
                    </div>
                </div>
            </div>
            {meeting_section_admin}
            {dual_time_section}
            
            <!-- CTA Button -->
            <div style="text-align: center;">
                <a href="{frontend_url}/dashboard/bookings" style="display: inline-block; background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); color: #ffffff; text-decoration: none; padding: 16px 32px; border-radius: 12px; font-weight: 600; font-size: 15px; box-shadow: 0 4px 14px rgba(59, 130, 246, 0.4);">
                    View All Bookings &#8594;
                </a>
            </div>
        </div>
        
        <!-- Footer -->
        <div style="background: #f8fafc; padding: 20px 32px; border-top: 1px solid #e2e8f0;">
            <p style="color: #94a3b8; font-size: 11px; margin: 0; text-align: center;">
                &#169; {2026} DotStark AI Booking System
            </p>
        </div>
    </div>
</body>
</html>
    """
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
    safe = {k: html_escape(str(v)) if v else "" for k, v in booking_details.items()}
    safe_name = html_escape(customer_name)
    meeting_section = _build_cancellation_meeting_block(safe)

    subject = "Appointment Cancelled"
    body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #fef2f2;">
    <div style="max-width: 520px; margin: 40px auto; background: #ffffff; border-radius: 24px; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,0.1);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); padding: 48px 32px; text-align: center;">
            <div style="width: 80px; height: 80px; background: rgba(255,255,255,0.2); border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 20px; border: 3px solid rgba(255,255,255,0.3);">
                <span style="font-size: 40px;">&#10006;</span>
            </div>
            <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 700;">Appointment Cancelled</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 12px 0 0; font-size: 15px;">Your booking has been cancelled</p>
        </div>
        
        <!-- Content -->
        <div style="padding: 40px 32px; text-align: center;">
            <p style="color: #374151; margin: 0 0 8px; font-size: 16px;">Hello <strong>{safe_name}</strong>,</p>
            <p style="color: #6b7280; margin: 0 0 32px; font-size: 15px; line-height: 1.6;">Your appointment has been cancelled as requested. We're sorry to see you go!</p>
            
            <!-- Cancelled Details -->
            <div style="background: #fef2f2; border: 1px solid #fecaca; border-radius: 16px; padding: 24px; margin-bottom: 28px; display: inline-block; min-width: 200px;">
                <p style="margin: 0 0 8px; color: #ef4444; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Former Appointment</p>
                <p style="margin: 0; color: #1f2937; font-size: 18px; font-weight: 600;">{safe.get("date", "N/A")}</p>
                <p style="margin: 8px 0 0; color: #6b7280; font-size: 14px;">{safe.get("time", "")}</p>
            </div>
            {meeting_section}
            
            <p style="color: #374151; font-size: 14px; margin: 0 0 16px; line-height: 1.6;">
                If you'd like to rebook, feel free to start a new conversation anytime. We'd love to see you again!
            </p>
            
            <p style="color: #9ca3af; font-size: 12px; margin: 0;">
                This is an automated cancellation notice from DotStark AI
            </p>
        </div>
        
        <!-- Footer -->
        <div style="background: #fef2f2; padding: 20px 32px; border-top: 1px solid #fecaca;">
            <p style="color: #fca5a5; font-size: 11px; margin: 0; text-align: center;">
                &#169; {2026} DotStark AI Booking System
            </p>
        </div>
    </div>
</body>
</html>
    """
    await send_email_with_failover(
        org_id=org_id,
        channel="booking_cancellation",
        to_email=customer_email,
        subject=subject,
        body_html=body,
        request_id=request_id,
        delivery_id=delivery_id,
        payload={
            "booking_details": booking_details,
            "subject": subject,
            "body_html": body,
        },
        attempt_count=attempt_count,
    )
