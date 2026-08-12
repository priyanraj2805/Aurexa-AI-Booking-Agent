"""Impersonation endpoints (Phase 8 + Phase 15 org-admin consent LINK).

Flow (Phase 15): support.impersonate + MFA step-up → impersonation REQUEST
(status 'pending') → a confirmation LINK (24h TTL) is emailed to the
impersonated user (the org admin) → the admin opens it and clicks
"I Confirm" on the hosted page → status becomes 'approved' → the super admin
re-confirms with a fresh MFA step-up → the bridge issues the 30-min tenant
JWT and the row becomes 'active'. Starting a confirmed impersonation ends
any prior active one for the same admin (single active session).

The 6-digit OTP flow (Phase 14) still applies to conversation PEEK; this
module only issues emailed consent links. The link token is stored as a
sha256 hash (never plaintext), single-use, expires after 24h, and SMTP
absence fails the request (fail-closed).

Tenant-side enforcement is unchanged: get_current_user rejects any JWT whose
jti has no matching un-revoked public.impersonation_sessions row, and the
trusted backend revokes the session on stop.
"""
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.bridge_client import bridge_call
from app.core.config import settings
from app.core.consent_pages import consent_page as _consent_page
from app.core.impersonation_notify import send_confirmation_link_email
from app.core.inapp_notify import create_inapp_notification
from app.core.rbac import CurrentAdmin, require_permission
from app.core.security import create_impersonation_handshake, sha256_hex
from app.core.stepup import require_stepup
from app.api.v1.endpoints.support import _require_service_key
from app.db.database import get_db

router = APIRouter()


class ImpersonationStartRequest(BaseModel):
    org_id: UUID
    user_email: str


class ImpersonationStopRequest(BaseModel):
    pass


async def _end_active(db: AsyncSession, admin: CurrentAdmin, ended_by: UUID):
    rows = (
        await db.execute(
            text(
                """
                SELECT id, token_jti FROM super_admin.impersonations
                WHERE admin_id = :admin_id AND status = 'active'
                """
            ),
            {"admin_id": admin.id},
        )
    ).mappings().all()
    for row in rows:
        try:
            await bridge_call(
                "POST",
                "/impersonate/stop",
                admin.email,
                json_body={"token_jti": row["token_jti"]},
            )
        except Exception:
            pass
        await db.execute(
            text(
                """
                UPDATE super_admin.impersonations
                SET status = 'ended', ended_at = now(), ended_by = :ended_by
                WHERE id = :id AND status = 'active'
                """
            ),
            {"id": row["id"], "ended_by": ended_by},
        )
        await write_audit_log(
            actor_id=ended_by,
            actor_email=admin.email,
            action="impersonation.end",
            resource_type="impersonation",
            resource_id=str(row["id"]),
            new_values={"reason": "superseded"},
        )


