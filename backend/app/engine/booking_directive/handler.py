"""DirectiveHandler — processes structured directive responses without LLM.

Flow: handle_response -> verify -> complete -> update booking state -> next directive
Idempotent via directive status + is_duplicate on messageId.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.booking_directive import service as directive_service
from app.models.booking_directive import DirectiveStatus


async def handle_directive_response(
    db: AsyncSession,
    *,
    directive_id,
    customer_id: str,
    option_id: str,
    booking_id=None,
):
    ok, reason, directive = await directive_service.verify(db, directive_id, customer_id, option_id)
    if not ok:
        # Expired -> generate fresh with current availability
        if reason == "expired" and directive:
            # Caller will generate new SELECT_SLOT with fresh slots
            return {"error": "expired", "directive": directive, "reason": reason}
        return {"error": reason, "directive": directive}
    # Complete (idempotent)
    directive = await directive_service.complete(db, directive_id, option_id, booking_id=booking_id)
    return {"ok": True, "directive": directive, "selected_option": directive.selected_option}
