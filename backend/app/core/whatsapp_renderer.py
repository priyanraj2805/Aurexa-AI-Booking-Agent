"""
WhatsApp Renderer — structured response → transport wa_msg.

Maps StructuredWhatsAppResponse from whatsapp_presenter to the existing
wa_msg shapes understood by Baileys (interactive) and Cloud (text).
Preserves authoritative option IDs/values, respects transport caps.
No LLM, no state mutations.
"""
from __future__ import annotations

from typing import Any, Dict


def _truncate(text: str, limit: int = 4096) -> str:
    t = text or ""
    return t[:limit] if len(t) > limit else t


def render_structured(
    structured: Any,
    *,
    transport: str = "baileys",
) -> Dict[str, Any]:
    """
    structured: StructuredWhatsAppResponse or dict with type/body/intro/outro/options/directive_type/directive_id
    transport: "baileys" | "cloud" — cloud always text+numbered fallback
    Returns wa_msg dict {type, body, ...} compatible with existing send layers.
    """
    # Normalize input
    stype = getattr(structured, "type", None) or (structured.get("type") if isinstance(structured, dict) else None) or "text"
    body = getattr(structured, "body", None)
    if body is None and isinstance(structured, dict):
        body = structured.get("body")
    intro = getattr(structured, "intro", None)
    if intro is None and isinstance(structured, dict):
        intro = structured.get("intro")
    outro = getattr(structured, "outro", None)
    if outro is None and isinstance(structured, dict):
        outro = structured.get("outro")
    dtype = getattr(structured, "directive_type", None)
    if dtype is None and isinstance(structured, dict):
        dtype = structured.get("directive_type")
    did = getattr(structured, "directive_id", None)
    if did is None and isinstance(structured, dict):
        did = structured.get("directive_id")
    options = getattr(structured, "options", None)
    if options is None and isinstance(structured, dict):
        options = structured.get("options")

    # TEXT
    if stype == "text":
        txt = _truncate(str(body or intro or ""))
        # WhatsApp ** → * (existing behavior)
        txt = txt.replace("**", "*")
        return {"type": "text", "body": txt}

    # DIRECTIVE
    if stype == "directive":
        # Build body from LLM intro/outro only; options are authoritative
        parts = []
        if intro:
            parts.append(str(intro).strip())
        if outro:
            parts.append(str(outro).strip())
        combined_body = "\n\n".join([p for p in parts if p]) or (str(body or intro or dtype or "Please make a selection").strip())
        combined_body = combined_body.replace("**", "*")
        # Preserve authoritative options verbatim (ids/labels/values)
        norm_options = []
        for o in options or []:
            if isinstance(o, dict):
                norm_options.append({"id": str(o.get("id") or o.get("value") or ""), "label": str(o.get("label") or o.get("title") or o.get("id") or "")})
            elif isinstance(o, str):
                norm_options.append({"id": o, "label": o})

        # Build a minimal directive dict for existing renderer
        directive_for_render: Dict[str, Any] = {
            "type": str(dtype or "SELECT_SLOT"),
            "title": combined_body,
            "description": "",
            "options": [{"id": o["id"], "label": o["label"]} for o in norm_options],
        }

        # Cloud transport: never interactive, use numbered text (existing whatsapp_cloud behavior)
        if (transport or "baileys").lower() == "cloud":
            # Import here to avoid circular
            try:
                from app.core.whatsapp_directive_renderer import render as _render
                from app.core.whatsapp_cloud import wa_msg_to_text
                wa_msg = _render(directive_for_render)
                txt = wa_msg_to_text(wa_msg)
                return {"type": "text", "body": _truncate(txt, 4096)}
            except Exception:
                # Fallback numbered
                lines = [combined_body, ""]
                for i, o in enumerate(norm_options[:10], 1):
                    lines.append(f"{i}. {o['label']}")
                if norm_options:
                    lines.append("")
                    lines.append("Reply with the option number.")
                return {"type": "text", "body": _truncate("\n".join(lines), 4096)}

        # Baileys: delegate to existing interactive renderer (preserves caps)
        try:
            from app.core.whatsapp_directive_renderer import render as _render

            wa_msg = _render(directive_for_render)
            # Ensure body is our intro/outro (renderer already set title as body)
            # wa_msg body already = combined_body
            return wa_msg
        except Exception:
            # Fallback text
            lines = [combined_body, ""]
            for i, o in enumerate(norm_options[:10], 1):
                lines.append(f"{i}. {o['label']}")
            if norm_options:
                lines.append("")
                lines.append("Reply with the option number.")
            return {"type": "text", "body": _truncate("\n".join(lines), 4096)}

    # Unknown -> text
    return {"type": "text", "body": _truncate(str(body or intro or ""), 4096)}
