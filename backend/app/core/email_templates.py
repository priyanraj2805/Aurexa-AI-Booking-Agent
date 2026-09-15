"""
DotStark Email Templates v2
Clean, minimal, professional design.
"""
from __future__ import annotations
import html
from typing import Optional
from app.core.email_design_tokens import tokens, TEMPLATE_THEMES


def _esc(value: str) -> str:
    """HTML escape."""
    return html.escape(str(value)) if value else ""


def _base(title: str, preheader: str, content: str, *, header_color: str = "#ef5d25", header_icon: str = "", footer: str = "") -> str:
    """Minimal base template. Everything inline for email client compat."""
    footer = footer or "DotStark AI Booking System"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)}</title>
<!--[if mso]>
<noscript><xml>
<o:OfficeDocumentSettings>
<o:PixelsPerInch>96</o:PixelsPerInch>
</o:OfficeDocumentSettings>
</xml></noscript>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#f4f4f5;">
<tr><td align="center" style="padding:40px 16px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="480" style="max-width:480px;background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">

<!-- HEADER -->
<tr><td style="background-color:{header_color};padding:32px 32px 28px;text-align:center;">
{f'<div style="width:48px;height:48px;margin:0 auto 12px;background:rgba(255,255,255,0.2);border-radius:50%;line-height:48px;font-size:24px;color:#fff;">{header_icon}</div>' if header_icon else ''}
<h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.3px;">{_esc(title)}</h1>
</td></tr>

<!-- CONTENT -->
<tr><td style="padding:28px 32px 32px;">
{content}
</td></tr>

<!-- FOOTER -->
<tr><td style="padding:16px 32px;border-top:1px solid #e5e5e5;text-align:center;">
<p style="margin:0;font-size:11px;color:#a1a1aa;">{_esc(footer)}</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _divider() -> str:
    return '<hr style="border:none;border-top:1px solid #e5e5e5;margin:20px 0;">'


def _label_value(label: str, value: str) -> str:
    return f'<td style="width:50%;padding:8px 0;"><p style="margin:0 0 2px;font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;">{_esc(label)}</p><p style="margin:0;font-size:14px;font-weight:600;color:#111111;">{_esc(value)}</p></td>'


def _status_pill(text: str, color: str, bg: str) -> str:
    return f'<span style="display:inline-block;padding:3px 10px;border-radius:9999px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;background-color:{bg};color:{color};">{_esc(text)}</span>'


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

def render_otp_email(code: str) -> tuple[str, str]:
    title = "Verification Required"
    content = f"""
<p style="margin:0 0 20px;font-size:14px;color:#52525b;line-height:1.6;">
    We received a verification request. Use the code below to continue.
</p>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
<tr><td align="center" style="padding:8px 0 24px;">
    <div style="background-color:#f4f4f5;border:2px dashed #e5e5e5;border-radius:10px;padding:20px 32px;display:inline-block;">
        <span style="font-family:'SF Mono',Monaco,Consolas,monospace;font-size:32px;font-weight:700;letter-spacing:8px;color:#111111;">{_esc(code)}</span>
    </div>
</td></tr>
</table>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
<tr><td style="padding:12px 16px;background-color:#fefce8;border-radius:8px;">
    <p style="margin:0;font-size:12px;color:#854d0e;line-height:1.5;">
        <strong>Expires in 5 minutes.</strong> If you didn't request this, ignore this email.
    </p>
</td></tr>
</table>
"""
    return title, _base(title, f"Your code: {code}", content, header_color="#ef5d25", header_icon="🔐")


def render_auth_otp_email(code: str, purpose: str) -> tuple[str, str]:
    configs = {
        "register": ("Verify Your Email", "Enter this code to finish creating your account", "Register"),
        "password_reset": ("Reset Your Password", "Enter this code to set a new password", "Password Reset"),
        "signin": ("Sign-In Verification", "Confirm it's really you", "Sign-In"),
    }
    title, subtitle, label = configs.get(purpose, configs["signin"])

    content = f"""
<p style="margin:0 0 20px;font-size:14px;color:#52525b;line-height:1.6;">
    {_esc(subtitle)}
</p>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
<tr><td align="center" style="padding:8px 0 24px;">
    <div style="background-color:#f4f4f5;border:2px dashed #e5e5e5;border-radius:10px;padding:20px 32px;display:inline-block;">
        <span style="font-family:'SF Mono',Monaco,Consolas,monospace;font-size:32px;font-weight:700;letter-spacing:8px;color:#111111;">{_esc(code)}</span>
    </div>
</td></tr>
</table>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
<tr><td style="padding:12px 16px;background-color:#fefce8;border-radius:8px;">
    <p style="margin:0;font-size:12px;color:#854d0e;line-height:1.5;">
        <strong>Expires in 15 minutes.</strong> This is a {_esc(label)} code for your DotStark account.
    </p>
</td></tr>
</table>
"""
    return title, _base(title, f"Your {label.lower()} code: {code}", content, header_color="#ef5d25", header_icon="🔐")


