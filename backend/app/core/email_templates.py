"""
DotStark Email Templates
Modern, design-system-aligned email templates matching the admin panel UI.
"""
from __future__ import annotations
import html
from typing import Optional
from uuid import UUID
from app.core.email_design_tokens import tokens, inline_styles, TEMPLATE_THEMES


def _escape(value: str) -> str:
    """HTML escape a string."""
    return html.escape(str(value)) if value else ""


def _build_base_template(
    *,
    theme_key: str,
    preheader: str,
    content_html: str,
    footer_text: Optional[str] = None,
) -> str:
    """Build complete email with base layout."""
    theme = TEMPLATE_THEMES.get(theme_key, TEMPLATE_THEMES["otp"])
    footer = footer_text or f"© {2026} DotStark AI Booking System"
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light dark">
    <meta name="supported-color-schemes" content="light dark">
    <title>{_escape(theme["header_title"])}</title>
    {inline_styles()}
    <!--[if mso]>
    <style>
        .email-card {{ border: 1px solid #e5e5e5 !important; }}
        .email-button {{ background-color: #ef5d25 !important; }}
    </style>
    <![endif]-->
</head>
<body>
    <div class="email-wrapper" role="article" aria-roledescription="email" aria-label="{_escape(theme["header_title"])}">
        <!-- Preheader text (hidden, shown in inbox preview) -->
        <div style="display: none; max-height: 0; overflow: hidden; font-size: 1px; line-height: 1px; color: transparent;">
            {_escape(preheader)}
        </div>
        
        <div class="email-card" role="main">
            <!-- Header -->
            <header class="email-header" style="background: {theme['header_bg']};">
                <div class="email-header-icon" style="background: rgba(255,255,255,0.2);" aria-hidden="true">
                    {theme["header_icon"]}
                </div>
                <h1 class="email-header-title" style="color: var(--color-primary-foreground);">
                    {_escape(theme["header_title"])}
                </h1>
                <p class="email-header-subtitle" style="color: rgba(255,255,255,0.85);">
                    {_escape(theme["header_subtitle"])}
                </p>
            </header>
            
            <!-- Content -->
            <main class="email-content">
                {content_html}
            </main>
            
            <!-- Footer -->
            <footer class="email-footer">
                <p class="email-footer-text">{_escape(footer)}</p>
            </footer>
        </div>
    </div>
</body>
</html>"""


def _build_greeting(name: str) -> str:
    """Build greeting section."""
    safe_name = _escape(name)
    return f"""
    <p style="margin: 0 0 8px; font-size: {tokens.typography.font_size_lg}; color: var(--color-foreground);">
        Hello <strong style="color: var(--color-primary);">{safe_name}</strong>,
    </p>
    <p style="margin: 0 0 {tokens.spacing.xl}; font-size: {tokens.typography.font_size_md}; line-height: {tokens.typography.line_height_relaxed}; color: var(--color-muted-foreground);">
        Great news! Your appointment has been successfully booked. We can't wait to see you!
    </p>"""


def _build_appointment_card(details: dict) -> str:
    """Build appointment details card."""
    raw_source = details.get("source_channel") or "Web Widget"
    date = _escape(details.get("date") or "TBD")
    time = _escape(details.get("time") or "TBD")
    source = _escape(raw_source)
    source_emoji = "💬" if raw_source == "Web Widget" else "📱"
    
    return f"""
    <div class="email-card-inner">
        <div style="display: table; width: 100%; border-collapse: collapse; margin-bottom: {tokens.spacing.lg}; padding-bottom: {tokens.spacing.lg}; border-bottom: 1px dashed var(--color-border);">
            <div style="display: table-row;">
                <div style="display: table-cell; width: 48px; vertical-align: top;">
                    <div style="width: 48px; height: 48px; background: var(--color-primary); border-radius: {tokens.radius.md}; display: flex; align-items: center; justify-content: center; font-size: 24px;" aria-hidden="true">📅</div>
                </div>
                <div style="display: table-cell; vertical-align: middle; padding-left: {tokens.spacing.md};">
                    <p style="margin: 0; font-size: {tokens.typography.font_size_xs}; text-transform: uppercase; letter-spacing: 0.5px; color: var(--color-muted-foreground);">Appointment Details</p>
                    <p style="margin: 4px 0 0; font-size: {tokens.typography.font_size_xl}; font-weight: {tokens.typography.font_weight_semibold}; color: var(--color-foreground);">{date}</p>
                </div>
            </div>
        </div>
        
        <div class="email-grid">
            <div class="email-grid-row">
                <div class="email-grid-cell">
                    <p class="email-label">⏰ Time</p>
                    <p class="email-value">{time}</p>
                </div>
                <div class="email-grid-cell">
                    <p class="email-label">{source_emoji} Booked via</p>
                    <p class="email-value">{source}</p>
                </div>
            </div>
        </div>
    </div>"""


def _build_meeting_block(safe: dict, audience: str = "customer") -> str:
    """Build meeting type/link status block."""
    meeting_type = safe.get("meeting_type_snapshot") or "OFFLINE"
    meeting_link_status = safe.get("meeting_link_status") or "NOT_REQUIRED"
    meeting_link_url = safe.get("meeting_link_url") or ""
    meeting_link_provider = safe.get("meeting_link_provider") or "Meeting Provider"
    
    if meeting_type != "ONLINE":
        return f"""
        <div class="meeting-block status-badge--offline">
            <p style="margin: 0; font-size: {tokens.typography.font_size_sm}; color: var(--color-foreground);">
                <strong>Meeting Type:</strong> Offline
            </p>
        </div>"""
    
    if meeting_link_status == "READY" and meeting_link_url:
        safe_url = _escape(meeting_link_url)
        safe_provider = _escape(meeting_link_provider)
        return f"""
        <div class="meeting-block meeting-block--ready">
            <p style="margin: 0 0 8px; font-size: {tokens.typography.font_size_sm}; color: var(--color-foreground);">
                <strong>Meeting Type:</strong> Online ({safe_provider})
            </p>
            <p style="margin: 0 0 8px; font-size: {tokens.typography.font_size_sm}; color: var(--color-success);">
                <strong>Meeting link status:</strong> <span class="status-badge status-badge--ready">Ready</span>
            </p>
            <a href="{safe_url}" class="meeting-link">Join Meeting</a>
        </div>"""
    
    if meeting_link_status == "UNAVAILABLE":
        guidance = (
            "Your meeting link is temporarily unavailable. Please retry shortly or contact support if this persists."
            if audience == "customer"
            else "Meeting link is temporarily unavailable. Please follow up with the customer and retry provider connection if needed."
        )
        return f"""
        <div class="meeting-block meeting-block--unavailable">
            <p style="margin: 0 0 8px; font-size: {tokens.typography.font_size_sm}; color: var(--color-foreground);">
                <strong>Meeting Type:</strong> Online ({_escape(meeting_link_provider)})
            </p>
            <p style="margin: 0 0 8px; font-size: {tokens.typography.font_size_sm}; color: #92400e;">
                <strong>Meeting link status:</strong> <span class="status-badge status-badge--unavailable">Unavailable</span>
            </p>
            <p style="margin: 0; font-size: {tokens.typography.font_size_sm}; line-height: {tokens.typography.line_height_relaxed}; color: #92400e;">{_escape(guidance)}</p>
        </div>"""
    
    return f"""
    <div class="meeting-block">
        <p style="margin: 0 0 6px; font-size: {tokens.typography.font_size_sm}; color: var(--color-foreground);">
            <strong>Meeting Type:</strong> Online ({_escape(meeting_link_provider)})
        </p>
        <p style="margin: 0; font-size: {tokens.typography.font_size_sm}; color: var(--color-muted-foreground);">
            <strong>Meeting link status:</strong> {_escape(meeting_link_status)}
        </p>
    </div>"""


def _build_dual_time_block(safe: dict) -> str:
    """Build dual timezone block."""
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
    
    rows = []
    if has_user:
        rows.append(f"""
        <div class="email-grid-row">
            <div class="email-grid-cell" style="width: 100%; padding: 0;">
                <p class="email-label">User time ({_escape(user_timezone or "")})</p>
                <p class="email-value">{_escape(user_slot_start_at or "")} → {_escape(user_slot_end_at or "")}</p>
            </div>
        </div>""")
    if has_org:
        rows.append(f"""
        <div class="email-grid-row">
            <div class="email-grid-cell" style="width: 100%; padding: 0; margin-top: {tokens.spacing.md};">
                <p class="email-label">Business time ({_escape(org_timezone_snapshot or "")})</p>
                <p class="email-value">{_escape(org_slot_start_at or "")} → {_escape(org_slot_end_at or "")}</p>
            </div>
        </div>""")
    
    return f"""
    <div class="email-card-inner">
        <p class="email-section-title">Time Context</p>
        <div class="email-grid">
            {''.join(rows)}
        </div>
    </div>"""


def _build_info_box(text: str, icon: str = "⚠") -> str:
    """Build info/warning box."""
    return f"""
    <div class="info-box">
        <p class="info-box-title">{icon} <strong>Note</strong></p>
        <p class="info-box-text">{_escape(text)}</p>
    </div>"""


def _build_admin_customer_card(safe: dict) -> str:
    """Build customer info card for admin email."""
    name = _escape(safe.get("name") or "Customer")
    email = _escape(safe.get("email") or "")
    phone = _escape(safe.get("phone") or "Not provided")
    
    return f"""
    <div class="email-card-inner" style="background: linear-gradient(135deg, var(--color-info-bg) 0%, var(--color-muted) 100%); border-color: var(--color-info-border);">
        <div style="display: table; width: 100%; border-collapse: collapse; margin-bottom: {tokens.spacing.lg};">
            <div style="display: table-row;">
                <div style="display: table-cell; width: 44px; vertical-align: top;">
                    <div style="width: 44px; height: 44px; background: var(--color-info); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 20px;" aria-hidden="true">👤</div>
                </div>
                <div style="display: table-cell; vertical-align: middle; padding-left: {tokens.spacing.md};">
                    <p style="margin: 0; font-size: {tokens.typography.font_size_xs}; text-transform: uppercase; letter-spacing: 0.5px; color: var(--color-muted-foreground);">Customer</p>
                    <p style="margin: 4px 0 0; font-size: {tokens.typography.font_size_xl}; font-weight: {tokens.typography.font_weight_semibold}; color: var(--color-foreground);">{name}</p>
                </div>
                <div style="display: table-cell; vertical-align: middle; text-align: right;">
                    <span style="background: var(--color-info-bg); color: var(--color-info); padding: 6px 12px; border-radius: {tokens.radius.pill}; font-size: {tokens.typography.font_size_xs}; font-weight: {tokens.typography.font_weight_medium};">New Customer</span>
                </div>
            </div>
        </div>
        
        <div class="email-grid">
            <div class="email-grid-row">
                <div class="email-grid-cell">
                    <div style="background: var(--color-card); border-radius: {tokens.radius.md}; padding: {tokens.spacing.md};">
                        <p class="email-label">✉ Email</p>
                        <p class="email-value" style="word-break: break-all;">{email}</p>
                    </div>
                </div>
                <div class="email-grid-cell">
                    <div style="background: var(--color-card); border-radius: {tokens.radius.md}; padding: {tokens.spacing.md};">
                        <p class="email-label">📞 Phone</p>
                        <p class="email-value">{phone}</p>
                    </div>
                </div>
            </div>
        </div>
    </div>"""


def _build_admin_appointment_card(safe: dict) -> str:
    """Build appointment card for admin email."""
    date = _escape(safe.get("date") or "TBD")
    time = _escape(safe.get("time") or "TBD")
    source = _escape(safe.get("source_channel") or "Web Widget")
    
    return f"""
    <div class="email-card-inner" style="background: linear-gradient(135deg, var(--color-success-bg) 0%, var(--color-muted) 100%); border-color: var(--color-success-border);">
        <div style="display: table; width: 100%; border-collapse: collapse;">
            <div style="display: table-row;">
                <div style="display: table-cell; text-align: center; padding: 0 {tokens.spacing.md}; border-right: 1px solid var(--color-success-border);">
                    <p style="margin: 0; font-size: {tokens.typography.font_size_xs}; text-transform: uppercase; letter-spacing: 0.5px; color: var(--color-success);">Date</p>
                    <p style="margin: 6px 0 0; font-size: {tokens.typography.font_size_2xl}; font-weight: {tokens.typography.font_weight_bold}; color: var(--color-foreground);">{date}</p>
                </div>
                <div style="display: table-cell; text-align: center; padding: 0 {tokens.spacing.md}; border-right: 1px solid var(--color-success-border);">
                    <p style="margin: 0; font-size: {tokens.typography.font_size_xs}; text-transform: uppercase; letter-spacing: 0.5px; color: var(--color-success);">Time</p>
                    <p style="margin: 6px 0 0; font-size: {tokens.typography.font_size_2xl}; font-weight: {tokens.typography.font_weight_bold}; color: var(--color-foreground);">{time}</p>
                </div>
                <div style="display: table-cell; text-align: center; padding: 0 {tokens.spacing.md};">
                    <p style="margin: 0; font-size: {tokens.typography.font_size_xs}; text-transform: uppercase; letter-spacing: 0.5px; color: var(--color-success);">Via</p>
                    <p style="margin: 6px 0 0; font-size: {tokens.typography.font_size_md}; font-weight: {tokens.typography.font_weight_semibold}; color: var(--color-foreground);">{source}</p>
                </div>
            </div>
        </div>
    </div>"""


def _build_cancelled_details(safe: dict) -> str:
    """Build cancelled appointment details."""
    date = _escape(safe.get("date") or "N/A")
    time = _escape(safe.get("time") or "")
    
    return f"""
    <div style="background: var(--color-destructive-bg); border: 1px solid var(--color-destructive-border); border-radius: {tokens.radius.lg}; padding: {tokens.spacing.lg}; margin-bottom: {tokens.spacing.lg}; display: inline-block; min-width: 200px; text-align: center;">
        <p style="margin: 0 0 8px; font-size: {tokens.typography.font_size_xs}; text-transform: uppercase; letter-spacing: 0.5px; color: var(--color-destructive);">Former Appointment</p>
        <p style="margin: 0; font-size: {tokens.typography.font_size_xl}; font-weight: {tokens.typography.font_weight_semibold}; color: var(--color-foreground);">{date}</p>
        <p style="margin: 8px 0 0; font-size: {tokens.typography.font_size_md}; color: var(--color-muted-foreground);">{time}</p>
    </div>"""


# ─────────────────────────────────────────────────────────────────────────────
# Public Template Functions
# ─────────────────────────────────────────────────────────────────────────────

def render_otp_email(
    code: str,
    *,
    org_name: str = "DotStark AI",
    expiry_minutes: int = 5,
) -> tuple[str, str]:
    """Render OTP verification email (booking flow)."""
    safe_code = _escape(code)
    content = f"""
    {_build_greeting("there")}
    
    <div class="code-box">
        <span class="code-value">{safe_code}</span>
    </div>
    
    <div class="info-box">
        <p class="info-box-title">⏱ <strong>Expires in {expiry_minutes} minutes</strong></p>
        <p class="info-box-text">If you didn't request this code, please ignore this email. This is an automated message from {_escape(org_name)}.</p>
    </div>
    """
    subject = f"{code} - Your Verification Code"
    preheader = f"Your verification code is {code}. Expires in {expiry_minutes} minutes."
    html_body = _build_base_template(
        theme_key="otp",
        preheader=preheader,
        content_html=content,
    )
    return subject, html_body


def render_auth_otp_email(
    code: str,
    purpose: str,
) -> tuple[str, str]:
    """Render auth OTP email (register/password_reset/signin)."""
    safe_code = _escape(code)
    theme_map = {
        "register": "auth_register",
        "password_reset": "auth_password_reset",
        "signin": "auth_signin",
    }
    theme_key = theme_map.get(purpose, "auth_signin")
    theme = TEMPLATE_THEMES[theme_key]
    
    expiry_minutes = 15
    content = f"""
    <p style="margin: 0 0 {tokens.spacing.xl}; font-size: {tokens.typography.font_size_md}; line-height: {tokens.typography.line_height_relaxed}; color: var(--color-muted-foreground);">
        {_escape(theme["header_subtitle"])}
    </p>
    
    <div class="code-box">
        <span class="code-value" style="background: linear-gradient(135deg, #ff6b00 0%, #e55f00 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;">{safe_code}</span>
    </div>
    
    <div class="info-box">
        <p class="info-box-title">⏱ <strong>Expires in {expiry_minutes} minutes</strong></p>
        <p class="info-box-text">If you didn't request this code, please ignore this email. This is an automated message from the DotStark AI Booking System.</p>
    </div>
    """
    subject_map = {
        "register": f"{code} - Verify your email",
        "password_reset": f"{code} - Password Reset Code",
        "signin": f"{code} - Your Sign-In Code",
    }
    subject = subject_map.get(purpose, f"{code} - Your Sign-In Code")
    preheader = f"Your {purpose.replace('_', ' ')} code is {code}. Expires in {expiry_minutes} minutes."
    html_body = _build_base_template(
        theme_key=theme_key,
        preheader=preheader,
        content_html=content,
    )
    return subject, html_body


def render_booking_confirmation_customer(
    booking_details: dict,
    *,
    org_name: str = "DotStark AI",
) -> tuple[str, str]:
    """Render booking confirmation email for customer."""
    # NOTE: keep values raw here — inner builders (_build_*) HTML-escape
    # each field exactly once. Pre-escaping here would double-escape
    # (`&` -> `&amp;` -> `&amp;amp;`) and break URLs with query params.
    safe = {k: str(v) if v else "" for k, v in booking_details.items()}
    name = safe.get("name") or "Customer"
    
    content = f"""
    {_build_greeting(name)}
    {_build_appointment_card(safe)}
    {_build_meeting_block(safe, audience="customer")}
    {_build_dual_time_block(safe)}
    
    <div class="info-box">
        <p class="info-box-title">🔔 <strong>Reminder</strong></p>
        <p class="info-box-text">We'll send you a reminder before your appointment. Need to make changes? Contact us directly.</p>
    </div>
    """
    subject = "✔ Booking Confirmed!"
    preheader = f"Your appointment on {safe.get('date', 'TBD')} at {safe.get('time', 'TBD')} is confirmed."
    html_body = _build_base_template(
        theme_key="booking_confirmation_customer",
        preheader=preheader,
        content_html=content,
    )
    return subject, html_body


def render_booking_confirmation_admin(
    booking_details: dict,
    *,
    frontend_url: str = "",
) -> tuple[str, str]:
    """Render booking confirmation email for admin/org."""
    # Keep raw — builders escape once; subject/preheader use raw values
    # (preheader is escaped once inside _build_base_template).
    safe = {k: str(v) if v else "" for k, v in booking_details.items()}
    name = safe.get("name") or "Customer"
    date = safe.get("date") or "TBD"
    
    content = f"""
    <p style="margin: 0 0 {tokens.spacing.xl}; font-size: {tokens.typography.font_size_md}; line-height: {tokens.typography.line_height_relaxed}; color: var(--color-muted-foreground);">
        A new appointment has been booked through your AI Assistant. Details below:
    </p>
    
    {_build_admin_customer_card(safe)}
    {_build_admin_appointment_card(safe)}
    {_build_meeting_block(safe, audience="admin")}
    {_build_dual_time_block(safe)}
    
    <div style="text-align: center; margin-top: {tokens.spacing.xl};">
        <a href="{_escape(frontend_url)}/dashboard/bookings" class="email-button">View All Bookings →</a>
    </div>
    """
    subject = f"📅 New Booking: {name} - {date}"
    preheader = f"New booking from {name} on {date} at {safe.get('time', 'TBD')}."
    html_body = _build_base_template(
        theme_key="booking_confirmation_admin",
        preheader=preheader,
        content_html=content,
        footer_text=f"© {2026} DotStark AI Booking System",
    )
    return subject, html_body


def render_booking_cancellation(
    customer_name: str,
    booking_details: dict,
) -> tuple[str, str]:
    """Render booking cancellation email for customer."""
    # Builders escape fields once; escape direct insertions here once.
    safe_name = _escape(customer_name or "Customer")
    safe = {k: str(v) if v else "" for k, v in booking_details.items()}
    
    content = f"""
    <p style="margin: 0 0 8px; font-size: {tokens.typography.font_size_lg}; color: var(--color-foreground); text-align: center;">
        Hello <strong>{safe_name}</strong>,
    </p>
    <p style="margin: 0 0 {tokens.spacing.xl}; font-size: {tokens.typography.font_size_md}; line-height: {tokens.typography.line_height_relaxed}; color: var(--color-muted-foreground); text-align: center;">
        Your appointment has been cancelled as requested. We're sorry to see you go!
    </p>
    
    {_build_cancelled_details(safe)}
    {_build_meeting_block(safe, audience="customer")}
    
    <p style="margin: {tokens.spacing.lg} 0 0; font-size: {tokens.typography.font_size_md}; line-height: {tokens.typography.line_height_relaxed}; color: var(--color-foreground); text-align: center;">
        If you'd like to rebook, feel free to start a new conversation anytime. We'd love to see you again!
    </p>
    """
    subject = "Appointment Cancelled"
    preheader = f"Your appointment on {safe.get('date', 'N/A')} has been cancelled."
    html_body = _build_base_template(
        theme_key="booking_cancellation",
        preheader=preheader,
        content_html=content,
    )
    return subject, html_body


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