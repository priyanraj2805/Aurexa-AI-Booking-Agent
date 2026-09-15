"""
DotStark Email Design Tokens
Single source of truth for email styling — matches admin-panel globals.css
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ColorTokens:
    """Color palette matching admin-panel CSS variables."""
    # Light mode (DotStark brand)
    primary: str = "#ef5d25"
    primary_foreground: str = "#ffffff"
    primary_hover: str = "#e0521f"
    
    background: str = "#fafafa"
    foreground: str = "#111111"
    
    card: str = "#ffffff"
    card_foreground: str = "#111111"
    card_border: str = "#e5e5e5"
    
    muted: str = "#f5f5f5"
    muted_foreground: str = "#71717a"
    
    border: str = "#e5e5e5"
    input: str = "#e5e5e5"
    
    destructive: str = "#ef4444"
    destructive_foreground: str = "#ffffff"
    destructive_bg: str = "#fef2f2"
    destructive_border: str = "#fecaca"
    
    success: str = "#10b981"
    success_foreground: str = "#ffffff"
    success_bg: str = "#ecfdf5"
    success_border: str = "#a7f3d0"
    
    warning: str = "#f59e0b"
    warning_foreground: str = "#ffffff"
    warning_bg: str = "#fffbeb"
    warning_border: str = "#fde68a"
    
    info: str = "#3b82f6"
    info_foreground: str = "#ffffff"
    info_bg: str = "#eff6ff"
    info_border: str = "#bfdbfe"
    
    # Dark mode (Kilo palette) - for @media (prefers-color-scheme: dark)
    dark_primary: str = "#3b82f6"
    dark_primary_foreground: str = "#ffffff"
    dark_background: str = "#0a0a0a"
    dark_foreground: str = "#ffffff"
    dark_card: str = "#121212"
    dark_card_foreground: str = "#ffffff"
    dark_card_border: str = "#27272a"
    dark_muted: str = "#1f1f22"
    dark_muted_foreground: str = "#a1a1aa"
    dark_border: str = "#27272a"


@dataclass(frozen=True)
class SpacingTokens:
    """Spacing scale (4px base unit)."""
    xs: str = "4px"
    sm: str = "8px"
    md: str = "16px"
    lg: str = "24px"
    xl: str = "32px"
    xxl: str = "48px"


@dataclass(frozen=True)
class RadiusTokens:
    """Border radius scale."""
    sm: str = "6px"
    md: str = "10px"
    lg: str = "12px"
    xl: str = "16px"
    pill: str = "9999px"


@dataclass(frozen=True)
class TypographyTokens:
    """Typography scale matching admin-panel."""
    font_family: str = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    font_size_xs: str = "11px"
    font_size_sm: str = "12px"
    font_size_base: str = "13px"
    font_size_md: str = "14px"
    font_size_lg: str = "16px"
    font_size_xl: str = "18px"
    font_size_2xl: str = "24px"
    font_size_3xl: str = "32px"
    line_height_tight: str = "1.2"
    line_height_normal: str = "1.5"
    line_height_relaxed: str = "1.6"
    font_weight_normal: str = "400"
    font_weight_medium: str = "500"
    font_weight_semibold: str = "600"
    font_weight_bold: str = "700"


@dataclass(frozen=True)
class ShadowTokens:
    """Shadow scale."""
    sm: str = "0 1px 2px rgba(0,0,0,0.05)"
    md: str = "0 4px 6px rgba(0,0,0,0.07)"
    lg: str = "0 10px 40px rgba(0,0,0,0.08)"
    xl: str = "0 20px 60px rgba(0,0,0,0.1)"


@dataclass(frozen=True)
class DesignTokens:
    """Complete design token set."""
    colors: ColorTokens = ColorTokens()
    spacing: SpacingTokens = SpacingTokens()
    radius: RadiusTokens = RadiusTokens()
    typography: TypographyTokens = TypographyTokens()
    shadows: ShadowTokens = ShadowTokens()


# Global instance
tokens = DesignTokens()


def get_css_variables(mode: Literal["light", "dark"] = "light") -> dict[str, str]:
    """Generate CSS custom properties for email templates."""
    c = tokens.colors
    if mode == "dark":
        return {
            "--color-primary": c.dark_primary,
            "--color-primary-foreground": c.dark_primary_foreground,
            "--color-background": c.dark_background,
            "--color-foreground": c.dark_foreground,
            "--color-card": c.dark_card,
            "--color-card-foreground": c.dark_card_foreground,
            "--color-card-border": c.dark_card_border,
            "--color-muted": c.dark_muted,
            "--color-muted-foreground": c.dark_muted_foreground,
            "--color-border": c.dark_border,
            "--color-destructive": c.destructive,
            "--color-success": c.success,
            "--color-warning": c.warning,
            "--color-info": c.info,
        }
    return {
        "--color-primary": c.primary,
        "--color-primary-foreground": c.primary_foreground,
        "--color-background": c.background,
        "--color-foreground": c.foreground,
        "--color-card": c.card,
        "--color-card-foreground": c.card_foreground,
        "--color-card-border": c.card_border,
        "--color-muted": c.muted,
        "--color-muted-foreground": c.muted_foreground,
        "--color-border": c.border,
        "--color-destructive": c.destructive,
        "--color-success": c.success,
        "--color-warning": c.warning,
        "--color-info": c.info,
    }


def inline_styles(mode: Literal["light", "dark"] = "light") -> str:
    """Generate inline CSS for email client compatibility."""
    vars = get_css_variables(mode)
    t = tokens
    css_vars = "; ".join(f"{k}: {v}" for k, v in vars.items())
    
    return f"""
    <style>
      :root {{{css_vars}}}
      @media (prefers-color-scheme: dark) {{
        :root {{
          {"; ".join(f"{k}: {v}" for k, v in get_css_variables("dark").items())}
        }}
      }}
      * {{ box-sizing: border-box; margin: 0; padding: 0; }}
      body {{
        font-family: {t.typography.font_family};
        font-size: {t.typography.font_size_base};
        line-height: {t.typography.line_height_normal};
        color: var(--color-foreground);
        background-color: var(--color-background);
        -webkit-text-size-adjust: 100%;
        -ms-text-size-adjust: 100%;
      }}
      .email-wrapper {{
        min-height: 100vh;
        padding: {t.spacing.xl} {t.spacing.md};
      }}
      .email-card {{
        background-color: var(--color-card);
        border: 1px solid var(--color-card-border);
        border-radius: {t.radius.xl};
        box-shadow: {t.shadows.lg};
        overflow: hidden;
        max-width: 520px;
        margin: 0 auto;
      }}
      .email-header {{
        padding: {t.spacing.xl} {t.spacing.lg};
        text-align: center;
        border-bottom: 1px solid var(--color-border);
      }}
      .email-header-icon {{
        width: 64px;
        height: 64px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-bottom: {t.spacing.md};
        font-size: 32px;
      }}
      .email-header-title {{
        font-size: {t.typography.font_size_2xl};
        font-weight: {t.typography.font_weight_bold};
        letter-spacing: -0.5px;
        margin: 0 0 {t.spacing.xs};
      }}
      .email-header-subtitle {{
        font-size: {t.typography.font_size_md};
        color: var(--color-muted-foreground);
        margin: 0;
      }}
      .email-content {{
        padding: {t.spacing.xl} {t.spacing.lg};
      }}
      .email-section {{
        margin-bottom: {t.spacing.lg};
      }}
      .email-section-title {{
        font-size: {t.typography.font_size_xs};
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: var(--color-muted-foreground);
        margin: 0 0 {t.spacing.sm};
        font-weight: {t.typography.font_weight_semibold};
      }}
      .email-card-inner {{
        background-color: var(--color-muted);
        border: 1px solid var(--color-border);
        border-radius: {t.radius.lg};
        padding: {t.spacing.lg};
      }}
      .email-grid {{
        display: table;
        width: 100%;
        border-collapse: collapse;
      }}
      .email-grid-row {{
        display: table-row;
      }}
      .email-grid-cell {{
        display: table-cell;
        padding: {t.spacing.sm} 0;
        vertical-align: top;
      }}
      .email-grid-cell:first-child {{
        width: 50%;
        padding-right: {t.spacing.md};
      }}
      .email-grid-cell:last-child {{
        width: 50%;
        padding-left: {t.spacing.md};
      }}
      .email-label {{
        font-size: {t.typography.font_size_xs};
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: var(--color-muted-foreground);
        margin: 0 0 4px;
      }}
      .email-value {{
        font-size: {t.typography.font_size_md};
        font-weight: {t.typography.font_weight_semibold};
        color: var(--color-foreground);
        margin: 0;
      }}
      .email-button {{
        display: inline-block;
        background-color: var(--color-primary);
        color: var(--color-primary-foreground);
        text-decoration: none;
        padding: {t.spacing.md} {t.spacing.xl};
        border-radius: {t.radius.lg};
        font-weight: {t.typography.font_weight_semibold};
        font-size: {t.typography.font_size_md};
        text-align: center;
        border: none;
        cursor: pointer;
      }}
      .email-button:hover {{
        background-color: {t.colors.primary_hover};
      }}
      .email-footer {{
        background-color: var(--color-muted);
        border-top: 1px solid var(--color-border);
        padding: {t.spacing.lg} {t.spacing.lg};
        text-align: center;
      }}
      .email-footer-text {{
        font-size: {t.typography.font_size_xs};
        color: var(--color-muted-foreground);
        margin: 0;
      }}
      .status-badge {{
        display: inline-block;
        padding: 6px 12px;
        border-radius: {t.radius.pill};
        font-size: {t.typography.font_size_xs};
        font-weight: {t.typography.font_weight_semibold};
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }}
      .status-badge--ready {{
        background-color: var(--color-success-bg);
        color: var(--color-success);
        border: 1px solid var(--color-success-border);
      }}
      .status-badge--unavailable {{
        background-color: var(--color-warning-bg);
        color: #92400e;
        border: 1px solid var(--color-warning-border);
      }}
      .status-badge--failed {{
        background-color: var(--color-destructive-bg);
        color: var(--color-destructive);
        border: 1px solid var(--color-destructive-border);
      }}
      .status-badge--offline {{
        background-color: var(--color-muted);
        color: var(--color-muted-foreground);
        border: 1px solid var(--color-border);
      }}
      .info-box {{
        background-color: var(--color-warning-bg);
        border: 1px solid var(--color-warning-border);
        border-radius: {t.radius.lg};
        padding: {t.spacing.md} {t.spacing.lg};
        margin: {t.spacing.lg} 0;
      }}
      .info-box-title {{
        font-weight: {t.typography.font_weight_semibold};
        color: #92400e;
        margin: 0 0 {t.spacing.xs};
        display: flex;
        align-items: center;
        gap: 6px;
      }}
      .info-box-text {{
        color: #92400e;
        font-size: {t.typography.font_size_sm};
        line-height: {t.typography.line_height_relaxed};
        margin: 0;
      }}
      .meeting-block {{
        background-color: var(--color-info-bg);
        border: 1px solid var(--color-info-border);
        border-radius: {t.radius.lg};
        padding: {t.spacing.lg};
        margin-bottom: {t.spacing.lg};
      }}
      .meeting-block--ready {{
        background-color: var(--color-success-bg);
        border-color: var(--color-success-border);
      }}
      .meeting-block--unavailable {{
        background-color: var(--color-warning-bg);
        border-color: var(--color-warning-border);
      }}
      .meeting-link {{
        display: inline-block;
        background-color: var(--color-info);
        color: var(--color-info-foreground);
        text-decoration: none;
        padding: {t.spacing.sm} {t.spacing.md};
        border-radius: {t.radius.md};
        font-weight: {t.typography.font_weight_semibold};
        font-size: {t.typography.font_size_sm};
        margin-top: {t.spacing.md};
      }}
      .divider {{
        height: 1px;
        background-color: var(--color-border);
        margin: {t.spacing.lg} 0;
      }}
      .code-box {{
        background: linear-gradient(135deg, var(--color-muted) 0%, var(--color-background) 100%);
        border: 2px dashed var(--color-border);
        border-radius: {t.radius.lg};
        padding: {t.spacing.lg} {t.spacing.xl};
        text-align: center;
        margin: {t.spacing.lg} 0;
      }}
      .code-value {{
        font-family: 'SF Mono', 'Fira Code', Monaco, monospace;
        font-size: {t.typography.font_size_3xl};
        font-weight: {t.typography.font_weight_bold};
        letter-spacing: 0.5em;
        background: linear-gradient(135deg, var(--color-primary) 0%, #fc0d5e 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
      }}
    </style>
    """


# Template-specific color overrides
TEMPLATE_THEMES = {
    "otp": {
        "header_bg": "linear-gradient(135deg, var(--color-primary) 0%, #fc0d5e 100%)",
        "header_icon": "🔐",
        "header_title": "Verification Required",
        "header_subtitle": "Enter this code to continue",
    },
    "auth_register": {
        "header_bg": "linear-gradient(135deg, #ff6b00 0%, #e55f00 100%)",
        "header_icon": "🔐",
        "header_title": "Verify Your Email",
        "header_subtitle": "Enter this code to finish creating your account",
    },
    "auth_password_reset": {
        "header_bg": "linear-gradient(135deg, #ff6b00 0%, #e55f00 100%)",
        "header_icon": "🔐",
        "header_title": "Reset Your Password",
        "header_subtitle": "Enter this code to set a new password",
    },
    "auth_signin": {
        "header_bg": "linear-gradient(135deg, #ff6b00 0%, #e55f00 100%)",
        "header_icon": "🔐",
        "header_title": "Sign-In Verification",
        "header_subtitle": "Enter this code to confirm it's really you",
    },
    "booking_confirmation_customer": {
        "header_bg": "linear-gradient(135deg, var(--color-success) 0%, #059669 100%)",
        "header_icon": "✔",
        "header_title": "You're All Set!",
        "header_subtitle": "Your appointment has been confirmed",
    },
    "booking_confirmation_admin": {
        "header_bg": "linear-gradient(135deg, var(--color-info) 0%, #1d4ed8 100%)",
        "header_icon": "📅",
        "header_title": "New Booking Received!",
        "header_subtitle": "via DotStark AI Booking System",
    },
    "booking_cancellation": {
        "header_bg": "linear-gradient(135deg, var(--color-destructive) 0%, #dc2626 100%)",
        "header_icon": "✖",
        "header_title": "Appointment Cancelled",
        "header_subtitle": "Your booking has been cancelled",
    },
}