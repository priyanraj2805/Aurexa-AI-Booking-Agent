"""
WhatsApp Presenter — LLM is ONLY presentation/knowledge, never state.

WhatsApp-only. Booking engine + directive system remain authoritative.
LLM may control: wording, tone, greeting, explanation, formatting, emoji, intro/outro.
LLM must NOT control: booking state, missing_fields, member IDs, slot IDs, availability,
prices, OTP, booking creation, directive IDs, option IDs/values, DB writes.

Backend determines text vs directive. For directives, options are supplied verbatim;
LLM generates only intro/outro around them.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredWhatsAppResponse:
    type: str  # "text" | "directive"
    body: Optional[str] = None
    intro: Optional[str] = None
    outro: Optional[str] = None
    directive_type: Optional[str] = None
    directive_id: Optional[str] = None
    options: Optional[List[Dict[str, Any]]] = None


# Tone map — reuse OrgConfig.custom_prompts.tone values (professional/friendly/casual/formal/empathetic)
_TONE_GUIDANCE = {
    "professional": "Tone: Professional — concise, polite, no emojis, clear sentences.",
    "friendly": "Tone: Friendly — warm, conversational, light emojis (1-2 max), encouraging.",
    "casual": "Tone: Casual — relaxed, short sentences, friendly emojis.",
    "formal": "Tone: Formal — respectful, full sentences, minimal emojis.",
    "empathetic": "Tone: Empathetic — caring, reassuring, gentle emojis.",
    "warm": "Tone: Warm — kind, welcoming, soft emojis.",
    "concise": "Tone: Concise — very short, direct, no filler.",
    "luxury": "Tone: Luxury/Premium — refined, premium wording, subtle emojis.",
}

_FALLBACK_TONE = "friendly"


def _tone_line(tone: Optional[str]) -> str:
    key = (tone or _FALLBACK_TONE).strip().lower()
    return _TONE_GUIDANCE.get(key, _TONE_GUIDANCE[_FALLBACK_TONE]) + f" (requested: {key})"


def _sanitize_intro_outro(text: str, max_chars: int = 600) -> str:
    t = (text or "").strip()
    # Strip any leaked option IDs / attempt to invent availability
    # Keep within WhatsApp body limits
    if len(t) > max_chars:
        t = t[: max_chars - 3].rstrip() + "..."
    # Remove internal IDs if leaked
    t = re.sub(r"\b(member_|slot_|date_)[a-zA-Z0-9_]+\b", "", t)
    return t.strip()


class WhatsAppPresenter:
    """
    LLM presentation layer for WhatsApp only. No DB writes.
    Uses existing LLMOrchestrator via lazy import to avoid circular deps.
    """

    def __init__(self, llm=None):
        self._llm = llm

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        try:
            from app.engine.orchestrator import LLMOrchestrator

            return LLMOrchestrator()
        except Exception:
            return None

    # ── Directive ───────────────────────────────────────────────

    async def present_directive(
        self,
        *,
        directive,
        tone: Optional[str] = None,
        org_name: Optional[str] = None,
    ) -> StructuredWhatsAppResponse:
        """
        directive: BookingDirective ORM or dict with type/title/description/options/id
        Returns StructuredWhatsAppResponse with intro/outro + authoritative options.
        LLM fallback is directive.title/description verbatim.
        """
        dtype = getattr(directive, "type", None) or (directive.get("type") if isinstance(directive, dict) else "") or ""
        title = getattr(directive, "title", "") or (directive.get("title") if isinstance(directive, dict) else "") or ""
        desc = getattr(directive, "description", "") or (directive.get("description") if isinstance(directive, dict) else "") or ""
        did = str(getattr(directive, "id", "") or (directive.get("id") if isinstance(directive, dict) else "") or "")
        raw_options = getattr(directive, "options", None)
        if raw_options is None and isinstance(directive, dict):
            raw_options = directive.get("options") or []
        options = list(raw_options or [])

        # Fallback without LLM
        fallback_intro = f"{title}\n\n{desc}".strip() if desc else title.strip()
        if not fallback_intro:
            fallback_intro = "Please make a selection."

        llm = self._get_llm()
        if not llm or not settings.LLM_API_KEY:
            logger.info(
                "whatsapp.presenter.fallback",
                extra={
                    "event": "whatsapp.presenter.fallback",
                    "directive_type": dtype,
                    "directive_id": did[:8] if did else None,
                    "reason": "no_llm",
                    "tone": tone or _FALLBACK_TONE,
                },
            )
            return StructuredWhatsAppResponse(
                type="directive",
                intro=fallback_intro,
                outro=None,
                directive_type=dtype,
                directive_id=did or None,
                options=options,
            )

        # Build prompt: LLM generates ONLY intro/outro, never options
        options_preview = "\n".join(f"- {o.get('label')}" for o in options[:5])
        system = (
            "You are Aurexa, the WhatsApp presentation layer for a booking assistant.\n"
            "STRICT RULES:\n"
            "- You control ONLY wording, tone, greeting, explanation, formatting, emoji usage, intro/outro.\n"
            "- You must NOT invent or modify: booking state, availability, doctors/members, slots, prices, OTP, booking IDs, directive IDs, option IDs/values.\n"
            "- The authoritative options will be supplied by the backend — you must NOT list alternative options.\n"
            "- Keep intro to 1-3 short sentences, outro to 0-1 sentence. WhatsApp-friendly.\n"
            "- Respect the requested tone. Do not add disallowed content.\n"
            "- Output JSON only: {\"intro\": \"...\", \"outro\": \"...\"}\n"
        )
        user_prompt = (
            f"{_tone_line(tone)}\n"
            f"Organization: {org_name or 'Aurexa'}\n"
            f"Directive type: {dtype}\n"
            f"Directive title: {title}\n"
            f"Directive description: {desc}\n"
            f"Authoritative option labels (for context ONLY, do not output IDs):\n{options_preview or '(no options)'}\n"
            f"Task: Write intro and optional outro for this WhatsApp message. "
            f"Intro should be attractive and conversational in the requested tone. "
            f"Outro should be a brief helpful follow-up question if appropriate, else empty.\n"
            f"Return JSON with keys intro, outro."
        )
        try:
            # Short timeout, 300 tokens max
            raw = await llm._call_llm(user_prompt, system_prompt=system, json_mode=True)
            if not raw:
                raise RuntimeError("empty llm response")
            # Robust JSON salvage
            clean = raw
            if "```json" in raw:
                clean = raw.split("```json")[-1].split("```")[0].strip()
            elif "```" in raw:
                clean = raw.split("```")[-1].split("```")[0].strip()
            m = re.search(r"\{.*\}", clean, re.DOTALL)
            if m:
                clean = m.group(0)
            parsed = json.loads(clean)
            intro = _sanitize_intro_outro(str(parsed.get("intro") or ""), 600)
            outro = _sanitize_intro_outro(str(parsed.get("outro") or ""), 300)
            if not intro:
                intro = fallback_intro
            logger.info(
                "whatsapp.presenter.directive",
                extra={
                    "event": "whatsapp.presenter.directive",
                    "directive_type": dtype,
                    "directive_id": did[:8] if did else None,
                    "tone": tone or _FALLBACK_TONE,
                    "has_outro": bool(outro),
                },
            )
            return StructuredWhatsAppResponse(
                type="directive",
                intro=intro,
                outro=outro or None,
                directive_type=dtype,
                directive_id=did or None,
                options=options,
            )
        except Exception as e:
            logger.warning(
                "whatsapp.presenter.fallback",
                extra={
                    "event": "whatsapp.presenter.fallback",
                    "directive_type": dtype,
                    "directive_id": did[:8] if did else None,
                    "reason": str(e)[:120],
                    "tone": tone or _FALLBACK_TONE,
                },
            )
            return StructuredWhatsAppResponse(
                type="directive",
                intro=fallback_intro,
                outro=None,
                directive_type=dtype,
                directive_id=did or None,
                options=options,
            )

    # ── Knowledge (text) ───────────────────────────────────────

    async def present_knowledge(
        self,
        *,
        user_question: str,
        retrieved_chunks: Optional[List[Dict[str, Any]]] = None,
        tone: Optional[str] = None,
        org_name: Optional[str] = None,
    ) -> StructuredWhatsAppResponse:
        """
        Answer informational question using ONLY retrieved_chunks (PDF/website) + live org data.
        If no relevant chunks, return honest fallback.
        """
        chunks = list(retrieved_chunks or [])
        llm = self._get_llm()
        # Build retrieved context
        if chunks:
            knowledge = "\n\n".join(
                f"[{i+1}] ({c.get('source_type') or c.get('source') or 'knowledge'}) {c.get('source_label') or ''}\n{(c.get('content') or c.get('snippet') or '')[:900]}"
                for i, c in enumerate(chunks[:5])
            )
        else:
            knowledge = ""

        if not chunks:
            # Honest fallback without LLM if possible
            msg = "Sorry, I don't have that information available right now. Would you like to book an appointment or contact the organization for details?"
            # Still allow tone-aware LLM rewrite if available, but with strict no-hallucination system
            if not llm or not settings.LLM_API_KEY:
                return StructuredWhatsAppResponse(type="text", body=msg)

        system = (
            "You are Aurexa, WhatsApp knowledge assistant.\n"
            "Rules:\n"
            "- Answer using ONLY the retrieved organization knowledge provided (PDFs/website) and live org data.\n"
            "- Do NOT invent doctors, departments, services, prices, hours, availability, policies, or contact info.\n"
            "- If retrieved knowledge does not support the answer, say you don't have that information and offer booking/contact.\n"
            "- Respect the requested tone for wording/emoji/length, but not facts.\n"
            "- WhatsApp-friendly, concise (2-6 sentences).\n"
        )
        user_prompt = (
            f"{_tone_line(tone)}\n"
            f"Organization: {org_name or 'Aurexa'}\n"
            f"User question: {user_question}\n"
            f"Retrieved organization knowledge:\n{knowledge or '(none — no relevant chunks)'}\n"
            f"Task: Answer the user question naturally for WhatsApp. Use retrieved knowledge if relevant. If none relevant, honestly say you don't have it."
        )
        try:
            llm = self._get_llm()
            if not llm or not settings.LLM_API_KEY:
                raise RuntimeError("no_llm")
            raw = await llm._call_llm(user_prompt, system_prompt=system, json_mode=False)
            if not raw:
                raise RuntimeError("empty")
            body = raw.strip()
            # Basic hallucination guard: if no chunks, ensure fallback phrase present
            if not chunks and "don't have" not in body.lower() and "not have" not in body.lower():
                body = "Sorry, I don't have that information available right now. " + body
            if len(body) > 1500:
                body = body[:1497] + "..."
            logger.info(
                "whatsapp.presenter.knowledge",
                extra={
                    "event": "whatsapp.presenter.knowledge",
                    "tone": tone or _FALLBACK_TONE,
                    "chunks": len(chunks),
                    "source_types": ",".join(sorted({c.get('source_type') or '' for c in chunks})),
                },
            )
            return StructuredWhatsAppResponse(type="text", body=body)
        except Exception as e:
            logger.warning(
                "whatsapp.presenter.knowledge.fallback",
                extra={"event": "whatsapp.presenter.knowledge.fallback", "reason": str(e)[:120], "tone": tone or _FALLBACK_TONE},
            )
            if chunks:
                # Extractive fallback: concatenate snippets
                fallback = "\n\n".join((c.get('content') or c.get('snippet') or "")[:400] for c in chunks[:2])
                return StructuredWhatsAppResponse(type="text", body=fallback[:1200] or "Here's what I found — let me know if you'd like to book.")
            return StructuredWhatsAppResponse(
                type="text", body="Sorry, I don't have that information available right now. Would you like to book an appointment?"
            )
