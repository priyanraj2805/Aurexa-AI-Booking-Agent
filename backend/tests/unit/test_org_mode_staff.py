"""Phase 2: staff invite + scope shapes."""

import sys

sys.path.insert(0, "backend")

from app.api.v1.endpoints.staff import (
    ALLOWED_ROLES,
    InviteAccept,
    InviteCreate,
)


def test_invite_create_defaults():
    d = InviteCreate(email="reception@h.com")
    assert d.role_code == "staff"
    assert d.unit_ids is None


def test_allowed_roles_cover_org_jobs():
    # owner / manager / staff(reception) / viewer map to mall jobs
    for r in ("owner", "manager", "staff", "viewer"):
        assert r in ALLOWED_ROLES


def test_accept_shape():
    a = InviteAccept(token="x" * 12, full_name="Reception", password="secret123")
    assert a.full_name == "Reception"
