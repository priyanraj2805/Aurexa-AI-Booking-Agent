"""Support endpoints (Phase 8) — read-only conversation peek with PII masking.

Reads flow through the read-only DB pool (SELECT-only role); transcripts are
masked before they leave the API. Every peek is audit-logged.

Phase 12 — org-admin support tickets. Tickets are stored in the control
plane; the tenant backend creates/lists them for an authenticated org admin
presenting the shared service key, and super admins triage the queue.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.config import settings
from app.core.consent_pages import consent_page
from app.core.impersonation_notify import send_peek_confirmation_link_email
from app.core.inapp_notify import create_inapp_notification
from app.core.otp import (
    PURPOSE_PEEK,
    has_valid_grant,
    issue_otp,
    verify_otp,
)
from app.core.pii import mask_dict, mask_text, mask_value
from app.core.rbac import CurrentAdmin, require_permission
from app.core.security import sha256_hex
from app.core.stepup import require_stepup
from app.core.support_ticket_notify import send_ticket_status_email
from app.db.database import get_db, get_readonly_db

router = APIRouter()

SESSION_COLUMNS = (
    "id, channel, session_status, current_intent, human_agent_active, "
    "created_at, updated_at"
)


def _require_service_key(
    x_support_service_key: Optional[str] = Header(None),
) -> None:
    """Guard internal ticket endpoints: the caller must present the shared
    service key that only the tenant backend knows."""
    if not x_support_service_key or not secrets.compare_digest(
        x_support_service_key, settings.SUPPORT_SERVICE_KEY
    ):
        raise HTTPException(status_code=403, detail="Invalid service key")


def _enforce_unmask_guard(
    request: Request, admin: CurrentAdmin, reason: Optional[str]
) -> None:
    """Gate unmasked PII access: MFA step-up + mandatory justification.

    Every transcript reveal must be freshly authenticated (step-up cookie)
    and justified; the reason is audit-logged on the peek.
    """
    require_stepup(request, admin)
    if not reason or not reason.strip() or len(reason.strip()) < 10:
        raise HTTPException(
            status_code=422,
            detail="A reason (min 10 characters) is required to view unmasked PII",
        )


async def _org_admin_email(rdb: AsyncSession, org_id: UUID) -> str:
    """The single registering admin email for an org: the earliest-created
    ACTIVE user in the tenant admin-panel login table. Exactly one admin —
    never broadcast to all org users."""
    email = (
        await rdb.execute(
            text(
                """
                SELECT email FROM users
                WHERE org_id = :org_id AND is_active = true
                ORDER BY created_at ASC
                LIMIT 1
                """
            ),
            {"org_id": org_id},
        )
    ).scalar()
    if not email:
        raise HTTPException(
            status_code=404,
            detail="No active admin account found for this organization",
        )
    return email


async def _has_peek_consent(
    db: AsyncSession, admin: CurrentAdmin, org_id: UUID
) -> bool:
    """True when the org admin has approved a transcript-peek request for this
    super admin + org (permanent grant — the admin explicitly approved)."""
    row = (
        await db.execute(
            text(
                """
                SELECT 1 FROM super_admin.peek_requests
                WHERE admin_id = :admin_id AND org_id = :org_id
                  AND status = 'approved'
                LIMIT 1
                """
            ),
            {"admin_id": admin.id, "org_id": org_id},
        )
    ).first()
    return row is not None


async def _enforce_peek_grant(
    db: AsyncSession, admin: CurrentAdmin, org_id: UUID
) -> None:
    """Conversation transcripts require approval from the org admin.

    Grant sources (either is sufficient): an org-admin consent link that was
    clicked ("approved" peek request, Phase 17), or a legacy verified OTP
    grant (Phase 14) for a short peek window.
    """
    if await _has_peek_consent(db, admin, org_id):
        return
    if await has_valid_grant(
        db,
        purpose=PURPOSE_PEEK,
        admin_id=admin.id,
        org_id=org_id,
    ):
        return
    raise HTTPException(
        status_code=403,
        detail="Org-admin consent required: request one via /support/peek/request",
    )


class PeekConsentRequest(BaseModel):
    org_id: UUID
    session_id: Optional[UUID] = None


async def _peek_consent_row(db: AsyncSession, token: str):
    """Look up the peek request whose stored hash matches the token from the
    emailed link (constant-time comparison against the hash)."""
    return (
        await db.execute(
            text(
                """
                SELECT id, admin_id, org_id, org_name, session_id,
                       channel_user_id, status, recipient_email,
                       approval_expires_at, approver_email, approved_at
                FROM super_admin.peek_requests
                WHERE approval_token_hash = :token_hash
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"token_hash": sha256_hex(token)},
        )
    ).mappings().first()