def render_booking_confirmation_customer(details: dict) -> tuple[str, str]:
    name = _esc(details.get("name", "Customer"))
    date = _esc(details.get("date", "TBD"))
    time = _esc(details.get("time", "TBD"))
    source = _esc(details.get("source_channel", "Web Widget"))
    meeting_type = details.get("meeting_type_snapshot") or "OFFLINE"
    meeting_status = details.get("meeting_link_status") or "NOT_REQUIRED"
    meeting_url = details.get("meeting_link_url") or ""
    meeting_provider = _esc(details.get("meeting_link_provider") or "")

    user_tz = details.get("user_timezone") or ""
    user_start = details.get("user_slot_start_at") or ""
    user_end = details.get("user_slot_end_at") or ""
    org_tz = details.get("org_timezone_snapshot") or ""
    org_start = details.get("org_slot_start_at") or ""
    org_end = details.get("org_slot_end_at") or ""
    has_user_tz = bool(user_tz and user_start and user_end)
    has_org_tz = bool(org_tz and org_start and org_end)

    title = "Booking Confirmed"

    # Appointment details block
    content = f"""
<p style="margin:0 0 20px;font-size:14px;color:#52525b;line-height:1.6;">
    Hi <strong style="color:#111;">{name}</strong>, your appointment is confirmed.
</p>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#f4f4f5;border-radius:10px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr>
        {_label_value("Date", date)}
        <td style="width:1px;background:#e5e5e5;"></td>
        {_label_value("Time", time)}
    </tr>
    </table>
    <hr style="border:none;border-top:1px solid #e5e5e5;margin:12px 0;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr>{_label_value("Booked via", source)}</tr>
    </table>
</td></tr>
</table>
"""

    # Meeting block (keep Meeting link status text for downstream tests + guidance)
    if meeting_type == "ONLINE" and meeting_status == "READY" and meeting_url:
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#166534;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0 0 8px;font-size:12px;color:#166534;">Meeting link status: <span class="status-badge status-badge--ready">READY</span><strong> READY</strong></p>
    <a href="{_esc(meeting_url)}" style="display:inline-block;background-color:#10b981;color:#ffffff;text-decoration:none;padding:8px 16px;border-radius:6px;font-size:12px;font-weight:600;">Join Meeting</a>
</td></tr>
</table>
"""
    elif meeting_type == "ONLINE" and meeting_status == "UNAVAILABLE":
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#fef3c7;border:1px solid #fde68a;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#92400e;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0 0 8px;font-size:12px;color:#92400e;">Meeting link status: <span class="status-badge status-badge--unavailable">UNAVAILABLE</span><strong> UNAVAILABLE</strong></p>
    <p style="margin:0;font-size:12px;color:#92400e;line-height:1.5;">Your meeting link is temporarily unavailable. Please retry shortly or contact support if this persists.</p>
</td></tr>
</table>
"""
    elif meeting_type == "ONLINE":
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#f4f4f5;border:1px solid #e5e5e5;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#71717a;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0;font-size:12px;color:#71717a;">Meeting link status: {_esc(meeting_status)}</p>
</td></tr>
</table>
"""

    # Timezone block
    if has_user_tz or has_org_tz:
        tz_rows = ""
        if has_user_tz:
            tz_rows += f'<tr><td style="padding:4px 0;font-size:12px;color:#71717a;">User time ({_esc(user_tz)})</td><td style="padding:4px 0;font-size:13px;font-weight:600;color:#111;text-align:right;">{_esc(user_start)} &rarr; {_esc(user_end)}</td></tr>'
        if has_org_tz:
            tz_rows += f'<tr><td style="padding:4px 0;font-size:12px;color:#71717a;">Business time ({_esc(org_tz)})</td><td style="padding:4px 0;font-size:13px;font-weight:600;color:#111;text-align:right;">{_esc(org_start)} &rarr; {_esc(org_end)}</td></tr>'

        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:12px 16px;background-color:#f4f4f5;border-radius:10px;">
    <p style="margin:0 0 8px;font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Time Context</p>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{tz_rows}</table>
</td></tr>
</table>
"""

    content += """
<p style="margin:0;font-size:12px;color:#a1a1aa;text-align:center;">Need to make changes? Contact us directly.</p>
"""
    return title, _base(title, f"Your appointment on {date} at {time} is confirmed.", content, header_color="#10b981", header_icon="✔")


