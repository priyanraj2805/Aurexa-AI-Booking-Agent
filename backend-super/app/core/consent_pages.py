"""Shared consent-page rendering (Phase 15/17).

Renders the public, unauthenticated HTML page an org admin reaches from an
emailed consent link (impersonation, transcript peek). Every page is built
from trusted server-side data only; the token itself is never rendered.
"""
from fastapi.responses import HTMLResponse

CONSENT_PAGE_STYLES = """
body{font-family:'Segoe UI',Arial,sans-serif;background:#0f172a;color:#e2e8f0;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.card{background:#1e293b;border:1px solid #334155;border-radius:12px;
padding:32px;max-width:460px;width:90%;text-align:center}
h1{font-size:20px;margin:0 0 8px}
p{color:#94a3b8;font-size:14px;line-height:1.5}
.badge{display:inline-block;margin:12px 0;padding:6px 14px;border-radius:999px;
font-size:12px;font-weight:600}
.ok{background:#052e16;color:#4ade80;border:1px solid #166534}
.bad{background:#450a0a;color:#f87171;border:1px solid #7f1d1d}
.info{background:#082f49;color:#38bdf8;border:1px solid #075985}
button{margin-top:16px;background:#4f46e5;color:#fff;border:none;border-radius:8px;
padding:10px 22px;font-size:14px;font-weight:600;cursor:pointer}
button:hover{background:#4338ca}
a{color:#38bdf8}
"""


def consent_page(title: str, badge: str, tone: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DotStark — {title}</title><style>{CONSENT_PAGE_STYLES}</style></head>
<body><div class="card">
<h1>{title}</h1><span class="badge {tone}">{badge}</span>
{body}
</div></body></html>"""
    )