@router.post("/peek/request")
async def peek_request_consent(
    payload: PeekConsentRequest,
    db: AsyncSession = Depends(get_db),
    rdb: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    """Issue a transcript-peek consent request and email the org admin a
    confirmation link — the same consent flow as impersonation.

    The link is single-use, TTL'd, never returned to the caller, and SMTP
    absence fails the request (fail-closed). Superceding a pending request
    invalidates the previous link so only the newest one can approve.
    """
    org_name = (
        await rdb.execute(
            text("SELECT name FROM organizations WHERE id = :org_id"),
            {"org_id": payload.org_id},
        )
    ).scalar()
    if not org_name:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not settings.SMTP_HOST:
        raise HTTPException(
            status_code=502,
            detail="Email delivery is not configured — cannot send the confirmation link",
        )

    recipient = await _org_admin_email(rdb, payload.org_id)

    channel_user_id = None
    if payload.session_id:
        channel_user_id = (
            await rdb.execute(
                text(
                    "SELECT channel_user_id FROM chat_sessions "
                    "WHERE id = :session_id AND org_id = :org_id"
                ),
                {"session_id": payload.session_id, "org_id": payload.org_id},
            )
        ).scalar()

    # Any still-open request for the same admin+org is superseded: its old
    # link stops working so only the newest confirmation can approve.
    await db.execute(
        text(
            """
            UPDATE super_admin.peek_requests
            SET status = 'expired', approval_token_hash = NULL
            WHERE admin_id = :admin_id AND org_id = :org_id
              AND status = 'pending'
            """
        ),
        {"admin_id": admin.id, "org_id": payload.org_id},
    )

    token = secrets.token_urlsafe(32)
    token_hash = sha256_hex(token)
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.IMPERSONATION_APPROVAL_LINK_TTL_HOURS
    )
    row_id = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.peek_requests
                    (admin_id, org_id, org_name, session_id, channel_user_id,
                     status, recipient_email, approval_token_hash,
                     approval_expires_at)
                VALUES (:admin_id, :org_id, :org_name, :session_id,
                        :channel_user_id, 'pending', :recipient_email,
                        :token_hash, :expires_at)
                RETURNING id
                """
            ),
            {
                "admin_id": admin.id,
                "org_id": payload.org_id,
                "org_name": org_name,
                "session_id": payload.session_id,
                "channel_user_id": channel_user_id,
                "recipient_email": recipient,
                "token_hash": token_hash,
                "expires_at": expires_at,
            },
        )
    ).scalar()

    link = (
        f"{settings.IMPERSONATION_CONSENT_BASE_URL.rstrip('/')}"
        f"/api/v1/support/peek/consent/{token}"
    )
    sent = send_peek_confirmation_link_email(
        to_email=recipient,
        org_name=org_name,
        link=link,
        ttl_hours=settings.IMPERSONATION_APPROVAL_LINK_TTL_HOURS,
        channel_user_id=channel_user_id,
    )
    if not sent:
        await db.execute(
            text(
                """
                UPDATE super_admin.peek_requests
                SET status = 'expired', approval_token_hash = NULL
                WHERE id = :id
                """
            ),
            {"id": row_id},
        )
        await db.commit()
        raise HTTPException(
            status_code=502,
            detail="Failed to deliver the confirmation email — no link was issued",
        )
    await db.commit()

    await write_audit_log(
        actor_email=admin.email,
        action="peek.consent.request",
        resource_type="peek_request",
        resource_id=str(row_id),
        new_values={
            "org_id": str(payload.org_id),
            "org_name": org_name,
            "session_id": str(payload.session_id) if payload.session_id else None,
            "channel_user_id": channel_user_id,
            "link_sent_to": recipient,
            "link_expires_at": expires_at.isoformat(),
            "link_ttl_hours": settings.IMPERSONATION_APPROVAL_LINK_TTL_HOURS,
        },
    )
    return {
        "id": str(row_id),
        "org_id": str(payload.org_id),
        "org_name": org_name,
        "session_id": str(payload.session_id) if payload.session_id else None,
        "status": "pending",
        "link_sent_to": recipient,
        "link_expires_at": expires_at.isoformat(),
        "link_ttl_hours": settings.IMPERSONATION_APPROVAL_LINK_TTL_HOURS,
    }


@router.get("/peek/request/{request_id}")
async def peek_request_status(
    request_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    """Poll endpoint: current status of a peek consent request."""
    row = (
        await db.execute(
            text(
                """
                SELECT id, org_id, org_name, session_id, channel_user_id,
                       status, recipient_email, approver_email, approved_at,
                       approval_expires_at, created_at
                FROM super_admin.peek_requests
                WHERE id = :id AND admin_id = :admin_id
                """
            ),
            {"id": request_id, "admin_id": admin.id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Peek request not found")

    status = row["status"]
    if (
        status == "pending"
        and row["approval_expires_at"]
        and datetime.now(timezone.utc) > row["approval_expires_at"]
    ):
        status = "expired"
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "org_name": row["org_name"],
        "session_id": str(row["session_id"]) if row["session_id"] else None,
        "channel_user_id": row["channel_user_id"],
        "status": status,
        "link_sent_to": row["recipient_email"],
        "approver_email": row["approver_email"],
        "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
        "expires_at": (
            row["approval_expires_at"].isoformat()
            if row["approval_expires_at"]
            else None
        ),
        "created_at": row["created_at"].isoformat(),
    }


@router.get("/peek/consent/{token}", response_class=HTMLResponse)
async def peek_consent_page(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public page the org admin reaches from the emailed confirmation link.
    Shows the request details and an "I Confirm" button (form POST)."""
    row = await _peek_consent_row(db, token)
    if not row:
        return consent_page(
            "Invalid link",
            "NOT RECOGNISED",
            "bad",
            "<p>This confirmation link is not valid. It may have been used "
            "already or issued for a different request. Ask the super admin "
            "to send a fresh request if you were expecting one.</p>",
        )
    if row["status"] == "approved":
        return consent_page(
            "Already confirmed",
            "APPROVED",
            "ok",
            "<p>This request was already approved by your account. The super "
            "admin can now open conversation transcripts in your organization."
            "</p>",
        )
    if (
        row["approval_expires_at"]
        and datetime.now(timezone.utc) > row["approval_expires_at"]
    ):
        return consent_page(
            "Link expired",
            "EXPIRED",
            "bad",
            f"<p>This confirmation link expired on "
            f"{row['approval_expires_at'].strftime('%Y-%m-%d %H:%M UTC')}.</p>"
            f"<p>Ask the super admin to send a new request if access is still "
            f"needed.</p>",
        )
    detail = (
        f"about a conversation with <strong>{row['channel_user_id']}</strong>"
        f" in " if row["channel_user_id"] else " in "
    )
    return consent_page(
        "Transcript access request",
        "ACTION REQUIRED",
        "info",
        f"<p>A super admin has requested permission to review a support "
        f"conversation {detail}<strong>{row['org_name']}</strong>.</p>"
        f"<p>You are receiving this because you are the registered account "
        f"for this organization. Once you confirm, the super admin can open "
        f"read-only transcripts. Every view is audit-logged.</p>"
        f'<form method="post" action="/api/v1/support/peek/consent/{token}/confirm">'
        f'<button type="submit">I Confirm</button></form>'
        f'<p style="font-size:12px">If you did not expect this request, simply '
        f"ignore this email. It expires automatically and no access is granted"
        f" without your confirmation.</p>",
    )


