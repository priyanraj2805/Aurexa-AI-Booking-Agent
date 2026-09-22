"""RBAC seed definitions — versioned source of truth for roles/permissions.

Mirrors backend-super's permission-string convention (resource.action).
Roles are per-org; permissions are global codes seeded by migration rbac_001.
Keep the code lists in sync with that migration.
"""

from typing import Any, Dict, List

TENANT_PERMISSIONS: List[str] = [
    "bookings.view", "bookings.manage", "bookings.cancel",
    "schedule.view", "schedule.manage",
    "catalog.view", "catalog.manage",
    "inbox.view", "inbox.manage",
    "integrations.view", "integrations.manage",
    "billing.view",
    "users.view", "users.manage",
    "roles.assign", "roles.view",
    "reports.view",
    "settings.view", "settings.manage",
]

TENANT_ROLES: List[Dict[str, Any]] = [
    {
        "code": "owner",
        "name": "Owner",
        "default": True,
        "inherit_to_children": False,
        "permissions": list(TENANT_PERMISSIONS),
    },
    {
        "code": "manager",
        "name": "Manager",
        "inherit_to_children": False,
        "permissions": [p for p in TENANT_PERMISSIONS if p != "roles.assign"],
    },
    {
        "code": "staff",
        "name": "Staff",
        "inherit_to_children": False,
        "permissions": [
            "bookings.view", "bookings.manage", "bookings.cancel",
            "schedule.view",
            "catalog.view",
            "inbox.view", "inbox.manage",
            "integrations.view",
            "reports.view",
        ],
    },
    {
        "code": "viewer",
        "name": "Viewer",
        "inherit_to_children": False,
        "permissions": [
            "bookings.view",
            "schedule.view",
            "catalog.view",
            "inbox.view",
            "integrations.view",
            "billing.view",
            "users.view",
            "roles.view",
            "reports.view",
            "settings.view",
        ],
    },
]