def render_booking_confirmation_admin(details: dict, *, frontend_url: str = "") -> tuple[str, str]:
    name = _esc(details.get("name", "Customer"))
    email = _esc(details.get("email", ""))
    phone = _esc(details.get("phone", ""))
    date = _esc(details.get("date", "TBD"))
    time = _esc(details.get("time", "TBD"))
    source = _esc(details.get("source_channel", "Web Widget"))
    meeting_type = details.get("meeting_type_snapshot") or "OFFLINE"
    meeting_status = details.get("meeting_link_status") or "NOT_REQUIRED"
    meeting_url = details.get("meeting_link_url") or ""
    meeting_provider = _esc(details.get("meeting_link_provider") or "")

    user_tz = details.get("user_timezone") or ""
    user_start = details.get("user_slot_start_at") or ""
    user_end = details.get("user_slot_end_at") or ""
    org_tz = details.get("org_timezone_snapshot") or ""
    org_start = details.get("org_slot_start_at") or ""
    org_end = details.get("org_slot_end_at") or ""
    has_user_tz = bool(user_tz and user_start and user_end)
    has_org_tz = bool(org_tz and org_start and org_end)

    title = "New Booking"

    content = f"""
<p style="margin:0 0 20px;font-size:14px;color:#52525b;line-height:1.6;">
    A new appointment has been booked through your AI assistant.
</p>

<!-- Customer card -->
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:16px;">
<tr><td style="padding:16px;background-color:#f4f4f5;border-radius:10px;">
    <p style="margin:0 0 8px;font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Customer</p>
    <p style="margin:0 0 4px;font-size:16px;font-weight:700;color:#111;">{name}</p>
    <p style="margin:0 0 2px;font-size:13px;color:#52525b;">{email}</p>
    <p style="margin:0;font-size:13px;color:#52525b;">{phone}</p>
</td></tr>
</table>

<!-- Appointment card -->
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:16px;">
<tr><td style="padding:16px;background-color:#f4f4f5;border-radius:10px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr>
        {_label_value("Date", date)}
        <td style="width:1px;background:#e5e5e5;"></td>
        {_label_value("Time", time)}
    </tr>
    </table>
    <hr style="border:none;border-top:1px solid #e5e5e5;margin:12px 0;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
    <tr>{_label_value("Source", source)}</tr>
    </table>
</td></tr>
</table>
"""

    # Meeting block (admin: guidance differs for UNAVAILABLE)
    if meeting_type == "ONLINE" and meeting_status == "READY" and meeting_url:
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:16px;">
<tr><td style="padding:16px;background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#166534;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0 0 8px;font-size:12px;color:#166534;">Meeting link status: <span class="status-badge status-badge--ready">READY</span><strong> READY</strong></p>
    <a href="{_esc(meeting_url)}" style="display:inline-block;background-color:#10b981;color:#ffffff;text-decoration:none;padding:8px 16px;border-radius:6px;font-size:12px;font-weight:600;">Join Meeting</a>
</td></tr>
</table>
"""
    elif meeting_type == "ONLINE" and meeting_status == "UNAVAILABLE":
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:16px;">
<tr><td style="padding:16px;background-color:#fef3c7;border:1px solid #fde68a;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#92400e;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0 0 8px;font-size:12px;color:#92400e;">Meeting link status: <span class="status-badge status-badge--unavailable">UNAVAILABLE</span><strong> UNAVAILABLE</strong></p>
    <p style="margin:0;font-size:12px;color:#92400e;line-height:1.5;">Meeting link is temporarily unavailable. Please follow up with the customer and retry provider connection if needed.</p>
</td></tr>
</table>
"""
    elif meeting_type == "ONLINE":
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:16px;">
<tr><td style="padding:16px;background-color:#f4f4f5;border:1px solid #e5e5e5;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#71717a;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0;font-size:12px;color:#71717a;">Meeting link status: {_esc(meeting_status)}</p>
</td></tr>
</table>
"""

    # Timezone block
    if has_user_tz or has_org_tz:
        tz_rows = ""
        if has_user_tz:
            tz_rows += f'<tr><td style="padding:4px 0;font-size:12px;color:#71717a;">User time ({_esc(user_tz)})</td><td style="padding:4px 0;font-size:13px;font-weight:600;color:#111;text-align:right;">{_esc(user_start)} &rarr; {_esc(user_end)}</td></tr>'
        if has_org_tz:
            tz_rows += f'<tr><td style="padding:4px 0;font-size:12px;color:#71717a;">Business time ({_esc(org_tz)})</td><td style="padding:4px 0;font-size:13px;font-weight:600;color:#111;text-align:right;">{_esc(org_start)} &rarr; {_esc(org_end)}</td></tr>'

        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:16px;">
<tr><td style="padding:12px 16px;background-color:#f4f4f5;border-radius:10px;">
    <p style="margin:0 0 8px;font-size:11px;color:#71717a;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Time Context</p>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{tz_rows}</table>
</td></tr>
</table>
"""

    # CTA
    url = _esc(frontend_url)
    content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