@router.post("/peek/consent/{token}/confirm", response_class=HTMLResponse)
async def peek_confirm_consent(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """The org admin clicked "I Confirm": approve the peek consent request.
    Single-use — a used or expired link cannot approve twice."""
    row = await _peek_consent_row(db, token)
    if not row:
        return consent_page(
            "Invalid link",
            "NOT RECOGNISED",
            "bad",
            "<p>This confirmation link is not valid. It may have been used "
            "already or issued for a different request.</p>",
        )
    if row["status"] == "approved":
        return consent_page(
            "Already confirmed",
            "APPROVED",
            "ok",
            "<p>This request was already approved by your account.</p>",
        )
    if (
        row["approval_expires_at"]
        and datetime.now(timezone.utc) > row["approval_expires_at"]
    ):
        return consent_page(
            "Link expired",
            "EXPIRED",
            "bad",
            "<p>This confirmation link has expired. No access was granted. "
            "Ask the super admin to send a new request.</p>",
        )

    await db.execute(
        text(
            """
            UPDATE super_admin.peek_requests
            SET status = 'approved',
                approver_email = :approver,
                approved_at = now(),
                approval_token_hash = NULL
            WHERE id = :id AND status = 'pending'
            """
        ),
        {"id": row["id"], "approver": row["recipient_email"]},
    )
    # Alert the requesting super admin in-app so they know the org admin
    # accepted and the transcript is now unlocked.
    if row.get("admin_id"):
        await create_inapp_notification(
            db,
            admin_id=row["admin_id"],
            kind="peek.approved",
            title=f"Transcript access approved by {row['recipient_email']}",
            body=(
                f"{row['recipient_email']} confirmed the request to review "
                f"conversations in {row['org_name']}. You can now open the "
                f"transcript."
            ),
            resource_type="peek",
            resource_id=str(row["id"]),
        )
    await db.commit()
    await write_audit_log(
        actor_email=row["recipient_email"],
        action="peek.consent.confirmed",
        resource_type="peek_request",
        resource_id=str(row["id"]),
        new_values={
            "org_id": str(row["org_id"]),
            "org_name": row["org_name"],
            "session_id": str(row["session_id"]) if row["session_id"] else None,
            "recipient_email": row["recipient_email"],
        },
    )
    return consent_page(
        "Confirmation recorded",
        "CONFIRMED",
        "ok",
        "<p>Thank you. Transcript access for your organization has been "
        "approved — the super admin can now open read-only conversation "
        "transcripts. Every view is audit-logged.</p>",
    )


class PeekOtpRequest(BaseModel):
    org_id: UUID
    otp: Optional[str] = None


@router.post("/peek/request-otp")
async def peek_request_otp(
    payload: PeekOtpRequest,
    db: AsyncSession = Depends(get_db),
    rdb: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    """Send the org-admin consent OTP for conversation peek.

    The OTP goes to the single registering admin of the org (earliest-created
    active user in the tenant users table). Fail-closed: no SMTP → 502 and
    no OTP is ever returned.
    """
    org_name = (
        await rdb.execute(
            text("SELECT name FROM organizations WHERE id = :org_id"),
            {"org_id": payload.org_id},
        )
    ).scalar()
    if not org_name:
        raise HTTPException(status_code=404, detail="Organization not found")

    recipient = await _org_admin_email(rdb, payload.org_id)
    return await issue_otp(
        db,
        purpose=PURPOSE_PEEK,
        admin_id=admin.id,
        org_id=payload.org_id,
        org_name=org_name,
        recipient_email=recipient,
        actor_email=admin.email,
    )


@router.post("/peek/verify-otp")
async def peek_verify_otp(
    payload: PeekOtpRequest,
    db: AsyncSession = Depends(get_db),
    rdb: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    """Verify the org-admin consent OTP. On success the peek grant is active
    for PEEK_GRANT_TTL_MINUTES so several transcripts can be opened."""
    if not payload.otp:
        raise HTTPException(status_code=422, detail="OTP is required")

    org_name = (
        await rdb.execute(
            text("SELECT name FROM organizations WHERE id = :org_id"),
            {"org_id": payload.org_id},
        )
    ).scalar()
    if not org_name:
        raise HTTPException(status_code=404, detail="Organization not found")

    await verify_otp(
        db,
        purpose=PURPOSE_PEEK,
        admin_id=admin.id,
        org_id=payload.org_id,
        otp=payload.otp,
        actor_email=admin.email,
    )
    return {
        "ok": True,
        "org_id": str(payload.org_id),
        "grant_ttl_minutes": settings.PEEK_GRANT_TTL_MINUTES,
    }


@router.get("/peek/grant")
async def peek_grant_status(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    """Whether the current admin may peek transcripts for an org: the org
    admin approved a consent request, or a legacy verified OTP grant is still
    inside its peek window."""
    granted = await _has_peek_consent(db, admin, org_id) or await has_valid_grant(
        db,
        purpose=PURPOSE_PEEK,
        admin_id=admin.id,
        org_id=org_id,
    )
    return {"org_id": str(org_id), "granted": granted}


class SupportTicketCreate(BaseModel):
    org_id: UUID
    org_name: str
    reporter_email: str
    reporter_name: Optional[str] = None
    subject: str
    detail: str
    priority: str = "normal"


class SupportTicketManage(BaseModel):
    status: Optional[str] = None
    reply: Optional[str] = None


@router.get("/orgs/{org_id}/conversations")
async def list_conversations(
    org_id: UUID,
    request: Request,
    unmask: bool = Query(False),
    reason: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    rdb: AsyncSession = Depends(get_readonly_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    can_unmask = unmask and admin.has_permission("pii.unmask")
    if can_unmask:
        _enforce_unmask_guard(request, admin, reason)

    exists = (
        await rdb.execute(
            text("SELECT 1 FROM organizations WHERE id = :org_id"),
            {"org_id": org_id},
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="Organization not found")

    offset = (page - 1) * page_size
    total = (
        await rdb.execute(
            text("SELECT count(*) FROM chat_sessions WHERE org_id = :org_id"),
            {"org_id": org_id},
        )
    ).scalar()
    rows = (
        await rdb.execute(
            text(
                f"""
                SELECT {SESSION_COLUMNS},
                       channel_user_id,
                       (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
                FROM chat_sessions s
                WHERE s.org_id = :org_id
                ORDER BY s.updated_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"org_id": org_id, "limit": page_size, "offset": offset},
        )
    ).mappings().all()
    items = [
        {
            "id": str(r["id"]),
            "channel": r["channel"],
            "channel_user_id": (
                r["channel_user_id"]
                if can_unmask and r["channel_user_id"]
                else (
                    mask_value("channel_user_id", r["channel_user_id"])
                    if r["channel_user_id"]
                    else None
                )
            ),
            "session_status": r["session_status"],
            "current_intent": r["current_intent"],
            "human_agent_active": r["human_agent_active"],
            "message_count": r["message_count"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]
    return {"total": total or 0, "page": page, "page_size": page_size, "items": items}


@router.get("/orgs/{org_id}/conversations/{session_id}/messages")
async def peek_conversation(
    org_id: UUID,
    session_id: UUID,
    request: Request,
    unmask: bool = Query(False),
    reason: Optional[str] = Query(None),
    rdb: AsyncSession = Depends(get_readonly_db),
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    can_unmask = unmask and admin.has_permission("pii.unmask")
    if can_unmask:
        _enforce_unmask_guard(request, admin, reason)
    await _enforce_peek_grant(db, admin, org_id)

    session = (
        await rdb.execute(
            text(
                f"""
                SELECT {SESSION_COLUMNS}, channel_user_id, state_data
                FROM chat_sessions
                WHERE id = :session_id AND org_id = :org_id
                """
            ),
            {"session_id": session_id, "org_id": org_id},
        )
    ).mappings().first()
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = (
        await rdb.execute(
            text(
                """
                SELECT id, role, content, sender_type, created_at
                FROM chat_messages
                WHERE session_id = :session_id
                ORDER BY created_at ASC
                """
            ),
            {"session_id": session_id},
        )
    ).mappings().all()

    masked_state = {}
    if session["state_data"]:
        try:
            raw = json.loads(session["state_data"])
            masked_state = raw if isinstance(raw, dict) and can_unmask else (
                mask_dict(raw) if isinstance(raw, dict) else raw
            )
        except Exception:
            masked_state = {"_masked": True}

    await write_audit_log(
        actor_email=admin.email,
        action="support.conversation.peek",
        resource_type="conversation",
        resource_id=str(session_id),
        new_values={
            "org_id": str(org_id),
            "message_count": len(messages),
            "unmasked": can_unmask,
            "reason": reason.strip() if can_unmask else None,
            "org_consent": "approval",
        },
    )

    return {
        "session_id": str(session["id"]),
        "channel": session["channel"],
        "channel_user_id": (
            session["channel_user_id"]
            if can_unmask and session["channel_user_id"]
            else (
                mask_value("channel_user_id", session["channel_user_id"])
                if session["channel_user_id"]
                else None
            )
        ),
        "session_status": session["session_status"],
        "state_data": masked_state,
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"] if can_unmask else mask_text(m["content"]),
                "sender_type": m["sender_type"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in messages
        ],
    }


@router.get("/sessions/{session_id}/peek")
async def peek_session(
    session_id: UUID,
    request: Request,
    unmask: bool = Query(False),
    reason: Optional[str] = Query(None),
    rdb: AsyncSession = Depends(get_readonly_db),
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.conversations.view")),
):
    """Peek one conversation by session id (org resolved from the row)."""
    can_unmask = unmask and admin.has_permission("pii.unmask")
    if can_unmask:
        _enforce_unmask_guard(request, admin, reason)

    session = (
        await rdb.execute(
            text(
                f"""
                SELECT {SESSION_COLUMNS}, channel_user_id, state_data, org_id
                FROM chat_sessions
                WHERE id = :session_id
                """
            ),
            {"session_id": session_id},
        )
    ).mappings().first()
    if not session:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _enforce_peek_grant(db, admin, session["org_id"])

    messages = (
        await rdb.execute(
            text(
                """
                SELECT id, role, content, sender_type, created_at
                FROM chat_messages
                WHERE session_id = :session_id
                ORDER BY created_at ASC
                """
            ),
            {"session_id": session_id},
        )
    ).mappings().all()

    masked_state = {}
    if session["state_data"]:
        try:
            raw = json.loads(session["state_data"])
            masked_state = raw if isinstance(raw, dict) and can_unmask else (
                mask_dict(raw) if isinstance(raw, dict) else raw
            )
        except Exception:
            masked_state = {"_masked": True}

    await write_audit_log(
        actor_email=admin.email,
        action="support.conversation.peek",
        resource_type="conversation",
        resource_id=str(session_id),
        new_values={
            "org_id": str(session["org_id"]),
            "message_count": len(messages),
            "unmasked": can_unmask,
            "reason": reason.strip() if can_unmask else None,
            "org_consent": "approval",
        },
    )

    return {
        "session_id": str(session["id"]),
        "channel": session["channel"],
        "channel_user_id": (
            session["channel_user_id"]
            if can_unmask and session["channel_user_id"]
            else (
                mask_value("channel_user_id", session["channel_user_id"])
                if session["channel_user_id"]
                else None
            )
        ),
        "session_status": session["session_status"],
        "state_data": masked_state,
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"] if can_unmask else mask_text(m["content"]),
                "sender_type": m["sender_type"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in messages
        ],
    }


# ── Phase 12: org-admin support tickets ────────────────────────────────

TICKET_COLUMNS = (
    "id, org_id, org_name, reporter_email, reporter_name, subject, detail, "
    "priority, status, reply, replied_by_email, replied_at, resolved_at, "
    "created_at, updated_at"
)


def _ticket_dict(row) -> dict:
    d = dict(row)
    for key in ("created_at", "updated_at", "replied_at", "resolved_at"):
        if d.get(key):
            d[key] = d[key].isoformat()
    d["org_id"] = str(d["org_id"])
    return d


@router.post("/tickets", dependencies=[Depends(_require_service_key)])
async def create_ticket(
    payload: SupportTicketCreate,
    db: AsyncSession = Depends(get_db),
):
    """Internal — the tenant backend creates a ticket for an authed org admin.

    Guarded by the shared service key; the tenant backend resolves the
    reporter's org itself so a caller can never file a ticket for another org.
    """
    subject = payload.subject.strip()
    detail = payload.detail.strip()
    if not subject or not detail:
        raise HTTPException(status_code=422, detail="Subject and detail are required")

    ticket_id = (
        await db.execute(
            text(
                """
                INSERT INTO super_admin.support_tickets
                    (org_id, org_name, reporter_email, reporter_name, subject,
                     detail, priority, status)
                VALUES
                    (:org_id, :org_name, :reporter_email, :reporter_name, :subject,
                     :detail, :priority, 'open')
                RETURNING id
                """
            ),
            {
                "org_id": payload.org_id,
                "org_name": payload.org_name,
                "reporter_email": payload.reporter_email,
                "reporter_name": payload.reporter_name,
                "subject": subject,
                "detail": detail,
                "priority": payload.priority if payload.priority in ("low", "normal", "high", "urgent") else "normal",
            },
        )
    ).scalar()
    await db.commit()

    await write_audit_log(
        actor_email=payload.reporter_email,
        action="support.ticket.create",
        resource_type="support_ticket",
        resource_id=str(ticket_id),
        new_values={
            "org_id": str(payload.org_id),
            "subject": subject,
            "priority": payload.priority,
        },
    )
    return {"id": str(ticket_id), "status": "open"}


@router.get("/tickets", dependencies=[Depends(_require_service_key)])
async def list_org_tickets(
    org_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Internal — the tenant backend lists tickets for ONE authenticated org."""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT {TICKET_COLUMNS}
                FROM super_admin.support_tickets
                WHERE org_id = :org_id
                ORDER BY created_at DESC
                """
            ),
            {"org_id": org_id},
        )
    ).mappings().all()
    return {"items": [_ticket_dict(r) for r in rows]}


@router.get("/queue")
async def list_ticket_queue(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.tickets.view")),
):
    """Super admin queue — every org's tickets in one place."""
    clauses = []
    params: dict = {"limit": limit}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if priority:
        clauses.append("priority = :priority")
        params["priority"] = priority
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT {TICKET_COLUMNS}
                FROM super_admin.support_tickets
                {where}
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()
    return {"items": [_ticket_dict(r) for r in rows]}


@router.get("/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.tickets.view")),
):
    row = (
        await db.execute(
            text(
                f"SELECT {TICKET_COLUMNS} FROM super_admin.support_tickets WHERE id = :id"
            ),
            {"id": ticket_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _ticket_dict(row)


@router.post("/tickets/{ticket_id}/manage")
async def manage_ticket(
    ticket_id: UUID,
    payload: SupportTicketManage,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.tickets.manage")),
):
    """Super admin triages a ticket: change status and/or reply to the org."""
    row = (
        await db.execute(
            text(
                f"SELECT {TICKET_COLUMNS} FROM super_admin.support_tickets WHERE id = :id"
            ),
            {"id": ticket_id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")

    new_status = payload.status
    if new_status is not None and new_status not in ("open", "in_progress", "resolved"):
        raise HTTPException(status_code=422, detail=f"Invalid status: {new_status}")

    if new_status is None and payload.reply is None:
        raise HTTPException(status_code=422, detail="Nothing to update")

    updates = ["updated_at = now()"]
    params: dict = {"id": ticket_id}
    if new_status:
        updates.append("status = :status")
        params["status"] = new_status
        if new_status == "resolved":
            updates.append("resolved_at = now()")
    if payload.reply is not None:
        updates.append("reply = :reply")
        updates.append("replied_by_id = :replied_by_id")
        updates.append("replied_by_email = :replied_by_email")
        updates.append("replied_at = now()")
        params["reply"] = payload.reply
        params["replied_by_id"] = admin.id
        params["replied_by_email"] = admin.email

    await db.execute(
        text(f"UPDATE super_admin.support_tickets SET {', '.join(updates)} WHERE id = :id"),
        params,
    )
    await db.commit()

    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="support.ticket.manage",
        resource_type="support_ticket",
        resource_id=str(ticket_id),
        new_values={
            "status": new_status,
            "has_reply": bool(payload.reply),
            "replied_at": datetime.now(timezone.utc).isoformat() if payload.reply else None,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    send_ticket_status_email(
        to_email=row["reporter_email"],
        org_name=row["org_name"],
        subject=row["subject"],
        status=new_status or row["status"],
        reply=payload.reply,
    )
    return {"ok": True}
