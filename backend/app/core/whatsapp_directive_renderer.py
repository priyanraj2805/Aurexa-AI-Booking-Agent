"""WhatsAppDirectiveRenderer — pure mapper.

BookingDirective -> WhatsApp interactive message dict.
No booking/business logic. Decides buttons vs list vs text.
Extensible: add new type branch, no engine change.
Reuses whatsapp_client caps (3 buttons, 10 rows).
"""

from typing import Dict


def render(directive) -> Dict:
    """Render a BookingDirective row/dict to WhatsApp shape.

    Accepts ORM object or dict with .type, .title, .description, .options.
    Returns same shape as format_action_for_whatsapp: {type, body, ...}
    """
    dtype = getattr(directive, "type", None) or getattr(directive, "directive_type", None) or (directive.get("type") if isinstance(directive, dict) else None)
    title = getattr(directive, "title", "") or (directive.get("title") if isinstance(directive, dict) else "")
    desc = getattr(directive, "description", "") or (directive.get("description") if isinstance(directive, dict) else "")
    options = getattr(directive, "options", None)
    if options is None and isinstance(directive, dict):
        options = directive.get("options", [])
    body = f"{title}\n\n{desc}".strip() if desc else str(title)
    # Normalize options to list of {id,label}
    norm = []
    for o in options or []:
        if isinstance(o, str):
            norm.append({"id": o, "label": o})
        elif isinstance(o, dict):
            norm.append({"id": str(o.get("id") or o.get("value") or o.get("label") or ""), "label": str(o.get("label") or o.get("id") or "")})
    # SELECT_DATE / SELECT_TIME / SELECT_SLOT -> buttons (<=3) or list (>3)
    if dtype in ("SELECT_DATE", "SELECT_TIME", "SELECT_SLOT"):
        if not norm:
            return {"type": "text", "body": body}
        if len(norm) <= 3:
            return {
                "type": "interactive_buttons",
                "body": body,
                "buttons": [{"id": o["id"][:64], "title": o["label"][:20]} for o in norm[:3]],
            }
        rows = [{"id": o["id"][:64], "title": o["label"][:24]} for o in norm[:10]]
        return {
            "type": "interactive_list",
            "body": body,
            "button_text": "Choose date" if dtype == "SELECT_DATE" else "Choose time",
            "sections": [{"title": "Available options", "rows": rows}],
        }
    if dtype == "CONFIRM_BOOKING":
        # Expect two options: confirm / change (if not provided, synthesize)
        if not norm:
            norm = [{"id": "confirm_booking", "label": "CONFIRM"}, {"id": "change_booking", "label": "CHANGE"}]
        # Limit to 3, but confirm uses 2
        return {
            "type": "interactive_buttons",
            "body": body,
            "buttons": [{"id": o["id"][:64], "title": o["label"][:20]} for o in norm[:3]],
        }
    if dtype in ("CHANGE_SELECTION", "CANCEL_BOOKING"):
        if not norm:
            return {"type": "text", "body": body}
        if len(norm) <= 3:
            return {"type": "interactive_buttons", "body": body, "buttons": [{"id": o["id"][:64], "title": o["label"][:20]} for o in norm[:3]]}
        rows = [{"id": o["id"][:64], "title": o["label"][:24]} for o in norm[:10]]
        return {"type": "interactive_list", "body": body, "button_text": "Choose", "sections": [{"title": "Options", "rows": rows}]}
    return {"type": "text", "body": body}