<tr><td align="center" style="padding:8px 0;">
    <a href="{url}/dashboard/bookings" style="display:inline-block;background-color:#ef5d25;color:#ffffff;text-decoration:none;padding:10px 24px;border-radius:8px;font-size:13px;font-weight:600;">View Bookings &rarr;</a>
</td></tr>
</table>
"""
    return title, _base(title, f"New booking from {name} on {date}", content, header_color="#3b82f6", header_icon="📅")


def render_booking_cancellation(name: str, details: dict) -> tuple[str, str]:
    safe_name = _esc(name)
    date = _esc(details.get("date", "N/A"))
    time = _esc(details.get("time", ""))
    meeting_type = details.get("meeting_type_snapshot") or "OFFLINE"
    meeting_status = details.get("meeting_link_status") or "NOT_REQUIRED"
    meeting_url = details.get("meeting_link_url") or ""
    meeting_provider = _esc(details.get("meeting_link_provider") or "")

    title = "Appointment Cancelled"

    content = f"""
<p style="margin:0 0 20px;font-size:14px;color:#52525b;line-height:1.6;">
    Hi <strong style="color:#111;">{safe_name}</strong>, your appointment has been cancelled.
</p>

<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#fef2f2;border:1px solid #fecaca;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:11px;color:#991b1b;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">Cancelled Appointment</p>
    <p style="margin:0;font-size:15px;font-weight:700;color:#111;">{date}</p>
    <p style="margin:4px 0 0;font-size:13px;color:#52525b;">{time}</p>
</td></tr>
</table>
"""

    # Meeting block for cancellation (customer-facing)
    if meeting_type == "ONLINE" and meeting_status == "READY" and meeting_url:
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#166534;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0 0 8px;font-size:12px;color:#166534;">Meeting link status: <span class="status-badge status-badge--ready">READY</span><strong> READY</strong></p>
    <a href="{_esc(meeting_url)}" style="display:inline-block;background-color:#10b981;color:#ffffff;text-decoration:none;padding:8px 16px;border-radius:6px;font-size:12px;font-weight:600;">Join Meeting</a>
</td></tr>
</table>
"""
    elif meeting_type == "ONLINE" and meeting_status == "UNAVAILABLE":
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#fef3c7;border:1px solid #fde68a;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#92400e;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0 0 8px;font-size:12px;color:#92400e;">Meeting link status: <span class="status-badge status-badge--unavailable">UNAVAILABLE</span><strong> UNAVAILABLE</strong></p>
    <p style="margin:0;font-size:12px;color:#92400e;line-height:1.5;">Your meeting link is temporarily unavailable. Please retry shortly or contact support if this persists.</p>
</td></tr>
</table>
"""
    elif meeting_type == "ONLINE":
        content += f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin-bottom:20px;">
<tr><td style="padding:16px;background-color:#f4f4f5;border:1px solid #e5e5e5;border-radius:10px;">
    <p style="margin:0 0 4px;font-size:12px;color:#71717a;"><strong>Meeting:</strong> {_esc(meeting_provider)}</p>
    <p style="margin:0;font-size:12px;color:#71717a;">Meeting link status: {_esc(meeting_status)}</p>
</td></tr>
</table>
"""

    content += """
<p style="margin:0 0 20px;font-size:14px;color:#52525b;line-height:1.6;">
    If you'd like to rebook, feel free to start a new conversation anytime.
</p>

<p style="margin:0;font-size:12px;color:#a1a1aa;text-align:center;">We'd love to see you again!</p>
"""
    return title, _base(title, f"Your appointment on {date} has been cancelled.", content, header_color="#ef4444", header_icon="✖")


# ─────────────────────────────────────────────────────────────────────────────
# Template Registry
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE_REGISTRY = {
    "otp": render_otp_email,
    "auth_register": lambda code: render_auth_otp_email(code, "register"),
    "auth_password_reset": lambda code: render_auth_otp_email(code, "password_reset"),
    "auth_signin": lambda code: render_auth_otp_email(code, "signin"),
    "booking_confirmation_customer": render_booking_confirmation_customer,
    "booking_confirmation_admin": render_booking_confirmation_admin,
    "booking_cancellation": render_booking_cancellation,
}


def get_template(template_name: str):
    """Get template renderer by name."""
    return TEMPLATE_REGISTRY.get(template_name)


def list_templates() -> list[str]:
    """List all available template names."""
    return list(TEMPLATE_REGISTRY.keys())
