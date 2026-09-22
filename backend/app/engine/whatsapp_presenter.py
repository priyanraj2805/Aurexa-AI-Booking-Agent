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

    # ── Objection (mid-booking concerns) ──────────────────────────

    async def present_objection(
        self, brief: Dict[str, Any]
    ) -> StructuredWhatsAppResponse:
        """Acknowledge a booking objection in the admin-chosen tone.

        The LLM controls WORDING only. Every fact must come from `brief`
        (business name, field label/description, required list, pending
        step). Deterministic fallback below uses the same live slots, so
        the reply is correct even with no LLM.
        """
        category = str(brief.get("category") or "")
        biz = str(brief.get("business_name") or "").strip()
        tone = str(brief.get("tone") or _FALLBACK_TONE)
        label = str(brief.get("field_label") or brief.get("field") or "details").strip()
        desc = str(brief.get("field_description") or "").strip()
        required = list(brief.get("required_labels") or [])
        step_title = str(brief.get("step_title") or "").strip()
        step_prompt = str(brief.get("step_prompt") or "").strip()

        why_sentence = f"We ask for your {label}."
        if desc:
            why_sentence = f"We ask for your {label} — {desc.rstrip('.')}."
        elif required:
            need_list = ", ".join(required[:6])
            why_sentence = (
                f"To confirm your booking we need {need_list} — "
                f"your {label} is one of them."
            )

        if category == "no_booking":
            fallback = (
                "No problem at all — just say hi whenever you'd like to book"
                f"{f' at {biz}' if biz else ''} and I'll pick up right where we left off."
            )
        elif category == "privacy":
            fallback = (
                f"Totally understand — your {label} stays with"
                f"{f' {biz}'} and is only used to confirm and manage your booking. "
                f"{step_title + ' ' if step_title else ''}Whenever you're ready, {step_prompt or 'just reply to continue.'}"
            )
        elif category == "refuse" and not brief.get("field_required", True):
            fallback = (
                f"No problem skipping that — {why_sentence} "
                f"{step_title + ' ' if step_title else ''}Whenever you're ready, {step_prompt or 'just reply to continue.'}"
            )
        else:
            # why_needed, refuse-required, or anything else: acknowledge,
            # explain from live slots, re-anchor to the pending step.
            at_biz = f" at {biz}" if biz else ""
            clean_desc = desc.rstrip(".") if desc else ""
            fallback = (
                f"Good question — we ask for your {label}{at_biz}"
                f"{f' — {clean_desc}' if clean_desc else ''}. "
                f"{step_title + ' ' if step_title else ''}Whenever you're ready, {step_prompt or 'just reply to continue.'}"
            )

        system = (
            "You are Aurexa, WhatsApp booking assistant handling a customer "
            "concern mid-booking.\n"
            "Rules:\n"
            "- Acknowledge the concern warmly, then answer it. Never ignore it.\n"
            "- Use ONLY these facts (business, field, requirement, pending step). "
            "Do NOT invent names, prices, hours, policies, or contact info.\n"
            "- Keep the pending step alive: end by guiding back to it.\n"
            "- Respect the requested tone for wording/emoji/length, but not facts.\n"
            "- WhatsApp-friendly, 2-4 short sentences.\n"
        )
        user_prompt = (
            f"{_tone_line(tone)}\n"
            f"Business: {biz or 'the business'}\n"
            f"Concern type: {category}\n"
            f"Facts: {why_sentence}\n"
            f"Pending step: {step_title or 'continue booking'}\n"
            f"Step prompt: {step_prompt or 'ask for the pending detail'}\n"
            "Task: acknowledge the concern and guide back to the pending step."
        )
        try:
            llm = self._get_llm()
            if not llm or not settings.LLM_API_KEY:
                raise RuntimeError("no_llm")
            raw = await llm._call_llm(user_prompt, system_prompt=system, json_mode=False)
            body = (raw or "").strip()
            if not body:
                raise RuntimeError("empty")
            body = _sanitize_intro_outro(body, max_chars=900)
            if not body:
                raise RuntimeError("empty_after_sanitize")
            logger.info(
                "whatsapp.presenter.objection",
                extra={"event": "whatsapp.presenter.objection", "category": category},
            )
            return StructuredWhatsAppResponse(type="text", body=body)
        except Exception as e:
            logger.warning(
                "whatsapp.presenter.objection.fallback",
                extra={"event": "whatsapp.presenter.objection.fallback", "reason": str(e)[:120]},
            )
            return StructuredWhatsAppResponse(type="text", body=fallback[:1200])

    # ── Directive ───────────────────────────────────────────────

    async def present_directive(
        self,
        *,
        directive,
        tone: Optional[str] = None,
        org_name: Optional[str] = None,
        # Contextual params to avoid repetitive greeting — WhatsApp is ongoing, not per-turn greeting
        user_message: Optional[str] = None,
        is_first_turn: Optional[bool] = None,
        turn_index: Optional[int] = None,
        collected_data: Optional[Dict[str, Any]] = None,
        agent_name: Optional[str] = None,
        business_name: Optional[str] = None,
        customer_name: Optional[str] = None,
    ) -> StructuredWhatsAppResponse:
        """
        directive: BookingDirective ORM or dict with type/title/description/options/id
        Returns StructuredWhatsAppResponse with intro/outro + authoritative options.
        LLM fallback is directive.title/description verbatim (no greeting).
        LLM must NOT invent greeting on every turn — only if is_first_turn or user greeted.
        """
        dtype = getattr(directive, "type", None) or (directive.get("type") if isinstance(directive, dict) else "") or ""
        title = getattr(directive, "title", "") or (directive.get("title") if isinstance(directive, dict) else "") or ""
        desc = getattr(directive, "description", "") or (directive.get("description") if isinstance(directive, dict) else "") or ""
        did = str(getattr(directive, "id", "") or (directive.get("id") if isinstance(directive, dict) else "") or "")
        raw_options = getattr(directive, "options", None)
        if raw_options is None and isinstance(directive, dict):
            raw_options = directive.get("options") or []
        options = list(raw_options or [])

        # Resolve names correctly: agent/business vs customer — never confuse customer as business
        # org_name is legacy alias; prefer explicit agent/business
        resolved_agent = (agent_name or "").strip() or None
        resolved_business = (business_name or org_name or "Aurexa").strip() or "Aurexa"
        resolved_customer = (customer_name or (collected_data or {}).get("name") or "").strip() if isinstance(collected_data, dict) else (customer_name or "").strip()
        # Never use customer name as business/agent
        if resolved_customer and resolved_customer.lower() == resolved_business.lower():
            # If org name coincidentally equals customer (e.g., test org "Priyanshu"), keep business as Aurexa fallback for intro
            resolved_business = (business_name or "Aurexa").strip() or "Aurexa"

        # Fallback without LLM — deterministic, no greeting repetition
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

        # Build prompt: LLM generates ONLY intro/outro, never options — contextual, no repetitive greeting
        options_preview = "\n".join(f"- {o.get('label')}" for o in options[:5])
        # Conversation context for greeting decision
        is_first = bool(is_first_turn) if is_first_turn is not None else (turn_index == 0 if isinstance(turn_index, int) else False)
        # Detect if user just greeted (simple, avoid greeting on every turn)
        user_is_greeting = False
        if isinstance(user_message, str) and user_message.strip():
            low = user_message.strip().lower()
            user_is_greeting = low in {"hi", "hii", "hiii", "hello", "hey", "hola", "namaste"} or low.startswith(("hi ", "hello", "hey ", "hii ", "namaste"))
        # Booking context snippet
        sel_date = (collected_data or {}).get("date") if isinstance(collected_data, dict) else None
        sel_time = (collected_data or {}).get("time") if isinstance(collected_data, dict) else None
        system = (
            "You are Aurexa, the WhatsApp presentation layer for a booking assistant.\n"
            "STRICT RULES:\n"
            "- You control ONLY wording, tone, explanation, formatting, emoji usage, intro/outro.\n"
            "- You must NOT invent or modify: booking state, availability, doctors/members, slots, prices, OTP, booking IDs, directive IDs, option IDs/values.\n"
            "- The authoritative options will be supplied by the backend — you must NOT list alternative options.\n"
            "- WhatsApp is an ongoing conversation. Do NOT prepend 'Hello/Hi/Welcome/Thank you for reaching out' on every turn.\n"
            "  Greeting ONLY if: (a) is_first_turn=true, or (b) user just greeted (user_is_greeting=true), otherwise NO greeting.\n"
            "- Booking responses must be contextual to the current directive, not generic:\n"
            "  * SELECT_DATE → talk about dates (e.g., 'Sure! Here are the available dates. Please choose one.')\n"
            "  * SELECT_TIME/SELECT_SLOT → acknowledge selected date and talk about times (e.g., 'Great! 17 September is available. Here are the times:')\n"
            "  * CONFIRM_BOOKING → concise confirmation summary intro (e.g., 'You're almost done! Please confirm:')\n"
            "- Do NOT add generic closing sentences like 'Do you need any further assistance?' or 'We look forward to confirming' on every message. Only add outro when genuinely helpful (0-1 sentence), else empty.\n"
            "- Keep intro to 1-2 short sentences, outro to 0-1 sentence. WhatsApp-friendly, tone-aware.\n"
            "- Use business/agent name correctly: business/agent is the organization, customer is separate — never say 'Hello from <customer_name>'.\n"
            "- Output JSON only: {\"intro\": \"...\", \"outro\": \"...\"}\n"
        )
        user_prompt = (
            f"{_tone_line(tone)}\n"
            f"Business: {resolved_business}\n"
            f"Agent name: {resolved_agent or 'Aurexa Assistant'}\n"
            f"Customer name: {resolved_customer or '(unknown)'}\n"
            f"Conversation: is_first_turn={is_first}, user_is_greeting={user_is_greeting}, turn_index={turn_index}\n"
            f"User last message: {repr((user_message or '')[:200])}\n"
            f"Booking state: collected={repr({k: v for k, v in (collected_data or {}).items() if k in ('name','email','phone','date','time')}) if isinstance(collected_data, dict) else '{}'}, selected_date={sel_date}, selected_time={sel_time}\n"
            f"Directive type: {dtype}\n"
            f"Directive title: {title}\n"
            f"Directive description: {desc}\n"
            f"Authoritative option labels (for context ONLY, do not output IDs):\n{options_preview or '(no options)'}\n"
            f"Task: Write intro and optional outro for this WhatsApp message following the contextual greeting and closing rules above.\n"
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
            # Ensure horizon/booking-hours from directive description is in same message (smooth)
            # Append desc only if intro doesn't already contain that kind of info (avoid duplication like double horizon)
            if desc and desc.strip():
                low_desc = desc.strip().lower()
                low_intro = intro.lower() if intro else ""
                is_horizon = "we are available" in low_desc
                is_hours = "booking hours" in low_desc
                has_horizon = "we are available" in low_intro
                has_hours = "booking hours" in low_intro
                if is_horizon and not has_horizon:
                    intro = f"{intro}\n\n{desc.strip()}" if intro else desc.strip()
                elif is_hours and not has_hours:
                    intro = f"{intro}\n\n{desc.strip()}" if intro else desc.strip()
                elif not is_horizon and not is_hours and low_desc not in low_intro:
                    # Fallback for other descs
                    if desc.strip() not in (intro or ""):
                        intro = f"{intro}\n\n{desc.strip()}" if intro else desc.strip()
            # Enforce contextual greeting / closing rules (backend is truth)
            if not is_first and not user_is_greeting:
                # Strip leading greeting ONLY (hello/hi/hey/welcome), NOT "Thank you for providing..." which is contextual acknowledge
                intro = re.sub(
                    r"^\s*(hello[^\n]{0,60}?[\.!]?\s+|hi[^\n]{0,60}?[\.!]?\s+|hey[^\n]{0,60}?[\.!]?\s+|welcome[^\n]{0,60}?[\.!]?\s+)+",
                    "",
                    intro,
                    flags=re.I,
                ).strip()
                # Remove "Hello from <business>" leakage but keep "Thank you for providing..."
                intro = re.sub(r"^\s*hello\s+from\s+[^\n]*?[\.!]?\s+", "", intro, flags=re.I).strip()
            # Strip generic closing sentences on non-terminal steps
            if outro and dtype not in ("CONFIRM_BOOKING", "BOOKING_CONFIRMED"):
                low_out = outro.lower()
                if any(
                    phrase in low_out
                    for phrase in [
                        "further assistance",
                        "let us know",
                        "look forward",
                        "do you need any",
                        "need any further",
                        "please let us know",
                        "we are here to assist",
                        "here to assist if you have any questions",
                        "assist if you have any questions",
                    ]
                ):
                    outro = ""
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