@router.post("/start")
async def start_impersonation(
    payload: ImpersonationStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    """Request impersonation. Emails the org admin a 24h confirmation link.

    No tenant JWT is issued here — the request stays 'pending' until the org
    admin opens the link and clicks "I Confirm". Only then can the super
    admin confirm the session (which requires a fresh MFA step-up).
    """
    require_stepup(request, admin)

    org_name = (
        await db.execute(
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

    user_email = payload.user_email.strip().lower()

    # Any still-open request for the same admin+org+user is superseded: its
    # old link stops working so only the newest confirmation can approve.
    await db.execute(
        text(
            """
            UPDATE super_admin.impersonations
            SET status = 'expired', approval_token_hash = NULL
            WHERE admin_id = :admin_id AND org_id = :org_id
              AND user_email = :user_email AND status = 'pending'
            """
        ),
        {
            "admin_id": admin.id,
            "org_id": payload.org_id,
            "user_email": user_email,
        },
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
                INSERT INTO super_admin.impersonations
                    (admin_id, org_id, org_name, user_email, status,
                     approval_token_hash, approval_expires_at)
                VALUES (:admin_id, :org_id, :org_name, :user_email, 'pending',
                        :token_hash, :expires_at)
                RETURNING id
                """
            ),
            {
                "admin_id": admin.id,
                "org_id": payload.org_id,
                "org_name": org_name,
                "user_email": user_email,
                "token_hash": token_hash,
                "expires_at": expires_at,
            },
        )
    ).scalar()

    link = (
        f"{settings.IMPERSONATION_CONSENT_BASE_URL.rstrip('/')}"
        f"/api/v1/impersonation/consent/{token}"
    )
    sent = send_confirmation_link_email(
        to_email=user_email,
        org_name=org_name,
        user_email=user_email,
        link=link,
        ttl_hours=settings.IMPERSONATION_APPROVAL_LINK_TTL_HOURS,
    )
    if not sent:
        await db.execute(
            text(
                """
                UPDATE super_admin.impersonations
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
        action="impersonation.request",
        resource_type="impersonation",
        resource_id=str(row_id),
        new_values={
            "org_id": str(payload.org_id),
            "org_name": org_name,
            "user_email": user_email,
            "link_sent_to": user_email,
            "link_expires_at": expires_at.isoformat(),
            "link_ttl_hours": settings.IMPERSONATION_APPROVAL_LINK_TTL_HOURS,
        },
    )
    return {
        "id": str(row_id),
        "org_id": str(payload.org_id),
        "org_name": org_name,
        "user_email": user_email,
        "status": "pending",
        "link_sent_to": user_email,
        "link_expires_at": expires_at.isoformat(),
        "link_ttl_hours": settings.IMPERSONATION_APPROVAL_LINK_TTL_HOURS,
    }


async def _consent_row(db: AsyncSession, token: str):
    """Look up the impersonation request whose stored hash matches the token
    from the emailed link (constant-time comparison against the hash)."""
    return (
        await db.execute(
            text(
                """
                SELECT id, admin_id, org_id, org_name, user_email, status,
                       approval_expires_at, approver_email, approved_at
                FROM super_admin.impersonations
                WHERE approval_token_hash = :token_hash
                ORDER BY started_at DESC
                LIMIT 1
                """
            ),
            {"token_hash": sha256_hex(token)},
        )
    ).mappings().first()


@router.get("/consent/{token}", response_class=HTMLResponse)
async def consent_page(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public page the org admin reaches from the emailed confirmation link.
    Shows the request details and an "I Confirm" button (form POST)."""
    row = await _consent_row(db, token)
    if not row:
        return _consent_page(
            "Invalid link",
            "NOT RECOGNISED",
            "bad",
            "<p>This confirmation link is not valid. It may have been used "
            "already or issued for a different request. Ask the super admin "
            "to send a fresh request if you were expecting one.</p>",
        )
    if row["status"] == "approved":
        return _consent_page(
            "Already confirmed",
            "APPROVED",
            "ok",
            "<p>This request was already approved by your account. The super "
            "admin can now start the impersonation session.</p>",
        )
    if (
        row["approval_expires_at"]
        and datetime.now(timezone.utc) > row["approval_expires_at"]
    ):
        return _consent_page(
            "Link expired",
            "EXPIRED",
            "bad",
            f"<p>This confirmation link expired on "
            f"{row['approval_expires_at'].strftime('%Y-%m-%d %H:%M UTC')}.</p>"
            f"<p>Ask the super admin to send a new request if access is still "
            f"needed.</p>",
        )
    return _consent_page(
        "Impersonation access request",
        "ACTION REQUIRED",
        "info",
        f"<p>A super admin has requested to impersonate</p>"
        f"<p><strong>{row['user_email']}</strong> in <strong>{row['org_name']}</strong>."
        f"</p><p>You are receiving this because you are a registered account "
        f"for this organization. Once you confirm, the super admin can act on "
        f"your behalf. Every action they take is audit-logged.</p>"
        f'<form method="post" action="/api/v1/impersonation/consent/{token}/confirm">'
        f'<button type="submit">I Confirm</button></form>'
        f'<p style="font-size:12px">If you did not expect this request, simply '
        f"ignore this email. It expires automatically and no access is granted"
        f" without your confirmation.</p>",
    )


@router.post("/consent/{token}/confirm", response_class=HTMLResponse)
async def confirm_consent(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """The org admin clicked "I Confirm": approve the impersonation request.
    Single-use — a used or expired link cannot approve twice."""
    row = await _consent_row(db, token)
    if not row:
        return _consent_page(
            "Invalid link",
            "NOT RECOGNISED",
            "bad",
            "<p>This confirmation link is not valid. It may have been used "
            "already or issued for a different request.</p>",
        )
    if row["status"] == "approved":
        return _consent_page(
            "Already confirmed",
            "APPROVED",
            "ok",
            "<p>This request was already approved by your account.</p>",
        )
    if (
        row["approval_expires_at"]
        and datetime.now(timezone.utc) > row["approval_expires_at"]
    ):
        return _consent_page(
            "Link expired",
            "EXPIRED",
            "bad",
            "<p>This confirmation link has expired. No access was granted. "
            "Ask the super admin to send a new request.</p>",
        )

    await db.execute(
        text(
            """
            UPDATE super_admin.impersonations
            SET status = 'approved',
                approver_email = :approver,
                approved_at = now(),
                approval_token_hash = NULL
            WHERE id = :id AND status = 'pending'
            """
        ),
        {"id": row["id"], "approver": row["user_email"]},
    )
    # Phase 16: alert the requesting super admin in-app so they know the
    # org admin accepted and the session can now be started.
    if row.get("admin_id"):
        await create_inapp_notification(
            db,
            admin_id=row["admin_id"],
            kind="impersonation.approved",
            title=f"Impersonation approved by {row['user_email']}",
            body=(
                f"{row['user_email']} confirmed the request to impersonate them "
                f"in {row['org_name']}. Click to start the session."
            ),
            resource_type="impersonation",
            resource_id=str(row["id"]),
        )
    await db.commit()
    await write_audit_log(
        actor_email=row["user_email"],
        action="impersonation.consent.confirmed",
        resource_type="impersonation",
        resource_id=str(row["id"]),
        new_values={
            "org_id": str(row["org_id"]),
            "org_name": row["org_name"],
            "user_email": row["user_email"],
        },
    )
    return _consent_page(
        "Confirmation recorded",
        "CONFIRMED",
        "ok",
        "<p>Thank you. The impersonation request has been approved — the "
        "super admin can now start acting on your behalf. Every action they "
        "take will be visible in the audit log.</p>",
    )


@router.post("/{impersonation_id}/confirm")
async def confirm_impersonation(
    impersonation_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    """After org approval, mint the 30-min tenant token for the admin.

    Requires a fresh MFA step-up, since the approval may have taken hours.
    Ends any other active session for this admin first (single active).
    """
    require_stepup(request, admin)

    row = (
        await db.execute(
            text(
                """
                SELECT id, org_id, org_name, user_email, status
                FROM super_admin.impersonations
                WHERE id = :id AND admin_id = :admin_id
                """
            ),
            {"id": impersonation_id, "admin_id": admin.id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Impersonation request not found")
    if row["status"] != "approved":
        raise HTTPException(
            status_code=409, detail=f"Cannot confirm: status is '{row['status']}'"
        )

    await _end_active(db, admin, admin.id)

    result = await bridge_call(
        "POST",
        "/impersonate/start",
        admin.email,
        json_body={
            "org_id": str(row["org_id"]),
            "user_email": row["user_email"],
        },
    )
    jti = result["token_jti"]
    expires_at = datetime.fromisoformat(result["expires_at"])
    await db.execute(
        text(
            """
            UPDATE super_admin.impersonations
            SET status = 'active', token_jti = :jti, expires_at = :expires_at
            WHERE id = :id
            """
        ),
        {"id": impersonation_id, "jti": jti, "expires_at": expires_at},
    )
    await db.commit()

    await write_audit_log(
        actor_email=admin.email,
        action="impersonation.start",
        resource_type="impersonation",
        resource_id=str(impersonation_id),
        new_values={
            "org_id": str(row["org_id"]),
            "org_name": row["org_name"],
            "user_email": row["user_email"],
            "expires_at": result["expires_at"],
        },
    )

    # Phase 11b: hand the super admin into the tenant admin-panel as the
    # impersonated user via a short-lived handshake ticket — the raw tenant
    # JWT is NEVER exposed in this UI. The tenant backend validates the
    # handshake (shared bridge secret) and mints its own impersonation JWT.
    handshake = create_impersonation_handshake(
        user_email=row["user_email"],
        org_id=str(row["org_id"]),
        jti=jti,
        administrator_email=admin.email,
    )
    console_url = (
        f"{settings.TENANT_CONSOLE_URL.rstrip('/')}"
        f"/impersonate/enter?hs={handshake}"
    )

    return {
        "id": str(impersonation_id),
        "org_id": str(row["org_id"]),
        "org_name": row["org_name"],
        "user_email": row["user_email"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": result["expires_at"],
        "session_active": True,
        "console_url": console_url,
    }


@router.post("/{impersonation_id}/stop")
async def stop_impersonation(
    impersonation_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    row = (
        await db.execute(
            text(
                """
                SELECT id, token_jti, status FROM super_admin.impersonations
                WHERE id = :id AND admin_id = :admin_id
                """
            ),
            {"id": impersonation_id, "admin_id": admin.id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Impersonation not found")
    if row["status"] != "active":
        return {"ok": True, "already": True}

    try:
        await bridge_call(
            "POST",
            "/impersonate/stop",
            admin.email,
            json_body={"token_jti": row["token_jti"]},
        )
    except Exception:
        pass
    await db.execute(
        text(
            """
            UPDATE super_admin.impersonations
            SET status = 'ended', ended_at = now(), ended_by = :ended_by
            WHERE id = :id AND status = 'active'
            """
        ),
        {"id": impersonation_id, "ended_by": admin.id},
    )
    await write_audit_log(
        actor_id=admin.id,
        actor_email=admin.email,
        action="impersonation.end",
        resource_type="impersonation",
        resource_id=str(impersonation_id),
    )
    await db.commit()
    return {"ok": True}


@router.get("/active")
async def get_active_impersonation(
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    row = (
        await db.execute(
            text(
                """
                SELECT id, org_id, org_name, user_email, started_at, expires_at,
                       token_jti
                FROM super_admin.impersonations
                WHERE admin_id = :admin_id AND status = 'active'
                  AND expires_at > now()
                ORDER BY started_at DESC LIMIT 1
                """
            ),
            {"admin_id": admin.id},
        )
    ).mappings().first()
    if not row:
        return None
    # Mint a FRESH single-use handshake on every fetch so the panel always
    # shows a valid "Open console" link even after the previous one was
    # consumed (handshake_used_at on the tenant side is strictly one-time).
    handshake = create_impersonation_handshake(
        user_email=row["user_email"],
        org_id=str(row["org_id"]),
        jti=row["token_jti"],
        administrator_email=admin.email,
    )
    console_url = (
        f"{settings.TENANT_CONSOLE_URL.rstrip('/')}"
        f"/impersonate/enter?hs={handshake}"
    )
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "org_name": row["org_name"],
        "user_email": row["user_email"],
        "started_at": row["started_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        "console_url": console_url,
    }


@router.get("/{impersonation_id}")
async def get_impersonation(
    impersonation_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    """Fetch one impersonation request (for UI polling of approval state)."""
    row = (
        await db.execute(
            text(
                """
                SELECT id, org_id, org_name, user_email, status,
                       started_at, expires_at, ended_at,
                       approved_at, rejected_at, approver_email
                FROM super_admin.impersonations
                WHERE id = :id AND admin_id = :admin_id
                """
            ),
            {"id": impersonation_id, "admin_id": admin.id},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Impersonation request not found")
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "org_name": row["org_name"],
        "user_email": row["user_email"],
        "status": row["status"],
        "started_at": row["started_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
        "rejected_at": row["rejected_at"].isoformat() if row["rejected_at"] else None,
        "approver_email": row["approver_email"],
    }


@router.get("")
async def list_impersonations(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    admin: CurrentAdmin = Depends(require_permission("support.impersonate")),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT id, org_name, user_email, status, started_at, expires_at, ended_at
                FROM super_admin.impersonations
                WHERE admin_id = :admin_id
                ORDER BY started_at DESC
                LIMIT :limit
                """
            ),
            {"admin_id": admin.id, "limit": min(limit, 200)},
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "org_name": r["org_name"],
            "user_email": r["user_email"],
            "status": r["status"],
            "started_at": r["started_at"].isoformat(),
            "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
        }
        for r in rows
    ]


class TenantExitRequest(BaseModel):
    token_jti: str
    user_email: str


@router.post("/tenant-exit", dependencies=[Depends(_require_service_key)])
async def tenant_exit(
    payload: TenantExitRequest,
    db: AsyncSession = Depends(get_db),
):
    """Called best-effort by the tenant backend when the impersonated user
    hits "Exit impersonation": mirrors the revocation onto the control plane
    so super_admin.impersonations does not stay 'active' until the admin
    clicks "End now"."""
    row = (
        await db.execute(
            text(
                """
                SELECT id FROM super_admin.impersonations
                WHERE token_jti = :jti AND status = 'active'
                """
            ),
            {"jti": payload.token_jti},
        )
    ).mappings().first()
    if not row:
        return {"ok": True, "changed": False}
    await db.execute(
        text(
            """
            UPDATE super_admin.impersonations
            SET status = 'ended', ended_at = now(), ended_by = NULL
            WHERE id = :id AND status = 'active'
            """
        ),
        {"id": row["id"]},
    )
    await db.commit()
    await write_audit_log(
        actor_email=payload.user_email,
        action="impersonation.end",
        resource_type="impersonation",
        resource_id=str(row["id"]),
        new_values={"reason": "tenant_user_exit"},
    )
    return {"ok": True, "changed": True}
